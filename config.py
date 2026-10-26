# ====================== 全局配置 ======================
import os
import sys

import serial
from PyQt6.QtGui import QColor

# ---- 应用信息 ----
APP_NAME = "Leeco‑Serial 樂創串口调试终端"
WINDOW_WIDTH = 1040
WINDOW_HEIGHT = 720
APP_ICON_NAME = "app.ico"   # 多尺寸 ICO：窗口/任务栏与打包 exe 通用

# ---- 波特率 ----
BAUD_RATES = ["9600", "115200", "38400", "57600", "256000", "512000", "460800", "921600"]

# ---- 映射表 ----
ENCODING_MAP = {"UTF-8": "utf-8", "GBK": "gbk", "ASCII": "latin-1"}
PARITY_MAP = {"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN,
              "Odd": serial.PARITY_ODD}

# ---- 颜色 ----
RECV_COLOR = QColor("#3478d8")
TX_COLOR = QColor("#e6a23c")
ERROR_COLOR = QColor("#f5222d")

# ---- 串口工作参数 ----
READ_CHUNK_SIZE = 4096      # 单次 read 最大字节
READ_TIMEOUT = 0.05         # 阻塞读超时（秒）
WRITE_TIMEOUT = 2.0         # 写超时，防止流控挂起导致关闭卡死
AGG_BYTES = 4096            # 接收聚合阈值（字节）
AGG_INTERVAL = 0.05         # 接收聚合阈值（秒）
MAX_BLOCKS = 10000          # 接收区最大显示行数（超出自动丢最旧）

# ---- 其他 ----
HISTORY_MAX = 20            # 发送历史最多保留条数

# ---- 接收区外观 ----
DEFAULT_FONT_SIZE = 12      # 接收区默认字号（pt）
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24
RECV_THEME_BLUE = "blue"    # 蓝底白字
RECV_THEME_GREEN = "green"  # 黑底绿字（经典）
RECV_FONT_FAMILY = "Consolas"   # 等宽字体，缺失时回退系统字体

# 接收区配色模板（字体与字号由 setFont 控制，QSS 只负责背景/边框/颜色）
RECV_THEME_STYLE = {
    RECV_THEME_BLUE: (
        "QPlainTextEdit{background-color:#0b1018;color:#dde5f0;"
        "border:1px solid #232e3f;border-radius:8px;padding:6px;"
        "selection-background-color:#2f6bff;}"
    ),
    RECV_THEME_GREEN: (
        "QPlainTextEdit{background-color:#0a0f0a;color:#39d353;"
        "border:1px solid #1f2b1f;border-radius:6px;padding:6px;"
        "selection-background-color:#1d3a22;}"
    ),
}

# ---- 统计 / 速率图 ----
RATE_HISTORY = 60           # 速率图样本数（秒）
RATE_CHART_WIDTH = 170
RATE_CHART_HEIGHT = 56

# ---- 实时波形 ----
WAVE_HISTORY = 512          # 波形样本数（字节）
WAVE_CHART_HEIGHT = 110     # 波形条固定高度

# ---- 接收原始数据缓冲（用于 HEX 导出，超限丢最旧） ----
RX_RAW_CAP = 16 * 1024 * 1024

# ---- 多条发送列表 ----
MULTISEND_KEYS = ["multisend_used", "multisend_text", "multisend_delay"]


def resource_path(relative_path):
    """基于本模块所在目录解析资源，避免依赖启动目录（cwd）导致图标等找不到。"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)
