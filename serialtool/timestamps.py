# ====================== 时间戳格式化 ======================
"""接收数据的时间戳格式化（纯逻辑）。

嵌入式调试里三种时间戳各有用处：

- **时钟**（``12:34:56.789``）：和示波器/日志/同事的截图对时间；
- **相对连接时间**（``+12.345s``）：看"上电后多久打印"，不受系统时钟影响；
- **包间隔增量**（``+12ms``）：分析轮询周期、超时重发、卡顿定位。

三者都基于数据到达时的时间戳（``time.time()`` 秒），与界面无关，可单测。
"""

from __future__ import annotations

import datetime

from . import config

__all__ = [
    "MODE_CLOCK",
    "MODE_DELTA",
    "MODE_ELAPSED",
    "MODE_NONE",
    "TIMESTAMP_MODE_MAP",
    "TimestampFormatter",
    "format_delta",
]

MODE_NONE = "none"
MODE_CLOCK = config.TS_MODE_CLOCK
MODE_ELAPSED = config.TS_MODE_ELAPSED
MODE_DELTA = config.TS_MODE_DELTA

TIMESTAMP_MODE_MAP = dict(config.TIMESTAMP_MODES)


def format_delta(seconds: float) -> str:
    """把秒差格式化为紧凑文本：``12ms`` / ``1.234s`` / ``1m02.345s``。"""
    value = max(float(seconds), 0.0)
    if value < 1.0:
        return f"{value * 1000:.0f}ms"
    if value < 60.0:
        return f"{value:.3f}s"
    minutes, rest = divmod(value, 60.0)
    if minutes < 60:
        return f"{int(minutes)}m{rest:06.3f}s"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours}h{minutes:02d}m{rest:06.3f}s"


class TimestampFormatter:
    """按模式生成时间戳前缀；无模式时返回空串（调用方据此跳过前缀插入）。"""

    def __init__(self, mode: str = MODE_CLOCK) -> None:
        self.mode = mode if mode in (MODE_CLOCK, MODE_ELAPSED, MODE_DELTA) else MODE_CLOCK
        self._origin = 0.0  # 连接时刻（相对模式基准）
        self._last = 0.0  # 上一包时刻（增量模式基准）

    def reset(self, origin: float | None = None) -> None:
        """会话开始/重连时重置基准：相对时间与增量都从 0 起算。"""
        self._origin = origin if origin is not None else 0.0
        self._last = self._origin

    def set_mode(self, mode: str) -> None:
        if mode in (MODE_CLOCK, MODE_ELAPSED, MODE_DELTA):
            self.mode = mode

    def stamp(self, timestamp: float) -> str:
        """返回形如 ``"[12:34:56.789] "`` 的前缀；无时间戳时返回空串。"""
        if self.mode == MODE_CLOCK:
            clock = datetime.datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]
            return f"[{clock}] "
        if not self._origin:
            # 首包前未显式 reset：以首包为基准，避免出现巨大的相对时间
            self._origin = self._last = timestamp
        if self.mode == MODE_ELAPSED:
            return f"[+{format_delta(timestamp - self._origin)}] "
        delta = timestamp - self._last
        self._last = timestamp
        return f"[+{format_delta(delta)}] "
