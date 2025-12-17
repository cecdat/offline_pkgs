#!/bin/bash
set -e

echo "=== 1. 构建下载器基础镜像 (适应 Oracle Cloud) ==="

# Ubuntu 24.04
echo ">>> 构建 downloader:ubuntu-24.04 ..."
docker build --build-arg BASE_IMAGE=ubuntu:24.04 -t downloader:ubuntu-24.04 ./builder

# Debian 12
echo ">>> 构建 downloader:debian-12 ..."
docker build --build-arg BASE_IMAGE=debian:12 -t downloader:debian-12 ./builder

# Debian 11
echo ">>> 构建 downloader:debian-11 ..."
docker build --build-arg BASE_IMAGE=debian:11 -t downloader:debian-11 ./builder

echo "=== 2. 创建数据目录 ==="
mkdir -p ./data/temp_tasks
chmod -R 777 ./data  # 确保容器内有权限写入

echo "=== 准备就绪！请运行 docker-compose up -d ==="
