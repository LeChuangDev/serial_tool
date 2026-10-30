# ====================== 接收数据格式化管线 ======================
"""把串口原始字节转换为可显示文本。

从主窗口抽出的纯逻辑层：不含任何 Qt 控件、可直接单元测试。
主窗口只负责把结果交给视图渲染。

设计要点：
- **增量解码**：多字节字符被包边界切开时留在解码器里，下个包接着拼，
  避免 UTF-8/GBK 半字符显示成 ``�``。
- **按行模式**：以 ``\\n`` 切分，未闭合的末行留在缓冲；缓冲超过上限
  强制输出，防止设备一直不发换行导致内存持续增长。
"""

from __future__ import annotations

import codecs

from . import config, utils

__all__ = ["RecvFormatter", "SPLIT_BY_PACKET", "SPLIT_BY_LINE"]

SPLIT_BY_PACKET = "packet"
SPLIT_BY_LINE = "line"

# 界面文案 → 内部模式
SPLIT_MODE_MAP: dict[str, str] = {
    "按包": SPLIT_BY_PACKET,
    "按行(\\n)": SPLIT_BY_LINE,
}


class RecvFormatter:
    """有状态的接收格式化器（一个串口会话对应一个实例）。"""

    def __init__(self, encoding: str = "utf-8", split_mode: str = SPLIT_BY_PACKET) -> None:
        self._encoding = encoding
        self._split_mode = split_mode
        self._decoder: codecs.IncrementalDecoder | None = None
        self._line_buf = ""

    # ---------- 配置 ----------
    @property
    def encoding(self) -> str:
        return self._encoding

    @encoding.setter
    def encoding(self, value: str) -> None:
        if value != self._encoding:
            self._encoding = value
            self._decoder = None  # 编码变了，旧解码器残留无效

    @property
    def split_mode(self) -> str:
        return self._split_mode

    @split_mode.setter
    def split_mode(self, value: str) -> None:
        if value != self._split_mode:
            self._split_mode = value
            self._line_buf = ""  # 断行模式变了，旧的半行不应污染新输出

    def reset(self) -> None:
        """丢弃解码器与行缓冲（清屏 / 重连 / 切换编码时调用）。"""
        self._decoder = None
        self._line_buf = ""

    # ---------- 核心 ----------
    def format(self, chunk: bytes, *, hex_mode: bool = False) -> str:
        """格式化一个数据包。

        Args:
            chunk: 原始字节。
            hex_mode: ``True`` 时输出空格分隔的大写 HEX。

        Returns:
            可显示文本；返回空串表示"本包暂无可显示内容"（如按行模式下行未闭合）。
        """
        if not chunk:
            return ""
        if hex_mode:
            return utils.hex_str(chunk)
        return self._format_text(chunk)

    def _format_text(self, chunk: bytes) -> str:
        if self._decoder is None:
            self._decoder = codecs.getincrementaldecoder(self._encoding)(errors="replace")
        text = self._decoder.decode(chunk)
        if self._split_mode != SPLIT_BY_LINE:
            return text

        self._line_buf += text
        if len(self._line_buf) > config.LINE_BUF_CAP:
            # 长行保护：设备一直不发换行时强制输出，避免缓冲无限增长
            out, self._line_buf = self._line_buf, ""
            return out
        if "\n" not in self._line_buf:
            return ""
        parts = self._line_buf.split("\n")
        self._line_buf = parts.pop()  # 末段未闭合，留到下一包
        # 保留空行语义：只丢掉末段，中间的空行原样输出
        return "\n".join(parts)

    def take_pending(self) -> str:
        """取出按行模式下尚未闭合的残留文本（暂停/停止时用于兜底显示）。"""
        pending, self._line_buf = self._line_buf, ""
        return pending
