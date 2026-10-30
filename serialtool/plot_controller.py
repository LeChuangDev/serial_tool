# ====================== 绘图模式控制器 ======================
"""绘图（VOFA+ 风格示波器）相关的全部编排逻辑。

从主窗口抽出来的原因：主窗口要管连接、收发、文件、多条发送、日志、配置，
再塞进协议解码、示波器视图切换、通道管理表格与导出，已经远超单一职责。
这里以 **mixin** 形式提供 —— 方法直接访问主窗口的属性（``self.scope``、
``self.cb_protocol`` …），不引入新的持有关系，也不改变调用方。

主窗口通过 ``class LecoSerial(PlotController, QMainWindow)`` 继承，
并在 ``_bind_signals`` 里调用一次 ``bind_plot_signals()``。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import QColorDialog, QFileDialog, QTableWidgetItem

from . import config
from .plot_protocol import PlotDecoder
from .scope import VIEW_HISTOGRAM, VIEW_SPECTRUM, VIEW_WAVE, YRangeDialog

log = logging.getLogger(__name__)


class PlotController:
    """绘图模式控制器（mixin，需与主窗口一起继承使用）。

    依赖主窗口提供的属性：``scope``、``cb_protocol``、``cb_channel``、
    ``cb_window``、``cb_fft_points``、``ch_table``、``lab_plot_stat``、
    ``lab_ch_stats``、``btn_plot_*``、``btn_view_*``、``btn_yrange``、
    ``mode_stack``、``btn_plot``、``btn_terminal``、``mode_bar``、``store``
    以及 ``show_tip()``。
    """

    # ---------- 信号绑定 ----------
    def bind_plot_signals(self) -> None:
        """绘图面板的信号绑定（由主窗口 ``_bind_signals`` 调用一次）。"""
        self.mode_bar.group.idClicked.connect(self._on_mode_selected)
        self.cb_protocol.currentIndexChanged.connect(self._on_protocol_changed)
        self.btn_plot_clear.clicked.connect(self._on_plot_clear)
        self.btn_plot_pause.toggled.connect(self._on_plot_pause)
        self.btn_yrange.clicked.connect(self._on_y_range)
        self.btn_view_wave.clicked.connect(lambda: self._on_view_changed(VIEW_WAVE))
        self.btn_view_spectrum.clicked.connect(lambda: self._on_view_changed(VIEW_SPECTRUM))
        self.btn_view_histogram.clicked.connect(lambda: self._on_view_changed(VIEW_HISTOGRAM))
        self.cb_channel.currentIndexChanged.connect(self._on_channel_changed)
        self.cb_window.currentTextChanged.connect(self._on_window_changed)
        self.cb_fft_points.currentTextChanged.connect(self._on_fft_points_changed)
        self.btn_export.clicked.connect(self._export_plot_csv)
        # 通道管理表格：颜色列点击弹调色板，其余列编辑回写 scope
        self.ch_table.cellClicked.connect(self._on_ch_table_clicked)
        self.ch_table.cellChanged.connect(self._on_ch_table_changed)

    def _on_mode_selected(self, index: int) -> None:
        """左上角切换器：``idClicked`` 只在用户点击时触发，程序 setChecked 不会。"""
        if 0 <= index < len(config.MODES):
            self.switch_mode(config.MODES[index])

    def switch_mode(self, mode: str) -> None:
        """切换到指定模式；同一串口会话，切换不重连。"""
        if mode not in config.MODES or mode == self._mode:
            return
        self._set_mode(mode)
        if mode == config.MODE_PLOT:
            self._reset_plot_decoder()
            self.scope.clear()
            self.show_tip("已切换到绘图模式", 4000)
        self._update_plot_stat()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        plot = mode == config.MODE_PLOT
        self.btn_plot.setChecked(plot)
        self.btn_terminal.setChecked(not plot)
        self.mode_stack.setCurrentIndex(1 if plot else 0)

    def _on_protocol_changed(self, _index: int) -> None:
        self._reset_plot_decoder()
        self.scope.clear()
        self._update_plot_stat()

    def _reset_plot_decoder(self) -> None:
        protocol = config.PLOT_PROTOCOLS.get(
            self.cb_protocol.currentText(), config.PROTOCOL_JUSTFLOAT
        )
        self._plot_is_firewater = protocol == config.PROTOCOL_FIREWATER
        self._plot_decoder = PlotDecoder(protocol)
        self._plot_bad_lines = 0

    def _plot_feed(self, chunk: bytes, timestamp: float) -> None:
        """把一段字节喂给当前协议解码器，产出的帧交给示波器。"""
        if self._plot_decoder is None:
            self._reset_plot_decoder()
        result = self._plot_decoder.feed(chunk)
        if self._plot_is_firewater:
            frames, bad = result
            self._plot_bad_lines += bad
        else:
            frames = result
        for frame in frames:
            self.scope.add_frame(timestamp, frame)
        # 新帧可能带来新的通道，立即刷新通道下拉（统计标签仍按 rate_timer 秒级刷新）
        self._sync_channel_combo()

    def _on_plot_clear(self) -> None:
        self.scope.clear()
        self._update_plot_stat()

    def _on_plot_pause(self, checked: bool) -> None:
        """暂停：冻结曲线（下位机数据仍计入帧数，解冻后继续绘制）。"""
        self.scope.set_paused(checked)
        self.btn_plot_pause.setText("继续" if checked else "暂停")
        self.show_tip("已暂停绘图" if checked else "已继续绘图", 2500)

    def _on_y_range(self) -> None:
        """设置 Y 轴量程：自动跟随数据，或手动固定上下限。"""
        lo, hi, auto = self.scope.y_range()
        dialog = YRangeDialog(lo, hi, auto, self)
        if dialog.exec() != YRangeDialog.DialogCode.Accepted:
            return
        auto, lo, hi = dialog.values()
        if auto:
            self.scope.set_auto_range(True)
        else:
            self.scope.set_y_range(lo, hi)  # lo >= hi 会被内部拒绝，保持原量程

    def _on_view_changed(self, view: str) -> None:
        """切换波形 / 频谱 / 直方图视图。"""
        self.scope.set_view(view)
        self._update_channel_stats()

    def _on_channel_changed(self, index: int) -> None:
        self.scope.set_selected_channel(index)
        self._update_channel_stats()

    def _on_window_changed(self, text: str) -> None:
        try:
            seconds = float(text.rstrip("s"))
        except ValueError:
            return
        self.scope.set_window_seconds(seconds)

    def _on_fft_points_changed(self, text: str) -> None:
        with contextlib.suppress(ValueError):
            self.scope.set_fft_points(int(text))

    def _update_plot_stat(self) -> None:
        text = f"{self.scope.frame_count()} 帧 · {self.scope.channel_count()} 通道"
        if self._plot_bad_lines:
            text += f" · {self._plot_bad_lines} 坏行"
        self.lab_plot_stat.setText(text)
        self._sync_channel_combo()
        self._update_channel_stats()

    def _sync_channel_combo(self) -> None:
        """通道数变化时刷新通道下拉（频谱 / 直方图 / 统计所分析的通道）与通道表格。"""
        count = self.scope.channel_count()
        if count == self._channel_combo_count:
            return
        self._channel_combo_count = count
        self.cb_channel.blockSignals(True)
        self.cb_channel.clear()
        for i in range(1, max(count, 1) + 1):
            self.cb_channel.addItem(self.scope.channel_name(i - 1))
        self.cb_channel.blockSignals(False)
        self.cb_channel.setCurrentIndex(min(self.scope.selected_channel(), max(count - 1, 0)))
        self._sync_channel_table()

    def _sync_channel_table(self) -> None:
        """通道数变化时重建通道管理表格（颜色 / 名称 / 比例 / 偏置 / 小数位）。"""
        count = self.scope.channel_count()
        if count == self._ch_table_count:
            return
        self._ch_table_count = count
        self.ch_table.blockSignals(True)
        self.ch_table.setRowCount(count)
        for ch in range(count):
            self._fill_ch_table_row(ch)
        self.ch_table.blockSignals(False)

    def _fill_ch_table_row(self, ch: int) -> None:
        """填充通道表格一行（颜色块不可编辑，其余列可编辑）。

        颜色用图标（填色 pixmap）而非 ``setBackground`` 展示：QSS 里的
        ``QTableWidget::item`` 背景会覆盖 item 级背景，导致色块不显示。
        """
        meta = self.scope.channel_meta(ch)
        pixmap = QPixmap(20, 14)
        pixmap.fill(QColor(self.scope.channel_color(ch)))
        color_item = QTableWidgetItem("")
        color_item.setIcon(QIcon(pixmap))
        color_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.ch_table.setItem(ch, 0, color_item)
        self.ch_table.setItem(ch, 1, QTableWidgetItem(self.scope.channel_name(ch)))
        self.ch_table.setItem(ch, 2, QTableWidgetItem(f"{meta.scale:g}"))
        self.ch_table.setItem(ch, 3, QTableWidgetItem(f"{meta.offset:g}"))
        self.ch_table.setItem(ch, 4, QTableWidgetItem(str(meta.decimals)))

    def _on_ch_table_clicked(self, row: int, col: int) -> None:
        """点击颜色列弹出调色板，修改该通道线条颜色。"""
        if col != 0:
            return
        current = QColor(self.scope.channel_color(row))
        color = QColorDialog.getColor(current, self, "选择线条颜色")
        if color.isValid():
            self.scope.set_channel_color(row, color.name())
            item = self.ch_table.item(row, 0)
            if item is not None:
                pixmap = QPixmap(20, 14)
                pixmap.fill(color)
                item.setIcon(QIcon(pixmap))

    def _on_ch_table_changed(self, row: int, col: int) -> None:
        """通道表格编辑回写 scope（名称 / 比例 / 偏置 / 小数位）。"""
        item = self.ch_table.item(row, col)
        if item is None:
            return
        text = item.text().strip()
        if col == 1:  # 名称
            self.scope.set_channel_name(row, text)
            self._channel_combo_count = -1  # 名称变了，下次重建分析通道下拉
            self._sync_channel_combo()
        elif col == 2:  # 比例
            with contextlib.suppress(ValueError):
                self.scope.set_channel_scale(row, float(text))
        elif col == 3:  # 偏置
            with contextlib.suppress(ValueError):
                self.scope.set_channel_offset(row, float(text))
        elif col == 4:  # 小数位
            with contextlib.suppress(ValueError):
                self.scope.set_channel_decimals(row, int(float(text)))
        self._update_channel_stats()

    def _update_channel_stats(self) -> None:
        """更新选中通道的实时统计（min/max/avg/rms）。"""
        if self.scope.channel_count() == 0:
            self.lab_ch_stats.setText("")
            return
        ch = self.scope.selected_channel()
        s = self.scope.channel_stats(ch)
        stats = f"{self.scope.channel_name(ch)}  min {s['min']:.4g}  max {s['max']:.4g}"
        stats += f"  avg {s['mean']:.4g}  rms {s['rms']:.4g}"
        self.lab_ch_stats.setText(stats)

    def _export_plot_csv(self) -> None:
        """把示波器当前数据导出为 CSV（时间戳 + 各通道）。"""
        if self.scope.channel_count() == 0:
            self.show_tip("暂无绘图数据可导出", 3000)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出绘图数据", "plot_data.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            self._write_plot_csv(path)
        except OSError as exc:
            self.show_tip(f"导出失败：{exc}", 5000)

    def _write_plot_csv(self, path: str) -> None:
        """导出为 CSV（时间戳 + 各通道）。

        走 ``scope.export_rows()`` 而不是底层序列：导出值必须等于界面看到的值，
        否则用户设了比例 / 偏置后，CSV 与曲线对不上（且不该碰控件私有成员）。
        """
        names, rows = self.scope.export_rows()
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("time," + ",".join(names) + "\n")
            for t, values in rows:
                cells = [f"{t:.6f}"]
                for v in values:
                    cells.append("" if v != v else f"{v:.6g}")  # NaN（缺失通道）留空
                fh.write(",".join(cells) + "\n")
        self.show_tip(f"已导出 {len(rows)} 行到 {os.path.basename(path)}", 5000)

    def _restore_plot_view(self) -> None:
        """恢复绘图视图参数与通道元数据（名称 / 颜色 / 标定 / Y 轴量程）。

        通道元数据是用户花时间调出来的（对标 VOFA+ 的"数据"对象），
        重启后必须还在，否则每次开机都要重新命名十几条通道。
        """
        window_text = self.store.get_str("plot_window")
        if window_text:
            self.cb_window.setCurrentText(window_text)
            self._on_window_changed(self.cb_window.currentText())
        fft_text = self.store.get_str("plot_fft")
        if fft_text:
            self.cb_fft_points.setCurrentText(fft_text)
            self._on_fft_points_changed(self.cb_fft_points.currentText())
        view = self.store.get_str("plot_view")
        if view in (VIEW_WAVE, VIEW_SPECTRUM, VIEW_HISTOGRAM):
            self.scope.set_view(view)
            for button, name in (
                (self.btn_view_wave, VIEW_WAVE),
                (self.btn_view_spectrum, VIEW_SPECTRUM),
                (self.btn_view_histogram, VIEW_HISTOGRAM),
            ):
                button.setChecked(name == view)
        if not self.store.get_bool("plot_y_auto", True):
            self.scope.set_y_range(
                self.store.get_float("plot_y_lo", 0.0), self.store.get_float("plot_y_hi", 1.0)
            )
        try:
            meta = json.loads(self.store.get_str("plot_channels", "[]"))
        except (TypeError, ValueError):
            meta = []
            log.warning("通道配置解析失败，已回退默认")
        self.scope.import_meta(meta)
        self._sync_channel_combo()
