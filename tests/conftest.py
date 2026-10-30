"""pytest 公共夹具。

无显示器环境（CI）下强制 offscreen 平台，保证 GUI 相关测试可运行。
同时把 ``QSettings`` 默认后端指向临时 INI 文件：主窗口用的是系统默认位置，
不隔离的话测试会读写开发者真实配置（既污染用户环境，也让用例结果依赖历史状态）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 必须在导入任何 PyQt6 模块之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from PyQt6.QtCore import QSettings  # noqa: E402

_SETTINGS_DIR = tempfile.mkdtemp(prefix="serialtool-settings-")
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)


@pytest.fixture(scope="session")
def qapp():
    """全局唯一 QApplication（Qt 不允许重复创建）。"""
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def clean_settings():
    """每个用例开始前清空配置，消除用例间的隐式状态传递。

    ``main_window`` 构造时会 ``load_settings()``，若上一个用例把时间戳格式改成
    "包间隔增量" 并落盘，下一个用例就会读到脏值 —— 这类顺序依赖只在整套运行时
    暴露、单跑必过，排查成本极高。这里统一清空，保证用例彼此独立。
    """
    from serialtool.settings_store import APP_NAME, ORG_NAME

    store = QSettings(ORG_NAME, APP_NAME)
    store.clear()
    store.sync()
    yield store
    store.clear()
    store.sync()


@pytest.fixture()
def main_window(qapp, clean_settings):
    """构造主窗口并在用例结束后彻底停线程，避免 QThread 未结束导致进程退出异常。"""
    from serialtool.main_window import LecoSerial

    window = LecoSerial()
    window.show()
    qapp.processEvents()
    try:
        yield window
    finally:
        window._closing = True
        window.rate_timer.stop()
        window.port_timer.stop()
        window.reconnect_timer.stop()
        window._tip_timer.stop()
        window.port_scanner.shutdown()
        window.close_serial()
        window.deleteLater()
        qapp.processEvents()
