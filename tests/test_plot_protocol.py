"""绘图协议解码器测试（纯逻辑，无 Qt 依赖）。"""

from __future__ import annotations

import struct

from serialtool import config
from serialtool.plot_protocol import (
    FireWaterDecoder,
    JustFloatDecoder,
    PlotDecoder,
)

INF = struct.pack("<f", float("inf"))  # 00 00 80 7f


def _f(*values: float) -> bytes:
    return b"".join(struct.pack("<f", v) for v in values)


class TestJustFloat:
    def test_single_frame(self):
        decoder = JustFloatDecoder()
        assert decoder.feed(_f(1.0, 2.0, 3.0) + INF) == [[1.0, 2.0, 3.0]]

    def test_multiple_frames(self):
        decoder = JustFloatDecoder()
        data = _f(1.0) + INF + _f(2.0, 3.0) + INF
        assert decoder.feed(data) == [[1.0], [2.0, 3.0]]

    def test_frame_split_across_feeds(self):
        decoder = JustFloatDecoder()
        payload = _f(1.5, -2.5) + INF
        assert decoder.feed(payload[:3]) == []  # 不足 4 字节的碎片
        assert decoder.feed(payload[3:]) == [[1.5, -2.5]]

    def test_no_tail_keeps_buffer_bounded(self):
        """无帧尾的异常流不得撑爆内存（VOFA+ 会在此卡死）。"""
        decoder = JustFloatDecoder()
        assert decoder.feed(_f(*([1.0] * 5000))) == []
        assert decoder.feed(INF) == [[1.0] * 4096]  # 只保留最近 4096 个 float

    def test_image_preamble_skipped(self):
        """双 inf 结尾 = 图片前导帧，整帧丢弃。"""
        preamble = _f(*[0.0] * 5) + INF + INF
        nine = struct.unpack("<f", struct.pack("<f", 9.9))[0]  # float32 量化后的值
        decoder = JustFloatDecoder()
        assert decoder.feed(preamble + _f(nine) + INF) == [[nine]]

    def test_reset_clears_pending(self):
        decoder = JustFloatDecoder()
        decoder.feed(_f(1.0))  # 未闭合
        decoder.reset()
        assert decoder.feed(INF) == []


class TestFireWater:
    def test_basic_csv(self):
        decoder = FireWaterDecoder()
        assert decoder.feed(b"1.0,2.5,3.0\n") == ([[1.0, 2.5, 3.0]], 0)

    def test_prefix_and_crlf(self):
        decoder = FireWaterDecoder()
        assert decoder.feed(b"d0:1.0,-2.0\r\n") == ([[1.0, -2.0]], 0)

    def test_bad_line_counted(self):
        decoder = FireWaterDecoder()
        assert decoder.feed(b"1.0,2.0\nabc,def\n3.0\n") == ([[1.0, 2.0], [3.0]], 1)

    def test_line_split_across_feeds(self):
        decoder = FireWaterDecoder()
        assert decoder.feed(b"1.0,2") == ([], 0)
        assert decoder.feed(b".0\n") == ([[1.0, 2.0]], 0)

    def test_no_newline_keeps_buffer_bounded(self):
        decoder = FireWaterDecoder()
        decoder.feed(b"x" * (64 * 1024 + 100))
        assert decoder.feed(b"\n") == ([], 1)  # 保留的垃圾行按坏行计数


class TestFactory:
    def test_protocol_selection(self):
        assert isinstance(PlotDecoder(config.PROTOCOL_FIREWATER), FireWaterDecoder)
        assert isinstance(PlotDecoder(config.PROTOCOL_JUSTFLOAT), JustFloatDecoder)
        assert isinstance(PlotDecoder("unknown"), JustFloatDecoder)  # 兜底
