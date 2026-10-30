"""接收格式化管线单元测试（解码 / 断行 / HEX）。"""

from __future__ import annotations

from serialtool import config
from serialtool.recv_pipeline import SPLIT_BY_LINE, SPLIT_BY_PACKET, RecvFormatter


class TestHexMode:
    def test_hex_output(self):
        fmt = RecvFormatter()
        assert fmt.format(b"\x01\xa2", hex_mode=True) == "01 A2"

    def test_hex_mode_does_not_touch_decoder(self):
        fmt = RecvFormatter()
        assert fmt.format(b"\x01", hex_mode=True) == "01"
        assert fmt.format(b"ok") == "ok"


class TestIncrementalDecode:
    def test_utf8_split_across_packets(self):
        payload = "中文".encode()
        fmt = RecvFormatter(encoding="utf-8")
        first = fmt.format(payload[:2])
        second = fmt.format(payload[2:])
        assert first + second == "中文"

    def test_gbk_split_across_packets(self):
        payload = "测试".encode("gbk")
        fmt = RecvFormatter(encoding="gbk")
        assert fmt.format(payload[:1]) + fmt.format(payload[1:]) == "测试"

    def test_encoding_switch_resets_decoder(self):
        payload = "中文".encode()
        fmt = RecvFormatter(encoding="utf-8")
        fmt.format(payload[:2])  # 留下半个字符
        fmt.encoding = "gbk"
        # 旧残留必须被丢弃，新编码从干净状态解码
        assert fmt.format("测试".encode("gbk")) == "测试"

    def test_invalid_bytes_replaced_not_raised(self):
        fmt = RecvFormatter(encoding="utf-8")
        assert "\ufffd" in fmt.format(b"\xff\xfe")


class TestLineSplit:
    def test_partial_line_held_back(self):
        fmt = RecvFormatter(split_mode=SPLIT_BY_LINE)
        assert fmt.format(b"line1\nline2") == "line1"
        assert fmt.format(b" end\n") == "line2 end"

    def test_multiple_lines_in_one_packet(self):
        fmt = RecvFormatter(split_mode=SPLIT_BY_LINE)
        assert fmt.format(b"a\nb\n") == "a\nb"

    def test_blank_lines_preserved(self):
        fmt = RecvFormatter(split_mode=SPLIT_BY_LINE)
        assert fmt.format(b"a\n\nb\n") == "a\n\nb"

    def test_long_line_forced_out(self, monkeypatch):
        monkeypatch.setattr(config, "LINE_BUF_CAP", 8)
        fmt = RecvFormatter(split_mode=SPLIT_BY_LINE)
        assert fmt.format(b"0123456789") == "0123456789"
        assert fmt.format(b"abc") == ""  # 强制输出后缓冲已清空

    def test_mode_switch_clears_buffer(self):
        fmt = RecvFormatter(split_mode=SPLIT_BY_LINE)
        fmt.format(b"half")
        fmt.split_mode = SPLIT_BY_PACKET
        assert fmt.take_pending() == ""

    def test_take_pending(self):
        fmt = RecvFormatter(split_mode=SPLIT_BY_LINE)
        fmt.format(b"tail")
        assert fmt.take_pending() == "tail"
        assert fmt.take_pending() == ""


class TestReset:
    def test_reset_drops_pending_state(self):
        payload = "中".encode()
        fmt = RecvFormatter()
        fmt.format(payload[:1])
        fmt.reset()
        assert fmt.format(payload) == "中"
