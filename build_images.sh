#!/bin/bash
set -e

echo "=== 1. 构建 Ubuntu 系列镜像 ==="

# 26 Series (使用 rolling 标签代表开发版)
echo ">>> Building: ubuntu-rolling (Future 26.xx) ..."
docker build --build-arg BASE_IMAGE=ubuntu:rolling -t downloader:ubuntu-rolling ./builder

echo ">>> Building: ubuntu-25.10 ..."
docker build --build-arg BASE_IMAGE=ubuntu:25.10 -t downloader:ubuntu-25.10 ./builder

echo ">>> Building: ubuntu-24.04 ..."
docker build --build-arg BASE_IMAGE=ubuntu:24.04 -t downloader:ubuntu-24.04 ./builder

echo ">>> Building: ubuntu-22.04 ..."
docker build --build-arg BASE_IMAGE=ubuntu:22.04 -t downloader:ubuntu-22.04 ./builder

echo ">>> Building: ubuntu-18.04 (EOL) ..."
docker build --build-arg BASE_IMAGE=ubuntu:18.04 -t downloader:ubuntu-18.04 ./builder

echo ">>> Building: ubuntu-16.04 (EOL) ..."
docker build --build-arg BASE_IMAGE=ubuntu:16.04 -t downloader:ubuntu-16.04 ./builder

echo "=== 2. 构建 Debian 系列镜像 ==="

echo ">>> Building: debian-13 (Trixie) ..."
docker build --build-arg BASE_IMAGE=debian:trixie -t downloader:debian-13 ./builder

echo ">>> Building: debian-12 ..."
docker build --build-arg BASE_IMAGE=debian:12 -t downloader:debian-12 ./builder

echo ">>> Building: debian-11 ..."
docker build --build-arg BASE_IMAGE=debian:11 -t downloader:debian-11 ./builder

echo "=== 3. 权限修正 ==="
mkdir -p ./data/temp_tasks ./logs
chmod -R 777 ./data ./logs

echo "=== 全部镜像构建完成 ==="