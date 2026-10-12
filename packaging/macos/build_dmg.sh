#!/usr/bin/env bash
# ============================================================
#  macOS Universal .dmg 构建脚本
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/../.."

APP_NAME="xhs-study-pdf"
VERSION="${VERSION:-1.0.0}"
ICNS="core_engine/static/XHS-Downloader.icns"
DIST="dist"
APP="$DIST/$APP_NAME.app"

echo "[1/4] 安装依赖..."
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install flask requests img2pdf pillow pyyaml pyinstaller >/dev/null

echo "[2/4] PyInstaller 构建 macOS App..."
python3 -m PyInstaller \
    --noconfirm \
    --windowed \
    --name "$APP_NAME" \
    --icon "$ICNS" \
    --add-data "web_frontend:web_frontend" \
    --add-data "core_engine:core_engine" \
    --add-data "home_edition:home_edition" \
    --hidden-import "img2pdf" \
    --hidden-import "PIL" \
    --hidden-import "yaml" \
    home_edition/desktop/main_gui.py

echo "[3/4] 生成通用二进制 (arm64 + x86_64)..."
# 如需 Universal 二进制,可分别构建后使用 lipo 合并;此处先假设单架构构建产物
# 多架构合并示例:
#   lipo -create build_arm64/app build_x86_64/app -output "$APP/Contents/MacOS/$APP_NAME"

echo "[4/4] 打包 dmg..."
if ! command -v create-dmg >/dev/null 2>&1; then
    echo "未检测到 create-dmg, 使用 hdiutil 兜底"
    mkdir -p "$DIST/dmg"
    cp -R "$APP" "$DIST/dmg/"
    hdiutil create -volname "$APP_NAME" -srcfolder "$DIST/dmg" \
        -ov -format UDZO "$DIST/$APP_NAME-$VERSION.dmg"
else
    create-dmg \
        --volname "$APP_NAME" \
        --icon "$APP_NAME.app" 120 160 \
        --app-drop-link 380 160 \
        "$DIST/$APP_NAME-$VERSION.dmg" \
        "$APP"
fi

echo "完成! 产物: $DIST/$APP_NAME-$VERSION.dmg"
