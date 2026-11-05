# ====================== ChunkedBytes 环形字节缓冲测试 ======================
"""验证定长分块字节缓冲的环形语义、容量上限、流式导出与内存边界。

对应 ``raw_buffer.ChunkedBytes``：用分块队列替代单个 bytearray，丢弃是 O(1) 整块出队，
导出走 ``iter_chunks`` 流式迭代，绝不整体物化 8MB 缓冲。
"""

from __future__ import annotations

import pytest

from serialtool import config
from serialtool.raw_buffer import ChunkedBytes


def _fill(buf: ChunkedBytes, total: int, step: int = 7) -> None:
    """逐段写入 total 字节（用递增值填充，便于校验顺序与丢头）。"""
    for i in range(0, total, step):
        buf.append(bytes(range(i, min(i + step, total))))


class TestAppendAndLen:
    def test_empty_is_falsy(self):
        buf = ChunkedBytes(100)
        assert len(buf) == 0
        assert not buf
        assert buf.to_bytes() == b""

    def test_append_accumulates_and_is_truthy(self):
        buf = ChunkedBytes(100)
        buf.append(b"abc")
        buf.append(b"de")
        assert len(buf) == 5
        assert buf
        assert buf.to_bytes() == b"abcde"

    def test_extend_alias(self):
        buf = ChunkedBytes(100)
        buf.extend(b"xyz")
        assert buf.to_bytes() == b"xyz"

    def test_append_empty_is_noop(self):
        buf = ChunkedBytes(100)
        buf.append(b"")
        assert len(buf) == 0

    def test_maxlen_zero_drops_everything(self):
        buf = ChunkedBytes(0)
        buf.append(b"hello")
        assert len(buf) == 0
        assert not buf


class TestRingDrop:
    def test_oldest_dropped_when_over_cap(self):
        # 容量 20 字节，写入 35 字节，应保留最后 20 字节
        buf = ChunkedBytes(20)
        _fill(buf, 35)
        assert len(buf) == 20
        # 最后 20 字节应是 15..34（range 从 15 到 34 含端点共 20 个）
        assert buf.to_bytes() == bytes(range(15, 35))

    def test_exact_cap_kept(self):
        buf = ChunkedBytes(20)
        _fill(buf, 20)
        assert len(buf) == 20
        assert buf.to_bytes() == bytes(range(20))

    def test_drop_is_order_preserving_across_chunks(self):
        # 反复小步写入直到越过多个块边界
        buf = ChunkedBytes(config.RX_RAW_CHUNK * 3)
        for i in range(1000):
            buf.append(bytes([i % 256]))
        # 长度不超过容量
        assert len(buf) <= config.RX_RAW_CHUNK * 3
        data = buf.to_bytes()
        # 头部应是最旧的保留字节，尾部是最后一次写入的 999 % 256
        assert data[-1] == (999 % 256)

    def test_clear_empties(self):
        buf = ChunkedBytes(50)
        _fill(buf, 30)
        buf.clear()
        assert len(buf) == 0
        assert not buf


class TestChunking:
    def test_chunks_respect_chunk_size(self):
        buf = ChunkedBytes(config.RX_RAW_CHUNK * 2 + 10)
        # 一次性写入超过一块，应分成多块且每块 <= RX_RAW_CHUNK
        buf.append(b"x" * (config.RX_RAW_CHUNK * 2 + 10))
        assert len(buf._chunks) == 3  # 两块满 + 一块余量
        assert all(len(c) <= config.RX_RAW_CHUNK for c in buf._chunks)

    def test_iter_chunks_reconstructs_fully(self):
        buf = ChunkedBytes(1000)
        payload = bytes(range(200))
        buf.append(payload)
        joined = b"".join(buf.iter_chunks())
        assert joined == payload
        # 流式产出 = 整体拼接
        assert buf.to_bytes() == joined

    def test_iter_chunks_after_partial_drop(self):
        # 越过容量导致首块部分丢弃（_head 偏移），iter_chunks 应跳过已丢弃前缀
        buf = ChunkedBytes(config.RX_RAW_CHUNK)
        buf.append(b"a" * (config.RX_RAW_CHUNK + 5))
        chunks = list(buf.iter_chunks())
        assert b"".join(chunks) == buf.to_bytes()
        assert len(buf.to_bytes()) == config.RX_RAW_CHUNK


class TestMemoryBudget:
    def test_stays_within_cap(self):
        cap = 8 * 1024 * 1024
        buf = ChunkedBytes(cap)
        # 远超容量地灌数据，峰值内存仍应被容量框住
        for _ in range(50):
            buf.append(b"\x00" * (1024 * 1024))
        assert buf.memory_bytes() <= cap
        assert len(buf) <= cap

    def test_streaming_export_no_materialization(self):
        # iter_chunks 逐块产出，不依赖 to_bytes（to_bytes 才是整体物化）
        buf = ChunkedBytes(4 * 1024 * 1024)
        buf.append(b"\xab" * (4 * 1024 * 1024))
        seen = 0
        for chunk in buf.iter_chunks():
            seen += len(chunk)
            assert chunk  # 每块非空
        assert seen == len(buf)


class TestInvalid:
    def test_negative_maxlen_rejected(self):
        with pytest.raises(ValueError):
            ChunkedBytes(-1)
