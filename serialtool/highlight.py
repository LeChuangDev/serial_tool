# ====================== 关键字高亮 ======================
"""按规则给接收文本着色（纯逻辑，可在无 Qt 环境下单测）。

参考 Termite / Docklight 的做法：调试时真正需要"一眼看到"的往往只有
几类词（error、ready、reboot、某个 AT 关键字）。本模块只负责计算
"哪一段该用什么颜色"，渲染交给视图，因此可以完全脱离 GUI 测试。

规则以 JSON 存储（``[{"pattern": ..., "color": ..., "regex": ...}]``），
而不是 ``a|b|c`` 这种拼接串——正则里天然含 ``|``，拼接格式必然踩坑。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from . import config

__all__ = ["HighlightRule", "Highlighter", "parse_rules", "serialize_rules"]

log = logging.getLogger(__name__)

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_DEFAULT_COLOR = "#ef4a4e"


@dataclass(frozen=True, slots=True)
class HighlightRule:
    """一条高亮规则：字面量或正则 + 颜色。"""

    pattern: str
    color: str = _DEFAULT_COLOR
    regex: bool = False
    ignore_case: bool = True

    def __post_init__(self) -> None:
        if not _COLOR_RE.match(self.color or ""):
            object.__setattr__(self, "color", _DEFAULT_COLOR)

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "color": self.color,
            "regex": bool(self.regex),
            "ignore_case": bool(self.ignore_case),
        }

    @classmethod
    def from_dict(cls, raw: object) -> HighlightRule | None:
        if not isinstance(raw, dict):
            return None
        pattern = str(raw.get("pattern") or "").strip()
        if not pattern:
            return None
        return cls(
            pattern=pattern,
            color=str(raw.get("color") or _DEFAULT_COLOR),
            regex=bool(raw.get("regex", False)),
            ignore_case=bool(raw.get("ignore_case", True)),
        )

    def compile(self) -> re.Pattern[str] | None:
        """编译为可复用正则；非法表达式返回 ``None``（不拖垮整个渲染链）。"""
        flags = re.IGNORECASE if self.ignore_case else 0
        try:
            return re.compile(self.pattern if self.regex else re.escape(self.pattern), flags)
        except re.error as exc:
            log.warning("高亮规则正则非法，已跳过: %s (%s)", self.pattern, exc)
            return None


def parse_rules(text: str | None) -> list[HighlightRule]:
    """从 JSON 文本解析规则；空/损坏时回落到内置默认规则。"""
    if not text or not text.strip():
        return default_rules()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        log.warning("高亮规则 JSON 解析失败，使用默认规则")
        return default_rules()
    if not isinstance(raw, list):
        return default_rules()
    return [rule for rule in (HighlightRule.from_dict(item) for item in raw) if rule]


def serialize_rules(rules: Iterable[HighlightRule]) -> str:
    """规则序列化为 JSON（写入 QSettings / 导出配置文件）。"""
    return json.dumps([rule.to_dict() for rule in rules], ensure_ascii=False)


def default_rules() -> list[HighlightRule]:
    """内置默认规则：错误红 / 警告橙 / 成功绿 / 复位蓝。"""
    colors = ["#ef4a4e", "#e6a23c", "#3fb950", "#4a8cff"]
    patterns = ["error", "warn", r"^\s*(ok|ready|done)\b", r"\b(reboot|reset|rst)\b"]
    return [
        HighlightRule(pattern=pattern, color=color, regex=bool(index >= 2))
        for index, (pattern, color) in enumerate(zip(patterns, colors, strict=True))
    ]


class Highlighter:
    """规则集合 + 增量编译缓存。"""

    def __init__(self, rules: Iterable[HighlightRule] = (), enabled: bool = True) -> None:
        self._rules: list[HighlightRule] = []
        self._compiled: list[tuple[HighlightRule, re.Pattern[str]]] = []
        self._enabled = enabled
        self.set_rules(rules)

    @property
    def rules(self) -> list[HighlightRule]:
        return list(self._rules)

    @property
    def is_active(self) -> bool:
        return self._enabled and bool(self._compiled)

    def set_rules(self, rules: Iterable[HighlightRule]) -> None:
        """替换规则集合并预编译（渲染热路径里不再编译）。"""
        self._rules = list(rules)
        compiled: list[tuple[HighlightRule, re.Pattern[str]]] = []
        for rule in self._rules:
            pattern = rule.compile()
            if pattern is not None:
                compiled.append((rule, pattern))
        self._compiled = compiled

    def set_enabled(self, enabled: bool) -> None:
        """整体开关：关闭时保留规则，只是不再参与渲染。"""
        self._enabled = enabled

    def spans(self, text: str) -> list[tuple[int, int, str]]:
        """计算着色区间：``[(start, end, color)]``，按起点升序且互不重叠。

        重叠时**先定义者优先**（规则顺序即优先级），长度相同时取更早出现者，
        保证渲染结果稳定、不出现闪色。
        """
        if not self.is_active or not text:
            return []
        raw: list[tuple[int, int, str]] = []
        for rule, pattern in self._compiled:
            for match in pattern.finditer(text):
                start, end = match.start(), match.end()
                if end > start:
                    raw.append((start, end, rule.color))
                    if len(raw) >= config.HIGHLIGHT_MAX_SPANS:
                        break
        if not raw:
            return []
        raw.sort(key=lambda item: (item[0], -(item[1] - item[0])))
        result: list[tuple[int, int, str]] = []
        for start, end, color in raw:
            if result and start < result[-1][1]:
                continue  # 与前一段重叠：丢弃，保留先匹配者
            result.append((start, end, color))
        return result

    def segments(self, text: str, default_color: str) -> list[tuple[str, str]]:
        """把文本切成 ``[(片段, 颜色)]``，供渲染层逐段插入。"""
        spans = self.spans(text)
        if not spans:
            return [(text, default_color)]
        out: list[tuple[str, str]] = []
        cursor = 0
        for start, end, color in spans:
            if start > cursor:
                out.append((text[cursor:start], default_color))
            out.append((text[start:end], color))
            cursor = end
        if cursor < len(text):
            out.append((text[cursor:], default_color))
        return out


def with_color(rule: HighlightRule, color: str) -> HighlightRule:
    """返回换色的规则副本（规则表编辑时用，保持 dataclass 不可变语义）。"""
    return replace(rule, color=color)
