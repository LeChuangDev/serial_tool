@echo off
rem ===================== Leeco-Serial Windows build =====================
rem Keep VERSION in sync with APP_VERSION in serialtool/config.py
setlocal
cd /d "%~dp0"
set VERSION=3.5

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.10+ and add it to PATH.
    exit /b 1
)

echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt "pyinstaller>=6.10"
if errorlevel 1 exit /b 1

echo [2/3] Building with PyInstaller...
python -m PyInstaller --noconfirm --clean serial_tool.spec
if errorlevel 1 exit /b 1

echo [3/3] Packaging release archive...
if exist "dist\LeecoSerial.exe" (
    powershell -NoProfile -Command "Compress-Archive -Path 'dist\LeecoSerial.exe' -DestinationPath 'dist\LeecoSerial-v%VERSION%-win64.zip' -Force"
    echo.
    echo [DONE] dist\LeecoSerial.exe
    echo [DONE] dist\LeecoSerial-v%VERSION%-win64.zip
) else (
    echo [ERROR] dist\LeecoSerial.exe not found. Check the build log above.
    exit /b 1
)
endlocal
