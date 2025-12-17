#!/bin/bash
set -e

# =========================================================
# 智能下载器入口脚本 (全版本适配版)
# 支持: Ubuntu 16.04 - 26.04+, Debian 11 - 13
# =========================================================

OUTPUT_DIR="/output"
DEB_DIR="$OUTPUT_DIR/deb"
APT_TMP="/tmp/apt-download"

TARGET_ARCH=${TARGET_ARCH:-"amd64"}

echo ">>> [Downloader] 启动..."
echo ">>> 系统: $(grep PRETTY_NAME /etc/os-release | cut -d= -f2)"
echo ">>> 目标架构: $TARGET_ARCH"

if [ -z "$PACKAGES" ]; then
    echo "[错误] 未指定 PACKAGES 环境变量"
    exit 1
fi

# 1. 准备目录
mkdir -p "$DEB_DIR"
mkdir -p "$APT_TMP"/{state,cache,lists,etc}
mkdir -p "$APT_TMP/var/lib/dpkg"
touch "$APT_TMP/state/status"

# 2. 启用多架构支持
CURRENT_ARCH=$(dpkg --print-architecture)
if [ "$TARGET_ARCH" != "$CURRENT_ARCH" ]; then
    echo ">>> 启用交叉架构支持: $TARGET_ARCH"
    dpkg --add-architecture "$TARGET_ARCH"
fi

# 3. 智能生成 sources.list
source /etc/os-release
SOURCES_FILE="$APT_TMP/etc/sources.list"

# 定义一些特殊的 EOL 版本 (End of Life)
EOL_UBUNTUS=("xenial" "bionic" "trusty") # 16.04, 18.04, 14.04

if [ "$ID" == "ubuntu" ]; then
    # === Ubuntu 逻辑 ===
    
    # 默认标准源
    REPO_URL="http://archive.ubuntu.com/ubuntu"
    SEC_URL="http://security.ubuntu.com/ubuntu"
    PORTS_URL="http://ports.ubuntu.com/ubuntu-ports"
    OLD_REL_URL="http://old-releases.ubuntu.com/ubuntu"

    # 判断是否为 EOL 版本
    IS_EOL=0
    for eol in "${EOL_UBUNTUS[@]}"; do
        if [ "$VERSION_CODENAME" == "$eol" ]; then
            IS_EOL=1
            break
        fi
    done

    # 架构与源的匹配逻辑
    if [ "$TARGET_ARCH" == "amd64" ] || [ "$TARGET_ARCH" == "i386" ]; then
        # x86 架构
        if [ "$IS_EOL" -eq 1 ]; then
            echo ">>> 检测到 EOL 旧版本 ($VERSION_CODENAME)，切换至 old-releases 源"
            REPO_URL="$OLD_REL_URL"
            SEC_URL="$OLD_REL_URL"
        fi
        # 生成 x86 源
        cat > "$SOURCES_FILE" <<EOF
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME} main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME}-updates main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${SEC_URL} ${VERSION_CODENAME}-security main universe multiverse restricted
EOF
    else
        # ARM/RISCV 架构 (Ports)
        # 注意: Ubuntu Ports通常保留旧版本较久，暂使用标准Ports源
        # 如果遇到极老版本可能需要 old-releases-ports，但这里暂用标准Ports
        cat > "$SOURCES_FILE" <<EOF
deb [arch=$TARGET_ARCH] ${PORTS_URL} ${VERSION_CODENAME} main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${PORTS_URL} ${VERSION_CODENAME}-updates main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${PORTS_URL} ${VERSION_CODENAME}-security main universe multiverse restricted
EOF
    fi

elif [ "$ID" == "debian" ]; then
    # === Debian 逻辑 ===
    
    REPO_URL="http://deb.debian.org/debian"
    SEC_URL="http://security.debian.org/debian-security"
    
    # Debian 12/13 需要 non-free-firmware
    # Debian 11 不需要
    COMPONENTS="main contrib non-free"
    if [ "$VERSION_ID" -ge 12 ] || [ "$VERSION_CODENAME" == "trixie" ] || [ "$VERSION_CODENAME" == "sid" ]; then
        COMPONENTS="main contrib non-free non-free-firmware"
    fi
    
    cat > "$SOURCES_FILE" <<EOF
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME} ${COMPONENTS}
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME}-updates ${COMPONENTS}
deb [arch=$TARGET_ARCH] ${SEC_URL} ${VERSION_CODENAME}-security ${COMPONENTS}
EOF
fi

# 4. 配置 APT
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
  -o Acquire::Check-Valid-Until=false  # [关键] 防止旧版本报错 Release file expired
)

# 5. 执行
echo ">>> 更新索引..."
# 忽略旧版本的 GPG 错误 (允许 insecure)
apt-get "${APT_OPTS[@]}" -o Acquire::AllowInsecureRepositories=true update || echo "Update有警告，尝试继续..."

echo ">>> 开始下载..."
if apt-get "${APT_OPTS[@]}" -o Dir::Cache::archives="$DEB_DIR" --allow-unauthenticated install --reinstall -y $PACKAGES; then
    COUNT=$(ls "$DEB_DIR"/*.deb 2>/dev/null | wc -l)
    echo ">>> 下载成功！共 $COUNT 个文件"
    rm -rf "$DEB_DIR/partial" "$DEB_DIR/lock"
    chmod -R 777 "$OUTPUT_DIR"
else
    echo "[错误] 下载失败"
    exit 1
fi