# ====================== 分块数值序列 ======================
"""定长的分块浮点序列：为示波器环形缓冲省下 4 倍内存。

为什么不用 ``deque[float]``：Python 的 float 是堆上对象（24 字节）＋ deque 里的指针
（8 字节），每个采样点实际要 **32 字节**。16 通道 × 20 万点 = 100 MB，挂机一晚上
就能把普通工位的机器拖到换页。``array('d')`` 每点只要 8 字节，但 array 没有
``popleft``（删头部要整体搬移，O(n)）。

本模块用「块队列 + 块内偏移」补上这一点：块满才追加、丢弃只挪偏移或整块出队，
追加与丢弃都是均摊 O(1)，每点稳定 8 字节。

纯逻辑、不依赖 Qt，可直接单测。
"""

from __future__ import annotations

from array import array
from collections import deque
from itertools import islice

__all__ = ["ChunkedSeries"]

_BLOCK = 4096  # 每块样本数：8 字节 × 4096 = 32KB，块小便于整块回收


class ChunkedSeries:
    """定长（环形）浮点序列，语义上等于 ``deque(maxlen=n)`` 的数值专用子集。

    支持 ``append`` / ``extend_const`` / ``drop_front`` / ``popleft`` / ``len`` /
    ``index``（含负索引）/ ``iter`` / ``tail`` / ``clear``。不支持切片 —— 绘制与统计
    都走迭代或 ``tail()``，切片只会诱使调用方复制整段数据（正是要避免的）。
    """

    __slots__ = ("_blocks", "_head", "_maxlen", "_size")

    def __init__(self, maxlen: int) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen 必须为正数")
        self._blocks: deque[array] = deque()
        self._head = 0  # 第一块内已丢弃的元素个数
        self._maxlen = maxlen
        self._size = 0

    # ---------- 写入 ----------
    def append(self, value: float) -> None:
        """追加一个采样点；超出 ``maxlen`` 时自动丢弃最旧的。"""
        blocks = self._blocks
        if not blocks or len(blocks[-1]) >= _BLOCK:
            blocks.append(array("d"))
        blocks[-1].append(value)
        self._size += 1
        if self._size > self._maxlen:
            self._drop(self._size - self._maxlen)

    def extend_const(self, count: int, value: float) -> None:
        """追加 ``count`` 个相同值。

        新通道要用 NaN 补齐历史（保证时间轴对齐），逐点 append 太慢，
        ``array * n`` 是 C 层重复，代价可以忽略。
        """
        block_template = array("d", [value])
        while count > 0:
            blocks = self._blocks
            if not blocks or len(blocks[-1]) >= _BLOCK:
                blocks.append(array("d"))
            take = min(_BLOCK - len(blocks[-1]), count)
            blocks[-1].extend(block_template * take)
            self._size += take
            count -= take
        if self._size > self._maxlen:
            self._drop(self._size - self._maxlen)

    def drop_front(self, count: int) -> None:
        """丢弃最旧的 ``count`` 个元素（超过总量则清空）。"""
        if count > 0:
            self._drop(min(count, self._size))

    def popleft(self) -> float:
        if not self._size:
            raise IndexError("pop from empty ChunkedSeries")
        value = self._blocks[0][self._head]
        self._drop(1)
        return value

    def _drop(self, count: int) -> None:
        blocks = self._blocks
        while count > 0 and blocks:
            available = len(blocks[0]) - self._head
            if count >= available:
                blocks.popleft()
                self._head = 0
                count -= available
                self._size -= available
            else:
                self._head += count
                self._size -= count
                count = 0

    def clear(self) -> None:
        self._blocks.clear()
        self._head = 0
        self._size = 0

    # ---------- 读取 ----------
    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def __getitem__(self, index: int) -> float:
        size = self._size
        if index < 0:
            index += size
        if not 0 <= index < size:
            raise IndexError("ChunkedSeries index out of range")
        position = index + self._head
        return self._blocks[position // _BLOCK][position % _BLOCK]

    def __iter__(self):
        head = self._head
        for index, block in enumerate(self._blocks):
            # 第一块可能有已丢弃的前缀；其余块都是满块
            yield from islice(block, head if index == 0 else 0, None)

    def tail(self, count: int) -> list[float]:
        """最后 ``count`` 个元素（不足则全给），只复制需要的部分。"""
        count = min(count, self._size)
        start = self._size - count
        return [self[i] for i in range(start, self._size)]

    def memory_bytes(self) -> int:
        """当前样本占用的近似字节数（测试与自检用；不含块对象自身开销）。"""
        return sum(len(block) for block in self._blocks) * 8

    def __repr__(self) -> str:
        return f"ChunkedSeries(size={self._size}, maxlen={self._maxlen})"
