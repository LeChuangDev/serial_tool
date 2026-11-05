# ====================== 定长分块原始字节缓冲 ======================
"""为 HEX 导出保留"最近 N 字节"原始串口数据，且不随会话无限增长。

为什么不用单个 ``bytearray``：``bytearray`` 超出上限后靠 ``del buf[:n]`` 丢弃旧数据，
这是一次 O(n) 的内存搬移（16MB 上限下每次约 0.15ms，连续接收时会周期性触发）；
导出时还要整体 ``memoryview`` + ``bytes()`` 复制一大块。

本模块用「块队列 + 块内偏移」实现同样的环形语义，但：
- 丢弃只整块出队（O(1)，无 memmove）；
- 导出走 ``iter_chunks()`` 逐块流式写出，不整体物化；
- 每字节 1 字节（与 bytearray 相同），上限由 ``maxlen`` 控制，越界丢最旧；
- 块大小固定（``config.RX_RAW_CHUNK``），块数有界（8MB / 64KB = 128 块），
  不会因"每条数据包一个块"而堆积成千上万个小对象。

纯逻辑、不依赖 Qt，可直接单测。
"""

from __future__ import annotations

from collections.abc import Iterator

from . import config

__all__ = ["ChunkedBytes"]


class ChunkedBytes:
    """定长（环形）原始字节序列，语义上等于「容量受限的 bytearray」，但丢弃是 O(1)。

    支持 ``append`` / ``extend`` / ``clear`` / ``len`` / ``bool`` / ``iter_chunks`` /
    ``to_bytes`` / ``memory_bytes``。不做切片 —— 导出走 ``iter_chunks`` 流式迭代，
    切片只会诱使调用方整体复制缓冲（正是要避免的）。
    """

    __slots__ = ("_chunks", "_head", "_maxlen", "_size")

    def __init__(self, maxlen: int) -> None:
        if maxlen < 0:
            raise ValueError("maxlen 必须 >= 0")
        self._chunks: list[bytearray] = []
        self._head = 0  # 首块内已丢弃的字节数
        self._maxlen = maxlen
        self._size = 0

    # ---------- 写入 ----------
    def append(self, data: bytes) -> None:
        """追加一段字节；超过 ``maxlen`` 时自动丢弃最旧的（maxlen=0 视为禁用，直接丢弃）。"""
        if self._maxlen == 0 or not data:
            return
        view = memoryview(data)
        chunks = self._chunks
        # 先填当前未填满的块，避免每次追加都新建小块
        if chunks and len(chunks[-1]) < config.RX_RAW_CHUNK:
            last = chunks[-1]
            take = min(config.RX_RAW_CHUNK - len(last), len(view))
            last.extend(view[:take])
            self._size += take
            view = view[take:]
        while view:
            take = min(config.RX_RAW_CHUNK, len(view))
            chunks.append(bytearray(view[:take]))
            self._size += take
            view = view[take:]
        if self._size > self._maxlen:
            self._drop(self._size - self._maxlen)

    # 别名，语义对齐 bytearray.extend
    extend = append

    def _drop(self, count: int) -> None:
        chunks = self._chunks
        while count > 0 and chunks:
            available = len(chunks[0]) - self._head
            if count >= available:
                chunks.pop(0)
                self._head = 0
                count -= available
                self._size -= available
            else:
                self._head += count
                self._size -= count
                count = 0

    def clear(self) -> None:
        self._chunks.clear()
        self._head = 0
        self._size = 0

    # ---------- 读取 ----------
    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def memory_bytes(self) -> int:
        """当前容纳的字节数（不含块对象自身开销）。"""
        return self._size

    def to_bytes(self) -> bytes:
        """整体拼回 ``bytes``（测试与少量内部用途；导出请用 ``iter_chunks`` 流式）。"""
        if self._head == 0:
            return b"".join(self._chunks)
        out = bytearray(self._size)
        pos = 0
        for index, chunk in enumerate(self._chunks):
            start = self._head if index == 0 else 0
            seg = len(chunk) - start
            out[pos : pos + seg] = chunk[start:]
            pos += seg
        return bytes(out)

    def iter_chunks(self) -> Iterator[bytes]:
        """逐块产出有效字节（不含已丢弃的前缀），供流式导出。"""
        for index, chunk in enumerate(self._chunks):
            start = self._head if index == 0 else 0
            yield bytes(chunk[start:])

    def __iter__(self) -> Iterator[int]:
        for chunk in self.iter_chunks():
            yield from chunk

    def __repr__(self) -> str:
        return f"ChunkedBytes(size={self._size}, maxlen={self._maxlen})"
