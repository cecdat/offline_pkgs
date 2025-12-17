# 📦 OPaaS - Linux 离线包工场 (Linux Offline Package Factory)

**OPaaS** 是一个专门为**内网（Air-gapped）环境**服务器设计的可视化离线软件下载平台。它能自动解析并下载软件及其所有依赖链，并打包生成一键安装脚本，彻底解决离线环境下的“依赖地狱”问题。

---

## 🛠️ 核心运行机制 (How it Works)

OPaaS 并不仅仅是一个简单的 `apt download` 包装器。为了保证下载的依赖是**纯净且完整**的，它采用了独特的**容器化沙箱**与**跨架构模拟**技术：

### 1. 兄弟容器架构 (Sibling Containers)
当你在 Web 界面提交一个任务时，OPaaS 的后端（运行在 Docker 中）会调用宿主机的 Docker 守护进程，**动态启动一个新的临时容器**（Worker）。
*   这个 Worker 容器是一个纯净的 OS 环境（如纯净的 Ubuntu 24.04）。
*   它不依赖宿主机的任何库，确保下载的依赖包完全基于目标系统的初始状态计算，从而避免“宿主机已安装该依赖导致漏下载”的问题。

### 2. 跨架构全量下载 (Cross-Architecture)
本项目最核心的特性是支持**跨架构下载**。
*   **场景**：你的办公电脑是 Windows/Mac (x86_64)，但你需要给内网的 **华为鲲鹏/树莓派 (ARM64)** 服务器下载软件。
*   **原理**：Worker 容器在启动时，会根据你选择的目标架构（Target Arch），自动执行以下黑科技：
    1.  `dpkg --add-architecture <target_arch>`：让 APT 包管理器“骗”过系统，认为自己支持目标架构。
    2.  **智能换源**：自动识别架构，x86 使用 `archive.ubuntu.com`，ARM 使用 `ports.ubuntu.com`。
    3.  `apt-get install --download-only`：只下载，不安装，确保不会因为架构不兼容而报错。

### 3. "零依赖" 安装包
下载完成后，系统会自动生成一个 `install.sh` 脚本，并与所有 `.deb` 文件一起打包成 `.tar.gz`。
在离线服务器上，你**不需要安装任何额外的工具**（如 Python/Docker），只需要系统自带的 `bash` 和 `tar` 即可完成安装。

---

## 🚀 快速部署

### 环境要求
*   **宿主机**：Linux (Ubuntu/Debian/CentOS 均可)，需要能访问公网。
*   **核心组件**：Docker, Docker Compose。

### 部署步骤

#### 第一步：构建基础镜像 (必做)
OPaaS 需要预先构建好用于执行下载任务的“基底镜像”。
> **注意**：即使你的宿主机是 ARM 架构（如 Oracle Cloud Ampere），此脚本也能自动构建出支持下载 x86 软件包的镜像。

```bash
# 赋予脚本执行权限
chmod +x build_images.sh builder/entrypoint.sh

# 开始构建 (耗时约 1-3 分钟)
./build_images.sh
```

#### 第二步：启动服务
```bash
docker-compose up -d
```

#### 第三步：访问界面
打开浏览器访问：`http://<宿主机IP>:8000`

---

## 📖 使用指南

1.  **选择系统**：选择离线服务器的操作系统版本（如 `Ubuntu 24.04`）。
2.  **选择架构**：
    *   **amd64 (x86_64)**：适用于 Intel/AMD 芯片的常规服务器。
    *   **arm64 (aarch64)**：适用于 鲲鹏、飞腾、树莓派、Mac M1/M2/M3 (Linux VM)、Oracle ARM 实例。
3.  **输入软件包**：输入你想安装的软件名，支持多个，用空格隔开。
    *   *例如*：`vim nginx htop docker.io`
4.  **构建 & 下载**：点击“构建”，等待任务完成，下载生成的 `.tar.gz` 文件。

### 在离线服务器上安装

将下载的压缩包上传到内网服务器，然后执行：

```bash
# 1. 解压
tar -xzvf offline_pkg_xxxx.tar.gz

# 2. 进入目录
cd offline_pkg_xxxx

# 3. 一键安装
# 脚本会自动配置本地 apt 源并安装所有 deb 包
sudo bash install.sh
```

---

## 📂 目录结构说明

```text
.
├── backend/                # Web 服务后端 (FastAPI + Vue3)
│   ├── main.py             # 任务通过 Docker SDK 调度兄弟容器
│   └── templates/          # install.sh 的生成模板
├── builder/                # 下载器镜像 (Worker) 的构建文件
│   └── entrypoint.sh       # [核心] 处理跨架构换源与下载逻辑的脚本
├── data/                   # [自动生成] 挂载到宿主机的数据目录
│   └── temp_tasks/         # 临时的下载任务目录
├── build_images.sh         # 初始化脚本：构建 downloader 镜像
└── docker-compose.yml      # 服务编排
```

## ⚠️ 常见问题
1.  **下载速度慢？**
    下载速度取决于宿主机连接 Ubuntu/Debian 官方源的速度。建议在网速较好的机器上部署本服务。
2.  **支持哪些系统？**
    目前支持 Ubuntu 24.04, Debian 11, Debian 12。如需更多系统，需修改 `builder/Dockerfile` 和 `backend/main.py`。