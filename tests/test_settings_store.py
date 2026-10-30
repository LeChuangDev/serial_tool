"""配置存储单元测试：类型收敛、容错、导入导出兼容。"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QByteArray, QSettings

from serialtool.settings_store import SettingsStore, merge_unique


@pytest.fixture()
def store(tmp_path):
    settings = QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)
    settings.clear()
    yield SettingsStore(settings)
    settings.clear()


class TestBoolCoercion:
    @pytest.mark.parametrize(
        "stored, expected",
        [
            (True, True),
            (False, False),
            (1, True),
            (0, False),
            ("true", True),
            ("TRUE", True),
            ("yes", True),
            ("on", True),
            ("1", True),
            ("false", False),
            ("no", False),
            ("off", False),
            ("", False),
        ],
    )
    def test_reads_back_from_string(self, store, stored, expected):
        # ini 后端会把 bool 存成字符串，必须能正确读回
        store.set("flag", stored)
        store.sync()
        assert store.get_bool("flag") is expected

    def test_missing_key_uses_default(self, store):
        assert store.get_bool("nope", True) is True
        assert store.get_bool("nope", False) is False

    def test_garbage_value_uses_default(self, store):
        store.set("flag", "maybe")
        assert store.get_bool("flag", True) is True


class TestInt:
    def test_clamped(self, store):
        store.set("font", 99)
        assert store.get_int("font", 12, lo=8, hi=24) == 24
        store.set("font", 1)
        assert store.get_int("font", 12, lo=8, hi=24) == 8

    def test_non_numeric_falls_back(self, store):
        store.set("idx", "abc")
        assert store.get_int("idx", 3) == 3


class TestLists:
    def test_str_list_from_single_value(self, store):
        store.set("presets", "AT")
        assert store.get_str_list("presets") == ["AT"]

    def test_str_list_dedup_and_drop_empty(self, store):
        store.set("presets", ["AT", "AT", "", "AT+RST"])
        assert store.get_str_list("presets") == ["AT", "AT+RST"]

    def test_str_list_empty(self, store):
        assert store.get_str_list("missing") == []

    def test_bool_list(self, store):
        store.set("used", [True, False, "true", "0"])
        assert store.get_bool_list("used") == [True, False, True, False]

    def test_bool_list_single(self, store):
        store.set("used", True)
        assert store.get_bool_list("used") == [True]


class TestBytes:
    def test_bytearray_roundtrip(self, store):
        payload = QByteArray(b"\x01\x02\x03")
        store.set("geometry", payload)
        store.sync()
        restored = store.get_bytes("geometry")
        assert restored is not None
        assert bytes(restored) == b"\x01\x02\x03"

    def test_missing_returns_none(self, store):
        assert store.get_bytes("geometry") is None


def test_merge_unique():
    assert merge_unique(["a", "b"], ["b", "c", ""]) == ["a", "b", "c"]


def test_remove_and_contains(store):
    store.set("ck_pause", True)
    assert store.contains("ck_pause")
    store.remove("ck_pause")
    assert not store.contains("ck_pause")
