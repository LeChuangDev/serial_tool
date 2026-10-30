# ====================== 全局配置 ======================
"""常量、映射表与路径解析。

本模块只放"值"，不放逻辑：改行为请改代码模块，改参数请改这里。
"""

import os
import sys

import serial
from PyQt6.QtGui import QColor

# ---- 应用信息 ----
APP_NAME = "Leeco‑Serial 樂創串口调试终端"
APP_VERSION = "3.4"  # 顶部徽标与"关于"统一引用
WINDOW_WIDTH = 1180
WINDOW_HEIGHT = 800
APP_ICON_NAME = os.path.join("assets", "app.ico")  # 多尺寸 ICO：窗口/任务栏与打包 exe 通用
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- 波特率 ----
# 覆盖 MCU / 模块 / 工业设备常见档位；下拉可编辑，允许任意自定义值
BAUD_RATES = [
    "1200",
    "2400",
    "4800",
    "9600",
    "19200",
    "38400",
    "57600",
    "115200",
    "230400",
    "256000",
    "460800",
    "512000",
    "921600",
    "1500000",
    "2000000",
    "3000000",
]

# ---- 映射表 ----
ENCODING_MAP = {"UTF-8": "utf-8", "GBK": "gbk", "ASCII": "latin-1"}
PARITY_MAP = {
    "None": serial.PARITY_NONE,
    "Even": serial.PARITY_EVEN,
    "Odd": serial.PARITY_ODD,
    "Mark": serial.PARITY_MARK,
    "Space": serial.PARITY_SPACE,
}
# 流控：名称 → (rtscts, xonxoff, dsrdtr)
FLOW_MAP = {
    "无": (False, False, False),
    "RTS/CTS": (True, False, False),
    "XON/XOFF": (False, True, False),
}
# 行尾：界面文案 → 追加字节（发送时按此追加）
LINE_ENDINGS = {
    "无": b"",
    "\\r\\n": b"\r\n",
    "\\n": b"\n",
    "\\r": b"\r",
}

# ---- 颜色 ----
# 接收区为深色底：正文用浅灰白保证对比度，时间戳用弱化灰（正文配色见 RECV_THEME_TEXT）
TIME_COLOR = QColor("#7c8798")
TX_COLOR = QColor("#e6a23c")
ERROR_COLOR = QColor("#f5222d")
INFO_COLOR = QColor("#4a8cff")
OK_COLOR = QColor("#3fb950")
WARN_COLOR = QColor("#e6a23c")

# ---- 串口工作参数 ----
READ_CHUNK_SIZE = 4096  # 单次 read 最大字节
READ_TIMEOUT = 0.05  # 阻塞读超时（秒）
WRITE_TIMEOUT = 2.0  # 写超时，防止流控挂起导致关闭卡死
SEND_CHUNK_SIZE = 4096  # 发送文件分块大小（避免单次巨量写入卡住线程）
SEND_QUEUE_LIMIT = 512  # 文件发送时写队列最大积压块数（背压，约 2MB）
AGG_BYTES = 4096  # 接收聚合阈值（字节）
AGG_INTERVAL = 0.05  # 接收聚合阈值（秒）
MAX_BLOCKS = 10000  # 接收区最大显示行数（超出自动丢最旧）
LINE_BUF_CAP = 64 * 1024  # "按行"模式下无换行数据的缓冲上限（超出强制输出）
LINK_LOSS_TIMEOUT = 2.0  # 连续读失败超过该秒数即判定链路中断（触发自动重连）

# ---- 自动重连 ----
RECONNECT_MAX_ATTEMPTS = 10  # 0 表示无限重试
RECONNECT_BASE_DELAY = 0.8  # 首次重试间隔（秒）
RECONNECT_MAX_DELAY = 15.0  # 退避上限（秒）
RECONNECT_JITTER = 0.2  # 抖动比例，避免多实例同时抢占端口
RECONNECT_COOLDOWN = 1.2  # 断开后等待设备重新枚举的时间（秒）

# ---- 其他 ----
HISTORY_MAX = 30  # 发送历史最多保留条数
PRESET_DEFAULT = ["AT", "AT+RST", "AT+GMR", "AT+CWMODE=1"]

# ---- 时间戳 ----
TS_MODE_CLOCK = "clock"
TS_MODE_ELAPSED = "elapsed"
TS_MODE_DELTA = "delta"
# 界面文案 → 内部模式（第一项为默认）
TIMESTAMP_MODES = {
    "时钟 12:34:56.789": TS_MODE_CLOCK,
    "相对连接时间": TS_MODE_ELAPSED,
    "包间隔增量": TS_MODE_DELTA,
}
# 内部模式 → 界面文案（恢复配置时反向查找；两个方向都要有，否则只能单向转换）
TS_MODE_LABELS = {mode: label for label, mode in TIMESTAMP_MODES.items()}

# ---- 关键字高亮默认规则 ----
# 语法：pattern|#rrggbb|是否正则(0/1)
HIGHLIGHT_DEFAULT_RULES = [
    r"error|#ef4a4e|0",
    r"warn|#e6a23c|0",
    r"^\s*(ok|ready|done)\b|#3fb950|0",
    r"\b(reboot|reset|rst)\b|#4a8cff|0",
]
HIGHLIGHT_MAX_SPANS = 400  # 单行最多着色段，防止极端文本拖慢渲染

# ---- 接收区外观 ----
DEFAULT_FONT_SIZE = 12  # 接收区默认字号（pt）
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24
RECV_THEME_BLUE = "blue"  # 蓝底白字
RECV_THEME_GREEN = "green"  # 黑底绿字（经典）
RECV_FONT_FAMILY = "Consolas"  # 等宽字体，缺失时按下列顺序回退
RECV_FONT_FALLBACKS = [
    "Consolas",
    "Cascadia Mono",
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Menlo",
    "monospace",
]
# 注意：全局 QSS 的 QWidget{font-family} 会覆盖 setFont 的字体族，
# 因此等宽字体族必须写在接收区自身样式表里才能生效。
_RECV_FONT_CSS = (
    'font-family:"JetBrains Mono","Consolas","Cascadia Mono","DejaVu Sans Mono",monospace;'
)

# 接收区配色模板（字号由 setFont 控制，字体族/背景/边框/颜色由 QSS 控制）
RECV_THEME_STYLE = {
    RECV_THEME_BLUE: (
        "QPlainTextEdit{background-color:#0b1018;color:#dde5f0;"
        "border:1px solid #232e3f;border-radius:8px;padding:6px;"
        "selection-background-color:#2f6bff;" + _RECV_FONT_CSS + "}"
    ),
    RECV_THEME_GREEN: (
        "QPlainTextEdit{background-color:#0a0f0a;color:#39d353;"
        "border:1px solid #1f2b1f;border-radius:6px;padding:6px;"
        "selection-background-color:#1d3a22;" + _RECV_FONT_CSS + "}"
    ),
}

# 接收区正文颜色（与 QSS 背景配套；正文由代码显式设置字符格式，需与主题同步）
RECV_THEME_TEXT = {
    RECV_THEME_BLUE: QColor("#dde5f0"),
    RECV_THEME_GREEN: QColor("#39d353"),
}

# ---- 统计 / 速率图 ----
RATE_HISTORY = 60  # 速率图样本数（秒）
RATE_CHART_WIDTH = 170
RATE_CHART_HEIGHT = 44

# ---- 实时波形 ----
WAVE_HISTORY = 512  # 波形样本数（字节）
WAVE_CHART_HEIGHT = 96  # 波形条固定高度

# ---- 接收原始数据缓冲（用于 HEX 导出，超限丢最旧） ----
RX_RAW_CAP = 16 * 1024 * 1024

# ---- 工作模式（左上角切换） ----
MODE_TERMINAL = "terminal"  # 终端：文本收发
MODE_PLOT = "plot"  # 绘图：VOFA+ 风格协议示波器
MODES = [MODE_TERMINAL, MODE_PLOT]
MODE_LABELS = {MODE_TERMINAL: "终端", MODE_PLOT: "绘图"}

# ---- 绘图协议（兼容 VOFA+） ----
PROTOCOL_JUSTFLOAT = "justfloat"  # 小端 float 数组 + 0x7F800000 帧尾
PROTOCOL_FIREWATER = "firewater"  # CSV 文本流，逗号分隔 + 换行结尾
PLOT_PROTOCOLS = {
    "JustFloat（二进制浮点）": PROTOCOL_JUSTFLOAT,
    "FireWater（CSV 文本）": PROTOCOL_FIREWATER,
}
PLOT_PROTOCOL_LABELS = {mode: label for label, mode in PLOT_PROTOCOLS.items()}

# ---- 示波器 ----
SCOPE_HISTORY = 200_000  # 每通道最大采样点数（环形缓冲，超限丢最旧）
SCOPE_FPS = 30  # 重绘节流帧率（数据再快也只按此频率刷新，避免高频数据打满 CPU）
SCOPE_MIN_FPS = 10  # 自适应降帧下限：曲线很重时也不低于此帧率，保证还能看出走势
# 抽稀时每列的采样点数预算：超过就等距抽样，防止 16 通道长窗口把单帧绘制拖到上百毫秒
SCOPE_DECIMATE_POINTS_PER_COL = 3
# 单帧绘制的线段总数预算（通道数 × 像素列）：超了就把若干像素列合成一条粗线，
# 保证 16 通道宽窗口下仍能维持可用帧率（曲线是连续包络，视觉上几乎无损）
SCOPE_MAX_LINE_SEGMENTS = 12_000
SCOPE_MAX_CHANNELS = 16  # 自动检测通道数上限（防异常帧撑爆内存）
SCOPE_WINDOW_SECONDS = 10.0  # X 轴默认时间窗口（秒）
SCOPE_FFT_POINTS = 1024  # FFT 默认点数（取最近 N 个采样做频谱，2 的幂）
SCOPE_WINDOW_OPTIONS = [0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]  # 时间窗可选档位
SCOPE_FFT_OPTIONS = [256, 512, 1024, 2048, 4096]  # FFT 点数可选档位
SCOPE_COLORS = [
    "#4a8cff",
    "#3fb950",
    "#e6a23c",
    "#ef4a4e",
    "#b26bff",
    "#35d0d0",
    "#f06292",
    "#a8d878",
    "#ff9f43",
    "#7f8cff",
    "#00c2a8",
    "#ff6b9d",
    "#5ad1ff",
    "#c9d35f",
    "#ff8a80",
    "#8c9eff",
]
SCOPE_BG = "#0b1018"
SCOPE_GRID = "#1c2432"
SCOPE_AXIS = "#7c8798"


def resource_path(relative_path):
    """基于项目根目录解析资源（打包时基于 _MEIPASS），避免依赖启动目录。"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(PROJECT_ROOT, relative_path)


def app_dir():
    """可写文件（日志等）的基准目录：打包后为 exe 所在目录，源码运行时为项目根。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return PROJECT_ROOT
