import os
import shutil
import tarfile
import docker
import logging
from logging.handlers import RotatingFileHandler  # [新增] 引入轮转处理器
from datetime import datetime
from uuid import uuid4
from typing import List
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jinja2 import Environment, FileSystemLoader

# ==========================================
# 1. 日志配置 (升级版：文件 + 控制台)
# ==========================================

# 定义容器内的日志目录
LOG_DIR = "/app/logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE_PATH = os.path.join(LOG_DIR, "opaas_service.log")

# 获取根记录器
logger = logging.getLogger("OPaaS")
logger.setLevel(logging.INFO)
# 清除旧的 handler 防止重复打印
logger.handlers.clear()

# 格式器
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# [处理器 1] 文件轮转 (单个文件最大 10MB，保留最近 5 个)
file_handler = RotatingFileHandler(LOG_FILE_PATH, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# [处理器 2] 控制台输出 (保留 docker logs 功能)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ==========================================
# 2. 全局配置 (以下代码保持不变)
# ==========================================
CONTAINER_WORK_DIR = "/app/data/temp_tasks"
HOST_DATA_PATH = os.getenv("HOST_DATA_PATH", os.getcwd() + "/data")

TEMPLATE_DIR = "/app/templates"
STATIC_DIR = "/app/static"

# 发行版镜像映射
DISTRO_MAP = {
    "ubuntu_24_04": "downloader:ubuntu-24.04",
    "debian_12": "downloader:debian-12",
    "debian_11": "downloader:debian-11"
}

app = FastAPI()
docker_client = docker.from_env()
jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))

# 初始化目录
os.makedirs(CONTAINER_WORK_DIR, exist_ok=True)
app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="static")

logger.info(f"系统启动完成。宿主机数据路径: {HOST_DATA_PATH}")

# ==========================================
# 3. 数据模型
# ==========================================
class TaskRequest(BaseModel):
    distro: str
    arch: str          # [关键] 目标架构: amd64 或 arm64
    packages: List[str]

# ==========================================
# 4. 核心逻辑函数
# ==========================================

def generate_script(task_dir: str, distro: str, arch: str, packages: List[str]):
    """渲染 install.sh 脚本"""
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
        logger.info(f"脚本生成成功: {script_path}")
    except Exception as e:
        logger.error(f"脚本生成失败: {e}", exc_info=True)
        raise

def run_docker_worker(task_id: str, image: str, arch: str, packages: List[str]):
    """后台任务：调用 Docker 下载并打包"""
    container_task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    host_task_dir = os.path.join(HOST_DATA_PATH, "temp_tasks", task_id)
    
    logger.info(f"[{task_id}] 开始处理任务 | 镜像: {image} | 架构: {arch} | 包数: {len(packages)}")

    try:
        # 1. 启动 Docker 容器 (兄弟容器模式)
        logger.info(f"[{task_id}] 启动下载容器...")
        docker_client.containers.run(
            image=image,
            volumes={
                # 将宿主机路径挂载到下载器容器内部
                host_task_dir: {'bind': '/output', 'mode': 'rw'}
            },
            environment={
                "PACKAGES": " ".join(packages),
                "TARGET_ARCH": arch  # [关键] 传入目标架构给 entrypoint.sh
            },
            remove=True,
            network_mode="host" # 使用 Host 网络以获得最佳速度
        )
        
        # 2. 检查下载结果
        deb_dir = os.path.join(container_task_dir, "deb")
        if not os.path.exists(deb_dir) or not os.listdir(deb_dir):
            logger.error(f"[{task_id}] 失败: 下载目录为空或不存在")
            return

        pkg_count = len(os.listdir(deb_dir))
        logger.info(f"[{task_id}] 下载完成，共 {pkg_count} 个文件。开始打包...")

        # 3. 打包为 tar.gz
        tar_filename = f"offline_pkg_{task_id}.tar.gz"
        tar_path = os.path.join(CONTAINER_WORK_DIR, tar_filename)
        
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(container_task_dir, arcname="offline_pkg")
            
        logger.info(f"[{task_id}] 打包成功: {tar_filename}")
        
        # 4. 清理临时目录 (保留 tar 包)
        shutil.rmtree(container_task_dir)
        logger.info(f"[{task_id}] 临时目录已清理")

    except docker.errors.ContainerError as e:
        logger.error(f"[{task_id}] Docker 容器执行错误: {e.stderr}")
    except Exception as e:
        logger.error(f"[{task_id}] 未知异常: {e}", exc_info=True)

# ==========================================
# 5. API 接口
# ==========================================

@app.post("/api/create_task")
async def create_task(req: TaskRequest, background_tasks: BackgroundTasks, request: Request):
    client_ip = request.client.host
    logger.info(f"收到请求 [{client_ip}] | 系统: {req.distro} | 架构: {req.arch} | 包: {req.packages}")

    if req.distro not in DISTRO_MAP:
        logger.warning(f"拒绝不支持的系统: {req.distro}")
        raise HTTPException(status_code=400, detail="不支持的系统发行版")
    
    if not req.packages:
        raise HTTPException(status_code=400, detail="包列表不能为空")

    task_id = str(uuid4())[:8]
    task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    try:
        # 1. 生成安装脚本
        generate_script(task_dir, req.distro, req.arch, req.packages)
        
        # 2. 加入后台队列
        background_tasks.add_task(
            run_docker_worker, 
            task_id, 
            DISTRO_MAP[req.distro], 
            req.arch, 
            req.packages
        )
        
        logger.info(f"任务已创建: {task_id}")
        return {"task_id": task_id, "status": "queued"}
        
    except Exception as e:
        logger.error(f"任务创建失败: {e}", exc_info=True)
        # 清理可能创建的目录
        if os.path.exists(task_dir): shutil.rmtree(task_dir)
        raise HTTPException(status_code=500, detail="服务器内部错误")

@app.get("/api/check/{task_id}")
async def check_task(task_id: str):
    # 检查成品文件
    tar_path = os.path.join(CONTAINER_WORK_DIR, f"offline_pkg_{task_id}.tar.gz")
    # 检查过程目录
    task_dir = os.path.join(CONTAINER_WORK_DIR, task_id)
    
    if os.path.exists(tar_path):
        return {"status": "completed", "download_url": f"/api/download/{task_id}"}
    
    if os.path.exists(task_dir):
        # 简单的进度估算：检查 deb 目录下有多少文件
        deb_dir = os.path.join(task_dir, "deb")
        count = 0
        if os.path.exists(deb_dir):
            count = len(os.listdir(deb_dir))
        return {"status": "processing", "files_downloaded": count}
        
    return {"status": "failed"}

@app.get("/api/download/{task_id}")
async def download(task_id: str):
    tar_path = os.path.join(CONTAINER_WORK_DIR, f"offline_pkg_{task_id}.tar.gz")
    if os.path.exists(tar_path):
        logger.info(f"文件被下载: {task_id}")
        return FileResponse(
            tar_path, 
            media_type="application/gzip", 
            filename=f"offline_pkg_{task_id}.tar.gz"
        )
    logger.warning(f"下载 404: {task_id}")
    raise HTTPException(status_code=404, detail="文件不存在")

@app.get("/")
async def root():
    return RedirectResponse(url="/ui/index.html")

if __name__ == "__main__":
    import uvicorn
    # 生产环境建议通过 docker CMD 启动，这里仅供调试
    uvicorn.run(app, host="0.0.0.0", port=8000)