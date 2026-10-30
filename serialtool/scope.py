# ====================== 高性能多通道示波器控件 ======================
"""VOFA+ 风格的滚动示波器：多通道曲线 + 自动量程 + 抽稀渲染。

性能设计（三条）：
1. **抽稀**：绘制前把可见区间按像素列聚合成 min/max 包络（``decimate``），
   绘制代价只与像素宽度相关，与采样点数解耦 —— 10 万点和 1 千点画起来一样快。
2. **节流**：数据再快也只按 ``config.SCOPE_FPS`` 刷新重绘，避免高频数据把 UI 线程打满。
3. **限内存**：时间轴与每条通道都是环形缓冲（``deque(maxlen=...)``），
   越界丢最旧，长时间挂机采集不涨内存。

``decimate`` 抽成纯函数，方便不依赖 Qt 直接单测。
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from itertools import islice

from PyQt6.QtCore import QLineF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from . import analysis, config

__all__ = ["ScopeWidget", "ChannelMeta", "decimate", "transform_value", "YRangeDialog"]

_NAN = float("nan")


@dataclass(slots=True)
class ChannelMeta:
    """单条通道的显示元数据（对标 VOFA+ 的"数据"对象）。

    - ``name``     通道名，空则回退为 ``CH{n}``
    - ``color``    线条颜色（``#rrggbb``），空则用默认色板轮换
    - ``scale``    比例：显示值 = 原始值 × scale
    - ``offset``   偏置：显示值 = 原始值 × scale + offset
    - ``decimals`` 小数位数（图例 / 游标 / 统计格式化用）
    """

    name: str = ""
    color: str = ""
    scale: float = 1.0
    offset: float = 0.0
    decimals: int = 4


_MAX_META_NAME = 32  # 通道名长度上限（图例里太长会盖住曲线）

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _is_color(spec: object) -> bool:
    """只接受 ``#rrggbb``：非法颜色会被 ``QColor`` 静默变成黑色，宁可丢弃。"""
    if not isinstance(spec, str) or len(spec) != 7 or not spec.startswith("#"):
        return False
    return all(c in _HEX_DIGITS for c in spec[1:])


def _finite(value: object, default: float) -> float:
    """把任意配置值收敛为有限浮点数（NaN / inf / 非数值回退默认）。"""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def transform_value(v: float, scale: float, offset: float) -> float:
    """标定变换 ``显示值 = 原始值 × scale + offset``（VOFA+ 的"比例 / 偏置"）。

    scale=1 且 offset=0 时直接返回原值（快路径），NaN 会自然保持为 NaN。
    """
    if scale == 1.0 and offset == 0.0:
        return v
    return v * scale + offset


# 视图模式
VIEW_WAVE = "wave"  # 滚动波形
VIEW_SPECTRUM = "spectrum"  # FFT 频谱
VIEW_HISTOGRAM = "histogram"  # 直方图

# 画笔等常量化，避免 paintEvent 里逐帧 new QColor/QPen
_BG = QColor(config.SCOPE_BG)
_GRID = QColor(config.SCOPE_GRID)
_AXIS = QColor(config.SCOPE_AXIS)
_TEXT = QColor("#dde5f0")


def decimate(
    times, values, t0: float, t1: float, width: int, step: int = 1
) -> list[tuple[int, float, float]]:
    """把 ``(times, values)`` 抽稀成 ``width`` 个像素列。

    返回 ``[(列号, 最小值, 最大值), ...]``（只含非空列）。``values`` 中的 NaN
    （缺失通道）会被跳过。列内聚合 min/max 是示波器标准做法：既保留尖峰，又保证
    绘制复杂度 O(width) 与采样数无关。

    参数 ``times`` / ``values`` 是任意可迭代的数值序列（deque / list / 生成器均可）。
    ``step > 1`` 时按等距抽样（每 ``step`` 个点取 1 个）：采样点远超像素列时，
    逐点遍历只是把同一列的 min/max 反复比较，收益极低却让绘制耗时随通道数线性膨胀
    —— 16 通道 × 10 万点时会直接吃掉整个 UI 线程。由调用方按"每列目标点数"算出 step。
    """
    if width <= 0 or t1 <= t0:
        return []
    scale = width / (t1 - t0)
    mins = [math.inf] * width
    maxs = [-math.inf] * width
    if step > 1:
        times = islice(times, None, None, step)
        values = islice(values, None, None, step)
    for t, v in zip(times, values, strict=False):
        if v != v:  # 只这一处能可靠判断 NaN
            continue
        pos = (t - t0) * scale
        if pos < 0 or pos >= width:
            continue
        col = int(pos)
        if v < mins[col]:
            mins[col] = v
        if v > maxs[col]:
            maxs[col] = v
    out: list[tuple[int, float, float]] = []
    for col in range(width):
        if mins[col] != math.inf:
            out.append((col, mins[col], maxs[col]))
    return out


class ScopeWidget(QWidget):
    """多通道滚动示波器。时间轴与各通道对齐，自动量程，图例叠加在左上角。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scope")
        self.setMinimumHeight(180)
        self.setMouseTracking(True)

        self._times: deque[float] = deque(maxlen=config.SCOPE_HISTORY)
        self._series: list[deque[float]] = []
        self._meta: list[ChannelMeta] = []  # 与 _series 下标对齐的通道显示元数据
        self._paused = False
        self._window = config.SCOPE_WINDOW_SECONDS
        self._frame_count = 0
        self._fps = 0.0
        self._fps_t0 = 0.0
        self._fps_n = 0

        # 视图与分析
        self._view = VIEW_WAVE
        self._selected_channel = 0
        self._fft_points = config.SCOPE_FFT_POINTS
        self._cursor: tuple[float, list[float]] | None = None  # (时间戳, [各通道值])

        # Y 轴量程：auto 为真时按可见数据自动缩放，否则用 _y_lo/_y_hi 固定
        self._auto_range = True
        self._y_lo = 0.0
        self._y_hi = 1.0

        # 分析结果缓存：频谱 / 直方图 / 统计都是重计算（FFT 与全量遍历），
        # 30FPS 下每帧重算会吃掉大半个核。数据没变（_revision 不变）就直接复用。
        self._revision = 0
        self._cache: dict[tuple, object] = {}

        self._overlay_font = QFont()
        self._overlay_font.setPointSize(9)
        self._axis_font = QFont()
        self._axis_font.setPointSize(8)
        # QFontMetrics 构造要做字体度量，逐帧重建很浪费：字体不变就复用同一个
        self._overlay_fm = QFontMetrics(self._overlay_font)
        self._axis_fm = QFontMetrics(self._axis_font)
        self._color_cache: dict[str, QColor] = {}

        self._repaint_timer = QTimer(self)
        self._repaint_timer.setInterval(1000 // config.SCOPE_FPS)
        self._repaint_timer.timeout.connect(self._tick)
        self._repaint_timer.start()
        # 自适应节流：按实测绘制耗时降帧；已绘制的数据版本（用于跳过无变化重绘）
        self._frame_cost = 0.0
        self._painted_revision = -1

        self._fps_timer = QTimer(self)
        self._fps_timer.setInterval(1000)
        self._fps_timer.timeout.connect(self._settle_fps)
        self._fps_timer.start()

    # ---------- 数据写入 ----------
    def add_frame(self, timestamp: float, values: list[float]) -> None:
        """追加一帧（N 个通道的采样，共享同一时间戳）。暂停时丢弃。"""
        if self._paused:
            return
        m = min(len(values), config.SCOPE_MAX_CHANNELS)
        self._times.append(timestamp)
        series = self._series
        if m > len(series):
            pad = len(self._times) - 1  # 新通道用 NaN 补齐历史，保证对齐
            for _ in range(len(series), m):
                series.append(deque([_NAN] * pad, maxlen=config.SCOPE_HISTORY))
        # 元数据与新通道同步增长（保留用户已设的名称/颜色/标定）
        while len(self._meta) < m:
            self._meta.append(ChannelMeta())
        for i in range(m):
            series[i].append(values[i])
        for i in range(m, len(series)):
            series[i].append(_NAN)  # 本帧缺的通道补 NaN，维持时间轴对齐

        self._frame_count += 1
        self._fps_n += 1
        self._bump()

    def _bump(self) -> None:
        """数据或配置发生变化：作废分析结果缓存。"""
        self._revision += 1
        if self._cache:
            self._cache.clear()

    def revision(self) -> int:
        """数据版本号（测试与外部判断"数据是否变过"用）。"""
        return self._revision

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.update()

    def paused(self) -> bool:
        return self._paused

    def set_window_seconds(self, seconds: float) -> None:
        self._window = max(0.5, seconds)
        self._bump()

    # ---------- 视图 / 分析 ----------
    def set_view(self, view: str) -> None:
        """切换波形 / 频谱 / 直方图。"""
        self._view = view
        self._cursor = None
        self.update()

    def view(self) -> str:
        return self._view

    def set_selected_channel(self, ch: int) -> None:
        """选择频谱 / 直方图 / 统计所分析的通道。"""
        self._selected_channel = max(0, ch)
        self.update()

    def selected_channel(self) -> int:
        return self._selected_channel

    def set_fft_points(self, n: int) -> None:
        if n != self._fft_points:
            self._fft_points = n
            self._bump()

    # ---------- Y 轴量程 ----------
    def set_auto_range(self, auto: bool) -> None:
        """自动量程（按可见数据缩放）与固定量程切换。"""
        self._auto_range = auto
        self.update()

    def set_y_range(self, lo: float, hi: float) -> None:
        """设定固定 Y 轴量程（会自动切到非自动模式）。``lo >= hi`` 视为无效输入，忽略。"""
        if not (lo < hi):
            return
        self._y_lo, self._y_hi = lo, hi
        self._auto_range = False
        self.update()

    def y_range(self) -> tuple[float, float, bool]:
        """当前 Y 轴量程 ``(lo, hi, auto)``。"""
        return self._y_lo, self._y_hi, self._auto_range

    def cursor(self) -> tuple[float, list[float]] | None:
        """当前游标悬停的数据点 ``(时间戳, [通道值...])``，无悬停返回 None。"""
        return self._cursor

    def channel_stats(self, ch: int) -> dict[str, float]:
        """指定通道的实时统计（min/max/mean/rms/峰峰值），基于标定后数据。

        结果按数据版本缓存：数据没变时重复取不重算（面板每秒刷新一次统计）。
        """
        nan = float("nan")
        empty = {"min": nan, "max": nan, "mean": nan, "rms": nan, "p2p": nan}
        if not (0 <= ch < len(self._series)):
            return dict(empty)
        key = ("stats", ch)
        cached = self._cache.get(key)
        if cached is None:
            cached = analysis.channel_stats(list(self.channel_values(ch)))
            self._cache[key] = cached
        return dict(cached)  # 返回副本，避免调用方改动污染缓存

    def fft_data(self, ch: int) -> tuple[list[float], list[float], float]:
        """对指定通道最近 ``_fft_points`` 点做 FFT，返回 ``(freqs, mags, fs)``。

        采样率由时间戳自动估算，数据不足时返回空。纯计算、不触碰 UI，方便测试。
        结果按数据版本缓存 —— 纯 Python FFT 不便宜，不能每帧重算。
        """
        if not (0 <= ch < len(self._series)):
            return [], [], 0.0
        key = ("fft", ch)
        cached = self._cache.get(key)
        if cached is None:
            samples = list(self.channel_values(ch))[-self._fft_points :]
            times = list(self._times)[-len(samples) :]
            fs = analysis.estimate_sample_rate(times)
            if fs <= 0:
                return [], [], 0.0
            freqs, mags = analysis.fft_spectrum(samples, fs)
            cached = (freqs, mags, fs)
            self._cache[key] = cached
        return cached

    def histogram_data(self, ch: int, bins: int = 32) -> tuple[list[float], list[int]]:
        """指定通道的直方图 ``(bin 中心, 计数)``，基于标定后数据（同样带缓存）。"""
        if not (0 <= ch < len(self._series)):
            return [], []
        key = ("hist", ch, bins)
        cached = self._cache.get(key)
        if cached is None:
            cached = analysis.histogram(list(self.channel_values(ch)), bins)
            self._cache[key] = cached
        return cached

    def export_meta(self) -> list[dict]:
        """通道元数据快照（供配置持久化）。"""
        return [asdict(m) for m in self._meta]

    def import_meta(self, items: object) -> None:
        """恢复通道元数据（名称 / 颜色 / 标定）。

        允许在无数据时预置：新帧到达后按通道序号套用。所有字段都做类型与
        取值收敛 —— 配置文件可能被手改坏，坏值不能让控件崩或画出黑线。
        """
        if not isinstance(items, (list, tuple)):
            return
        meta: list[ChannelMeta] = []
        for raw in list(items)[: config.SCOPE_MAX_CHANNELS]:
            if not isinstance(raw, dict):
                continue
            color = raw.get("color", "")
            meta.append(
                ChannelMeta(
                    name=str(raw.get("name", ""))[:_MAX_META_NAME],
                    color=color if _is_color(color) else "",
                    scale=_finite(raw.get("scale", 1.0), 1.0),
                    offset=_finite(raw.get("offset", 0.0), 0.0),
                    decimals=max(0, min(9, int(_finite(raw.get("decimals", 4), 4)))),
                )
            )
        self._meta = meta
        self._color_cache.clear()
        self._bump()
        self.update()

    def export_rows(self) -> tuple[list[str], list[tuple[float, list[float]]]]:
        """导出用快照：``(通道名列表, [(时间戳, [各通道标定值]), ...])``。

        走 ``channel_values`` 而非底层原始序列 —— 导出的必须是界面上看到的值，
        否则用户设了比例/偏置后 CSV 与曲线对不上。
        """
        names = [self.channel_name(i) for i in range(len(self._series))]
        series = [list(self.channel_values(i)) for i in range(len(self._series))]
        times = list(self._times)
        rows: list[tuple[float, list[float]]] = []
        for j, t in enumerate(times):
            rows.append((t, [s[j] if j < len(s) else _NAN for s in series]))
        return names, rows

    def clear(self) -> None:
        # 注意：只清数据、保留 _meta —— "清空"清的是曲线，用户花时间调好的
        # 通道名 / 颜色 / 标定不该跟着一起没，重连后还能继续套用。
        self._times.clear()
        self._series.clear()
        self._frame_count = 0
        self._fps = 0.0
        self._fps_n = 0
        self._cursor = None
        self._bump()
        self.update()

    def fps(self) -> float:
        return self._fps

    def frame_count(self) -> int:
        return self._frame_count

    def channel_count(self) -> int:
        return len(self._series)

    # ---------- 通道元数据（名称 / 颜色 / 标定） ----------
    def _meta_at(self, ch: int) -> ChannelMeta:
        """返回下标 ``ch`` 的元数据，越界返回空元数据（避免到处判空）。"""
        if 0 <= ch < len(self._meta):
            return self._meta[ch]
        return ChannelMeta()

    def channel_name(self, ch: int) -> str:
        """通道显示名，空则回退 ``CH{n}``。"""
        return self._meta_at(ch).name or f"CH{ch + 1}"

    def channel_color(self, ch: int) -> str:
        """通道颜色（``#rrggbb``），空则用默认色板轮换。"""
        color = self._meta_at(ch).color
        return color or config.SCOPE_COLORS[ch % len(config.SCOPE_COLORS)]

    def _qcolor(self, spec: str) -> QColor:
        """取缓存的 ``QColor``：绘制每帧都要用，避免反复解析颜色字符串。

        注意：返回的是共享对象，**不要**对它做 ``setAlpha`` 之类改动（会污染缓存），
        需要改动的场景请先 ``QColor(...)`` 拷一份。
        """
        color = self._color_cache.get(spec)
        if color is None:
            color = QColor(spec)
            self._color_cache[spec] = color
        return color

    def channel_meta(self, ch: int) -> ChannelMeta:
        """通道完整元数据（供 UI 表格回填）。"""
        return self._meta_at(ch)

    def set_channel_name(self, ch: int, name: str) -> None:
        if 0 <= ch < len(self._meta):
            self._meta[ch].name = name.strip()
            self._bump()
            self.update()

    def set_channel_color(self, ch: int, color: str) -> None:
        if 0 <= ch < len(self._meta):
            self._meta[ch].color = color
            self._color_cache.pop(color, None)
            self.update()

    def set_channel_scale(self, ch: int, scale: float) -> None:
        if 0 <= ch < len(self._meta) and scale == scale:  # 拒绝 NaN
            self._meta[ch].scale = scale
            self._bump()  # 标定影响统计 / FFT / 直方图，必须作废缓存
            self.update()

    def set_channel_offset(self, ch: int, offset: float) -> None:
        if 0 <= ch < len(self._meta) and offset == offset:
            self._meta[ch].offset = offset
            self._bump()
            self.update()

    def set_channel_decimals(self, ch: int, decimals: int) -> None:
        if 0 <= ch < len(self._meta):
            self._meta[ch].decimals = max(0, min(decimals, 9))
            self.update()

    def channel_values(self, ch: int):
        """指定通道"标定后"的值序列（应用 scale/offset），供绘制与统计。

        scale=1 且 offset=0 时直接返回底层 deque（快路径），否则返回惰性生成器，
        避免为纯显示变换分配整段新列表。
        """
        if not (0 <= ch < len(self._series)):
            return []
        meta = self._meta_at(ch)
        if meta.scale == 1.0 and meta.offset == 0.0:
            return self._series[ch]
        return (transform_value(v, meta.scale, meta.offset) for v in self._series[ch])

    def _fmt_value(self, ch: int, v: float) -> str:
        """按通道小数位数格式化数值（NaN 显示为占位符）。"""
        if v != v:
            return "—"
        decimals = self._meta_at(ch).decimals
        return f"{v:.{decimals}f}"

    # ---------- 绘制 ----------
    def _trim(self, t0: float) -> None:
        """丢弃窗口外旧数据（时间轴单调递增，popleft 均摊 O(1)）。"""
        times = self._times
        while times and times[0] < t0:
            times.popleft()
            for s in self._series:
                if s:
                    s.popleft()

    def _tick(self) -> None:
        """重绘节流的心跳：数据没变且没有游标要刷新时，一次都不画。

        挂机不收数据 / 暂停时这是纯赚 —— 省掉每秒 30 次无意义的全量重绘。
        """
        if self._revision == self._painted_revision and self._cursor is None:
            return
        self.update()

    def _adapt(self, started: float) -> None:
        """按实测绘制耗时自适应调整重绘间隔（波形重时自动降帧，别占满 UI 线程）。"""
        cost = (time.perf_counter() - started) * 1000.0
        self._frame_cost = self._frame_cost * 0.7 + cost * 0.3  # 平滑，避免间隔抖动
        target = 1000.0 / config.SCOPE_FPS
        ceiling = 1000.0 / config.SCOPE_MIN_FPS
        interval = min(ceiling, max(target, self._frame_cost * 1.5))  # 占空比不超过 2/3
        if abs(interval - self._repaint_timer.interval()) >= max(4.0, interval * 0.15):
            self._repaint_timer.setInterval(int(interval))

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        started = time.perf_counter()
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            painter.end()
            self._painted_revision = self._revision
            self._adapt(started)

    def _paint(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), _BG)
        w, h = self.width(), self.height()
        left, right, top, bottom = 56, 14, 14, 26
        plot_w, plot_h = w - left - right, h - top - bottom
        if plot_w <= 10 or plot_h <= 10:
            return

        if not self._times:
            self._draw_placeholder(painter, w, h)
            return

        if self._view == VIEW_SPECTRUM:
            self._draw_spectrum(painter, left, top, plot_w, plot_h)
            self._draw_cursor(painter, left, top, plot_w, plot_h)
            return
        if self._view == VIEW_HISTOGRAM:
            self._draw_histogram(painter, left, top, plot_w, plot_h)
            self._draw_cursor(painter, left, top, plot_w, plot_h)
            return

        t1 = self._times[-1]
        t0 = t1 - self._window
        self._trim(t0)

        # 通道多 + 窗口宽时把若干像素列合成一条粗线，控制单帧线段总数
        channels = len(self._series)
        col_div = 1
        if channels * plot_w > config.SCOPE_MAX_LINE_SEGMENTS:
            col_div = math.ceil(channels * plot_w / config.SCOPE_MAX_LINE_SEGMENTS)
        plot_cols = max(1, plot_w // col_div)

        # 抽稀一次、同时统计全局量程（避免二次遍历）。标定后值参与量程与绘制。
        decimated: list[list[tuple[int, float, float]]] = []
        lo, hi = math.inf, -math.inf
        step = self._decimate_step(plot_cols)
        for ch in range(channels):
            cols = decimate(self._times, self.channel_values(ch), t0, t1, plot_cols, step)
            for _, vmin, vmax in cols:
                if vmin < lo:
                    lo = vmin
                if vmax > hi:
                    hi = vmax
            decimated.append(cols)
        if not self._auto_range and self._y_hi > self._y_lo:
            lo, hi = self._y_lo, self._y_hi  # 固定量程：曲线可被裁到框外，这是预期行为
        elif lo == math.inf:
            lo, hi = 0.0, 1.0
        elif lo == hi:
            pad = abs(hi) * 0.1 or 1.0
            lo, hi = lo - pad, hi + pad
        else:
            pad = (hi - lo) * 0.08
            lo, hi = lo - pad, hi + pad

        self._draw_grid(painter, left, top, plot_w, plot_h, lo, hi)
        self._draw_time_labels(painter, left, top, plot_w, plot_h, t0, t1)
        self._draw_series(painter, decimated, left, top, plot_w, plot_h, lo, hi, col_div)
        self._draw_legend(painter, left, top, plot_w)
        self._draw_cursor(painter, left, top, plot_w, plot_h)

    def _decimate_step(self, plot_w: int) -> int:
        """按"每列目标点数"算出抽样步长：点数远超像素列时等距抽样。

        抽稀是 O(点数 × 通道数)，16 通道长窗口下逐点遍历会让单帧绘制上百毫秒，
        而多出来的点只是把同一列的 min/max 反复比较 —— 抽样后包络几乎一致。
        """
        points = len(self._times)
        budget = max(plot_w, 1) * config.SCOPE_DECIMATE_POINTS_PER_COL
        if points <= budget:
            return 1
        return (points + budget - 1) // budget

    def _draw_placeholder(self, painter: QPainter, w: int, h: int) -> None:
        painter.setPen(_AXIS)
        painter.setFont(self._overlay_font)
        painter.drawText(
            0,
            0,
            w,
            h,
            int(Qt.AlignmentFlag.AlignCenter),
            "等待数据 … 先连接串口，再让下位机按 JustFloat / FireWater 协议发送",
        )

    def _draw_grid(
        self,
        painter: QPainter,
        left: int,
        top: int,
        plot_w: int,
        plot_h: int,
        lo: float,
        hi: float,
    ) -> None:
        painter.setPen(QPen(_GRID, 1))
        for i in range(4):
            x = left + int(plot_w * i / 3)
            painter.drawLine(x, top, x, top + plot_h)
        for i in range(4):
            y = top + int(plot_h * i / 3)
            painter.drawLine(left, y, left + plot_w, y)

        painter.setFont(self._axis_font)
        painter.setPen(_AXIS)
        fm = self._axis_fm
        for tick in self._nice_ticks(lo, hi, 4):
            y = top + plot_h - int(plot_h * (tick - lo) / (hi - lo))
            painter.drawText(2, y + fm.ascent() // 2, self._fmt_tick(tick))

    def _draw_time_labels(
        self,
        painter: QPainter,
        left: int,
        top: int,
        plot_w: int,
        plot_h: int,
        t0: float,
        t1: float,
    ) -> None:
        """X 轴时间标签（仅波形视图）。"""
        painter.setFont(self._axis_font)
        painter.setPen(_AXIS)
        fm = self._axis_fm
        left_label = f"-{self._fmt_seconds(t1 - t0)}"
        right_label = "0s"
        painter.drawText(left, top + plot_h + fm.ascent() + 2, left_label)
        painter.drawText(
            left + plot_w - fm.horizontalAdvance(right_label),
            top + plot_h + fm.ascent() + 2,
            right_label,
        )

    @staticmethod
    def _fmt_tick(v: float) -> str:
        if v == 0:
            return "0"
        if abs(v) >= 1000 or abs(v) < 0.001:
            return f"{v:.1e}"
        return f"{v:.4g}"

    @staticmethod
    def _fmt_seconds(v: float) -> str:
        if v < 1:
            return f"{v * 1000:.0f}ms"
        if v < 60:
            return f"{v:.1f}s"
        return f"{v / 60:.1f}m"

    def _nice_ticks(self, lo: float, hi: float, count: int) -> list[float]:
        """生成"整数"刻度（1/2/5 × 10^k），避免 0.333 这类刻度难看。"""
        span = hi - lo
        if span <= 0:
            return [lo]
        step = span / max(count - 1, 1)
        mag = 10 ** math.floor(math.log10(step))
        for m in (1.0, 2.0, 5.0, 10.0):
            if step / mag <= m:
                step = m * mag
                break
        start = math.floor(lo / step) * step
        ticks: list[float] = []
        v = start
        while v <= hi + step * 0.5:
            if v >= lo - step * 0.5:
                ticks.append(v)
            v += step
        return ticks

    def _draw_series(
        self,
        painter: QPainter,
        decimated: list[list[tuple[int, float, float]]],
        left: int,
        top: int,
        plot_w: int,
        plot_h: int,
        lo: float,
        hi: float,
        col_div: int = 1,
    ) -> None:
        span = hi - lo
        y_scale = plot_h / span
        pen = QPen()
        pen.setWidth(col_div)  # col_div>1 时一列代表多个像素，线宽同步加粗
        for idx, cols in enumerate(decimated):
            pen.setColor(self._qcolor(self.channel_color(idx)))
            painter.setPen(pen)
            # 一次性提交所有竖线段：逐条 drawLine 在 16 通道 × 上千列时是主要开销
            lines: list[QLineF] = []
            for col, vmin, vmax in cols:
                x = left + col * col_div + col_div / 2
                y_top = top + plot_h - (vmax - lo) * y_scale
                y_bot = top + plot_h - (vmin - lo) * y_scale
                if y_bot - y_top < 1.0:
                    y_bot = y_top + 1.0
                lines.append(QLineF(x, y_top, x, y_bot))
            if lines:
                painter.drawLines(lines)

    def _draw_legend(self, painter: QPainter, left: int, top: int, plot_w: int) -> None:
        """左上角叠加图例：颜色点 + 通道名 + 最新值 + 帧率（用自定义颜色/名称/标定值）。"""
        painter.setFont(self._overlay_font)
        fm = self._overlay_fm
        line_h = fm.height() + 3
        x, y = left + 10, top + 8
        for idx in range(min(len(self._series), 8)):
            series = self._series[idx]
            color = self._qcolor(self.channel_color(idx))
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(x, y - line_h + 5, 10, 10)
            meta = self._meta_at(idx)
            raw = series[-1] if series else _NAN
            latest = transform_value(raw, meta.scale, meta.offset)
            latest_txt = self._fmt_value(idx, latest)
            painter.setPen(_TEXT)
            painter.drawText(x + 16, y, f"{self.channel_name(idx)}  {latest_txt}")
            y += line_h
        if self._fps > 0 or self._paused:
            prefix = "已暂停 · " if self._paused else ""
            painter.setPen(_AXIS)
            painter.drawText(x, y + 4, f"{prefix}{self._fps:.0f} fps · {self._frame_count} 帧")

    def _settle_fps(self) -> None:
        now = time.monotonic()
        if self._fps_t0:
            dt = now - self._fps_t0
            if dt > 0:
                self._fps = self._fps_n / dt
        self._fps_t0 = now
        self._fps_n = 0

    # ---------- 频谱 / 直方图 / 游标 ----------
    @staticmethod
    def _fmt_freq(hz: float) -> str:
        if hz >= 1000:
            return f"{hz / 1000:.1f}k"
        return f"{hz:.0f}"

    def _draw_spectrum(
        self, painter: QPainter, left: int, top: int, plot_w: int, plot_h: int
    ) -> None:
        ch = self._selected_channel
        freqs, mags, fs = self.fft_data(ch)
        if not freqs:
            self._draw_placeholder(painter, self.width(), self.height())
            return
        hi = max(mags) or 1.0
        lo = 0.0
        self._draw_grid(painter, left, top, plot_w, plot_h, lo, hi)

        painter.setFont(self._axis_font)
        fm = self._axis_fm
        painter.setPen(_AXIS)
        nyq = fs / 2
        for tick in self._nice_ticks(0.0, nyq, 5):
            x = left + int(plot_w * tick / nyq)
            label = self._fmt_freq(tick)
            painter.drawText(
                x - fm.horizontalAdvance(label) // 2, top + plot_h + fm.ascent() + 2, label
            )

        pen = QPen()
        pen.setColor(self._qcolor(self.channel_color(ch)))
        pen.setWidth(1)
        painter.setPen(pen)
        prev: tuple[int, int] | None = None
        for f, m in zip(freqs, mags, strict=False):
            x = left + int(plot_w * f / nyq)
            y = top + plot_h - int(plot_h * (m - lo) / (hi - lo))
            if prev is not None:
                painter.drawLine(prev[0], prev[1], x, y)
            prev = (x, y)

        painter.setFont(self._overlay_font)
        painter.setPen(_TEXT)
        painter.drawText(
            left + 10,
            top + 16,
            f"{self.channel_name(ch)} 频谱 · {len(mags)} 点 · fs≈{self._fmt_freq(fs)}",
        )

    def _draw_histogram(
        self, painter: QPainter, left: int, top: int, plot_w: int, plot_h: int
    ) -> None:
        ch = self._selected_channel
        centers, counts = self.histogram_data(ch)
        if not centers:
            self._draw_placeholder(painter, self.width(), self.height())
            return
        bins = len(centers)
        lo = centers[0] - (centers[1] - centers[0]) / 2 if bins > 1 else centers[0] - 1
        hi = centers[-1] + (centers[-1] - centers[-2]) / 2 if bins > 1 else centers[0] + 1
        max_count = max(counts) or 1
        self._draw_grid(painter, left, top, plot_w, plot_h, 0, max_count)

        painter.setFont(self._axis_font)
        fm = self._axis_fm
        painter.setPen(_AXIS)
        for tick in self._nice_ticks(lo, hi, 5):
            x = left + int(plot_w * (tick - lo) / (hi - lo))
            label = self._fmt_tick(tick)
            painter.drawText(
                x - fm.horizontalAdvance(label) // 2, top + plot_h + fm.ascent() + 2, label
            )

        color = QColor(self._qcolor(self.channel_color(ch)))  # 拷贝后再改 alpha，避免污染缓存
        color.setAlpha(190)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        bar_w = plot_w / bins
        for i, count in enumerate(counts):
            bh = int(plot_h * count / max_count)
            x = left + int(plot_w * i / bins)
            painter.drawRect(x, top + plot_h - bh, max(1, int(bar_w) - 1), bh)

        painter.setFont(self._overlay_font)
        painter.setPen(_TEXT)
        stats = self.channel_stats(ch)
        painter.drawText(
            left + 10,
            top + 16,
            f"{self.channel_name(ch)} 分布 · n={sum(counts)} · 均值={stats['mean']:.4g}",
        )

    def _draw_cursor(
        self, painter: QPainter, left: int, top: int, plot_w: int, plot_h: int
    ) -> None:
        """绘制游标竖线与数值提示（仅波形视图，游标在鼠标移动时更新）。"""
        cursor = self._cursor
        if cursor is None or self._view != VIEW_WAVE or not self._times:
            return
        t_cursor, values = cursor
        t1 = self._times[-1]
        t0 = t1 - self._window
        if not (t0 <= t_cursor <= t1):
            return
        x = left + int(plot_w * (t_cursor - t0) / (t1 - t0))
        painter.setPen(QPen(QColor("#8b9bb4"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(x, top, x, top + plot_h)

        painter.setFont(self._overlay_font)
        fm = self._overlay_fm
        lines = [f"t={t_cursor - t1:.3f}s"]
        for i, v in enumerate(values[:8]):
            meta = self._meta_at(i)
            disp = transform_value(v, meta.scale, meta.offset)
            lines.append(f"{self.channel_name(i)} {self._fmt_value(i, disp)}")
        box_w = max(fm.horizontalAdvance(ln) for ln in lines) + 16
        box_h = len(lines) * (fm.height() + 2) + 8
        bx = x + 10 if x + 10 + box_w <= left + plot_w else x - 10 - box_w
        by = top + 8
        painter.setBrush(QColor(19, 26, 38, 220))
        painter.setPen(QPen(QColor("#394454"), 1))
        painter.drawRoundedRect(bx, by, box_w, box_h, 4, 4)
        painter.setPen(_TEXT)
        ty = by + fm.ascent() + 4
        for ln in lines:
            painter.drawText(bx + 8, ty, ln)
            ty += fm.height() + 2

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """波形视图下把鼠标 X 映射到时间，取最近采样点作游标。"""
        if self._view != VIEW_WAVE or not self._times:
            self._cursor = None
            super().mouseMoveEvent(event)
            return
        left, right = 56, 14
        plot_w = self.width() - left - right
        if plot_w <= 10:
            super().mouseMoveEvent(event)
            return
        t1 = self._times[-1]
        t0 = t1 - self._window
        px = event.position().x() - left
        t_cursor = t0 + px / plot_w * (t1 - t0)
        idx = self._nearest_index(t_cursor)
        values = [series[idx] if idx < len(series) else _NAN for series in self._series]
        self._cursor = (self._times[idx], values)
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._cursor = None
        self.update()
        super().leaveEvent(event)

    def _nearest_index(self, t: float) -> int:
        """用二分在单调递增时间轴上找最接近 ``t`` 的采样下标。"""
        times = self._times
        lo, hi = 0, len(times) - 1
        if t <= times[0]:
            return 0
        if t >= times[-1]:
            return hi
        while lo < hi:
            mid = (lo + hi) // 2
            if times[mid] < t:
                lo = mid + 1
            else:
                hi = mid
        if lo > 0 and t - times[lo - 1] < times[lo] - t:
            return lo - 1
        return lo


class YRangeDialog(QDialog):
    """Y 轴量程设置：自动（跟随可见数据）或手动固定上下限。

    固定量程对标 VOFA+ 的 Y 轴设定 —— 观察小幅波动叠加在大直流上时，
    自动量程会把波动压成一条直线，此时必须手动锁死量程。
    """

    def __init__(
        self,
        lo: float,
        hi: float,
        auto: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Y 轴量程")
        self.setModal(True)

        self.ck_auto = QCheckBox("自动（按可见数据缩放）")
        self.ck_auto.setChecked(auto)

        self.spin_lo = QDoubleSpinBox()
        self.spin_hi = QDoubleSpinBox()
        for spin in (self.spin_lo, self.spin_hi):
            spin.setDecimals(4)
            spin.setRange(-1e12, 1e12)
            spin.setSingleStep(0.5)
        self.spin_lo.setValue(lo)
        self.spin_hi.setValue(hi)

        form = QFormLayout()
        form.addRow("下限", self.spin_lo)
        form.addRow("上限", self.spin_hi)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.ck_auto)
        layout.addLayout(form)
        layout.addWidget(buttons)

        # QCheckBox.toggled 传的是"勾选自动"，输入可用性正好相反，故取反
        self.ck_auto.toggled.connect(lambda auto: self._sync_enabled(not auto))
        self._sync_enabled(not auto)

    def _sync_enabled(self, manual: bool) -> None:
        """``manual`` 为真时允许编辑上下限（自动模式下输入无意义）。"""
        self.spin_lo.setEnabled(manual)
        self.spin_hi.setEnabled(manual)

    def values(self) -> tuple[bool, float, float]:
        """返回 ``(auto, lo, hi)``。"""
        return self.ck_auto.isChecked(), self.spin_lo.value(), self.spin_hi.value()
