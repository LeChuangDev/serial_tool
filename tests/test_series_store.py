"""分块数值序列测试：内存与语义（替代 deque 的关键模块）。"""

from __future__ import annotations

import pytest

from serialtool.series_store import ChunkedSeries


class TestChunkedSeries:
    def test_append_and_index(self):
        s = ChunkedSeries(10)
        for i in range(5):
            s.append(float(i))
        assert len(s) == 5
        assert [s[i] for i in range(5)] == [0.0, 1.0, 2.0, 3.0, 4.0]

    def test_negative_index(self):
        s = ChunkedSeries(10)
        for i in range(3):
            s.append(float(i))
        assert s[-1] == 2.0
        assert s[-3] == 0.0

    def test_index_out_of_range(self):
        s = ChunkedSeries(4)
        s.append(1.0)
        with pytest.raises(IndexError):
            _ = s[1]
        with pytest.raises(IndexError):
            _ = s[-2]

    def test_maxlen_drops_oldest(self):
        s = ChunkedSeries(5)
        for i in range(12):
            s.append(float(i))
        assert len(s) == 5
        assert list(s) == [7.0, 8.0, 9.0, 10.0, 11.0]

    def test_extend_const(self):
        s = ChunkedSeries(10)
        s.extend_const(3, float("nan"))
        s.append(1.0)
        assert len(s) == 4
        assert s[0] != s[0]  # NaN
        assert s[3] == 1.0

    def test_extend_const_respects_maxlen(self):
        s = ChunkedSeries(4)
        s.extend_const(10, 0.0)
        assert len(s) == 4

    def test_drop_front_across_blocks(self):
        """跨块丢弃：9000 点丢弃 5000 后，剩下的必须还是原顺序的尾部。"""
        s = ChunkedSeries(20000)
        values = [float(i) for i in range(9000)]
        for v in values:
            s.append(v)
        s.drop_front(5000)
        assert len(s) == 4000
        assert list(s) == values[5000:]
        assert s[0] == 5000.0
        assert s[-1] == 8999.0

    def test_drop_front_more_than_size(self):
        s = ChunkedSeries(10)
        for i in range(3):
            s.append(float(i))
        s.drop_front(99)
        assert len(s) == 0
        assert list(s) == []

    def test_drop_front_keeps_block_boundary_alignment(self):
        """部分丢弃后再追加，索引映射不能错位（第一块有 head 偏移的路径）。"""
        s = ChunkedSeries(20000)
        for i in range(5000):
            s.append(float(i))
        s.drop_front(7)  # 第一块内偏移 7
        for i in range(5000, 5003):
            s.append(float(i))
        assert s[0] == 7.0
        assert s[-1] == 5002.0
        assert len(s) == 4996

    def test_popleft(self):
        s = ChunkedSeries(10)
        s.append(1.0)
        s.append(2.0)
        assert s.popleft() == 1.0
        assert len(s) == 1
        with pytest.raises(IndexError):
            ChunkedSeries(2).popleft()

    def test_tail(self):
        s = ChunkedSeries(100)
        for i in range(10):
            s.append(float(i))
        assert s.tail(3) == [7.0, 8.0, 9.0]
        assert s.tail(99) == [float(i) for i in range(10)]
        assert s.tail(0) == []

    def test_clear(self):
        s = ChunkedSeries(10)
        for i in range(5):
            s.append(float(i))
        s.clear()
        assert len(s) == 0
        assert not s
        s.append(1.0)
        assert list(s) == [1.0]

    def test_invalid_maxlen(self):
        with pytest.raises(ValueError):
            ChunkedSeries(0)

    def test_memory_per_point(self):
        """每点 8 字节：这是替代 deque[float]（32 字节）的全部意义。"""
        s = ChunkedSeries(50_000)
        for i in range(10_000):
            s.append(float(i))
        assert s.memory_bytes() == 10_000 * 8

    def test_nan_roundtrip(self):
        s = ChunkedSeries(8)
        s.append(float("nan"))
        s.append(float("inf"))
        assert s[0] != s[0]
        assert s[1] == float("inf")
