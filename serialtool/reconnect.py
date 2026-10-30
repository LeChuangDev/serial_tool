# ====================== 自动重连策略 ======================
"""断线重连的**纯状态机**（不依赖 Qt、不碰串口）。

拆出来的原因：重连时序（首次冷却、指数退避、抖动、次数上限）是最容易写错
也最难靠手工点击验证的部分。把它做成纯逻辑后，用假时钟就能精确断言
"第 3 次重试应等 3.2s" 这类行为。

调用方（主窗口）只负责三件事：定时问 :meth:`ReconnectScheduler.due`、
真正尝试打开端口、把结果通过 :meth:`note_attempt` 反馈回来。
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import config

__all__ = ["ReconnectPolicy", "ReconnectScheduler"]

# 退避下限：再急也不能 0 秒连打，否则 USB 枚举来不及完成
_MIN_DELAY = 0.05


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    """重连策略参数。``max_attempts = 0`` 表示无限重试。"""

    max_attempts: int = config.RECONNECT_MAX_ATTEMPTS
    base_delay: float = config.RECONNECT_BASE_DELAY
    max_delay: float = config.RECONNECT_MAX_DELAY
    jitter: float = config.RECONNECT_JITTER
    cooldown: float = config.RECONNECT_COOLDOWN

    def delay_for(self, attempt: int, random_value: float = 0.5) -> float:
        """第 ``attempt`` 次失败后应等待的秒数。

        Args:
            attempt: 已失败次数（从 1 开始）。
            random_value: ``[0, 1)`` 的随机数，注入以便测试确定化；
                0.5 表示不加抖动（对称区间中点）。
        """
        if attempt < 1:
            return _MIN_DELAY
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            delay *= 1.0 + self.jitter * (2.0 * random_value - 1.0)
        return max(delay, _MIN_DELAY)

    @property
    def unbounded(self) -> bool:
        return self.max_attempts <= 0


class ReconnectScheduler:
    """重连时序状态机。

    典型用法::

        scheduler.start()                       # 链路中断，进入冷却
        while not scheduler.due(): sleep(...)   # 等冷却/退避时间
        ok = try_open(...)
        scheduler.note_attempt()                # 排下一次（或耗尽后自动停止）
    """

    def __init__(
        self,
        policy: ReconnectPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        self._policy = policy or ReconnectPolicy()
        self._clock = clock
        self._rng = rng or random.Random()
        self._attempt = 0
        self._next_at = 0.0
        self._active = False
        self._stopped = False

    # ---------- 只读状态 ----------
    @property
    def policy(self) -> ReconnectPolicy:
        return self._policy

    @property
    def active(self) -> bool:
        """是否处于"正在等待下一次尝试"的状态（含冷却期）。"""
        return self._active

    @property
    def attempt(self) -> int:
        return self._attempt

    @property
    def stopped(self) -> bool:
        return self._stopped

    def exhausted(self) -> bool:
        """次数用尽（与"被用户主动停止"区分开）。"""
        return (
            (not self._active)
            and (not self._stopped)
            and (not self._policy.unbounded and self._attempt >= self._policy.max_attempts)
        )

    def seconds_until_due(self) -> float:
        if not self._active:
            return 0.0
        return max(self._next_at - self._clock(), 0.0)

    def describe(self) -> str:
        """状态栏文案：``自动重连 2/10 · 1.4s 后重试``。"""
        if not self._active:
            return ""
        total = "∞" if self._policy.unbounded else str(self._policy.max_attempts)
        wait = self.seconds_until_due()
        if wait <= 0:
            return f"自动重连 {self._attempt}/{total} · 正在尝试"
        return f"自动重连 {self._attempt}/{total} · {wait:.1f}s 后重试"

    # ---------- 状态迁移 ----------
    def start(self) -> float:
        """进入重连流程，返回首次尝试的时间点（``clock()`` 基准）。"""
        self._attempt = 0
        self._stopped = False
        self._active = True
        self._next_at = self._clock() + max(self._policy.cooldown, 0.0)
        return self._next_at

    def due(self) -> bool:
        """当前是否到点该发起一次尝试。"""
        return self._active and self._clock() >= self._next_at

    def note_attempt(self) -> int:
        """登记一次失败的尝试，并安排下一次；次数用尽则自动停止。

        Returns:
            本次是第几次尝试（从 1 开始）。
        """
        self._attempt += 1
        if not self._policy.unbounded and self._attempt >= self._policy.max_attempts:
            self._active = False
            return self._attempt
        self._next_at = self._clock() + self._policy.delay_for(self._attempt, self._rng.random())
        return self._attempt

    def stop(self) -> None:
        """主动停止（用户点击断开 / 关闭窗口 / 手动连接成功）。"""
        self._active = False
        self._stopped = True
        self._attempt = 0
        self._next_at = 0.0
