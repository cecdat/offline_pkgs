#!/bin/bash
set -e

# =========================================================
# 智能下载器入口脚本 (修复版)
# 支持: Ubuntu 16.04 - 26.04+, Debian 11 - 13
# 修复: preferences.d 目录缺失警告
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

# 1. 准备目录 (修复警告的关键)
# 创建 apt 所需的完整目录结构，防止报 DirectoryExists 警告
mkdir -p "$DEB_DIR"
mkdir -p "$APT_TMP"/{state,cache,lists,etc}
mkdir -p "$APT_TMP/etc/apt/preferences.d"  # [新增] 修复警告
mkdir -p "$APT_TMP/etc/apt/sources.list.d" # [新增] 标准结构
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

EOL_UBUNTUS=("xenial" "bionic" "trusty")

if [ "$ID" == "ubuntu" ]; then
    REPO_URL="http://archive.ubuntu.com/ubuntu"
    SEC_URL="http://security.ubuntu.com/ubuntu"
    PORTS_URL="http://ports.ubuntu.com/ubuntu-ports"
    OLD_REL_URL="http://old-releases.ubuntu.com/ubuntu"

    IS_EOL=0
    for eol in "${EOL_UBUNTUS[@]}"; do
        if [ "$VERSION_CODENAME" == "$eol" ]; then
            IS_EOL=1
            break
        fi
    done

    if [ "$TARGET_ARCH" == "amd64" ] || [ "$TARGET_ARCH" == "i386" ]; then
        if [ "$IS_EOL" -eq 1 ]; then
            REPO_URL="$OLD_REL_URL"
            SEC_URL="$OLD_REL_URL"
        fi
        cat > "$SOURCES_FILE" <<EOF
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME} main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${REPO_URL} ${VERSION_CODENAME}-updates main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${SEC_URL} ${VERSION_CODENAME}-security main universe multiverse restricted
EOF
    else
        cat > "$SOURCES_FILE" <<EOF
deb [arch=$TARGET_ARCH] ${PORTS_URL} ${VERSION_CODENAME} main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${PORTS_URL} ${VERSION_CODENAME}-updates main universe multiverse restricted
deb [arch=$TARGET_ARCH] ${PORTS_URL} ${VERSION_CODENAME}-security main universe multiverse restricted
EOF
    fi

elif [ "$ID" == "debian" ]; then
    REPO_URL="http://deb.debian.org/debian"
    SEC_URL="http://security.debian.org/debian-security"
    
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
  -o Dir::Etc::preferences="$APT_TMP/etc/apt/preferences"      # [新增]
  -o Dir::Etc::preferencesparts="$APT_TMP/etc/apt/preferences.d" # [新增]
  -o Dir::Etc::Trusted="/etc/apt/trusted.gpg"
  -o Dir::Etc::TrustedParts="/etc/apt/trusted.gpg.d"
  -o APT::Architecture="$TARGET_ARCH"
  -o APT::Install-Recommends=false 
  -o APT::Get::Download-Only=true
  -o Acquire::Retries=3
  -o Acquire::Check-Valid-Until=false
)

# 5. 执行
echo ">>> 更新索引..."
apt-get "${APT_OPTS[@]}" -o Acquire::AllowInsecureRepositories=true update || echo "Update警告(可忽略)..."

echo ">>> 开始下载依赖..."
# 这里如果包名不存在，apt-get 会直接返回非零状态码，导致脚本退出
if apt-get "${APT_OPTS[@]}" -o Dir::Cache::archives="$DEB_DIR" --allow-unauthenticated install --reinstall -y $PACKAGES; then
    COUNT=$(ls "$DEB_DIR"/*.deb 2>/dev/null | wc -l)
    echo ">>> 下载成功！共 $COUNT 个文件"
    rm -rf "$DEB_DIR/partial" "$DEB_DIR/lock"
    chmod -R 777 "$OUTPUT_DIR"
else
    echo "=================================================="
    echo "[FATAL ERROR] 下载失败"
    echo "原因可能如下："
    echo "1. 拼写错误：包名 '$PACKAGES' 不存在。"
    echo "2. 源不支持：该软件(如 openresty)不在官方默认源中，需要第三方源。"
    echo "=================================================="
    exit 1
fi