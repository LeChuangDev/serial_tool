# ====================== 配置持久化 ======================
"""QSettings 的类型安全封装。

原生 ``QSettings.value(key, default, type=bool)`` 在值缺失或类型不符时
行为不稳定（注册表里可能读回 ``"true"`` 字符串），本模块统一做一次
显式类型收敛，避免"上次退出时勾选状态错乱"这类难复现问题。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from PyQt6.QtCore import QByteArray, QSettings

__all__ = ["SettingsStore", "init_default_store"]

ORG_NAME = "Leeco"
APP_NAME = "SerialTool"

_TRUE_TOKENS = {"1", "true", "yes", "on"}
_FALSE_TOKENS = {"0", "false", "no", "off", ""}


def init_default_store() -> SettingsStore:
    """返回指向系统默认位置的配置存储（Windows 注册表 / *nix ini）。"""
    return SettingsStore(QSettings(ORG_NAME, APP_NAME))


class SettingsStore:
    """对 QSettings 的薄封装，所有读写都带类型收敛。"""

    def __init__(self, settings: QSettings) -> None:
        self._s = settings

    # ---------- 底层 ----------
    def raw(self) -> QSettings:
        return self._s

    def contains(self, key: str) -> bool:
        return self._s.contains(key)

    def remove(self, key: str) -> None:
        self._s.remove(key)

    def sync(self) -> None:
        self._s.sync()

    def set(self, key: str, value: Any) -> None:
        self._s.setValue(key, value)

    # ---------- 读取：带默认值与容错 ----------
    def get_str(self, key: str, default: str = "") -> str:
        value = self._s.value(key, default)
        if value is None:
            return default
        return str(value)

    def get_int(
        self, key: str, default: int = 0, *, lo: int | None = None, hi: int | None = None
    ) -> int:
        try:
            value = int(self._s.value(key, default))
        except (TypeError, ValueError):
            value = default
        if lo is not None:
            value = max(lo, value)
        if hi is not None:
            value = min(hi, value)
        return value

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self._s.value(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            token = value.strip().lower()
            if token in _TRUE_TOKENS:
                return True
            if token in _FALSE_TOKENS:
                return False
        return default

    def get_str_list(self, key: str) -> list[str]:
        """读取字符串列表；兼容历史版本存成单值的情况。"""
        value = self._s.value(key, [])
        return _as_str_list(value)

    def get_bool_list(self, key: str) -> list[bool]:
        value = self._s.value(key, [])
        if not isinstance(value, (list, tuple)):
            value = [value]
        out: list[bool] = []
        for item in value:
            if isinstance(item, bool):
                out.append(item)
            elif isinstance(item, (int, float)):
                out.append(bool(item))
            else:
                out.append(str(item).strip().lower() in _TRUE_TOKENS)
        return out

    def get_bytes(self, key: str) -> QByteArray | None:
        """读取几何/分割器状态（QSettings 在某些后端返回 str）。"""
        value = self._s.value(key)
        if value is None:
            return None
        if isinstance(value, QByteArray):
            return value
        if isinstance(value, (bytes, bytearray)):
            return QByteArray(bytes(value))
        return None


def _as_str_list(value: Any) -> list[str]:
    """把任意 QSettings 读回值收敛为 ``list[str]``（丢弃空值与重复项）。"""
    if value is None:
        return []
    items: Iterable[Any]
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        text = str(item)
        if text and text not in out:
            out.append(text)
    return out


def merge_unique(base: Sequence[str], extra: Iterable[str]) -> list[str]:
    """合并两个字符串序列并去重（保持 ``base`` 顺序）。"""
    out = list(base)
    for item in extra:
        if item and item not in out:
            out.append(item)
    return out
