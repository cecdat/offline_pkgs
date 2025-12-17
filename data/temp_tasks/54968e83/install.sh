#!/bin/bash
# 目标系统: ubuntu_24_04
# 生成时间: 2025-12-17 02:40:26
set -e
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
DEB_DIR="$BASE_DIR/deb"
LOG_FILE="$BASE_DIR/install.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo ">>> 开始安装 docker.io docker-compose-v2"
if [ ! -d "$DEB_DIR" ]; then echo "未找到deb目录"; exit 1; fi

echo ">>> [1/2] 解压..."
dpkg -i "$DEB_DIR"/*.deb >/dev/null 2>&1 || true
echo ">>> [2/2] 配置..."
dpkg -i "$DEB_DIR"/*.deb

echo ">>> 验证安装..."

dpkg -s docker.io >/dev/null 2>&1 && echo "[OK] docker.io" || echo "[FAIL] docker.io"

dpkg -s docker-compose-v2 >/dev/null 2>&1 && echo "[OK] docker-compose-v2" || echo "[FAIL] docker-compose-v2"
