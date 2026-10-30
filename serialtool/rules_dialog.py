# ====================== 高亮规则编辑器 ======================
"""关键字高亮规则的编辑对话框。

对外只暴露 :func:`run_rules_editor`：挡住对话框的细节，调用方拿到的是
"新规则列表"或 ``None``（用户取消）。

文本格式（一行一条，便于手改与复制粘贴）::

    error => #ef4a4e
    re:^\\s*(ok|ready)\\b => #3fb950

``re:`` 前缀表示正则，否则按字面量匹配（大小写不敏感）。
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import styles
from .highlight import HighlightRule, default_rules

log = logging.getLogger(__name__)

__all__ = ["run_rules_editor", "rules_to_text", "text_to_rules"]

_PLACEHOLDER = "#ef4a4e"


def rules_to_text(rules: list[HighlightRule]) -> str:
    """规则列表 → 可编辑文本。"""
    lines = []
    for rule in rules:
        prefix = "re:" if rule.regex else ""
        lines.append(f"{prefix}{rule.pattern} => {rule.color}")
    return "\n".join(lines)


def text_to_rules(text: str) -> tuple[list[HighlightRule], list[int]]:
    """文本 → 规则列表。

    Returns:
        ``(rules, bad_lines)``：``bad_lines`` 是解析失败的行号（从 1 开始），
        调用方据此提示用户，而不是静默丢规则。
    """
    rules: list[HighlightRule] = []
    bad: list[int] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, separator, color = line.rpartition("=>")
        if not separator:
            bad.append(number)
            continue
        pattern = pattern.strip()
        color = color.strip() or _PLACEHOLDER
        regex = pattern.startswith("re:")
        if regex:
            pattern = pattern[3:].strip()
        if not pattern:
            bad.append(number)
            continue
        rule = HighlightRule(pattern=pattern, color=color, regex=regex)
        if rule.compile() is None:
            bad.append(number)
            continue
        rules.append(rule)
    return rules, bad


class _RulesDialog(QDialog):
    """规则编辑窗口（模态）。"""

    def __init__(self, parent: QWidget | None, rules: list[HighlightRule]) -> None:
        super().__init__(parent)
        self.setWindowTitle("关键字高亮规则")
        self.resize(560, 420)
        self.setStyleSheet(styles.GLOBAL_QSS + styles.CARD_QSS)
        self._result: list[HighlightRule] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        hint = QLabel(
            "一行一条：<b>关键字 => #颜色</b>，正则加 <b>re:</b> 前缀。\n"
            "例：error => #ef4a4e     /     re:^\\s*(ok|ready)\\b => #3fb950\n"
            "匹配不区分大小写；规则顺序即优先级（先匹配者先着色）。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._edit = QPlainTextEdit(rules_to_text(rules))
        self._edit.setPlaceholderText("error => #ef4a4e")
        layout.addWidget(self._edit, 1)

        self._status = QLabel("")
        self._status.setObjectName("hint")
        layout.addWidget(self._status)

        btn_default = QPushButton("恢复默认")
        btn_default.setObjectName("small")
        btn_default.clicked.connect(lambda: self._edit.setPlainText(rules_to_text(default_rules())))
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("small")
        btn_cancel.clicked.connect(self.reject)
        btn_save = QPushButton("保存")
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self._on_save)

        actions = QHBoxLayout()
        actions.addWidget(btn_default)
        actions.addStretch()
        actions.addWidget(btn_cancel)
        actions.addWidget(btn_save)
        layout.addLayout(actions)

    def _on_save(self) -> None:
        rules, bad = text_to_rules(self._edit.toPlainText())
        if bad:
            self._status.setText("以下行无法解析，请检查格式：" + "、".join(map(str, bad)))
            return
        if not rules:
            self._status.setText("至少保留一条规则；如不想高亮，请直接取消并关闭开关。")
            return
        self._result = rules
        log.info("高亮规则已更新：%d 条", len(rules))
        self.accept()


def run_rules_editor(
    parent: QWidget | None, rules: list[HighlightRule]
) -> list[HighlightRule] | None:
    """打开规则编辑器；用户取消返回 ``None``。"""
    dialog = _RulesDialog(parent, rules)
    if dialog.exec() == QDialog.DialogCode.Accepted:
        return dialog._result
    return None
