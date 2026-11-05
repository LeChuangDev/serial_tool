#!/usr/bin/env sh
# ===================== Leeco-Serial Linux 一键打包 =====================
# 注意：PyInstaller 不支持交叉编译，请在与目标机相同架构（x86_64）的 Linux 上构建。
# 版本号需与 serialtool/config.py 的 APP_VERSION 保持一致。
set -e
cd "$(dirname "$0")"
VERSION=3.5

echo "[1/3] 安装依赖..."
python3 -m pip install -r requirements.txt "pyinstaller>=6.10"

echo "[2/3] PyInstaller 打包..."
python3 -m PyInstaller --noconfirm --clean serial_tool.spec

echo "[3/3] 压缩发布包..."
tar -czf "dist/LeecoSerial-v${VERSION}-linux-x64.tar.gz" -C dist LeecoSerial

echo ""
echo "[完成] dist/LeecoSerial"
echo "[完成] dist/LeecoSerial-v${VERSION}-linux-x64.tar.gz"
