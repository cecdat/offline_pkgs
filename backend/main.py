import os
import shutil
import tarfile
import docker
import logging
import glob  # [新增] 用于查找文件
import re    # [新增] 用于清理文件名非法字符
from logging.handlers import RotatingFileHandler
from datetime import datetime
from uuid import uuid4
from typing import List
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

# ================= 配置日志 =================
LOG_DIR = "/app/logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE_PATH = os.path.join(LOG_DIR, "opaas_service.log")

logger = logging.getLogger("OPaaS")
logger.setLevel(logging.INFO)
logger.handlers.clear()

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

file_handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ================= 全局变量 =================
CONTAINER_WORK_DIR = "/app/data/temp_tasks"
HOST_DATA_PATH = os.getenv("HOST_DATA_PATH", os.getcwd() + "/data")
TEMPLATE_DIR = "/app/templates"
STATIC_DIR = "/app/static"

DISTRO_MAP = {
    # Ubuntu 系列
    "ubuntu_26_series": "downloader:ubuntu-rolling", # 对应未来的 26
    "ubuntu_25_10": "downloader:ubuntu-25.10",
    "ubuntu_24_04": "downloader:ubuntu-24.04",
    "ubuntu_22_04": "downloader:ubuntu-22.04",
    "ubuntu_20_04": "downloader:ubuntu-20.04", # 顺手加上 20.04
    "ubuntu_18_04": "downloader:ubuntu-18.04",
    "ubuntu_16_04": "downloader:ubuntu-16.04",

    # Debian 系列
    "debian_13": "downloader:debian-13", # Trixie
    "debian_12": "downloader:debian-12",
    "debian_11": "downloader:debian-11"
}

app = FastAPI()
docker_client = docker.from_env()
jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

os.makedirs(CONTAINER_WORK_DIR, exist_ok=True)
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="static")

logger.info(f"服务启动 | 宿主机路径: {HOST_DATA_PATH}")

# ================= 辅助函数 =================

def sanitize_filename(name: str) -> str:
    """清理文件名，防止非法字符"""
    return re.sub(r'[^a-zA-Z0-9.\-_]', '', name)

def get_task_file_path(task_id: str):
    """根据 task_id 模糊查找生成的 tar.gz 文件"""
    # 查找模式: *_{task_id}.tar.gz
    pattern = os.path.join(CONTAINER_WORK_DIR, f"*_{task_id}.tar.gz")
    files = glob.glob(pattern)
    if files:
        return files[0] # 返回找到的第一个匹配文件
    return None

# ================= 模型 =================
class TaskRequest(BaseModel):
    distro: str
    arch: str
    packages: List[str]

# ================= 逻辑 =================
def generate_script(task_dir: str, distro: str, arch: str, packages: List[str]):
    try:
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
    except Exception as e:
        logger.error(f"脚本生成失败: {e}", exc_info=True)
        raise

def run_docker_worker(task_id: str, image: str, distro: str, arch: str, packages: List[str]):
    container_task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    host_task_dir = os.path.join(HOST_DATA_PATH, "temp_tasks", task_id)
    
    logger.info(f"[{task_id}] 启动任务 | 镜像: {image} | 架构: {arch} | 包: {len(packages)}个")

    try:
        # 启动 Docker
        docker_client.containers.run(
            image=image,
            volumes={ host_task_dir: {'bind': '/output', 'mode': 'rw'} },
            environment={
                "PACKAGES": " ".join(packages),
                "TARGET_ARCH": arch
            },
            remove=True,
            network_mode="host"
        )
        
        deb_dir = os.path.join(container_task_dir, "deb")
        if not os.path.exists(deb_dir) or not os.listdir(deb_dir):
            logger.error(f"[{task_id}] 失败: 下载目录为空")
            return

        # [修改点] 生成友好的文件名
        # 格式: 第一个包名_系统_架构_ID.tar.gz
        first_pkg = sanitize_filename(packages[0])
        safe_distro = sanitize_filename(distro)
        
        friendly_name = f"{first_pkg}_{safe_distro}_{arch}_{task_id}.tar.gz"
        tar_path = os.path.join(CONTAINER_WORK_DIR, friendly_name)
        
        logger.info(f"[{task_id}] 开始打包: {friendly_name}")

        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(container_task_dir, arcname="offline_pkg")
            
        logger.info(f"[{task_id}] 打包成功")
        shutil.rmtree(container_task_dir) # 清理临时目录

    except Exception as e:
        logger.error(f"[{task_id}] 异常: {e}", exc_info=True)

# ================= API =================
@app.post("/api/create_task")
async def create_task(req: TaskRequest, background_tasks: BackgroundTasks):
    if req.distro not in DISTRO_MAP:
        raise HTTPException(status_code=400, detail="不支持的系统")
    
    task_id = str(uuid4())[:8]
    task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    generate_script(task_dir, req.distro, req.arch, req.packages)
    
    background_tasks.add_task(
        run_docker_worker, 
        task_id, 
        DISTRO_MAP[req.distro], 
        req.distro, # 传入 distro 字符串用于文件名
        req.arch, 
        req.packages
    )
    
    return {"task_id": task_id}

@app.get("/api/check/{task_id}")
async def check_task(task_id: str):
    # [修改点] 使用 glob 模糊查找文件
    file_path = get_task_file_path(task_id)
    
    if file_path and os.path.exists(file_path):
        return {"status": "completed", "download_url": f"/api/download/{task_id}"}
    
    task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    if os.path.exists(task_dir):
        deb_dir = os.path.join(task_dir, "deb")
        count = len(os.listdir(deb_dir)) if os.path.exists(deb_dir) else 0
        return {"status": "processing", "files_downloaded": count}
        
    return {"status": "failed"}

@app.get("/api/download/{task_id}")
async def download(task_id: str):
    # [修改点] 查找真实文件路径
    file_path = get_task_file_path(task_id)
    
    if file_path and os.path.exists(file_path):
        filename = os.path.basename(file_path)
        return FileResponse(file_path, media_type="application/gzip", filename=filename)
        
    raise HTTPException(status_code=404, detail="文件不存在")

@app.get("/")
async def root():
    return RedirectResponse(url="/ui/index.html")