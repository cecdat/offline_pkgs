#!/bin/bash
set -e

# =========================================================
# 接收环境变量：
#   PACKAGES: 包列表 (例如 "vim nginx")
#   TARGET_ARCH: 目标架构 (例如 "amd64" 或 "arm64")
# =========================================================

OUTPUT_DIR="/output"
DEB_DIR="$OUTPUT_DIR/deb"
APT_TMP="/tmp/apt-download"

# 默认架构处理
TARGET_ARCH=${TARGET_ARCH:-"amd64"} 

echo ">>> [下载器] 启动..."
echo ">>> 宿主架构: $(dpkg --print-architecture)"
echo ">>> 目标架构: $TARGET_ARCH"
echo ">>> 待下载包: $PACKAGES"

if [ -z "$PACKAGES" ]; then
    echo "[错误] 未指定 PACKAGES 环境变量"
    exit 1
fi

# 1. 准备目录
mkdir -p "$DEB_DIR"
mkdir -p "$APT_TMP"/{state,cache,lists,etc}
mkdir -p "$APT_TMP/var/lib/dpkg"
touch "$APT_TMP/state/status"

# 2. 启用多架构支持 (核心步骤)
# 如果目标架构与当前容器架构不同，需要添加
CURRENT_ARCH=$(dpkg --print-architecture)
if [ "$TARGET_ARCH" != "$CURRENT_ARCH" ]; then
    echo ">>> 启用交叉架构支持: $TARGET_ARCH"
    dpkg --add-architecture "$TARGET_ARCH"
fi

# 3. 智能生成 sources.list (区分 x86 和 ARM 的源地址)
source /etc/os-release
SOURCES_FILE="$APT_TMP/etc/sources.list"

# 定义 URL 逻辑
if [ "$ID" == "ubuntu" ]; then
    if [ "$TARGET_ARCH" == "amd64" ] || [ "$TARGET_ARCH" == "i386" ]; then
        # x86 架构使用 archive 源
        REPO_URL="http://archive.ubuntu.com/ubuntu"
        SEC_URL="http://security.ubuntu.com/ubuntu"
    else
        # arm64/riscv 等架构使用 ports 源
        REPO_URL="http://ports.ubuntu.com/ubuntu-ports"
        SEC_URL="http://ports.ubuntu.com/ubuntu-ports"
    fi

    echo ">>> Ubuntu 源地址: $REPO_URL"
    cat > "$SOURCES_FILE" <<EOF
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME} main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME}-updates main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${SEC_URL} ${VERSION_CODENAME}-security main universe multiverse restricted
EOF

elif [ "$ID" == "debian" ]; then
    # Debian 的 mirror 通常包含全架构，但为了保险使用主源
    REPO_URL="http://deb.debian.org/debian"
    SEC_URL="http://security.debian.org/debian-security"
    
    COMPONENTS="main contrib non-free"
    if [ "$VERSION_ID" -ge 12 ]; then
        COMPONENTS="main contrib non-free non-free-firmware"
    fi
    
    echo ">>> Debian 源地址: $REPO_URL"
    cat > "$SOURCES_FILE" <<EOF
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME} ${COMPONENTS}
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME}-updates ${COMPONENTS}
deb [arch=$TARGET_ARCH] ${SEC_URL} ${VERSION_CODENAME}-security ${COMPONENTS}
EOF
fi

# 4. 配置 APT
# 关键点：强制指定 APT::Architecture 为目标架构
APT_OPTS=(
  -o Dir="$APT_TMP"
  -o Dir::State="$APT_TMP/state"
  -o Dir::State::Status="$APT_TMP/state/status"
  -o Dir::Cache="$APT_TMP/cache"
  -o Dir::Etc::sourcelist="$SOURCES_FILE"
  -o Dir::Etc::sourceparts="-"
  -o Dir::Etc::Trusted="/etc/apt/trusted.gpg"
  -o Dir::Etc::TrustedParts="/etc/apt/trusted.gpg.d"
  -o APT::Architecture="$TARGET_ARCH"
  -o APT::Install-Recommends=false 
  -o APT::Get::Download-Only=true
  -o Acquire::Retries=3
)

# 5. 更新与下载
echo ">>> 更新索引..."
apt-get "${APT_OPTS[@]}" update

echo ">>> 开始下载 (架构: $TARGET_ARCH)..."
# 注意：这里需要在包名后面加上 :$TARGET_ARCH 吗？
# 因为我们配置了 APT::Architecture，apt 会以为自己就是那个架构，所以通常不需要加后缀，
# 但为了保险，可以尝试直接 install。如果 apt 认为是在本机安装，它会寻找 Native 包。
# 由于我们是 Download-Only 且伪造了 status 文件，直接 install 即可。

if apt-get "${APT_OPTS[@]}" -o Dir::Cache::archives="$DEB_DIR" install --reinstall -y $PACKAGES; then
    COUNT=$(ls "$DEB_DIR"/*.deb 2>/dev/null | wc -l)
    echo ">>> 下载成功！共 $COUNT 个文件"
    rm -rf "$DEB_DIR/partial" "$DEB_DIR/lock"
    chmod -R 777 "$OUTPUT_DIR"
else
    echo "[错误] 下载失败，请检查包名是否在 $TARGET_ARCH 下可用"
    exit 1
fi