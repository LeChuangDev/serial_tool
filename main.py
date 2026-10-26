# ====================== 程序入口 ======================
import sys

MISSING_DEP_MSG = (
    "缺少依赖：{name}\n"
    "请先安装运行依赖：\n"
    "    pip install PyQt6 pyserial"
)


def main():
    # 依赖检查：给出可执行的提示，而不是直接抛 traceback
    try:
        import PyQt6  # noqa: F401
    except ImportError:
        print(MISSING_DEP_MSG.format(name="PyQt6"), file=sys.stderr)
        return 1
    try:
        import serial  # noqa: F401
    except ImportError:
        print(MISSING_DEP_MSG.format(name="pyserial"), file=sys.stderr)
        return 1

    # Windows 高 DPI：旧系统没有 shcore，失败不应影响启动
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    from PyQt6.QtWidgets import QApplication
    from serialtool.main_window import LecoSerial

    app = QApplication(sys.argv)
    win = LecoSerial()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
