# ====================== 主程序窗口 ======================
import codecs
import datetime
import os
import time
from collections import deque

import serial
import serial.tools.list_ports
from PyQt6.QtCore import QTimer, Qt, QDateTime, QSettings, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QTextCursor, QShortcut, QKeySequence, \
    QAction, QPainter, QFont, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QRadioButton, QLineEdit, QPlainTextEdit,
    QMessageBox, QFileDialog, QFrame, QSplitter, QTableWidget, QTableWidgetItem,
    QHeaderView, QMenu, QAbstractItemView,
)

from . import config
from . import styles
from . import utils
from .serial_worker import SerialWorker


# ====================== 自定义下拉控件（点击即展开） ======================
class ClickComboBox(QComboBox):
    def __init__(self):
        super().__init__()
        self.setStyleSheet(styles.CLICK_COMBO_QSS)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.showPopup()


# ====================== 接收区（Ctrl+滚轮调字号） ======================
class RecvPlainTextEdit(QPlainTextEdit):
    font_delta = pyqtSignal(int)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.font_delta.emit(1 if d > 0 else -1)
            e.accept()
            return
        super().wheelEvent(e)


# ====================== 迷你速率图（纯 QPainter 自绘） ======================
class MiniRateChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = deque(maxlen=config.RATE_HISTORY)  # (rx, tx) B/s
        self.setFixedSize(config.RATE_CHART_WIDTH, config.RATE_CHART_HEIGHT)
        self.setToolTip("收/发速率（最近 60 秒）")

    def add_sample(self, rx, tx):
        self._data.append((rx, tx))
        self.update()

    def clear(self):
        self._data.clear()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # 背景
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0f141d"))
        p.drawRoundedRect(0, 0, w, h, 6, 6)
        if not self._data:
            return
        maxv = max(max(max(r, t) for r, t in self._data), 1.0)
        n = len(self._data)
        bw = w / n
        # 水平网格线
        p.setPen(QColor("#1c2432"))
        for i in range(1, 4):
            y = h - h * i / 4
            p.drawLine(0, int(y), w, int(y))
        # 柱子：RX 蓝（左半），TX 橙（右半）
        for idx, (rx, tx) in enumerate(self._data):
            x = int(idx * bw)
            rx_h = h * rx / maxv
            tx_h = h * tx / maxv
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(37, 104, 238, 190))
            p.drawRect(x + 1, int(h - rx_h), max(int(bw / 2) - 1, 1), int(rx_h))
            p.setBrush(QColor(230, 162, 60, 160))
            p.drawRect(x + int(bw / 2), int(h - tx_h), max(int(bw / 2) - 1, 1), int(tx_h))


# ====================== 实时波形（纯 QPainter 自绘，字节值 0-255） ======================
class WaveChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = deque(maxlen=config.WAVE_HISTORY)  # 字节值 0-255
        self.setFixedHeight(config.WAVE_CHART_HEIGHT)
        self.setToolTip("接收字节实时波形（最近 512 字节，0-255）")

    def add_samples(self, data):
        self._data.extend(data)
        self.update()

    def clear(self):
        self._data.clear()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # 背景
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#0f141d"))
        p.drawRoundedRect(0, 0, w, h, 6, 6)
        n = len(self._data)
        if n < 2:
            return
        # 水平网格线
        p.setPen(QColor("#1c2432"))
        for i in range(1, 4):
            y = h - h * i / 4
            p.drawLine(0, int(y), w, int(y))
        # 折线：左→右滚动，满宽后窗口滑动
        pad = 4
        span = h - 2 * pad
        step = w / (config.WAVE_HISTORY - 1)
        path = QPainterPath()
        for i, v in enumerate(self._data):
            x = i * step
            y = pad + span * (1 - v / 255.0)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor(74, 140, 255, 230), 1.6))
        p.drawPath(path)


# ====================== 主程序窗口 ======================
class LecoSerial(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.resize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

        # 图标（多尺寸 ICO：标题栏 + 任务栏）
        icon_full_path = config.resource_path(config.APP_ICON_NAME)
        if os.path.exists(icon_full_path):
            self.setWindowIcon(QIcon(icon_full_path))

        # ---------- 状态变量 ----------
        self.rx_count = 0
        self.tx_count = 0
        self.rx_rate = 0.0
        self.tx_rate = 0.0
        self._rate_t0 = time.time()
        self._rate_rx = 0
        self._rate_tx = 0
        self.current_port = ""
        self.current_baud = 0
        self.current_bits = 8
        self.current_stop = 1
        self.current_parity = serial.PARITY_NONE
        self._connected = False
        self._closing = False
        self._connect_t0 = 0.0
        self.log_file = None
        self._log_buf = []
        self._log_date = None          # 当前日志文件对应的日期（按日滚动）
        self._rx_raw = bytearray()     # 接收原始字节缓冲（HEX 导出用，超限丢最旧）
        self.line_buf = ""
        self._decoder = None           # 增量解码器：避免多字节字符被包边界截断成乱码
        self._updating_history = False
        self._font_size = config.DEFAULT_FONT_SIZE
        self._recv_theme = config.RECV_THEME_BLUE
        self._ms_looping = False
        self._ms_loop_idx = 0
        self._port_set = set()         # 上次扫描到的串口集合（热插拔比对）
        self._port_missing = False     # 已提示过"串口拔出"，避免每 2s 刷屏
        self._dot_state = None         # 上次状态点样式（仅变化时重刷）
        self._sending_file = False     # 文件发送中（防重入）
        self._file_send = None         # 正在发送的文件句柄
        self._file_path = ""
        self._file_total = 0
        self.qs = QSettings("Leeco", "SerialTool")

        # ---------- 定时器 ----------
        self.send_timer = QTimer()
        self.rate_timer = QTimer()
        self.port_timer = QTimer()          # 热插拔检测
        self._reset_timer = QTimer(self)    # 复位脉冲分步（不阻塞 UI）
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self._on_reset_tick)
        self._reset_step = 0
        # 多条发送循环：用成员定时器驱动，停止时能真正取消（singleShot 链无法取消）
        self._ms_loop_timer = QTimer(self)
        self._ms_loop_timer.setSingleShot(True)
        self._ms_loop_timer.timeout.connect(self._ms_loop_tick)
        # 文件发送泵：定时向写队列补水，避免大文件占满内存或阻塞调用栈
        self._file_timer = QTimer(self)
        self._file_timer.setInterval(5)
        self._file_timer.timeout.connect(self._pump_file_send)

        # ---------- 串口工作线程 ----------
        self.worker = SerialWorker(self)
        self.worker.data_received.connect(self.on_data_received)
        self.worker.connected.connect(self.on_connected)
        self.worker.disconnected.connect(self.on_disconnected)
        self.worker.error.connect(self.on_worker_error)
        self.worker.lines_state.connect(self.on_lines_state)

        # ---------- 全局样式（含卡片样式，卡片必须与全局 QSS 同一次应用才生效） ----------
        self.setStyleSheet(styles.GLOBAL_QSS + styles.CARD_QSS)

        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        self._build_shortcuts()

        # ---------- 信号绑定 ----------
        self.btn_link.clicked.connect(self.switch_serial)
        self.btn_refresh.clicked.connect(self.refresh_port_list)
        self.btn_recv_clear.clicked.connect(self.clear_recv)
        self.btn_wave.toggled.connect(self.on_wave_toggled)
        self.btn_clearcnt.clicked.connect(self.clear_count)
        self.btn_timer.clicked.connect(self.toggle_timer_send)
        self.send_timer.timeout.connect(lambda: self.do_send_text(self.send_box.toPlainText(), False))
        self.btn_send.clicked.connect(lambda: self.do_send_text(self.send_box.toPlainText(), True))
        self.btn_sendfile.clicked.connect(self.send_file)
        self.btn_savelog.clicked.connect(self.save_log)
        self.btn_rst.clicked.connect(self.reset_target)
        self.btn_preset_send.clicked.connect(self.send_preset)
        self.btn_preset_add.clicked.connect(self.add_preset)
        self.cb_history.currentIndexChanged.connect(self.on_history_changed)
        self.ck_wrap.toggled.connect(self.on_auto_wrap)
        self.ck_dtr.toggled.connect(self.on_dtr_toggled)
        self.ck_rts.toggled.connect(self.on_rts_toggled)
        self.ck_log.toggled.connect(self.on_log_toggled)
        self.rate_timer.timeout.connect(self.update_rate)
        self.port_timer.timeout.connect(self.check_ports)
        self.btn_find_next.clicked.connect(self.find_next)
        self.btn_find_close.clicked.connect(self.hide_find_bar)
        self.find_edit.returnPressed.connect(self.find_next)
        # 多条发送列表
        self.btn_ms_add.clicked.connect(self.ms_add)
        self.btn_ms_del.clicked.connect(self.ms_del)
        self.btn_ms_clear.clicked.connect(self.ms_clear)
        self.btn_ms_send.clicked.connect(self.ms_send_selected)
        self.btn_ms_loop.clicked.connect(self.ms_loop)
        # 接收区
        self.recv_box.font_delta.connect(self.change_font_size)
        # 切换编码 / HEX 显示时丢弃解码器残留，避免半个字符串到下一段
        self.cb_encoding.currentIndexChanged.connect(lambda _: self._reset_decoder())
        self.ck_hex.toggled.connect(lambda _: self._reset_decoder())

        self.refresh_port_list()
        self.load_settings()

        self.rate_timer.start(1000)
        self.port_timer.start(5000)      # 5s 扫一次串口：Windows 枚举有开销，热插拔体验够用
        self.on_auto_wrap(self.ck_wrap.isChecked())
        self.apply_recv_theme()
        self.update_status()

    # =========================================================
    #  界面构建
    # =========================================================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(0)

        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.main_split)

        # ===== 左参数面板：串口配置 + 显示选项 + 信号线 三张卡片 =====
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 8, 0)
        lv.setSpacing(10)
        lv.addWidget(self._make_serial_card(), 6)
        lv.addWidget(self._make_display_card(), 4)
        lv.addWidget(self._make_signal_card(), 4)
        self.main_split.addWidget(left)

        # ===== 右侧：上接收 + 下发送 =====
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        self.v_split = QSplitter(Qt.Orientation.Vertical)
        rv.addWidget(self.v_split)
        self.v_split.addWidget(self._make_recv_panel())
        self.v_split.addWidget(self._make_send_panel())

        self.main_split.addWidget(right)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([270, 770])
        self.v_split.setSizes([410, 310])
        self.v_split.setCollapsible(0, False)
        self.v_split.setCollapsible(1, False)

    def _make_card(self, title):
        frame = QFrame()
        frame.setObjectName("card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        v.addWidget(t)
        return frame, v

    def _make_serial_card(self):
        """串口配置卡片：端口/波特率/数据位/停止位/校验位 + 刷新/连接。"""
        frame, v = self._make_card("串口配置")
        g = QGridLayout(); g.setSpacing(8); g.setColumnStretch(1, 1)
        self.cb_com = ClickComboBox(); g.addWidget(QLabel("端口"), 0, 0); g.addWidget(self.cb_com, 0, 1)
        self.cb_baud = ClickComboBox(); self.cb_baud.setEditable(True)
        self.cb_baud.addItems(config.BAUD_RATES)
        self.cb_baud.setCurrentText("115200")
        g.addWidget(QLabel("波特率"), 1, 0); g.addWidget(self.cb_baud, 1, 1)
        self.cb_data = ClickComboBox(); self.cb_data.addItems(["5", "6", "7", "8"])
        self.cb_data.setCurrentText("8")
        g.addWidget(QLabel("数据位"), 2, 0); g.addWidget(self.cb_data, 2, 1)
        self.cb_stop = ClickComboBox(); self.cb_stop.addItems(["1", "2"])
        self.cb_stop.setCurrentText("1")
        g.addWidget(QLabel("停止位"), 3, 0); g.addWidget(self.cb_stop, 3, 1)
        self.cb_check = ClickComboBox(); self.cb_check.addItems(["None", "Even", "Odd"])
        g.addWidget(QLabel("校验位"), 4, 0); g.addWidget(self.cb_check, 4, 1)
        v.addLayout(g)
        v.addStretch()
        h2 = QHBoxLayout(); h2.setSpacing(8)
        self.btn_refresh = QPushButton("刷新端口"); self.btn_refresh.setObjectName("small")
        self.btn_link = QPushButton("连接"); self.btn_link.setObjectName("primary")
        h2.addWidget(self.btn_refresh); h2.addWidget(self.btn_link, 1)
        v.addLayout(h2)
        return frame

    def _make_display_card(self):
        """显示选项卡片：HEX/时间戳/自动清除/自动换行/日志 + 断行模式。"""
        frame, v = self._make_card("显示选项")
        g2 = QGridLayout(); g2.setSpacing(8)
        self.ck_hex = QCheckBox("HEX 显示")
        self.ck_time = QCheckBox("时间戳")
        self.ck_clear = QCheckBox("自动清除")
        self.ck_wrap = QCheckBox("自动换行"); self.ck_wrap.setChecked(True)
        self.ck_log = QCheckBox("记录到文件")
        g2.addWidget(self.ck_hex, 0, 0); g2.addWidget(self.ck_time, 0, 1)
        g2.addWidget(self.ck_clear, 1, 0); g2.addWidget(self.ck_wrap, 1, 1)
        g2.addWidget(self.ck_log, 2, 0)
        v.addLayout(g2)
        h = QHBoxLayout(); h.setSpacing(8)
        h.addWidget(QLabel("断行:"))
        self.cb_break = ClickComboBox()
        self.cb_break.addItems(["按包", "按行(\\n)"])
        h.addWidget(self.cb_break, 1)
        v.addLayout(h)
        v.addStretch()
        return frame

    def _make_signal_card(self):
        """信号线卡片：DTR/RTS 开关按钮 + 复位。"""
        frame, v = self._make_card("信号线")
        h = QHBoxLayout(); h.setSpacing(8)
        self.ck_dtr = QPushButton("DTR"); self.ck_dtr.setObjectName("small")
        self.ck_dtr.setCheckable(True); self.ck_dtr.setEnabled(False)
        self.ck_dtr.setToolTip("DTR 信号线开关（连接后可用）")
        self.ck_rts = QPushButton("RTS"); self.ck_rts.setObjectName("small")
        self.ck_rts.setCheckable(True); self.ck_rts.setEnabled(False)
        self.ck_rts.setToolTip("RTS 信号线开关（连接后可用）")
        h.addWidget(self.ck_dtr, 1); h.addWidget(self.ck_rts, 1)
        v.addLayout(h)
        self.btn_rst = QPushButton("复位"); self.btn_rst.setObjectName("small")
        self.btn_rst.setToolTip("RTS 低电平复位脉冲，用于 ESP32 复位刷新\n若 EN 接 DTR，请改 reset_target() 里的 rts 为 dtr")
        v.addWidget(self.btn_rst)
        tip = QLabel("复位脉冲：RTS 低电平 150ms（适合 ESP32）")
        tip.setObjectName("hint"); tip.setWordWrap(True)
        v.addWidget(tip)
        v.addStretch()
        return frame

    def _make_recv_panel(self):
        """接收区卡片：标题工具条 + 实时波形 + 控制台 + 查找条。"""
        frame = QFrame(); frame.setObjectName("card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        head = QHBoxLayout(); head.setContentsMargins(12, 10, 12, 8); head.setSpacing(8)
        self.lab_recv_title = QLabel("接收")
        self.lab_recv_title.setObjectName("sectionTitle")
        head.addWidget(self.lab_recv_title)
        self.lab_recv_stat = QLabel("收 0 B  发 0 B | 行 0")
        self.lab_recv_stat.setObjectName("hint")
        head.addWidget(self.lab_recv_stat)
        head.addStretch()
        self.rate_chart = MiniRateChart()
        head.addWidget(self.rate_chart)
        self.btn_wave = QPushButton("波形"); self.btn_wave.setObjectName("tool")
        self.btn_wave.setCheckable(True)
        self.btn_wave.setToolTip("开关接收字节实时波形")
        head.addWidget(self.btn_wave)
        self.btn_pause = QPushButton("暂停"); self.btn_pause.setObjectName("tool")
        self.btn_pause.setCheckable(True)
        head.addWidget(self.btn_pause)
        self.btn_recv_clear = QPushButton("清空"); self.btn_recv_clear.setObjectName("tool")
        head.addWidget(self.btn_recv_clear)
        self.btn_clearcnt = QPushButton("清空计数"); self.btn_clearcnt.setObjectName("tool")
        head.addWidget(self.btn_clearcnt)
        v.addLayout(head)
        sep = QFrame(); sep.setObjectName("hsep"); v.addWidget(sep)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(10, 8, 10, 10); bl.setSpacing(6)
        # 实时波形条（默认隐藏）
        self.wave_chart = WaveChart()
        self.wave_chart.setVisible(False)
        bl.addWidget(self.wave_chart)
        self.recv_box = RecvPlainTextEdit()
        self.recv_box.setReadOnly(True)
        self.recv_box.setMaximumBlockCount(config.MAX_BLOCKS)
        self.recv_box.setPlaceholderText("等待数据 ...")
        self.recv_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.recv_box.customContextMenuRequested.connect(self._recv_context_menu)
        bl.addWidget(self.recv_box, 1)
        # 查找条（Ctrl+F 呼出）
        self.find_bar = QWidget()
        fb = QHBoxLayout(self.find_bar)
        fb.setContentsMargins(0, 0, 0, 0); fb.setSpacing(6)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("查找 ...")
        self.btn_find_next = QPushButton("下一个"); self.btn_find_next.setObjectName("small")
        self.btn_find_close = QPushButton("关闭"); self.btn_find_close.setObjectName("small")
        fb.addWidget(self.find_edit, 1)
        fb.addWidget(self.btn_find_next)
        fb.addWidget(self.btn_find_close)
        self.find_bar.setVisible(False)
        bl.addWidget(self.find_bar)
        v.addWidget(body, 1)
        return frame

    def _make_send_panel(self):
        """发送区卡片：模式/选项/编码工具条 + 输入框 + 预设历史 + 操作按钮。"""
        frame = QFrame(); frame.setObjectName("card")
        v = QVBoxLayout(frame)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        # 头部：模式 / 选项 / 编码
        head = QHBoxLayout(); head.setContentsMargins(12, 10, 12, 8); head.setSpacing(8)
        self.lab_send_title = QLabel("发送")
        self.lab_send_title.setObjectName("sectionTitle")
        head.addWidget(self.lab_send_title)
        self.send_ascii = QRadioButton("ASCII"); self.send_ascii.setChecked(True)
        self.send_hex = QRadioButton("HEX")
        head.addWidget(self.send_ascii); head.addWidget(self.send_hex)
        head.addSpacing(6)
        self.ck_newline = QCheckBox("新行\\r\\n")
        self.ck_echo = QCheckBox("TX 回显")
        self.ck_crc = QCheckBox("追加CRC16")
        head.addWidget(self.ck_newline); head.addWidget(self.ck_echo); head.addWidget(self.ck_crc)
        head.addSpacing(6)
        head.addWidget(QLabel("编码:"))
        self.cb_encoding = ClickComboBox()
        self.cb_encoding.addItems(["UTF-8", "GBK", "ASCII"])
        head.addWidget(self.cb_encoding)
        head.addStretch()
        v.addLayout(head)
        sep = QFrame(); sep.setObjectName("hsep"); v.addWidget(sep)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(10, 8, 10, 10); bl.setSpacing(6)
        self.send_box = QPlainTextEdit()
        self.send_box.setPlaceholderText("输入要发送的内容 ...")
        self.send_box.setMinimumHeight(90)
        bl.addWidget(self.send_box, 1)

        # 预设 / 历史
        row = QHBoxLayout(); row.setSpacing(8)
        row.addWidget(QLabel("预设:"))
        self.cb_preset = ClickComboBox()
        self.cb_preset.addItems(["AT", "AT+RST", "AT+GMR", "AT+CWMODE=1"])
        row.addWidget(self.cb_preset, 1)
        self.btn_preset_send = QPushButton("发送"); self.btn_preset_send.setObjectName("small")
        self.btn_preset_add = QPushButton("添加"); self.btn_preset_add.setObjectName("small")
        row.addWidget(self.btn_preset_send)
        row.addWidget(self.btn_preset_add)
        row.addSpacing(6)
        row.addWidget(QLabel("历史:"))
        self.cb_history = ClickComboBox()
        row.addWidget(self.cb_history, 1)
        bl.addLayout(row)

        b = QHBoxLayout(); b.setSpacing(8)
        self.btn_send = QPushButton("发送"); self.btn_send.setObjectName("primary")
        self.btn_timer = QPushButton("定时发送"); self.btn_timer.setObjectName("tool")
        self.btn_timer.setCheckable(True)
        self.edit_period = QLineEdit("100"); self.edit_period.setFixedWidth(64)
        self.edit_period.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.btn_sendfile = QPushButton("发送文件"); self.btn_sendfile.setObjectName("tool")
        self.btn_savelog = QPushButton("保存日志"); self.btn_savelog.setObjectName("tool")
        b.addWidget(self.btn_send)
        b.addSpacing(6)
        b.addWidget(self.btn_timer)
        b.addWidget(QLabel("周期ms:"))
        b.addWidget(self.edit_period)
        b.addStretch()
        b.addWidget(self.btn_sendfile)
        b.addWidget(self.btn_savelog)
        # 发送面板右下角连接状态 pill（对齐画布）
        send_pill = QFrame(); send_pill.setObjectName("pill")
        sp = QHBoxLayout(send_pill)
        sp.setContentsMargins(8, 2, 10, 2); sp.setSpacing(6)
        self.dot2 = QLabel()
        self.dot2.setObjectName("dot")
        self.dot2.setFixedSize(8, 8)
        self.lab_conn2 = QLabel("未连接")
        self.lab_conn2.setMinimumWidth(60)
        sp.addWidget(self.dot2)
        sp.addWidget(self.lab_conn2)
        b.addWidget(send_pill)
        bl.addLayout(b)

        bl.addWidget(self._make_multisend())
        v.addWidget(body, 1)
        return frame

    def _make_multisend(self):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        head = QHBoxLayout(); head.setSpacing(8)
        t = QLabel("多条发送"); t.setObjectName("cardTitle")
        head.addWidget(t)
        head.addStretch()
        # 折叠开关：默认收起，为接收区/输入区腾出空间
        self.btn_ms_fold = QPushButton("展开"); self.btn_ms_fold.setObjectName("small")
        self.btn_ms_fold.setCheckable(True)
        self.btn_ms_fold.setToolTip("展开/收起多条发送列表")
        head.addWidget(self.btn_ms_fold)
        # 操作按钮组（收起时一并隐藏）
        self.ms_actions = QWidget()
        ah = QHBoxLayout(self.ms_actions)
        ah.setContentsMargins(0, 0, 0, 0); ah.setSpacing(8)
        self.btn_ms_add = QPushButton("添加"); self.btn_ms_add.setObjectName("small")
        self.btn_ms_del = QPushButton("删除"); self.btn_ms_del.setObjectName("small")
        self.btn_ms_clear = QPushButton("清空"); self.btn_ms_clear.setObjectName("small")
        ah.addWidget(self.btn_ms_add); ah.addWidget(self.btn_ms_del); ah.addWidget(self.btn_ms_clear)
        head.addWidget(self.ms_actions)
        v.addLayout(head)

        # 可折叠主体：表格 + 底部按钮行
        self.ms_body = QWidget()
        bv = QVBoxLayout(self.ms_body)
        bv.setContentsMargins(0, 0, 0, 0); bv.setSpacing(4)
        self.ms_table = QTableWidget(0, 3)
        self.ms_table.setHorizontalHeaderLabels(["使用", "指令内容", "延迟ms"])
        self.ms_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ms_table.setColumnWidth(0, 50)
        self.ms_table.setColumnWidth(2, 70)
        self.ms_table.verticalHeader().setVisible(False)
        self.ms_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ms_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ms_table.setMinimumHeight(90)
        bv.addWidget(self.ms_table)

        b = QHBoxLayout(); b.setSpacing(8)
        self.btn_ms_send = QPushButton("发送选中"); self.btn_ms_send.setObjectName("small")
        self.btn_ms_loop = QPushButton("循环发送"); self.btn_ms_loop.setObjectName("small")
        self.btn_ms_loop.setCheckable(True)
        b.addWidget(self.btn_ms_send)
        b.addWidget(self.btn_ms_loop)
        b.addStretch()
        bv.addLayout(b)

        v.addWidget(self.ms_body)
        self.btn_ms_fold.toggled.connect(self.on_ms_fold)
        self.btn_ms_fold.setChecked(True)   # 默认收起
        return box

    def on_ms_fold(self, folded):
        self.ms_body.setVisible(not folded)
        self.ms_actions.setVisible(not folded)
        self.btn_ms_fold.setText("展开" if folded else "收起")

    def _build_menu(self):
        mb = self.menuBar()
        # 顶部栏品牌标题 + 版本徽标（对齐画布顶部栏）
        brand = QLabel("串口调试终端")
        brand.setObjectName("brandTitle")
        mb.setCornerWidget(brand, Qt.Corner.TopLeftCorner)
        ver = QLabel("v" + config.APP_VERSION)
        ver.setObjectName("brandVer")
        mb.setCornerWidget(ver, Qt.Corner.TopRightCorner)
        m_file = mb.addMenu("文件")
        a1 = QAction("发送文件...", self); a1.triggered.connect(self.send_file)
        a2 = QAction("保存接收日志...", self); a2.triggered.connect(self.save_log)
        a3 = QAction("退出", self); a3.triggered.connect(self.close)
        m_file.addAction(a1); m_file.addAction(a2); m_file.addSeparator(); m_file.addAction(a3)
        m_serial = mb.addMenu("串口")
        a4 = QAction("刷新端口", self); a4.setShortcut("F5")
        a4.triggered.connect(self.refresh_port_list)
        a5 = QAction("连接 / 断开", self); a5.triggered.connect(self.switch_serial)
        m_serial.addAction(a4); m_serial.addAction(a5)
        m_tools = mb.addMenu("工具")
        a7 = QAction("校验和计算", self); a7.triggered.connect(lambda: self.open_tools(0))
        a8 = QAction("HEX / ASCII 转换", self); a8.triggered.connect(lambda: self.open_tools(1))
        a9 = QAction("导出接收数据(HEX)...", self); a9.triggered.connect(self.export_log_hex)
        m_tools.addAction(a7); m_tools.addAction(a8); m_tools.addSeparator(); m_tools.addAction(a9)
        m_help = mb.addMenu("帮助")
        a6 = QAction("关于", self); a6.triggered.connect(self.show_about)
        m_help.addAction(a6)

    def open_tools(self, tab=0):
        """打开校验和 / 转换工具对话框（tab: 0 校验和, 1 HEX/ASCII 转换）。"""
        from .tools_dialog import ToolsDialog
        dlg = ToolsDialog(self, tab)
        dlg.exec()

    def _build_statusbar(self):
        """状态栏：连接 pill（圆点+状态）+ 端口芯片 + 临时提示 + 收发统计。"""
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        # 连接状态 pill：圆点 + 文字一体胶囊（宽度定宽避免每秒刷新伸缩）
        pill = QFrame(); pill.setObjectName("pill")
        ph = QHBoxLayout(pill)
        ph.setContentsMargins(8, 2, 10, 2); ph.setSpacing(6)
        self.dot = QLabel()
        self.dot.setObjectName("dot")
        self.dot.setFixedSize(8, 8)
        self.lab_conn = QLabel("未连接")
        self.lab_conn.setMinimumWidth(150)
        ph.addWidget(self.dot)
        ph.addWidget(self.lab_conn)
        sb.addWidget(pill)
        self.lab_port = QLabel("")
        self.lab_port.setObjectName("portChip")
        sb.addWidget(self.lab_port)
        self.lab_tip = QLabel("")
        self.lab_tip.setObjectName("hint")
        self.lab_status = QLabel(self._fmt_status(0, 0, 0, 0))
        sb.addWidget(self.lab_tip, 1)
        sb.addPermanentWidget(self.lab_status)
        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(lambda: self.lab_tip.setText(""))

    def _build_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Return"), self,
                  activated=lambda: self.do_send_text(self.send_box.toPlainText(), True))
        QShortcut(QKeySequence("Ctrl+Enter"), self,
                  activated=lambda: self.do_send_text(self.send_box.toPlainText(), True))
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.show_find_bar)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.clear_recv)
        QShortcut(QKeySequence("Esc"), self.find_edit, activated=self.hide_find_bar)

    # =========================================================
    #  串口连接 / 断开 / 切换
    # =========================================================
    def restyle_link_btn(self, obj):
        self.btn_link.setObjectName(obj)
        st = self.btn_link.style(); st.unpolish(self.btn_link); st.polish(self.btn_link)

    def close_serial(self):
        self.send_timer.stop()
        self.btn_timer.setChecked(False)
        self._reset_timer.stop()
        self._ms_stop_loop()
        if self._sending_file:
            self._finish_file_send(interrupted=True)   # 关闭文件句柄并复位状态
        # 只要线程在跑就停，包含"连接中"的重试阶段：
        # 否则此时断开/关窗会留下仍在运行的 QThread（进程退出时 Qt 致命退出），
        # 且线程随后成功打开串口会把界面改成"已连接"。
        if self.worker.isRunning():
            self.worker.stop()
        if self.worker.isRunning():
            print("【警告】串口线程未在超时内退出，可能仍在收尾")
        self.worker.clear_cmds()      # 丢弃未发完的写入，避免下次连接时误发
        if self._connected:
            print("【日志】串口已关闭")

    def open_port(self, port, baud, bits, stop, par):
        self.current_port = port
        self.current_baud = baud
        self.current_bits = bits
        self.current_stop = stop
        self.current_parity = par
        self.worker.open(port, baud, bits, stop, par)

    def switch_serial(self):
        try:
            port = self.cb_com.currentText()
            baud = int(self.cb_baud.currentText())
            bits = int(self.cb_data.currentText())
            stop = int(self.cb_stop.currentText())
            par = config.PARITY_MAP[self.cb_check.currentText()]
        except (ValueError, KeyError):
            QMessageBox.warning(self, "参数错误", "请检查串口参数是否正确填写")
            return
        if self._connected:
            changed = (port != self.current_port or baud != self.current_baud
                       or bits != self.current_bits or stop != self.current_stop
                       or par != self.current_parity)
            if not changed:
                self.close_serial()
                return
            self.close_serial()
        self.open_port(port, baud, bits, stop, par)

    # 热插拔检测：只提示，不自动断开（避免 ESP32 复位时设备短暂消失被误判）
    def _scan_ports(self):
        """扫描当前串口设备名（过滤蓝牙虚拟串口）。"""
        try:
            ports = serial.tools.list_ports.comports()
        except Exception:
            return []
        out = []
        for p in ports:
            d = p.description.lower()
            if "bluetooth" in d or "蓝牙" in d:
                continue
            out.append(p.device)
        return sorted(out)

    def check_ports(self):
        if self._closing:
            return
        now = set(self._scan_ports())
        if now != self._port_set:
            self.refresh_port_list()      # 热插拔：下拉列表自动同步（保留当前选择）
        if self._connected and self.current_port and self.current_port not in now:
            if not self._port_missing:
                self._port_missing = True
                self.show_tip("提示：串口已拔出，请重新连接", 5000)
                print("【日志】检测到串口拔出")
        else:
            self._port_missing = False

    def refresh_port_list(self):
        ports = self._scan_ports()
        self._port_set = set(ports)
        cur = self.cb_com.currentText()
        self.cb_com.blockSignals(True)
        self.cb_com.clear()
        self.cb_com.addItems(ports)
        if cur in ports:
            self.cb_com.setCurrentText(cur)
        elif len(ports) == 1:
            self.cb_com.setCurrentText(ports[0])
        self.cb_com.blockSignals(False)

    # ---------- worker 信号槽 ----------
    def on_connected(self, info):
        self._connected = True
        self._connect_t0 = time.time()
        self._reset_decoder()          # 新会话不拼接上一次的残留字符
        self.restyle_link_btn("danger")
        self.ck_dtr.setEnabled(True); self.ck_rts.setEnabled(True)
        self.lab_port.setText(f"{self.current_port} @ {self.current_baud}")
        self.append_recv_text(f"= 串口已连接 {info} =", self._recv_text_color())
        self.update_status()
        self.update_conn_label()

    def on_disconnected(self, reason):
        self._connected = False
        self._ms_stop_loop()
        if self._closing:
            return
        self.restyle_link_btn("primary")
        self.ck_dtr.setEnabled(False); self.ck_rts.setEnabled(False)
        self.lab_port.setText("")
        self.append_recv_text(f"= {reason} =", self._recv_text_color())
        self.update_status()
        self.update_conn_label()

    def on_worker_error(self, msg):
        if self._closing:
            # 退出过程中不再弹模态框，否则会阻塞关闭
            print(f"【警告】{msg}")
            return
        if "串口打开失败" in msg:
            QMessageBox.warning(self, "串口错误", msg)
        else:
            print(f"【警告】{msg}")
            self.append_recv_text(f"!!! {msg}", config.ERROR_COLOR)

    def on_lines_state(self, dtr, rts):
        """打开端口后的实际 DTR/RTS 电平：以界面勾选状态为准回写硬件，
        保证"按钮显示"与"实际信号线"一致（pyserial 默认会断言这两条线）。"""
        for btn, real, setter in ((self.ck_dtr, dtr, self.worker.set_dtr),
                                  (self.ck_rts, rts, self.worker.set_rts)):
            want = btn.isChecked()
            if want != real:
                setter(want)      # 用户设置与硬件实际不符时下发用户值

    # =========================================================
    #  接收处理
    # =========================================================
    def on_data_received(self, packets):
        if self._closing:
            return
        self.rx_count += sum(len(p) for p, _ in packets)
        texts = []
        for chunk, ts in packets:
            # 原始字节缓冲（HEX 导出用）：超过上限时一次性回收，避免每包都搬移整块
            self._rx_raw.extend(chunk)
            if len(self._rx_raw) > config.RX_RAW_CAP:
                keep = config.RX_RAW_CAP // 8 * 7
                del self._rx_raw[:len(self._rx_raw) - keep]
            # 实时波形（仅开启时采样，deque 有界）
            if self.btn_wave.isChecked():
                self.wave_chart.add_samples(chunk)
            res = self.format_received(chunk)
            if res:
                texts.append((res, ts))
        if self.log_file and texts:
            self._log_buf.extend(t for t, _ in texts)
        if not self.btn_pause.isChecked():
            if self.ck_clear.isChecked():
                self.recv_box.clear()
            self.append_recv_batch(texts)

    def _reset_decoder(self):
        """丢弃解码器内部残留的半个多字节字符（切换编码 / 清屏 / 重连时调用）。"""
        self._decoder = None
        self.line_buf = ""

    def _recv_text_color(self):
        """接收区正文颜色跟随当前配色主题（深色底用浅色字，保证对比度）。"""
        return config.RECV_THEME_TEXT.get(self._recv_theme, config.RECV_COLOR)

    def format_received(self, buf):
        if self.ck_hex.isChecked():
            return utils.hex_str(buf)
        # 增量解码：多字节字符被包边界切开时留在解码器里，下个包接着拼
        if self._decoder is None:
            self._decoder = codecs.getincrementaldecoder(self.encoding)(errors="replace")
        text = self._decoder.decode(buf)
        if self.cb_break.currentText().startswith("按行"):
            self.line_buf += text
            if len(self.line_buf) > config.LINE_BUF_CAP:
                # 长行保护：设备一直不发换行时强制输出，避免缓冲无限增长
                out, self.line_buf = self.line_buf, ""
                return out
            if "\n" in self.line_buf:
                parts = self.line_buf.split("\n")
                self.line_buf = parts.pop()
                return "\n".join(p for p in parts if p != "")
            return ""
        return text

    def _follow_tail(self):
        """接收区当前是否停在底部：用户向上翻历史时不强行拉回。"""
        sb = self.recv_box.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def append_recv_batch(self, texts):
        """批量渲染：一次光标操作插入整批文本，减少重排。"""
        if not texts:
            return
        follow = self._follow_tail()
        cursor = self.recv_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        # 分别取副本：QTextCharFormat 在 Python 中赋值是同一对象，共用会互相覆盖颜色
        fmt = cursor.charFormat(); fmt.setForeground(self._recv_text_color())
        ts_fmt = cursor.charFormat(); ts_fmt.setForeground(config.TIME_COLOR)
        time_checked = self.ck_time.isChecked()
        for text, ts in texts:
            if time_checked:
                t = QDateTime.fromMSecsSinceEpoch(int(ts * 1000)).toString("HH:mm:ss.zzz")
                cursor.setCharFormat(ts_fmt)
                cursor.insertText(f"[{t}] ")
            cursor.setCharFormat(fmt)
            cursor.insertText(text.rstrip("\n") + "\n")
        if follow:
            self.recv_box.setTextCursor(cursor)
            self.recv_box.ensureCursorVisible()

    def append_recv_text(self, text, color: QColor):
        """单行追加（TX 回显 / 状态提示用）。"""
        if not text:
            return
        follow = self._follow_tail()
        cursor = self.recv_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = cursor.charFormat(); fmt.setForeground(color)
        ts_fmt = cursor.charFormat(); ts_fmt.setForeground(config.TIME_COLOR)
        if self.ck_time.isChecked():
            t = QDateTime.currentDateTime().toString("HH:mm:ss.zzz")
            cursor.setCharFormat(ts_fmt)
            cursor.insertText(f"[{t}] ")
        cursor.setCharFormat(fmt)
        cursor.insertText(text.rstrip("\n") + "\n")
        if follow:
            self.recv_box.setTextCursor(cursor)
            self.recv_box.ensureCursorVisible()

    @property
    def encoding(self):
        return config.ENCODING_MAP[self.cb_encoding.currentText()]

    # =========================================================
    #  发送处理
    # =========================================================
    def do_send_text(self, text, show_warn=True):
        if not self._connected:
            if show_warn:
                QMessageBox.warning(self, "提示", "请先打开串口")
            return False
        if self.send_hex.isChecked():
            data, err = utils.parse_hex(text)
            if data is None:
                if show_warn:
                    QMessageBox.warning(self, "格式错误",
                                        f"HEX 格式错误，请用空格分隔，如: 01 A2 FF\n{err}")
                return False
        else:
            data = text.encode(self.encoding, errors="ignore")
        if self.ck_crc.isChecked() and data:
            data += utils.crc16_modbus(data).to_bytes(2, "little")
        if self.ck_newline.isChecked():
            data += b"\r\n"
        if not data:
            return False
        self.worker.write(data)
        self.tx_count += len(data)
        self.add_history(text)
        self.update_status()
        if self.ck_echo.isChecked():
            extra = ""
            if self.ck_newline.isChecked(): extra += " [\\r\\n]"
            if self.ck_crc.isChecked(): extra += " [CRC16]"
            self.append_recv_text(f">>> TX{extra}: {text}", config.TX_COLOR)
        return True

    def send_preset(self):
        self.do_send_text(self.cb_preset.currentText(), True)

    def add_preset(self):
        text = self.send_box.toPlainText().strip()
        if text and self.cb_preset.findText(text) < 0:
            self.cb_preset.addItem(text)
            self.cb_preset.setCurrentIndex(self.cb_preset.count() - 1)

    def add_history(self, text):
        if not text:
            return
        self._updating_history = True
        idx = self.cb_history.findText(text)
        if idx >= 0:
            self.cb_history.removeItem(idx)
        self.cb_history.insertItem(0, text)
        while self.cb_history.count() > config.HISTORY_MAX:
            self.cb_history.removeItem(self.cb_history.count() - 1)
        self.cb_history.setCurrentIndex(-1)
        self._updating_history = False

    def on_history_changed(self):
        if self._updating_history:
            return
        t = self.cb_history.currentText()
        if t:
            self.send_box.setPlainText(t)

    def send_file(self):
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口"); return
        if self._sending_file:
            self.show_tip("正在发送文件，请等待完成", 3000)
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件", "", "所有文件 (*)")
        if not path:
            return
        try:
            f = open(path, "rb")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"读取文件失败: {e}")
            return
        self._file_send = f
        self._file_path = path
        self._file_total = 0
        self._sending_file = True
        self.btn_sendfile.setEnabled(False)
        self.append_recv_text(f">>> 开始发送文件: {os.path.basename(path)}", config.TX_COLOR)
        # 异步泵送：send_file 立即返回，由定时器按队列水位持续补水
        self._file_timer.start()
        self._pump_file_send()

    def _pump_file_send(self):
        """按写队列水位分块喂数据（背压），不阻塞 UI、不整文件驻留内存。"""
        if not self._sending_file:
            self._file_timer.stop()
            return
        if self._closing or not self._connected:
            self._finish_file_send(interrupted=True)
            return
        f = self._file_send
        if f is None:
            self._finish_file_send(interrupted=False)
            return
        try:
            while self.worker.pending_writes() < config.SEND_QUEUE_LIMIT:
                chunk = f.read(config.SEND_CHUNK_SIZE)
                if not chunk:
                    self._finish_file_send(interrupted=False)
                    return
                self.worker.write(chunk)
                self._file_total += len(chunk)
        except Exception as e:
            print(f"【警告】文件发送失败: {e}")
            self._finish_file_send(interrupted=True)

    def _finish_file_send(self, interrupted=False):
        self._file_timer.stop()
        if self._file_send is not None:
            try:
                self._file_send.close()
            except Exception:
                pass
        self._file_send = None
        self._sending_file = False
        self.btn_sendfile.setEnabled(True)
        total = self._file_total
        name = os.path.basename(self._file_path) if self._file_path else ""
        if total:
            self.tx_count += total
            self.update_status()
        if interrupted:
            self.append_recv_text(
                f">>> 文件发送中断: {name}（已入队 {total} 字节）", config.ERROR_COLOR)
        elif total:
            self.append_recv_text(f">>> 文件已发送: {name} ({total} 字节)", config.TX_COLOR)

    # =========================================================
    #  多条发送列表
    # =========================================================
    def ms_add(self):
        text = self.send_box.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先在发送区输入指令内容")
            return
        row = self.ms_table.rowCount()
        self.ms_table.insertRow(row)
        used = QTableWidgetItem()
        used.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        used.setCheckState(Qt.CheckState.Checked)
        self.ms_table.setItem(row, 0, used)
        self.ms_table.setItem(row, 1, QTableWidgetItem(text))
        self.ms_table.setItem(row, 2, QTableWidgetItem("100"))
        self.ms_table.selectRow(row)

    def ms_del(self):
        rows = sorted({i.row() for i in self.ms_table.selectedItems()}, reverse=True)
        for r in rows:
            self.ms_table.removeRow(r)

    def ms_clear(self):
        self._ms_stop_loop()
        self.ms_table.setRowCount(0)

    def _ms_checked_rows(self):
        rows = []
        for row in range(self.ms_table.rowCount()):
            it = self.ms_table.item(row, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                text = self.ms_table.item(row, 1).text()
                delay = 100
                try:
                    delay = int(self.ms_table.item(row, 2).text())
                except (ValueError, AttributeError):
                    pass
                rows.append((text, max(delay, 0)))
        return rows

    def ms_send_selected(self):
        for text, _ in self._ms_checked_rows():
            self.do_send_text(text, False)

    def ms_loop(self):
        if self._ms_looping:
            self._ms_stop_loop()
            return
        rows = self._ms_checked_rows()
        if not rows:
            QMessageBox.information(self, "提示", "请先勾选要循环发送的指令")
            self.btn_ms_loop.setChecked(False)
            return
        self._ms_looping = True
        self._ms_loop_idx = 0
        self.btn_ms_loop.setText("停止循环")
        self._ms_loop_tick()

    def _ms_loop_tick(self):
        """循环发送一步：用成员定时器链，_ms_stop_loop() 能确定性取消。"""
        if not self._ms_looping or not self._connected:
            self._ms_stop_loop()
            return
        rows = self._ms_checked_rows()
        if not rows:
            self._ms_stop_loop()
            return
        idx = self._ms_loop_idx % len(rows)
        self._ms_loop_idx = idx + 1
        text, delay = rows[idx]
        self.do_send_text(text, False)
        self._ms_loop_timer.start(max(delay, 1))

    def _ms_stop_loop(self):
        self._ms_looping = False
        self._ms_loop_timer.stop()
        self.btn_ms_loop.setChecked(False)
        self.btn_ms_loop.setText("循环发送")

    # =========================================================
    #  复位 / 定时 / 速率 / 信号 / 日志
    # =========================================================
    def reset_target(self):
        """ESP32 复位：RTS 断言（=低电平，多数板 EN 经三极管接 RTS）保持 150ms 后释放。
        若你的板子 EN 接的是 DTR，把下面所有 rts 换成 dtr。
        分步由 QTimer 驱动，UI 不阻塞。"""
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口"); return
        self._reset_timer.stop()
        self._reset_step = 0
        self.append_recv_text("= 复位脉冲：RTS 低电平 150ms =", config.TX_COLOR)
        # 先释放：RTS 已勾选（一直是低电平）时也能产生一次真实跳变
        self.worker.set_rts(False)
        self._reset_timer.start(50)

    def _on_reset_tick(self):
        self._reset_step += 1
        if self._reset_step == 1:
            self.worker.set_rts(True)       # 拉低 → 进入复位
            self.append_recv_text("= RTS 断言（低）→ 复位中 =", config.TX_COLOR)
            self._reset_timer.start(150)
        elif self._reset_step == 2:
            self.worker.set_rts(False)      # 释放 → 芯片启动
            self.append_recv_text("= RTS 释放（高）→ 启动 =", config.TX_COLOR)
            self._reset_timer.stop()
            if self.ck_rts.isChecked():
                # 硬件已释放，勾选状态同步取消，避免"按钮显示断言、实际已释放"
                self.ck_rts.setChecked(False)

    def toggle_timer_send(self):
        if self.send_timer.isActive():
            self.send_timer.stop(); self.btn_timer.setChecked(False); return
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口"); return
        try:
            t = int(self.edit_period.text())
        except ValueError:
            QMessageBox.warning(self, "参数错误", "请输入合法数字"); return
        if t <= 0:
            QMessageBox.warning(self, "参数错误", "周期必须大于 0"); return
        self.send_timer.start(t)
        self.btn_timer.setChecked(True)

    def update_rate(self):
        now = time.time()
        dt = now - self._rate_t0
        if dt > 0:
            self.rx_rate = (self.rx_count - self._rate_rx) / dt
            self.tx_rate = (self.tx_count - self._rate_tx) / dt
        self._rate_t0 = now; self._rate_rx = self.rx_count; self._rate_tx = self.tx_count
        self.update_status()
        self.update_conn_label()
        self.update_recv_stat()
        self._check_log_rollover()
        self._flush_log()
        if self._connected:
            self.rate_chart.add_sample(self.rx_rate, self.tx_rate)

    def update_status(self):
        self.lab_status.setText(self._fmt_status(self.rx_count, self.tx_count,
                                                 self.rx_rate, self.tx_rate))

    @staticmethod
    def _fmt_status(rx, tx, rx_rate, tx_rate):
        """统计文字按固定宽度对齐，避免每秒刷新时标签宽度伸缩产生残影。"""
        return (f"RX:{rx:>8}  TX:{tx:>8}"
                f"  收:{rx_rate:>6.0f} B/s 发:{tx_rate:>6.0f} B/s")

    def show_tip(self, text, msec=5000):
        """状态栏临时提示：独立标签显示，不覆盖连接状态与统计。"""
        self.lab_tip.setText(text)
        self._tip_timer.start(msec)

    def update_recv_stat(self):
        self.lab_recv_stat.setText(
            f"收 {self._fmt_bytes(self.rx_count)}  发 {self._fmt_bytes(self.tx_count)}"
            f"  |  行 {self.recv_box.blockCount()}")

    @staticmethod
    def _fmt_bytes(n):
        if n >= 1024 * 1024:
            return f"{n / 1048576:.1f}MB"
        if n >= 1024:
            return f"{n / 1024:.1f}KB"
        return f"{n}B"

    def update_conn_label(self):
        if self._connected and self._connect_t0:
            secs = max(int(time.time() - self._connect_t0), 0)
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            self.lab_conn.setText(f"已连接 {h:02d}:{m:02d}:{s:02d}")
            self.lab_conn2.setText("已连接")
            state = "on"
        else:
            self.lab_conn.setText("未连接")
            self.lab_conn2.setText("未连接")
            state = "off"
        # 仅在状态切换时重刷 QSS，避免每秒 unpolish/polish 两个圆点
        if state != self._dot_state:
            self._dot_state = state
            for d in (self.dot, self.dot2):
                d.setObjectName("dotGreen" if state == "on" else "dot")
                st = d.style(); st.unpolish(d); st.polish(d)

    def clear_count(self):
        self.rx_count = 0; self.tx_count = 0
        self.rx_rate = 0; self.tx_rate = 0
        self.rate_chart.clear()
        self.update_status()
        self.update_recv_stat()

    def clear_recv(self):
        """清空接收区（连同波形与原始缓冲）。"""
        self.recv_box.clear()
        self.wave_chart.clear()
        self._rx_raw.clear()
        self._reset_decoder()

    def on_wave_toggled(self, checked):
        self.wave_chart.setVisible(checked)
        if checked:
            self.wave_chart.update()

    def on_auto_wrap(self, checked):
        self.recv_box.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth
                                      if checked else QPlainTextEdit.LineWrapMode.NoWrap)

    def on_dtr_toggled(self, checked):
        self.worker.set_dtr(checked)

    def on_rts_toggled(self, checked):
        self.worker.set_rts(checked)

    # ---------- 接收区外观 ----------
    def apply_recv_theme(self):
        self.recv_box.setStyleSheet(config.RECV_THEME_STYLE[self._recv_theme])
        f = QFont(config.RECV_FONT_FAMILY, self._font_size)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.recv_box.setFont(f)

    def set_recv_theme(self, theme):
        self._recv_theme = theme
        self.apply_recv_theme()

    def change_font_size(self, delta):
        self._font_size = max(config.MIN_FONT_SIZE,
                              min(config.MAX_FONT_SIZE, self._font_size + delta))
        self.apply_recv_theme()

    def _recv_context_menu(self, pos):
        menu = QMenu(self)
        a_clear = menu.addAction("清空显示")
        a_copy = menu.addAction("复制")
        a_copy.setEnabled(self.recv_box.textCursor().hasSelection())
        a_save = menu.addAction("保存接收日志...")
        a_export = menu.addAction("导出接收数据(HEX)...")
        menu.addSeparator()
        a_hex = menu.addAction("HEX 显示"); a_hex.setCheckable(True)
        a_hex.setChecked(self.ck_hex.isChecked())
        a_time = menu.addAction("时间戳"); a_time.setCheckable(True)
        a_time.setChecked(self.ck_time.isChecked())
        a_wrap = menu.addAction("自动换行"); a_wrap.setCheckable(True)
        a_wrap.setChecked(self.ck_wrap.isChecked())
        menu.addSeparator()
        tm = menu.addMenu("接收配色")
        a_blue = tm.addAction("蓝底白字"); a_blue.setCheckable(True)
        a_blue.setChecked(self._recv_theme == config.RECV_THEME_BLUE)
        a_green = tm.addAction("黑底绿字"); a_green.setCheckable(True)
        a_green.setChecked(self._recv_theme == config.RECV_THEME_GREEN)
        menu.addSeparator()
        a_font_up = menu.addAction("字体放大")
        a_font_down = menu.addAction("字体缩小")
        a_font_reset = menu.addAction("恢复默认字号")
        act = menu.exec(self.recv_box.mapToGlobal(pos))
        if act is None:
            return
        if act == a_clear:
            self.clear_recv()
        elif act == a_copy:
            self.recv_box.copy()
        elif act == a_save:
            self.save_log()
        elif act == a_export:
            self.export_log_hex()
        elif act == a_hex:
            self.ck_hex.setChecked(a_hex.isChecked())
        elif act == a_time:
            self.ck_time.setChecked(a_time.isChecked())
        elif act == a_wrap:
            self.ck_wrap.setChecked(a_wrap.isChecked())
        elif act == a_blue:
            self.set_recv_theme(config.RECV_THEME_BLUE)
        elif act == a_green:
            self.set_recv_theme(config.RECV_THEME_GREEN)
        elif act == a_font_up:
            self.change_font_size(1)
        elif act == a_font_down:
            self.change_font_size(-1)
        elif act == a_font_reset:
            self._font_size = config.DEFAULT_FONT_SIZE
            self.apply_recv_theme()

    # ---------- 日志 ----------
    def _flush_log(self):
        if self.log_file and self._log_buf:
            try:
                self.log_file.write("\n".join(self._log_buf) + "\n")
                self.log_file.flush()
            except Exception as e:
                print(f"【警告】日志写入失败: {e}")
            self._log_buf.clear()

    def on_log_toggled(self, checked):
        if checked:
            try:
                name = self._open_log()
                self.append_recv_text(f">>> 开始记录: {os.path.basename(name)}", config.TX_COLOR)
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法创建日志文件: {e}")
                self.ck_log.blockSignals(True); self.ck_log.setChecked(False); self.ck_log.blockSignals(False)
                self.log_file = None
        else:
            if self.log_file:
                self._flush_log()
                self.log_file.close(); self.log_file = None
                self.append_recv_text(">>> 停止记录", config.TX_COLOR)

    def _log_dir(self):
        d = os.path.join(config.app_dir(), "logs")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _open_log(self):
        """按日期命名：log/serial_YYYYMMDD.log（跨天自动滚动新文件）。"""
        name = os.path.join(self._log_dir(),
                            "serial_" + datetime.date.today().strftime("%Y%m%d") + ".log")
        self.log_file = open(name, "a", encoding="utf-8")
        self._log_date = datetime.date.today()
        return name

    def _check_log_rollover(self):
        if self.log_file and datetime.date.today() != self._log_date:
            self._flush_log()
            try:
                self.log_file.close()
            except Exception:
                pass
            name = self._open_log()
            self.append_recv_text(f">>> 跨天，日志已切换到: {os.path.basename(name)}", config.TX_COLOR)

    def save_log(self):
        self._flush_log()
        path, _ = QFileDialog.getSaveFileName(self, "保存接收日志", "recv_log.txt", "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.recv_box.toPlainText())
            QMessageBox.information(self, "完成", f"已保存到:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败: {e}")

    def export_log_hex(self):
        """导出接收到的原始字节为 HEX 文本（每行 16 字节）。"""
        if not self._rx_raw:
            QMessageBox.information(self, "提示", "当前没有已接收的原始数据")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出接收数据(HEX)", "recv_log_hex.txt", "文本文件 (*.txt)")
        if not path:
            return
        try:
            raw = self._rx_raw
            with open(path, "w", encoding="ascii") as f:
                for i in range(0, len(raw), 16):
                    f.write(" ".join(f"{b:02X}" for b in raw[i:i + 16]) + "\n")
            QMessageBox.information(self, "完成",
                                    f"已导出 {len(raw)} 字节到:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"导出失败: {e}")

    def show_about(self):
        QMessageBox.about(self, "关于", f"{config.APP_NAME}\n版本 {config.APP_VERSION}\n\n"
                          "串口调试助手：HEX/ASCII 收发、时间戳、定时发送、发送文件、\n"
                          "CRC16、DTR/RTS、ESP32 复位、预设命令、多条发送列表、\n"
                          "多编码、日志记录、设置记忆、速率显示、接收区查找\n\n"
                          "快捷键：Ctrl+Enter 发送 · Ctrl+F 查找 · Ctrl+L 清空 · F5 刷新端口\n"
                          "接收区 Ctrl+滚轮 调整字号")

    # =========================================================
    #  查找
    # =========================================================
    def show_find_bar(self):
        self.find_bar.setVisible(True)
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def hide_find_bar(self):
        self.find_bar.setVisible(False)
        self.recv_box.setFocus()

    def find_next(self):
        text = self.find_edit.text()
        if not text:
            return
        if not self.recv_box.find(text):
            # 未命中：回到顶部再找一次（循环查找）
            cursor = self.recv_box.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.recv_box.setTextCursor(cursor)
            self.recv_box.find(text)

    # =========================================================
    #  设置持久化
    # =========================================================
    def load_settings(self):
        geo = self.qs.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        p = self.qs.value("port", "")
        if p:
            self.cb_com.setCurrentText(p)
        self.cb_baud.setCurrentText(self.qs.value("baud", "115200"))
        self.cb_data.setCurrentText(self.qs.value("data", "8"))
        self.cb_stop.setCurrentText(self.qs.value("stop", "1"))
        self.cb_check.setCurrentText(self.qs.value("parity", "None"))
        self.cb_encoding.setCurrentText(self.qs.value("encoding", "UTF-8"))
        self.cb_break.setCurrentIndex(int(self.qs.value("break_mode", 0)))
        if self.qs.value("send_mode", "ascii") == "hex":
            self.send_hex.setChecked(True)
        else:
            self.send_ascii.setChecked(True)
        self.edit_period.setText(self.qs.value("period", "100"))
        # 注意：暂停状态与"记录到文件"不做持久化恢复。
        # 否则上次在暂停时退出，下次启动界面正常但接收不刷新；或静默写入日志文件。
        for name, w in [("ck_hex", self.ck_hex), ("ck_time", self.ck_time),
                        ("ck_clear", self.ck_clear), ("ck_wrap", self.ck_wrap),
                        ("ck_newline", self.ck_newline), ("ck_echo", self.ck_echo),
                        ("ck_crc", self.ck_crc)]:
            w.setChecked(self.qs.value(name, False, type=bool))
        presets = self.qs.value("presets", [])
        if not isinstance(presets, list):
            presets = [presets]
        for it in presets:
            if it and self.cb_preset.findText(it) < 0:
                self.cb_preset.addItem(it)

        # 接收区外观
        self._font_size = int(self.qs.value("font_size", config.DEFAULT_FONT_SIZE))
        self._recv_theme = self.qs.value("recv_theme", config.RECV_THEME_BLUE)
        if self._recv_theme not in config.RECV_THEME_STYLE:
            self._recv_theme = config.RECV_THEME_BLUE

        # 多条发送列表
        used = self.qs.value("multisend_used", [])
        texts = self.qs.value("multisend_text", [])
        delays = self.qs.value("multisend_delay", [])
        if not isinstance(used, list): used = [used]
        if not isinstance(texts, list): texts = [texts]
        if not isinstance(delays, list): delays = [delays]
        self.btn_ms_fold.setChecked(self.qs.value("ms_fold", True, type=bool))
        for i, t in enumerate(texts):
            row = self.ms_table.rowCount()
            self.ms_table.insertRow(row)
            u = QTableWidgetItem()
            u.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            u.setCheckState(Qt.CheckState.Checked if (i < len(used) and used[i])
                            else Qt.CheckState.Unchecked)
            self.ms_table.setItem(row, 0, u)
            self.ms_table.setItem(row, 1, QTableWidgetItem(t))
            d = str(delays[i]) if i < len(delays) else "100"
            self.ms_table.setItem(row, 2, QTableWidgetItem(d))

        # 分割器状态
        s = self.qs.value("main_split")
        if s:
            self.main_split.restoreState(s)
        s = self.qs.value("v_split")
        if s:
            self.v_split.restoreState(s)

    def save_settings(self):
        self.qs.setValue("geometry", self.saveGeometry())
        self.qs.setValue("port", self.cb_com.currentText())
        self.qs.setValue("baud", self.cb_baud.currentText())
        self.qs.setValue("data", self.cb_data.currentText())
        self.qs.setValue("stop", self.cb_stop.currentText())
        self.qs.setValue("parity", self.cb_check.currentText())
        self.qs.setValue("encoding", self.cb_encoding.currentText())
        self.qs.setValue("break_mode", self.cb_break.currentIndex())
        self.qs.setValue("send_mode", "hex" if self.send_hex.isChecked() else "ascii")
        self.qs.setValue("period", self.edit_period.text())
        for name, w in [("ck_hex", self.ck_hex), ("ck_time", self.ck_time),
                        ("ck_clear", self.ck_clear), ("ck_wrap", self.ck_wrap),
                        ("ck_newline", self.ck_newline), ("ck_echo", self.ck_echo),
                        ("ck_crc", self.ck_crc)]:
            self.qs.setValue(name, w.isChecked())
        # 暂停 / 记录到文件属于"单次会话"状态，不写入配置（避免下次启动意外暂停或自动建日志）
        self.qs.remove("ck_pause")
        self.qs.remove("ck_log")
        self.qs.setValue("presets",
                         [self.cb_preset.itemText(i) for i in range(self.cb_preset.count())])
        # 接收区外观
        self.qs.setValue("font_size", self._font_size)
        self.qs.setValue("recv_theme", self._recv_theme)
        # 多条发送列表
        used, texts, delays = [], [], []
        for row in range(self.ms_table.rowCount()):
            it = self.ms_table.item(row, 0)
            used.append(bool(it and it.checkState() == Qt.CheckState.Checked))
            texts.append(self.ms_table.item(row, 1).text())
            delays.append(self.ms_table.item(row, 2).text())
        self.qs.setValue("multisend_used", used)
        self.qs.setValue("multisend_text", texts)
        self.qs.setValue("multisend_delay", delays)
        self.qs.setValue("ms_fold", self.btn_ms_fold.isChecked())
        # 分割器状态
        self.qs.setValue("main_split", self.main_split.saveState())
        self.qs.setValue("v_split", self.v_split.saveState())

    def closeEvent(self, e):
        self._closing = True
        self.rate_timer.stop()
        self.port_timer.stop()
        self.close_serial()
        self._flush_log()
        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass
        self.save_settings()
        e.accept()
