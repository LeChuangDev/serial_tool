# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows / Linux 通用，onedir 模式）。

采用 onedir 而非 onefile 的原因：
- onefile 每次启动都要解压到临时目录，冷启动慢 1~2 秒；
- onefile 的自解压行为更容易触发 Windows Defender / SmartScreen 误报；
- onedir 的 Qt 插件（platforms / imageformats）缺失时更容易排查。

用法（项目根目录）：
    python -m PyInstaller --noconfirm --clean serial_tool.spec

产物：dist/LeecoSerial/（Windows 内为 LeecoSerial.exe，Linux 内为同名可执行文件）。
版本号唯一来源是 serialtool/config.py 的 APP_VERSION，本文件不再硬编码。
"""

import os
import re
import sys

APP_NAME = "LeecoSerial"
APP_DISPLAY = "Leeco-Serial 樂創串口调试终端"


def _read_app_version() -> str:
    """从 serialtool/config.py 读取 APP_VERSION（唯一版本源）。"""
    path = os.path.join(SPECPATH, "serialtool", "config.py")
    with open(path, encoding="utf-8") as fh:
        match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', fh.read(), re.MULTILINE)
    if not match:
        raise SystemExit("打包失败：无法从 serialtool/config.py 读取 APP_VERSION")
    return match.group(1)


APP_VERSION = _read_app_version()
_parts = [int(x) for x in re.findall(r"\d+", APP_VERSION)]
APP_VERSION_STR = ".".join(str(p) for p in (_parts + [0, 0, 0])[:3])
APP_VERSION_TUPLE = tuple((_parts + [0, 0, 0, 0])[:4])

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
    [],
    exclude_binaries=True,  # onedir：二进制不入 exe，交给 COLLECT
    name=APP_NAME,
    console=False,  # GUI 程序不弹控制台窗口
    strip=False,
    upx=False,  # 关闭 UPX：压缩产物常被杀毒软件误报
    **win_kwargs,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)
