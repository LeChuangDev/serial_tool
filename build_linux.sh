#!/usr/bin/env sh
# ===================== Leeco-Serial Linux build =====================
# Version comes from serialtool/config.py (single source of truth).
# PyInstaller cannot cross-compile: build on the target architecture (x86_64).
set -e
cd "$(dirname "$0")"

PY=python3
command -v "$PY" >/dev/null 2>&1 || { echo "[ERROR] python3 not found"; exit 1; }

echo "[1/4] Installing dependencies..."
"$PY" -m pip install -r requirements-dev.txt

VERSION=$("$PY" -c "import serialtool.config as c; print(c.APP_VERSION)")
PKG="LeecoSerial-v${VERSION}-linux-x64.tar.gz"
echo "[info] version = ${VERSION}"

echo "[2/4] Static check and tests..."
"$PY" -m ruff check .
"$PY" -m pytest -q

echo "[3/4] Building with PyInstaller..."
"$PY" -m PyInstaller --noconfirm --clean serial_tool.spec

echo "[4/4] Packaging release archive..."
[ -x "dist/LeecoSerial/LeecoSerial" ] || { echo "[ERROR] dist/LeecoSerial/LeecoSerial not found"; exit 1; }
rm -rf dist/LeecoSerial/logs
tar -czf "dist/${PKG}" -C dist LeecoSerial
( cd dist && sha256sum "$PKG" > SHA256SUMS.txt )

echo ""
echo "[DONE] dist/LeecoSerial/LeecoSerial"
echo "[DONE] dist/${PKG}"
echo "[DONE] dist/SHA256SUMS.txt"
