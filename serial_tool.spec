# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows / Linux 通用，单文件模式）。

用法（项目根目录）：
    python -m PyInstaller --noconfirm --clean serial_tool.spec

产物：dist/LeecoSerial（Windows 为 LeecoSerial.exe，Linux 为同名 ELF 单文件）。
改版本号时同步更新本文件的 APP_VERSION_STR / APP_VERSION_TUPLE。
"""

import os
import sys

APP_NAME = "LeecoSerial"
APP_VERSION_STR = "3.5.0"
APP_VERSION_TUPLE = (3, 5, 0, 0)  # Windows 版本资源用
APP_DISPLAY = "Leeco-Serial 樂創串口调试终端"

# 资源文件打进包内（config.resource_path 兼容 sys._MEIPASS，运行时仍可定位）
datas = [
    (os.path.join(SPECPATH, "assets", "app.ico"), "assets"),
    (os.path.join(SPECPATH, "assets", "icon.png"), "assets"),
]

# Windows exe 文件属性（右键 → 属性 → 详细信息）；非 Windows 平台跳过
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    win_kwargs = {
        "icon": os.path.join(SPECPATH, "assets", "app.ico"),
        "version": VSVersionInfo(
            ffi=FixedFileInfo(
                filevers=APP_VERSION_TUPLE,
                prodvers=APP_VERSION_TUPLE,
                mask=0x3F,
                flags=0x0,
                OS=0x40004,
                fileType=0x1,
                subtype=0x0,
                date=(0, 0),
            ),
            kids=[
                StringFileInfo(
                    [
                        StringTable(
                            "040904B0",
                            [
                                StringStruct("CompanyName", "Leeco"),
                                StringStruct("FileDescription", APP_DISPLAY),
                                StringStruct("FileVersion", APP_VERSION_STR),
                                StringStruct("InternalName", APP_NAME),
                                StringStruct("OriginalFilename", APP_NAME + ".exe"),
                                StringStruct("ProductName", "Leeco-Serial"),
                                StringStruct("ProductVersion", APP_VERSION_STR),
                            ],
                        )
                    ]
                ),
                VarFileInfo([VarStruct("Translation", [1033, 1200])]),
            ],
        ),
    }
else:
    win_kwargs = {}

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    datas=datas,
    hiddenimports=[],
    # 项目未使用这些重依赖：排除以减小体积、加快启动
    excludes=["tkinter", "numpy", "scipy", "matplotlib", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name=APP_NAME,
    console=False,  # GUI 程序不弹控制台窗口
    strip=False,
    upx=False,  # 关闭 UPX：压缩产物常被杀毒软件误报
    **win_kwargs,
)
