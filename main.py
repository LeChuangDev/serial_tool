# ====================== 程序入口 ======================
"""启动流程：日志 → 依赖检查 → DPI → 主窗口 → 全局异常兜底。

保持入口极薄：任何业务逻辑都不应出现在这里。
"""

from __future__ import annotations

import os
import sys

MISSING_DEP_MSG = "缺少依赖：{name}\n请先安装运行依赖：\n    pip install -r requirements.txt"


def _check_dependencies() -> bool:
    """依赖检查：给出可执行提示，而不是直接抛 traceback。"""
    for module, name in (("PyQt6", "PyQt6"), ("serial", "pyserial")):
        try:
            __import__(module)
        except ImportError:
            print(MISSING_DEP_MSG.format(name=name), file=sys.stderr)
            return False
    return True


def _enable_high_dpi() -> None:
    """Windows 高 DPI 感知（旧系统缺 shcore，失败不影响启动）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def main() -> int:
    if not _check_dependencies():
        return 1
    _enable_high_dpi()

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from serialtool import config
    from serialtool.logging_setup import install_excepthook, setup_logging

    setup_logging(os.path.join(config.app_dir(), "logs"))

    app = QApplication(sys.argv)
    app.setApplicationName(config.APP_NAME)
    app.setApplicationVersion(config.APP_VERSION)
    app.setOrganizationName("Leeco")

    from serialtool.main_window import LecoSerial

    # 先装钩子再建窗口：构造阶段的异常也能落盘，窗口就绪后再补弹窗
    holder: dict[str, object] = {"window": None}

    def _report_fatal(summary: str) -> None:
        """异常钩子 → 主线程弹窗（延迟到事件循环，避免在异常栈里开对话框）。"""
        window = holder["window"]
        if window is not None:
            QTimer.singleShot(0, lambda: window.fatal_error.emit(summary))

    install_excepthook(_report_fatal)

    window = LecoSerial()
    holder["window"] = window
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
