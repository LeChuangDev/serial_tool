# ====================== 纯函数工具 ======================
"""无副作用纯函数集合：HEX 解析、校验算法、格式化。

本模块不依赖 Qt / pyserial，可被单元测试独立覆盖。
"""

from __future__ import annotations

import re

__all__ = [
    "parse_hex",
    "crc16_modbus",
    "crc8",
    "xor_sum",
    "byte_sum",
    "hex_str",
    "hex_dump_lines",
    "format_bytes",
    "format_duration",
    "validate_baud",
]

# 允许的分隔符：空格 / 制表 / 换行 / 中英文逗号 / 中英文分号
_SEPARATORS = re.compile(r"[\s,，;；]+")
_HEX_PREFIX = re.compile(r"0[xX]")


def parse_hex(text: str) -> tuple[bytes | None, str | None]:
    """解析 HEX 字符串为字节。

    Args:
        text: 形如 ``"01 A2 FF"`` / ``"0x01,0xA2"`` 的字符串。

    Returns:
        ``(data, err)``：成功时 ``err`` 为 ``None``；失败时 ``data`` 为 ``None``，
        ``err`` 定位到具体出错元素（序号 + 原文 + 原因）。
    """
    if not text:
        return b"", None
    cleaned = _SEPARATORS.sub(" ", _HEX_PREFIX.sub("", text))
    parts = cleaned.split()
    if not parts:
        return b"", None
    for index, token in enumerate(parts, start=1):
        try:
            value = int(token, 16)
        except ValueError:
            return None, f"第 {index} 个元素 '{token}' 不是合法十六进制"
        if not 0 <= value <= 0xFF:
            return None, f"第 {index} 个元素 '{token}' 超出单字节范围 (00-FF)"
    return bytes(int(token, 16) for token in parts), None


def crc16_modbus(data: bytes) -> int:
    """Modbus CRC16 校验值（多项式 0xA001 反射，初值 0xFFFF）。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def crc8(data: bytes, poly: int = 0x07) -> int:
    """CRC-8/ATM：多项式 0x07，初值 0x00，无反射、无最终异或。"""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def xor_sum(data: bytes) -> int:
    """逐字节异或校验值（单字节）。"""
    value = 0
    for byte in data:
        value ^= byte
    return value


def byte_sum(data: bytes) -> int:
    """累加和校验值（单字节，取低 8 位）。"""
    return sum(data) & 0xFF


def hex_str(data: bytes) -> str:
    """字节转大写十六进制字符串（空格分隔）。"""
    return " ".join(f"{byte:02X}" for byte in data)


def hex_dump_lines(data: bytes, width: int = 16) -> list[str]:
    """按固定宽度切分为 HEX 文本行（导出用，避免一次拼接超大字符串）。"""
    if width <= 0:
        raise ValueError("width 必须为正整数")
    return [hex_str(data[i : i + width]) for i in range(0, len(data), width)]


def format_bytes(count: int) -> str:
    """字节数人类可读化：B / KB / MB / GB（保留一位小数）。"""
    if count >= 1024**3:
        return f"{count / 1024**3:.1f}GB"
    if count >= 1024**2:
        return f"{count / 1024**2:.1f}MB"
    if count >= 1024:
        return f"{count / 1024:.1f}KB"
    return f"{count}B"


def format_duration(seconds: float) -> str:
    """秒数格式化为 ``HH:MM:SS``（负数按 0 处理）。"""
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# 常用波特率：串口硬件通常支持，超出范围直接判为非法
_MIN_BAUD = 50
_MAX_BAUD = 12_000_000


def validate_baud(text: str) -> tuple[int | None, str | None]:
    """校验用户输入/选择的波特率。

    Returns:
        ``(baud, err)``：合法时 ``err`` 为 ``None``。
    """
    raw = (text or "").strip()
    if not raw:
        return None, "波特率不能为空"
    try:
        baud = int(raw)
    except ValueError:
        return None, f"波特率 '{raw}' 不是整数"
    if not _MIN_BAUD <= baud <= _MAX_BAUD:
        return None, f"波特率需在 {_MIN_BAUD} ~ {_MAX_BAUD} 之间"
    return baud, None
