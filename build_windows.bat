@echo off
rem ===================== Leeco-Serial Windows build =====================
rem Version comes from serialtool/config.py (single source of truth).
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto no_python

echo [1/4] Installing dependencies...
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto failed

set "VERFILE=%TEMP%\leeco_serial_version.txt"
python -c "import serialtool.config as c;print(c.APP_VERSION,end='')" > "%VERFILE%" 2>nul
if errorlevel 1 goto no_version
set /p VERSION=<"%VERFILE%"
del "%VERFILE%" >nul 2>nul
if not defined VERSION goto no_version
set "PKG=LeecoSerial-v%VERSION%-win64.zip"
echo [info] version = %VERSION%

echo [2/4] Static check and tests...
python -m ruff check .
if errorlevel 1 goto failed
python -m pytest -q
if errorlevel 1 goto failed

echo [3/4] Building with PyInstaller...
python -m PyInstaller --noconfirm --clean serial_tool.spec
if errorlevel 1 goto failed

echo [4/4] Packaging release archive...
if not exist "dist\LeecoSerial\LeecoSerial.exe" goto no_exe

rem drop runtime logs left by local test runs so they never ship in the archive
if exist "dist\LeecoSerial\logs" rmdir /s /q "dist\LeecoSerial\logs"

set /a RETRY=1
:zipretry
if exist "dist\%PKG%" del /f /q "dist\%PKG%"
powershell -NoProfile -Command "Compress-Archive -LiteralPath 'dist\LeecoSerial' -DestinationPath 'dist\%PKG%' -Force"
if not errorlevel 1 goto zipok
echo [warn] archive locked by another process, retry %RETRY%/3
set /a RETRY+=1
if %RETRY% GTR 3 goto zipfail
timeout /t 3 /nobreak >nul
goto zipretry

:zipok
powershell -NoProfile -Command "$h=(Get-FileHash -LiteralPath 'dist\%PKG%' -Algorithm SHA256).Hash.ToLower(); Set-Content -Path 'dist\SHA256SUMS.txt' -Value ($h + '  %PKG%') -Encoding ASCII"
if errorlevel 1 goto failed
echo.
echo [DONE] dist\LeecoSerial\LeecoSerial.exe
echo [DONE] dist\%PKG%
echo [DONE] dist\SHA256SUMS.txt
exit /b 0

:no_python
echo [ERROR] python not found. Install Python 3.10+ and add it to PATH.
exit /b 1
:no_version
echo [ERROR] cannot read APP_VERSION from serialtool/config.py
exit /b 1
:no_exe
echo [ERROR] dist\LeecoSerial\LeecoSerial.exe not found, check the build log above.
exit /b 1
:zipfail
echo [ERROR] failed to create dist\%PKG%
exit /b 1
:failed
echo [ERROR] build failed, see the log above.
exit /b 1
