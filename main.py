# ====================== 程序入口 ======================
import sys

from PyQt6.QtWidgets import QApplication


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)

    from main_window import LecoSerial
    app = QApplication(sys.argv)
    win = LecoSerial()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
