import os
import shutil
import tarfile
import docker
import docker.errors # [关键] 用于捕获容器退出码非0时的输出
import logging
import glob
import hashlib
import time
import re
import json
import redis
from logging.handlers import RotatingFileHandler
from datetime import datetime
from uuid import uuid4
from typing import List
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

# ================= 日志配置 =================
LOG_DIR = "/app/logs"
os.makedirs(LOG_DIR, exist_ok=True)
logger = logging.getLogger("OPaaS")
logger.setLevel(logging.INFO)
logger.handlers.clear()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler = RotatingFileHandler(os.path.join(LOG_DIR, "opaas.log"), maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

# ================= 全局配置 =================
CONTAINER_WORK_DIR = "/app/data/temp_tasks"
CACHE_DIR = "/app/data/cache"
TEMP_STORAGE_DIR = "/app/data/temp_files"
HOST_DATA_PATH = os.getenv("HOST_DATA_PATH", os.getcwd() + "/data")
REDIS_HOST = os.getenv("REDIS_HOST", "opaas-redis") 

for d in [CONTAINER_WORK_DIR, CACHE_DIR, TEMP_STORAGE_DIR]:
    os.makedirs(d, exist_ok=True)

TEMPLATE_DIR = "/app/templates"
STATIC_DIR = "/app/static"

DISTRO_MAP = {
    "ubuntu_26_series": "downloader:ubuntu-rolling",
    "ubuntu_25_10": "downloader:ubuntu-25.10",
    "ubuntu_24_04": "downloader:ubuntu-24.04",
    "ubuntu_22_04": "downloader:ubuntu-22.04",
    "ubuntu_18_04": "downloader:ubuntu-18.04",
    "ubuntu_16_04": "downloader:ubuntu-16.04",
    "debian_13": "downloader:debian-13",
    "debian_12": "downloader:debian-12",
    "debian_11": "downloader:debian-11"
}

# 常用包集合 (扁平化，用于判断缓存策略)
COMMON_PACKAGES_SET = {
    'docker.io', 'docker-compose-v2',
    'openssh-server', 'vim', 'net-tools',
    'curl', 'wget', 'git', 'build-essential',
    'python3-pip', 'unzip', 'zip',
    'mariadb-client', 'htop', 'jq'
}

# 初始化 Redis 连接
try:
    r_client = redis.Redis(host=REDIS_HOST, port=6379, db=0, decode_responses=True)
    r_client.ping()
    logger.info(f"Redis 连接成功: {REDIS_HOST}")
except Exception as e:
    logger.error(f"Redis 连接失败: {e}")
    # 注意：如果 Redis 连不上，建议直接退出或降级，这里暂不强制退出但后续会报错
    
app = FastAPI()
docker_client = docker.from_env()
jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="static")

# ================= 辅助函数 =================

def sanitize_filename(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9.\-_]', '', name)

def calculate_cache_key(distro: str, arch: str, packages: List[str]) -> str:
    sorted_pkgs = sorted(list(set(packages)))
    raw_str = f"{distro}|{arch}|{' '.join(sorted_pkgs)}"
    return hashlib.md5(raw_str.encode()).hexdigest()

def clean_old_temp_files():
    """清理物理过期的临时文件"""
    retention_seconds = 3 * 24 * 60 * 60 
    now = time.time()
    for f in glob.glob(os.path.join(TEMP_STORAGE_DIR, "*.tar.gz")):
        if os.stat(f).st_mtime < (now - retention_seconds):
            try:
                os.remove(f)
                logger.info(f"[清理] 删除过期物理文件: {os.path.basename(f)}")
            except Exception as e:
                logger.error(f"删除失败: {e}")

def is_common_request(packages: List[str]) -> bool:
    if not packages: return False
    for pkg in packages:
        if pkg not in COMMON_PACKAGES_SET:
            return False
    return True

# ================= 核心逻辑 =================

class TaskRequest(BaseModel):
    distro: str
    arch: str
    packages: List[str]

def generate_script(task_dir: str, distro: str, arch: str, packages: List[str]):
    template = jinja_env.get_template("install.sh.j2")
    content = template.render(
        distro=distro,
        arch=arch,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        packages=packages
    )
    script_path = os.path.join(task_dir, "install.sh")
    with open(script_path, "w") as f:
        f.write(content)
    os.chmod(script_path, 0o755)

def run_docker_worker(task_id: str, image: str, distro: str, arch: str, packages: List[str], cache_key: str, save_to_cache: bool):
    container_task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    host_task_dir = os.path.join(HOST_DATA_PATH, "temp_tasks", task_id)
    
    # 更新 Redis 状态
    r_client.hset(f"task:{task_id}", mapping={"status": "processing", "progress": "0"})
    
    logger.info(f"[{task_id}] 启动构建 | 镜像: {image}")

    try:
        # 启动容器
        docker_client.containers.run(
            image=image,
            volumes={ host_task_dir: {'bind': '/output', 'mode': 'rw'} },
            environment={ "PACKAGES": " ".join(packages), "TARGET_ARCH": arch },
            remove=True, 
            network_mode="host"
        )
        
        # 检查结果目录
        deb_dir = os.path.join(container_task_dir, "deb")
        if not os.path.exists(deb_dir) or not os.listdir(deb_dir):
            raise Exception("下载成功但目录为空，系统异常")

        first_pkg = sanitize_filename(packages[0])
        final_filename = ""
        final_path = ""
        
        if save_to_cache:
            final_filename = f"common_pkg_{cache_key[:8]}.tar.gz"
            final_path = os.path.join(CACHE_DIR, f"{cache_key}.tar.gz")
            logger.info(f"[{task_id}] 保存到高速缓存")
        else:
            final_filename = f"custom_pkg_{task_id}.tar.gz"
            final_path = os.path.join(TEMP_STORAGE_DIR, f"{task_id}.tar.gz")
            logger.info(f"[{task_id}] 保存到临时存储 (3天)")

        # 打包
        with tarfile.open(final_path, "w:gz") as tar:
            tar.add(container_task_dir, arcname=f"offline_pkg_{first_pkg}")

        shutil.rmtree(container_task_dir)

        # 更新 Redis 完成状态
        task_info = {
            "status": "completed",
            "file_path": final_path,
            "filename": final_filename
        }
        r_client.hset(f"task:{task_id}", mapping=task_info)
        
        # 临时文件设置 3 天过期
        if not save_to_cache:
            r_client.expire(f"task:{task_id}", 3 * 24 * 60 * 60)

        # 写入缓存索引
        if save_to_cache:
            cache_info = {
                "file_path": final_path,
                "filename": final_filename,
                "created_at": str(datetime.now())
            }
            r_client.set(f"cache:{cache_key}", json.dumps(cache_info))

    except docker.errors.ContainerError as e:
        # [关键] 捕获容器内部脚本的错误输出 (echo ... exit 1)
        error_msg = e.stderr.decode('utf-8').strip() if e.stderr else str(e)
        logger.error(f"[{task_id}] 容器构建失败: {error_msg}")
        r_client.hset(f"task:{task_id}", mapping={"status": "failed", "error": error_msg})

    except Exception as e:
        logger.error(f"[{task_id}] 系统异常: {e}", exc_info=True)
        r_client.hset(f"task:{task_id}", mapping={"status": "failed", "error": str(e)})

# ================= API =================

@app.post("/api/create_task")
async def create_task(req: TaskRequest, background_tasks: BackgroundTasks):
    clean_old_temp_files()

    if req.distro not in DISTRO_MAP:
        raise HTTPException(status_code=400, detail="不支持的系统")
    
    # 1. 计算指纹
    cache_key = calculate_cache_key(req.distro, req.arch, req.packages)
    is_common = is_common_request(req.packages)
    
    # 2. 检查缓存
    if is_common:
        cached_json = r_client.get(f"cache:{cache_key}")
        if cached_json:
            cache_data = json.loads(cached_json)
            if os.path.exists(cache_data["file_path"]):
                logger.info(f"Redis 缓存命中! Key: {cache_key}")
                pseudo_task_id = "CACHED_" + cache_key
                return {
                    "task_id": pseudo_task_id,
                    "status": "cached",
                    "download_url": f"/api/download/{pseudo_task_id}"
                }
            else:
                r_client.delete(f"cache:{cache_key}")

    # 3. 创建新任务
    task_id = str(uuid4())[:8]
    task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    r_client.hset(f"task:{task_id}", mapping={"status": "queued", "created_at": str(datetime.now())})

    generate_script(task_dir, req.distro, req.arch, req.packages)
    
    background_tasks.add_task(
        run_docker_worker, 
        task_id, 
        DISTRO_MAP[req.distro], 
        req.distro, 
        req.arch, 
        req.packages,
        cache_key,
        is_common
    )
    
    return {"task_id": task_id, "status": "queued"}

@app.get("/api/check/{task_id}")
async def check_task(task_id: str):
    if task_id.startswith("CACHED_"):
        return {"status": "completed", "download_url": f"/api/download/{task_id}"}

    task_info = r_client.hgetall(f"task:{task_id}")
    
    if not task_info:
        return {"status": "failed", "detail": "Task expired or not found"}
    
    status = task_info.get("status")
    
    if status == "completed":
        return {"status": "completed", "download_url": f"/api/download/{task_id}"}
    
    elif status == "failed":
        # 返回详细错误信息供前端展示
        return {"status": "failed", "detail": task_info.get("error", "未知错误")}
    
    else:
        # 处理中：读取物理文件数
        real_task_dir = os.path.join(CONTAINER_WORK_DIR, task_id, "deb")
        count = 0
        if os.path.exists(real_task_dir):
             count = len(os.listdir(real_task_dir))
        return {"status": "processing", "files_downloaded": count}

@app.get("/api/download/{task_id}")
async def download(task_id: str):
    file_path = ""
    filename = ""

    if task_id.startswith("CACHED_"):
        key = task_id.replace("CACHED_", "")
        cached_json = r_client.get(f"cache:{key}")
        if not cached_json:
            raise HTTPException(status_code=404, detail="Cache index missing")
        data = json.loads(cached_json)
        file_path = data["file_path"]
        filename = data["filename"]
    else:
        task_info = r_client.hgetall(f"task:{task_id}")
        if not task_info or task_info.get("status") != "completed":
             raise HTTPException(status_code=404, detail="Task not ready or expired")
        file_path = task_info["file_path"]
        filename = task_info["filename"]

    if file_path and os.path.exists(file_path):
        return FileResponse(file_path, media_type="application/gzip", filename=filename)
        
    raise HTTPException(status_code=404, detail="File lost on disk")

@app.get("/")
async def root():
    return RedirectResponse(url="/ui/index.html")