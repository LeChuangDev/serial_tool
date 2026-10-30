# ====================== 绘图协议解码（VOFA+ 兼容） ======================
"""把串口字节流解析成"帧"（每帧是一组通道的采样值）。

兼容 VOFA+ 两种最常用的数据引擎，纯逻辑、不依赖 Qt，方便单测与复用：

- ``JustFloatDecoder``  小端 float32 数组，以 ``+inf``（字节 ``00 00 80 7f``）作帧尾。
  适合通道多、频率高；解析走 ``struct.unpack`` 的 C 实现，不逐字节 Python 循环。
- ``FireWaterDecoder``  CSV 文本流：``[prefix:]ch0,ch1,...,chN\\n``。
  直观、像 ``printf``，适合通道少、频率低。

性能与健壮性取向（对比 VOFA+ 官方文档自述的两处短板）：

1. 解析用 ``struct.unpack`` 批量解 float，把 O(字节数) 的 Python 循环压成一次 C 调用；
2. 无帧尾 / 无换行的异常流**不会撑爆内存**：未闭合的浮点与文本缓冲都有硬上限，
   超限丢最旧 —— VOFA+ 文档明说 JustFloat 若一直收不到 ``00 00 80 7f`` 会缓冲爆满卡死。
"""

from __future__ import annotations

import struct
from collections import deque

__all__ = ["JustFloatDecoder", "FireWaterDecoder", "PlotDecoder"]

# +inf 的 IEEE-754 位模式（小端字节序 00 00 80 7f），JustFloat 的帧尾约定
_INF_FLOAT = float("inf")

# 未闭合帧 / 文本缓冲的硬上限：防止异常流无帧尾导致内存无界增长
_MAX_PENDING_FLOATS = 4096
_MAX_PENDING_BYTES = 64 * 1024


class JustFloatDecoder:
    """小端 float32 数组 + ``0x7F800000`` 帧尾。

    约定：一个采样帧 = 若干通道的 float 值，紧随其后一个 ``+inf`` 标记帧尾。
    由于 ``+inf`` 被保留作帧尾，数据通道不得发送 ``+inf``（与 VOFA+ 一致）。
    """

    def __init__(self) -> None:
        self._buf = bytearray()  # 不足 4 字节的跨包碎片
        self._cur: deque[float] = deque()  # 尚未闭合的帧（等待帧尾）

    def reset(self) -> None:
        """清空缓冲与未闭合帧（切换协议 / 重新连接时调用）。"""
        self._buf.clear()
        self._cur.clear()

    def feed(self, data: bytes) -> list[list[float]]:
        """喂入一段字节，返回本次能解析出的完整帧（每帧 = 通道值列表）。"""
        self._buf.extend(data)
        n = len(self._buf) - (len(self._buf) % 4)
        frames: list[list[float]] = []
        if n:
            values = struct.unpack_from(f"<{n // 4}f", self._buf)
            del self._buf[:n]
            cur = self._cur
            i, k = 0, len(values)
            while i < k:
                value = values[i]
                if value == _INF_FLOAT:
                    # 连续两个帧尾 = 图片前导帧（VOFA+ 专有），整帧丢弃
                    if i + 1 < k and values[i + 1] == _INF_FLOAT:
                        cur.clear()
                        i += 2
                        continue
                    if cur:
                        frames.append(list(cur))
                        cur.clear()
                    i += 1
                else:
                    cur.append(value)
                    if len(cur) > _MAX_PENDING_FLOATS:
                        # 无帧尾的异常流：丢最旧，保证内存有界（VOFA+ 会在此卡死）
                        for _ in range(len(cur) - _MAX_PENDING_FLOATS):
                            cur.popleft()
                    i += 1
        return frames


class FireWaterDecoder:
    """CSV 文本流：``[prefix:]ch0,ch1,...,chN\\n``。

    换行（``\\n`` / ``\\r\\n``）结尾才成一帧；逗号分隔通道；可选 ``prefix:``
    会被剥离（取最后一个冒号之后）。整行解析失败按坏帧跳过，不半帧输出。
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> tuple[list[list[float]], int]:
        """喂入字节，返回 ``(帧列表, 坏行数)``。"""
        self._buf.extend(data)
        # 无换行的异常流：只保留最近一段，防止缓冲无界增长
        if len(self._buf) > _MAX_PENDING_BYTES:
            del self._buf[: len(self._buf) - _MAX_PENDING_BYTES]

        frames: list[list[float]] = []
        errors = 0
        while True:
            idx = self._buf.find(b"\n")
            if idx < 0:
                break
            line = bytes(self._buf[:idx]).rstrip(b"\r")
            del self._buf[: idx + 1]
            if not line:
                continue
            values = self._parse_line(line)
            if values:
                frames.append(values)
            else:
                errors += 1
        return frames, errors

    @staticmethod
    def _parse_line(line: bytes) -> list[float] | None:
        if b":" in line:
            line = line.rsplit(b":", 1)[1]
        parts = line.split(b",")
        out: list[float] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            try:
                out.append(float(part))
            except ValueError:
                return None  # 坏行整体丢弃，避免产出半帧
        return out or None


def PlotDecoder(protocol: str):  # noqa: N802 (工厂，与类并列命名)
    """按协议名返回对应解码器实例。"""
    from . import config

    if protocol == config.PROTOCOL_FIREWATER:
        return FireWaterDecoder()
    return JustFloatDecoder()
