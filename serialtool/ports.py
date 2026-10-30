# ====================== 串口枚举与设备身份 ======================
"""串口枚举、设备身份匹配与后台扫描线程。

自动重连的关键前提：**设备重新插入后 COM 号通常会变**（COM3 → COM15）。
只按端口名重连在真实开发板上几乎必然失败，因此这里把 USB 的
``VID:PID`` 与序列号抽成 :class:`PortIdentity`，重连时按相似度打分选口。

本模块不依赖 Qt 控件，可脱离界面单测；仅 ``PortScanner`` 用到 ``QThread``。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import serial.tools.list_ports
from PyQt6.QtCore import QThread, pyqtSignal

__all__ = [
    "PortInfo",
    "PortIdentity",
    "PortScanner",
    "enumerate_ports",
    "is_virtual_port",
    "match_score",
    "pick_port",
]

log = logging.getLogger(__name__)

# 蓝牙虚拟串口：调试场景几乎无用，却会挤占端口下拉列表
_VIRTUAL_KEYWORDS = ("bluetooth", "蓝牙")

# COM10 应排在 COM9 之后（Windows 枚举本身是按字符串排的）
_NUMERIC_SUFFIX = re.compile(r"(\d+)")


def is_virtual_port(description: str | None) -> bool:
    """判断是否为应过滤的虚拟串口（按设备描述匹配）。"""
    text = (description or "").lower()
    return any(keyword in text for keyword in _VIRTUAL_KEYWORDS)


@dataclass(frozen=True, slots=True)
class PortIdentity:
    """一个物理设备的身份指纹（用于跨 COM 号识别同一块板子）。"""

    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    description: str = ""

    def describe(self) -> str:
        """人类可读的身份描述（日志/界面用）。"""
        parts = [self.device]
        if self.vid is not None and self.pid is not None:
            parts.append(f"VID:PID={self.vid:04X}:{self.pid:04X}")
        if self.serial_number:
            parts.append(f"SN={self.serial_number}")
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class PortInfo:
    """一个串口的完整信息（比 pyserial 的 ListPortInfo 更精简、可比较）。"""

    device: str
    description: str = ""
    hwid: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    manufacturer: str | None = None
    product: str | None = None

    @property
    def label(self) -> str:
        """下拉框显示文本：``COM3 · USB-SERIAL CH340``。"""
        return f"{self.device} · {self.description}" if self.description else self.device

    @property
    def identity(self) -> PortIdentity:
        return PortIdentity(
            device=self.device,
            vid=self.vid,
            pid=self.pid,
            serial_number=self.serial_number,
            description=self.description,
        )

    @property
    def short_identity(self) -> str:
        """单行紧凑身份（会话卡片用，保证窄栏不折行）。"""
        usb = ""
        if self.vid is not None and self.pid is not None:
            usb = f"{self.vid:04X}:{self.pid:04X}"
        parts = [part for part in (usb, self.serial_number) if part]
        return " · ".join(parts) if parts else "标准串口"

    def detail_text(self) -> str:
        """会话卡片里的两行详情（USB 信息 / 驱动线索）。"""
        usb = ""
        if self.vid is not None and self.pid is not None:
            usb = f"VID:PID {self.vid:04X}:{self.pid:04X}"
        serial_part = f"SN {self.serial_number}" if self.serial_number else ""
        bits = [part for part in (usb, serial_part) if part]
        if bits:
            return "  ".join(bits)
        return self.hwid or "标准串口（无 USB 信息）"


def _natural_key(device: str) -> tuple:
    """按数字分段排序：COM2 < COM9 < COM10。"""
    parts = _NUMERIC_SUFFIX.split(device.upper())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def enumerate_ports(include_virtual: bool = False) -> list[PortInfo]:
    """枚举当前可用串口（默认过滤蓝牙虚拟口），按端口号自然排序。"""
    try:
        raw = serial.tools.list_ports.comports()
    except Exception as exc:  # 驱动异常/注册表损坏时 pyserial 会抛
        log.warning("串口枚举失败: %s", exc)
        return []
    result: list[PortInfo] = []
    for port in raw:
        if not include_virtual and is_virtual_port(port.description):
            continue
        result.append(
            PortInfo(
                device=port.device,
                description=port.description or "",
                hwid=getattr(port, "hwid", "") or "",
                vid=getattr(port, "vid", None),
                pid=getattr(port, "pid", None),
                serial_number=getattr(port, "serial_number", None),
                manufacturer=getattr(port, "manufacturer", None),
                product=getattr(port, "product", None),
            )
        )
    result.sort(key=lambda item: _natural_key(item.device))
    return result


def match_score(identity: PortIdentity, info: PortInfo) -> int:
    """给"是否同一设备"打分，0 表示不匹配。

    优先级（越靠前越可信）：
    100 端口名与 USB 身份全同；90 仅端口名相同（无 USB 信息可比）；
    80 USB 序列号 + VID/PID 全同（换了 COM 号）；60 仅 VID/PID 相同；
    40 描述相同（廉价 CH340 常无序列号）。
    """
    same_device = bool(identity.device) and identity.device.upper() == info.device.upper()
    vid_pid = (
        identity.vid is not None
        and identity.vid == info.vid
        and identity.pid is not None
        and identity.pid == info.pid
    )
    serial_same = bool(identity.serial_number) and identity.serial_number == info.serial_number

    if same_device and vid_pid:
        return 100
    if same_device and identity.vid is None:
        return 90
    if same_device:
        return 85  # 同端口名但 USB 身份对不上：可能是插了另一块板，仍优先尝试
    if serial_same and vid_pid:
        return 80
    if vid_pid and identity.serial_number is None:
        return 60
    if identity.description and identity.description == info.description:
        return 40
    return 0


def pick_port(ports: list[PortInfo], identity: PortIdentity | None) -> PortInfo | None:
    """在候选端口里选出最像目标设备的一个；没有匹配返回 ``None``。"""
    if not ports:
        return None
    if identity is None:
        return ports[0]
    scored = [(match_score(identity, info), info) for info in ports]
    scored = [(score, info) for score, info in scored if score > 0]
    if not scored:
        return None
    scored.sort(key=lambda pair: (-pair[0], _natural_key(pair[1].device)))
    return scored[0][1]


class PortScanner(QThread):
    """串口枚举线程。

    Windows 上 ``list_ports.comports()`` 可能耗时几十到几百毫秒，
    放在 UI 线程会造成周期性微卡顿；改为后台扫描、信号回传。
    """

    ports_scanned = pyqtSignal(list)  # [PortInfo]
    scan_failed = pyqtSignal(str)

    def scan(self) -> bool:
        """触发一次扫描；上一次未结束时跳过，避免任务堆积。"""
        if self.isRunning():
            return False
        self.start()
        return True

    def run(self) -> None:
        try:
            result = enumerate_ports()
        except Exception as exc:  # 兜底：绝不让线程带着异常退出
            log.warning("串口枚举失败: %s", exc)
            self.scan_failed.emit(str(exc))
            return
        self.ports_scanned.emit(result)

    def shutdown(self, timeout: float = 1.5) -> None:
        """关闭前等扫描线程收尾（规避 ``QThread.wait()`` 在本环境的原生崩溃）。"""
        deadline = time.monotonic() + timeout
        while not self.isFinished() and time.monotonic() < deadline:
            time.sleep(0.005)
