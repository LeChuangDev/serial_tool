# ====================== 通用自绘控件 ======================
"""与业务解耦的可复用控件：点击即展开下拉框、支持 Ctrl+滚轮缩放的接收区、
自绘速率图与字节波形图。

抽出到独立模块的目的：主窗口只做编排，控件可单独预览与测试。
"""

from __future__ import annotations

from collections import deque

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QComboBox, QPlainTextEdit, QWidget

from . import config, styles

__all__ = ["ClickComboBox", "RecvPlainTextEdit", "MiniRateChart", "WaveChart"]

# 画笔颜色常量化：paintEvent 里逐帧 new QColor 会持续产生临时对象
_CHART_BG = QColor("#0f141d")
_CHART_GRID = QColor("#1c2432")
_RX_BAR = QColor(37, 104, 238, 190)
_TX_BAR = QColor(230, 162, 60, 160)
_WAVE_LINE = QColor(74, 140, 255, 230)


class ClickComboBox(QComboBox):
    """点击输入框即展开下拉（默认需点箭头，触控板/小控件体验差）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet(styles.CLICK_COMBO_QSS)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().mousePressEvent(event)
        self.showPopup()


class RecvPlainTextEdit(QPlainTextEdit):
    """接收区文本框：Ctrl+滚轮调整字号，滚动事件上抛给主窗口处理。"""

    font_delta = pyqtSignal(int)

    def wheelEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta:
                self.font_delta.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class MiniRateChart(QWidget):
    """收/发速率迷你柱状图（RX 蓝 / TX 橙，各占半格）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: deque[tuple[float, float]] = deque(maxlen=config.RATE_HISTORY)
        self.setFixedSize(config.RATE_CHART_WIDTH, config.RATE_CHART_HEIGHT)
        self.setToolTip(f"收/发速率（最近 {config.RATE_HISTORY} 秒）")

    def add_sample(self, rx: float, tx: float) -> None:
        self._data.append((rx, tx))
        self.update()

    def clear(self) -> None:
        self._data.clear()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_CHART_BG)
        painter.drawRoundedRect(0, 0, width, height, 6, 6)
        if not self._data:
            return

        peak = max(max(rx, tx) for rx, tx in self._data) or 1.0
        bar_width = width / len(self._data)
        painter.setPen(_CHART_GRID)
        for i in range(1, 4):
            y = int(height - height * i / 4)
            painter.drawLine(0, y, width, y)

        half = max(int(bar_width / 2) - 1, 1)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (rx, tx) in enumerate(self._data):
            x = int(index * bar_width)
            rx_height = int(height * rx / peak)
            tx_height = int(height * tx / peak)
            painter.setBrush(_RX_BAR)
            if rx_height:
                painter.drawRect(x + 1, height - rx_height, half, rx_height)
            painter.setBrush(_TX_BAR)
            if tx_height:
                painter.drawRect(x + int(bar_width / 2), height - tx_height, half, tx_height)


class WaveChart(QWidget):
    """接收字节实时波形（0-255，最近 N 字节，满宽后窗口滑动）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: deque[int] = deque(maxlen=config.WAVE_HISTORY)
        self.setFixedHeight(config.WAVE_CHART_HEIGHT)
        self.setToolTip(f"接收字节实时波形（最近 {config.WAVE_HISTORY} 字节，0-255）")

    def add_samples(self, data: bytes) -> None:
        self._data.extend(data)
        self.update()

    def clear(self) -> None:
        self._data.clear()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_CHART_BG)
        painter.drawRoundedRect(0, 0, width, height, 6, 6)
        if len(self._data) < 2:
            return

        painter.setPen(_CHART_GRID)
        for i in range(1, 4):
            y = int(height - height * i / 4)
            painter.drawLine(0, y, width, y)

        pad = 4
        span = height - 2 * pad
        step = width / (config.WAVE_HISTORY - 1)
        path = QPainterPath()
        for index, value in enumerate(self._data):
            x = index * step
            y = pad + span * (1 - value / 255.0)
            path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
        painter.setPen(QPen(_WAVE_LINE, 1.6))
        painter.drawPath(path)
