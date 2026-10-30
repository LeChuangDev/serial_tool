"""主窗口集成测试：接收渲染、暂停、端口列表、发送守卫。

用 offscreen 平台在无显示器环境运行，覆盖"信号 → 渲染"这条最容易回归的链路。
"""

from __future__ import annotations

import time

from serialtool import config


def _feed(window, data: bytes) -> None:
    window.on_data_received([(data, time.time())])


class TestReceiveRendering:
    def test_plain_text_with_timestamp(self, main_window):
        # 显式指定格式，避免受前序用例改过 cb_ts 的影响（"包间隔增量"无冒号）
        main_window.cb_ts.setCurrentText(config.TS_MODE_LABELS[config.TS_MODE_CLOCK])
        main_window.ck_time.setChecked(True)
        _feed(main_window, b"hello\n")
        text = main_window.recv_box.toPlainText()
        assert "hello" in text
        assert text.count(":") >= 2  # HH:mm:ss.zzz

    def test_hex_display(self, main_window):
        main_window.ck_hex.setChecked(True)
        _feed(main_window, b"\x01\xa2\xff")
        assert "01 A2 FF" in main_window.recv_box.toPlainText()

    def test_multi_byte_char_split_across_packets(self, main_window):
        payload = "中文测试".encode()
        _feed(main_window, payload[:2])
        _feed(main_window, payload[2:])
        assert "中文测试" in main_window.recv_box.toPlainText()

    def test_line_mode_holds_partial_line(self, main_window):
        main_window.cb_break.setCurrentIndex(1)  # 按行
        _feed(main_window, b"first\nsecond")
        text = main_window.recv_box.toPlainText()
        assert "first" in text
        assert "second" not in text


class TestPause:
    def test_pause_freezes_render_but_counts(self, main_window):
        main_window.btn_pause.setChecked(True)
        blocks_before = main_window.recv_box.blockCount()
        rx_before = main_window.rx_count
        _feed(main_window, b"paused\n")
        assert main_window.recv_box.blockCount() == blocks_before
        assert main_window.rx_count > rx_before
        assert main_window._paused_dropped > 0

    def test_resume_clears_dropped_counter(self, main_window):
        main_window.btn_pause.setChecked(True)
        _feed(main_window, b"while paused\n")
        main_window.btn_pause.setChecked(False)
        _feed(main_window, b"resumed\n")
        assert main_window._paused_dropped == 0
        assert "resumed" in main_window.recv_box.toPlainText()

    def test_paused_bytes_still_captured_for_hex_export(self, main_window):
        main_window.btn_pause.setChecked(True)
        _feed(main_window, b"\x01\x02")
        assert bytes(main_window._rx_raw).endswith(b"\x01\x02")


class TestPortList:
    def test_description_shown_but_device_used(self, main_window):
        main_window._apply_port_list([("COM3", "USB-SERIAL CH340"), ("COM9", "")])
        assert main_window._current_port() == "COM3"
        assert main_window.cb_com.itemText(0) == "COM3 · USB-SERIAL CH340"

    def test_preferred_port_restored(self, main_window):
        main_window._apply_port_list([("COM3", "A"), ("COM7", "B")])
        main_window._preferred_port = "COM7"
        main_window._apply_port_list([("COM7", "B"), ("COM8", "C")])
        assert main_window._current_port() == "COM7"

    def test_port_lost_falls_back_to_single_device(self, main_window):
        main_window._apply_port_list([("COM3", "A"), ("COM4", "B")])
        main_window._apply_port_list([("COM5", "C")])
        assert main_window._current_port() == "COM5"


class TestSendGuard:
    def test_send_blocked_without_connection(self, main_window):
        assert main_window.do_send_text("abc", False) is False

    def test_invalid_hex_rejected(self, main_window):
        main_window.cb_com.clear()
        main_window.cb_com.addItem("COM_FAKE", "COM_FAKE")
        main_window._connected = True  # 绕过连接校验，只验 HEX 解析
        main_window.send_hex.setChecked(True)
        try:
            assert main_window.do_send_text("ZZ", False) is False
        finally:
            main_window._connected = False
            main_window.send_hex.setChecked(False)


class TestMultisend:
    def test_add_and_read_rows(self, main_window):
        main_window.send_box.setPlainText("AT+PING")
        main_window.ms_add()
        rows = main_window._ms_checked_rows()
        assert rows and rows[-1] == ("AT+PING", 100)

    def test_invalid_delay_defaults(self, main_window):
        main_window.send_box.setPlainText("AT")
        main_window.ms_add()
        row = main_window.ms_table.rowCount() - 1
        main_window.ms_table.item(row, 2).setText("oops")
        assert main_window._ms_checked_rows()[-1] == ("AT", 100)


class TestFileSendProgress:
    def test_progress_label(self, main_window):
        main_window._file_size = 2048
        main_window._file_total = 1024
        main_window._update_file_progress()
        assert "50%" in main_window.lab_file.text()
        assert main_window.lab_file.isVisibleTo(main_window)

    def test_finish_resets_button(self, main_window):
        main_window._sending_file = True
        main_window.btn_sendfile.setText("取消发送")
        main_window._finish_file_send()
        assert main_window.btn_sendfile.text() == "发送文件"
        assert main_window._sending_file is False


def _fragment_colors(document) -> list[tuple[str, str]]:
    """取出整篇文档内所有 (片段, 前景色) 对。"""
    out: list[tuple[str, str]] = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                color = fragment.charFormat().foreground().color().name()
                out.append((fragment.text(), color))
            iterator += 1
        block = block.next()
    return out


class TestTimestampModes:
    def test_delta_mode_shows_interval(self, main_window):
        main_window.ck_time.setChecked(True)
        main_window.cb_ts.setCurrentText("包间隔增量")
        now = time.time()
        main_window.on_data_received([(b"first\n", now)])
        main_window.on_data_received([(b"second\n", now + 0.05)])
        text = main_window.recv_box.toPlainText()
        assert "+50ms" in text

    def test_switching_format_enables_checkbox(self, main_window):
        main_window.ck_time.setChecked(False)
        main_window.cb_ts.setCurrentText("相对连接时间")
        assert main_window.ck_time.isChecked() is True
        assert main_window._ts.mode == "elapsed"


class TestHighlightRendering:
    def test_error_keyword_colored(self, main_window):
        from serialtool.highlight import HighlightRule

        main_window._highlighter.set_rules([HighlightRule(pattern="error", color="#ef4a4e")])
        main_window.ck_highlight.setChecked(True)
        _feed(main_window, b"an error here\n")
        assert ("error", "#ef4a4e") in _fragment_colors(main_window.recv_box.document())

    def test_disabled_highlight_keeps_single_color(self, main_window):
        from serialtool.highlight import HighlightRule

        main_window._highlighter.set_rules([HighlightRule(pattern="error", color="#ef4a4e")])
        main_window.ck_highlight.setChecked(False)
        _feed(main_window, b"an error here\n")
        colors = {color for _text, color in _fragment_colors(main_window.recv_box.document())}
        assert "#ef4a4e" not in colors


class _NoopWorker:
    """替身：记录写入内容，不启动线程。"""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.read_errors = 0
        self.write_errors = 0

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def send_break(self, _duration: float = 0.25) -> None:
        self.written.append(b"<BREAK>")

    def pending_writes(self) -> int:
        return len(self.written)

    def isRunning(self) -> bool:  # noqa: N802 (Qt 命名)
        return False

    def set_dtr(self, _on: bool) -> None:
        return None

    def set_rts(self, _on: bool) -> None:
        return None

    def clear_cmds(self) -> None:
        return None


class TestSendLineEnding:
    def test_eol_appended(self, main_window):
        main_window.worker = _NoopWorker()
        main_window._connected = True
        main_window.cb_eol.setCurrentText("\\r\\n")
        assert main_window.do_send_text("AT", False) is True
        assert main_window.worker.written == [b"AT\r\n"]

    def test_no_eol_keeps_raw(self, main_window):
        main_window.worker = _NoopWorker()
        main_window._connected = True
        main_window.cb_eol.setCurrentText("无")
        main_window.send_hex.setChecked(True)
        assert main_window.do_send_text("01 03", False) is True
        assert main_window.worker.written == [b"\x01\x03"]
        main_window.send_hex.setChecked(False)

    def test_crc_appended_before_eol(self, main_window):
        main_window.worker = _NoopWorker()
        main_window._connected = True
        main_window.ck_crc.setChecked(True)
        main_window.cb_eol.setCurrentText("\\n")
        assert main_window.do_send_text("A", False) is True
        from serialtool import utils

        data = main_window.worker.written[0]
        expected_crc = utils.crc16_modbus(b"A").to_bytes(2, "little")
        assert data == b"A" + expected_crc + b"\n"

    def test_break_sends_command(self, main_window):
        main_window.worker = _NoopWorker()
        main_window._connected = True
        main_window.send_break()
        assert main_window.worker.written == [b"<BREAK>"]


class TestAutoReconnectFlow:
    def test_disconnect_starts_reconnect_when_enabled(self, main_window):
        main_window.btn_autoreconnect.setChecked(True)
        main_window._connected = True
        main_window.current_port = "COM3"
        main_window._user_close = False
        main_window.on_disconnected("串口已断开")
        try:
            assert main_window._reconnector.active is True
            assert main_window.lab_reconnect.isVisibleTo(main_window)
        finally:
            main_window._stop_reconnect("", silent=True)

    def test_disconnect_ignored_when_disabled(self, main_window):
        main_window.btn_autoreconnect.setChecked(False)
        main_window._connected = True
        main_window.current_port = "COM3"
        main_window.on_disconnected("串口已断开")
        assert main_window._reconnector.active is False

    def test_user_disconnect_does_not_reconnect(self, main_window):
        main_window.btn_autoreconnect.setChecked(True)
        main_window._connected = True
        main_window.current_port = "COM3"
        main_window._user_close = True
        main_window.on_disconnected("串口已断开")
        assert main_window._reconnector.active is False

    def test_missing_device_counts_attempt(self, main_window):
        from serialtool.reconnect import ReconnectPolicy, ReconnectScheduler

        main_window.btn_autoreconnect.setChecked(True)
        main_window.current_port = "COM3"
        main_window._reconnector = ReconnectScheduler(
            ReconnectPolicy(max_attempts=3, base_delay=0.1, cooldown=0.0)
        )
        main_window._reconnector.start()
        main_window._try_reconnect([])
        assert main_window._reconnector.attempt == 1
        assert main_window._reconnector.active is True
        main_window._stop_reconnect("", silent=True)

    def test_success_after_reconnect_updates_counter(self, main_window, monkeypatch):
        from serialtool.ports import PortInfo
        from serialtool.reconnect import ReconnectPolicy, ReconnectScheduler

        main_window.btn_autoreconnect.setChecked(True)
        main_window.current_port = "COM3"
        main_window.current_baud = 115200
        main_window._reconnector = ReconnectScheduler(
            ReconnectPolicy(max_attempts=3, base_delay=0.1, cooldown=0.0)
        )
        main_window._reconnector.start()
        monkeypatch.setattr(main_window, "open_port", lambda *args, **kwargs: True)
        main_window._try_reconnect(
            [PortInfo(device="COM15", description="CH340", vid=0x1A86, pid=0x7523)]
        )
        assert main_window._reconnect_pending is True
        main_window.on_connected("COM15 @ 115200")
        assert main_window._reconnect_count == 1
        assert main_window._reconnector.active is False


class TestPortProfile:
    def test_profile_saved_and_restored(self, main_window):
        main_window.current_port = "COM7"
        main_window.current_baud = 921600
        main_window.current_bits = 8
        main_window.current_stop = 1
        main_window.current_parity = "N"
        main_window.cb_flow.setCurrentText("RTS/CTS")
        main_window._remember_port_profile()

        main_window.cb_baud.setCurrentText("9600")
        main_window.cb_flow.setCurrentText("无")
        main_window._apply_port_profile("COM7")
        assert main_window.cb_baud.currentText() == "921600"
        assert main_window.cb_flow.currentText() == "RTS/CTS"

    def test_unknown_port_keeps_current(self, main_window):
        main_window.cb_baud.setCurrentText("57600")
        main_window._apply_port_profile("COM_NOT_SEEN")
        assert main_window.cb_baud.currentText() == "57600"


class TestPortInfoCard:
    def test_tuple_ports_are_normalized(self, main_window):
        main_window._apply_port_list([("COM3", "CH340"), ("COM9", "")])
        assert main_window.lab_port.text() == "COM3"
        assert "CH340" in main_window.lab_driver.text()

    def test_port_info_objects_accepted(self, main_window):
        from serialtool.ports import PortInfo

        main_window._apply_port_list(
            [
                PortInfo(
                    device="COM11", description="CP210x", vid=0x10C4, pid=0xEA60, serial_number="S1"
                )
            ]
        )
        assert main_window._current_port() == "COM11"
        assert "10C4" in main_window.lab_detail.text()


class TestSettingsRoundtrip:
    def test_timestamp_mode_persisted(self, main_window):
        main_window.cb_ts.setCurrentText("包间隔增量")
        main_window.save_settings()
        main_window.cb_ts.setCurrentIndex(0)  # 模拟重启后的默认值
        main_window.load_settings()
        assert main_window.cb_ts.currentText() == "包间隔增量"
        assert main_window._ts.mode == "delta"

    def test_unknown_timestamp_mode_falls_back(self, main_window):
        main_window.store.set("ts_mode", "not-a-mode")
        main_window.load_settings()
        assert main_window.cb_ts.currentText() == "时钟 12:34:56.789"

    def test_eol_and_autoreconnect_persisted(self, main_window):
        main_window.cb_eol.setCurrentText("\\n")
        main_window.btn_autoreconnect.setChecked(True)
        main_window.save_settings()
        main_window.cb_eol.setCurrentText("无")
        main_window.btn_autoreconnect.setChecked(False)
        main_window.load_settings()
        assert main_window.cb_eol.currentText() == "\\n"
        assert main_window.btn_autoreconnect.isChecked() is True

    def test_legacy_newline_flag_migrates(self, main_window):
        main_window.store.remove("eol")
        main_window.store.set("newline", True)
        main_window.load_settings()
        assert main_window.cb_eol.currentText() == "\\r\\n"

    def test_highlight_rules_persisted(self, main_window):
        from serialtool.highlight import HighlightRule

        main_window._highlighter.set_rules([HighlightRule(pattern="panic", color="#ff8800")])
        main_window.save_settings()
        main_window._highlighter.set_rules([])
        main_window.load_settings()
        assert [rule.pattern for rule in main_window._highlighter.rules] == ["panic"]


class TestLayoutAudit:
    """布局回归：控件实际尺寸不得小于 sizeHint（否则文字被省略号截断）。

    这类问题肉眼不易发现，但用户切换选项后就看不到自己选了什么
    （例如流控选中 XON/XOFF 却显示 "XON/X…"）。用程序化审计替代人工截图检查。
    """

    @staticmethod
    def _clipped(window) -> list[str]:
        from PyQt6.QtWidgets import QComboBox, QLabel, QPushButton

        problems: list[str] = []
        for cls in (QLabel, QPushButton, QComboBox):
            for widget in window.findChildren(cls):
                if not widget.isVisible():
                    continue
                # 固定尺寸的装饰件（状态圆点等）按设计小于 sizeHint，跳过
                fixed = widget.minimumSize() == widget.maximumSize() and widget.minimumWidth() > 0
                if fixed:
                    continue
                hint, real = widget.sizeHint(), widget.size()
                if hint.width() > real.width() + 2 or hint.height() > real.height() + 2:
                    label = widget.objectName() or getattr(widget, "text", lambda: "")()[:16]
                    problems.append(f"{cls.__name__}({label}) {hint.width()}x{hint.height()}")
        return problems

    def test_no_clipping_at_default_size(self, main_window, qapp):
        main_window.resize(1180, 780)
        qapp.processEvents()
        assert self._clipped(main_window) == []

    def test_no_clipping_at_minimum_size(self, main_window, qapp):
        main_window.resize(main_window.minimumSize())
        qapp.processEvents()
        assert main_window.width() >= 1100  # 最小宽度要能容纳连接栏参数
        assert self._clipped(main_window) == []

    def test_no_clipping_in_plot_mode(self, main_window, qapp):
        main_window.switch_mode("plot")
        main_window.resize(main_window.minimumSize())
        qapp.processEvents()
        assert self._clipped(main_window) == []

    def test_long_combo_items_are_readable(self, main_window):
        """选中长选项后仍能完整显示：宽度下限取自内容，而不是写死的固定像素。"""
        from PyQt6.QtWidgets import QComboBox

        for combo in main_window.findChildren(QComboBox):
            if not combo.isVisible() or combo.count() == 0:
                continue
            longest = max(range(combo.count()), key=lambda i: len(combo.itemText(i)))
            combo.setCurrentIndex(longest)
            assert combo.width() + 2 >= combo.sizeHint().width(), (
                f"选项被截断: {combo.currentText()}"
            )


class TestModeSwitch:
    """左上角模式切换：终端 ↔ 绘图，同一串口会话不重连。"""

    @staticmethod
    def _justfloat(*values: float) -> bytes:
        import struct

        return b"".join(struct.pack("<f", v) for v in values) + struct.pack("<f", float("inf"))

    def test_switch_to_plot_sets_stack(self, main_window):
        main_window.switch_mode("plot")
        assert main_window._mode == "plot"
        assert main_window.mode_stack.currentIndex() == 1
        assert main_window.btn_plot.isChecked()

    def test_plot_mode_feeds_scope(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0, 3.0))
        assert main_window.scope.frame_count() == 1
        assert main_window.scope.channel_count() == 3

    def test_terminal_mode_does_not_feed_scope(self, main_window):
        # 默认即终端模式，喂 JustFloat 字节不应进入示波器
        _feed(main_window, self._justfloat(1.0, 2.0, 3.0))
        assert main_window.scope.frame_count() == 0

    def test_firewater_feeds_scope(self, main_window):
        main_window.switch_mode("plot")
        main_window.cb_protocol.setCurrentText("FireWater（CSV 文本）")
        _feed(main_window, b"1.0,2.0,3.0,4.0\n")
        assert main_window.scope.frame_count() == 1
        assert main_window.scope.channel_count() == 4

    def test_protocol_switch_resets_scope(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0))
        assert main_window.scope.frame_count() == 1
        main_window.cb_protocol.setCurrentText("FireWater（CSV 文本）")
        assert main_window.scope.frame_count() == 0  # 切协议清空曲线

    def test_mode_persisted(self, main_window):
        main_window.switch_mode("plot")
        main_window.save_settings()
        main_window.switch_mode("terminal")
        main_window.load_settings()
        assert main_window._mode == "plot"
        assert main_window.mode_stack.currentIndex() == 1

    def test_switch_back_to_terminal_keeps_both(self, main_window):
        # 绘图模式喂协议数据 → 切回终端喂文本，两者互不干扰
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0))
        assert main_window.scope.frame_count() == 1

        main_window.switch_mode("terminal")
        _feed(main_window, b"hello terminal\n")
        assert "hello terminal" in main_window.recv_box.toPlainText()
        assert main_window.scope.frame_count() == 1  # 曲线保留


class TestPlotAnalysis:
    """绘图模式分析功能：通道下拉 / 视图切换 / 统计 / CSV 导出。"""

    @staticmethod
    def _justfloat(*values: float) -> bytes:
        import struct

        return b"".join(struct.pack("<f", v) for v in values) + struct.pack("<f", float("inf"))

    def test_channel_combo_syncs(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0, 3.0))
        assert main_window.cb_channel.count() == 3
        assert main_window.cb_channel.itemText(2) == "CH3"

    def test_view_switch(self, main_window):
        main_window.switch_mode("plot")
        main_window._on_view_changed("spectrum")
        assert main_window.scope.view() == "spectrum"
        main_window._on_view_changed("histogram")
        assert main_window.scope.view() == "histogram"

    def test_channel_stats_label(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0))
        main_window.update_rate()
        assert "min" in main_window.lab_ch_stats.text()
        assert "CH1" in main_window.lab_ch_stats.text()

    def test_export_csv(self, main_window, tmp_path):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0))
        out = tmp_path / "plot.csv"
        main_window._write_plot_csv(str(out))
        text = out.read_text(encoding="utf-8")
        assert "time,CH1,CH2" in text
        assert len(text.splitlines()) == 2  # 表头 + 1 行数据


class TestPlotControls:
    """绘图工具栏：暂停 / Y 轴量程 —— 建了按钮就得真接线。"""

    @staticmethod
    def _justfloat(*values: float) -> bytes:
        import struct

        return b"".join(struct.pack("<f", v) for v in values) + struct.pack("<f", float("inf"))

    def test_pause_button_freezes_scope(self, main_window):
        main_window.switch_mode("plot")
        main_window.btn_plot_pause.setChecked(True)
        assert main_window.scope.paused() is True
        assert main_window.btn_plot_pause.text() == "继续"
        _feed(main_window, self._justfloat(1.0))
        assert main_window.scope.frame_count() == 0  # 暂停期间丢弃

        main_window.btn_plot_pause.setChecked(False)
        assert main_window.scope.paused() is False
        assert main_window.btn_plot_pause.text() == "暂停"
        _feed(main_window, self._justfloat(1.0))
        assert main_window.scope.frame_count() == 1

    def test_y_range_applies_fixed_range(self, main_window, monkeypatch):
        """用假对话框替掉模态弹窗：只验证取值被正确下发到示波器。"""
        import serialtool.plot_controller as pc

        class FakeDialog:
            DialogCode = type("Code", (), {"Accepted": 1})

            def __init__(self, lo, hi, auto, parent=None):
                pass

            def exec(self):
                return 1

            def values(self):
                return False, -2.0, 2.0

        monkeypatch.setattr(pc, "YRangeDialog", FakeDialog)
        main_window.switch_mode("plot")
        main_window._on_y_range()
        assert main_window.scope.y_range() == (-2.0, 2.0, False)

    def test_y_range_persisted(self, main_window):
        main_window.switch_mode("plot")
        main_window.scope.set_y_range(0.0, 8.0)
        main_window.save_settings()
        main_window.scope.set_auto_range(True)
        main_window._restore_plot_view()
        assert main_window.scope.y_range() == (0.0, 8.0, False)

    def test_channel_meta_persisted(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0))
        main_window.scope.set_channel_name(0, "温度")
        main_window.scope.set_channel_color(0, "#ff0000")
        main_window.scope.set_channel_scale(0, 3.0)
        main_window.save_settings()

        main_window.scope.set_channel_name(0, "被改坏")  # 弄脏后恢复
        main_window._restore_plot_view()
        assert main_window.scope.channel_name(0) == "温度"
        assert main_window.scope.channel_color(0) == "#ff0000"
        assert main_window.scope.channel_meta(0).scale == 3.0

    def test_export_csv_uses_calibrated_values(self, main_window, tmp_path):
        """比例/偏置标定后，CSV 里必须是标定值 —— 不能和曲线对不上。"""
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0))
        main_window.scope.set_channel_scale(0, 10.0)
        out = tmp_path / "cal.csv"
        main_window._write_plot_csv(str(out))
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "time,CH1"
        assert lines[1].endswith(",10")  # 1.0 × 10


class TestChannelTable:
    """通道管理表格：同步 / 重命名 / 颜色 / 比例 / 偏置 / 小数位。"""

    @staticmethod
    def _justfloat(*values: float) -> bytes:
        import struct

        return b"".join(struct.pack("<f", v) for v in values) + struct.pack("<f", float("inf"))

    def test_table_syncs_rows(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0, 3.0))
        assert main_window.ch_table.rowCount() == 3
        assert main_window.ch_table.item(0, 1).text() == "CH1"
        assert main_window.ch_table.item(2, 1).text() == "CH3"

    def test_rename_via_table(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0, 2.0))
        main_window.ch_table.item(0, 1).setText("温度")
        main_window._on_ch_table_changed(0, 1)
        assert main_window.scope.channel_name(0) == "温度"
        # 分析通道下拉同步刷新为自定义名称
        assert main_window.cb_channel.itemText(0) == "温度"

    def test_scale_offset_edit(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0))
        main_window.ch_table.item(0, 2).setText("10")
        main_window._on_ch_table_changed(0, 2)
        assert main_window.scope.channel_meta(0).scale == 10.0
        main_window.ch_table.item(0, 3).setText("5")
        main_window._on_ch_table_changed(0, 3)
        assert main_window.scope.channel_meta(0).offset == 5.0

    def test_decimals_edit(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0))
        main_window.ch_table.item(0, 4).setText("2")
        main_window._on_ch_table_changed(0, 4)
        assert main_window.scope.channel_meta(0).decimals == 2

    def test_color_reflected_in_table(self, main_window):
        main_window.switch_mode("plot")
        _feed(main_window, self._justfloat(1.0))
        main_window.scope.set_channel_color(0, "#ff0000")
        # 重建表格后颜色单元格以图标色块展示（QSS 会覆盖 item 背景，故用图标）
        main_window._ch_table_count = -1
        main_window._sync_channel_table()
        item = main_window.ch_table.item(0, 0)
        assert not item.icon().isNull()
        assert main_window.scope.channel_color(0) == "#ff0000"
