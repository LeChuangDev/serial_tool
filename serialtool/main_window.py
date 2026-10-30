# ====================== 主程序窗口 ======================
"""主窗口：只做 UI 编排与状态机，业务逻辑下沉到独立模块。

分工：
- ``ports``            串口枚举、设备身份（VID:PID/SN）匹配、后台扫描线程
- ``reconnect``        断线重连时序（冷却 / 指数退避 / 次数上限）
- ``recv_pipeline``    接收字节 → 可显示文本（解码/断行/HEX）
- ``timestamps``       三种时间戳格式（时钟 / 相对 / 增量）
- ``highlight``        关键字着色规则（字面量或正则）
- ``serial_worker``    串口读写、命令队列、信号线
- ``settings_store``   配置持久化（类型收敛）
- ``panels`` / ``widgets``  布局与自绘控件
- 本模块               信号绑定、用户操作编排、状态迁移
"""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import os
import time
from dataclasses import fields

import serial
from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import config, panels, styles, utils
from .highlight import Highlighter, parse_rules, serialize_rules
from .ports import PortInfo, PortScanner, enumerate_ports, pick_port
from .reconnect import ReconnectPolicy, ReconnectScheduler
from .recv_pipeline import SPLIT_BY_PACKET, SPLIT_MODE_MAP, RecvFormatter
from .serial_worker import SerialWorker
from .settings_store import SettingsStore, init_default_store
from .timestamps import TimestampFormatter

log = logging.getLogger(__name__)

# 面板里这些字段是容器而非交互控件，不挂到主窗口属性上
_PANEL_SKIP_FIELDS = frozenset({"frame", "root", "body_layout"})

# 高亮规则条数上限（防御被手改坏的配置文件）
_MAX_HIGHLIGHT_RULES = 50

# 窗口缩放下限：低于此值连接栏两行会挤不下（参数下拉框被迫收窄 → 省略号截断）
_MIN_WIDTH = 1120
_MIN_HEIGHT = 680


class LecoSerial(QMainWindow):
    """串口调试终端主窗口。"""

    # 供线程内异常钩子投递回主线程（Qt 信号跨线程自动排队）
    fatal_error = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(config.APP_NAME)
        self.resize(config.WINDOW_WIDTH, config.WINDOW_HEIGHT)

        icon_path = config.resource_path(config.APP_ICON_NAME)
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # ---------- 计数器 / 连接状态 ----------
        self.rx_count = 0
        self.tx_count = 0
        self.rx_frames = 0
        self.rx_rate = 0.0
        self.tx_rate = 0.0
        self.peak_rx = 0.0
        self._rate_t0 = time.monotonic()
        self._rate_rx = 0
        self._rate_tx = 0
        self.current_port = ""
        self.current_baud = 0
        self.current_bits = 8
        self.current_stop = 1
        self.current_parity = serial.PARITY_NONE
        self.current_flow: tuple[bool, bool, bool] = (False, False, False)
        self._connected = False
        self._closing = False
        self._user_close = False  # 主动断开标记：用于区分"掉线"与"用户点断开"
        self._connect_t0 = 0.0
        self._session_seconds = 0.0
        self._reconnect_count = 0

        # ---------- 接收管线 ----------
        self._formatter = RecvFormatter()
        self._rx_raw = bytearray()  # 原始字节缓冲（HEX 导出用，超限丢最旧）
        self._paused_dropped = 0  # 暂停期间未显示的字节数
        self._font_size = config.DEFAULT_FONT_SIZE
        self._recv_theme = config.RECV_THEME_BLUE
        self._ts = TimestampFormatter()
        self._highlighter = Highlighter()

        # ---------- 日志文件 ----------
        self.log_file = None
        self._log_buf: list[str] = []
        self._log_date: datetime.date | None = None

        # ---------- 发送历史 / 多条发送 / 文件发送 ----------
        self._updating_history = False
        self._history: list[str] = []
        self._history_index = -1
        self._ms_looping = False
        self._ms_loop_idx = 0
        self._sending_file = False
        self._file_send = None
        self._file_path = ""
        self._file_total = 0
        self._file_size = 0

        # ---------- 端口列表 / 自动重连 ----------
        self._port_set: set[str] = set()
        self._port_map: dict[str, PortInfo] = {}
        self._port_missing = False
        self._preferred_port = ""
        self._port_info: PortInfo | None = None
        self._reconnector = ReconnectScheduler(ReconnectPolicy())
        self._reconnect_pending = False  # 已发起打开、正在等 connected/open_failed
        self._reconnect_reason = ""

        self._dot_state: str | None = None
        self._loading = False  # 加载配置期间抑制"已启用…"这类交互提示
        self.store: SettingsStore = init_default_store()

        # ---------- 定时器 ----------
        self.send_timer = QTimer(self)
        self.rate_timer = QTimer(self)
        self.port_timer = QTimer(self)
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.setInterval(250)
        self.reconnect_timer.timeout.connect(self._reconnect_tick)
        self._reset_timer = QTimer(self)  # 复位脉冲分步（不阻塞 UI）
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self._on_reset_tick)
        self._reset_step = 0
        # 多条发送循环：成员定时器驱动，停止时能真正取消
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
        self.worker.open_failed.connect(self.on_open_failed)
        self.worker.link_lost.connect(self.on_link_lost)
        self.worker.lines_state.connect(self.on_lines_state)
        self.port_scanner = PortScanner(self)
        self.port_scanner.ports_scanned.connect(self._on_ports_scanned)
        self.port_scanner.scan_failed.connect(self._on_scan_failed)

        # ---------- 样式与布局 ----------
        self.setStyleSheet(styles.GLOBAL_QSS + styles.CARD_QSS)
        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        self._build_shortcuts()
        self._bind_signals()

        self.refresh_port_list()
        self.load_settings()
        self._apply_recv_style()

        self.rate_timer.start(1000)
        self.port_timer.start(5000)  # 5s 扫一次：Windows 枚举有开销，够用
        self.on_auto_wrap(self.ck_wrap.isChecked())
        self.update_status()
        self.update_stats()
        self.update_recv_stat()
        self.update_conn_label()

    # =========================================================
    #  界面构建
    # =========================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # 顶部连接栏：参数与连接动作放在一起，符合串口工具的肌肉记忆
        self.conn_bar = panels.ConnectionBar.build()
        self._bind_panel(self.conn_bar)
        root.addWidget(self.conn_bar.frame)

        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self.main_split, 1)

        # 左侧窄栏：会话 / 显示 / 统计（只读或低频操作）
        self.session_card = panels.SessionCard.build()
        self.display_card = panels.DisplayCard.build()
        self.stats_card = panels.StatsCard.build()
        for card in (self.session_card, self.display_card, self.stats_card):
            self._bind_panel(card)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(10)
        left_layout.addWidget(self.session_card.frame, 4)
        left_layout.addWidget(self.display_card.frame, 6)
        left_layout.addWidget(self.stats_card.frame, 4)
        left.setMinimumWidth(320)
        left.setMaximumWidth(400)
        self.main_split.addWidget(left)

        # 右侧：接收区占主视野，发送区用页签收纳三类发送方式
        self.recv_panel = panels.RecvPanel.build()
        self.send_panel = panels.SendPanel.build()
        for panel in (self.recv_panel, self.send_panel):
            self._bind_panel(panel)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        self.v_split = QSplitter(Qt.Orientation.Vertical)
        right_layout.addWidget(self.v_split)
        self.v_split.addWidget(self.recv_panel.frame)
        self.v_split.addWidget(self.send_panel.frame)

        self.main_split.addWidget(right)
        self.main_split.setStretchFactor(0, 0)
        self.main_split.setStretchFactor(1, 1)
        self.main_split.setSizes([300, 880])
        self.v_split.setSizes([430, 330])
        self.v_split.setCollapsible(0, False)
        self.v_split.setCollapsible(1, False)

        # 最小尺寸按"所有字段都能完整显示"反推：先让布局算一遍，再取 Qt 给出的
        # 最小需求与人工下限的较大者。不写死原因：字体（字号/字宽）会影响
        # sizeHint，硬编码会在换环境后重新出现省略号截断。
        root.activate()
        self.setMinimumSize(
            max(_MIN_WIDTH, self.minimumSizeHint().width()),
            max(_MIN_HEIGHT, self.minimumSizeHint().height()),
        )

    def _bind_panel(self, panel: object) -> None:
        """把面板控件挂到主窗口同名属性（Qt setupUi 惯例），编排逻辑直接引用。"""
        for field in fields(panel):
            if field.name not in _PANEL_SKIP_FIELDS:
                setattr(self, field.name, getattr(panel, field.name))

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()
        brand = QLabel("串口调试终端")
        brand.setObjectName("brandTitle")
        menu_bar.setCornerWidget(brand, Qt.Corner.TopLeftCorner)
        version = QLabel("v" + config.APP_VERSION)
        version.setObjectName("brandVer")
        menu_bar.setCornerWidget(version, Qt.Corner.TopRightCorner)

        file_menu = menu_bar.addMenu("文件")
        file_menu.addAction(self._action("发送文件...", self.choose_send_file))
        file_menu.addAction(self._action("保存接收日志...", self.save_log, "Ctrl+S"))
        file_menu.addSeparator()
        file_menu.addAction(self._action("导出接收数据(HEX)...", self.export_log_hex))
        file_menu.addSeparator()
        file_menu.addAction(self._action("导出配置...", self.export_settings))
        file_menu.addAction(self._action("导入配置...", self.import_settings))
        file_menu.addSeparator()
        file_menu.addAction(self._action("退出", self.close))

        serial_menu = menu_bar.addMenu("串口")
        serial_menu.addAction(self._action("刷新端口", self.refresh_port_list, "F5"))
        serial_menu.addAction(self._action("连接 / 断开", self.switch_serial, "Ctrl+D"))
        serial_menu.addSeparator()
        self.act_autoreconnect = QAction("自动重连", self)
        self.act_autoreconnect.setCheckable(True)
        self.act_autoreconnect.triggered.connect(self.btn_autoreconnect.setChecked)
        serial_menu.addAction(self.act_autoreconnect)
        serial_menu.addAction(self._action("立即重连一次", self.manual_reconnect))

        tools_menu = menu_bar.addMenu("工具")
        tools_menu.addAction(self._action("校验和计算", lambda: self.open_tools(0)))
        tools_menu.addAction(self._action("HEX / ASCII 转换", lambda: self.open_tools(1)))
        tools_menu.addSeparator()
        tools_menu.addAction(self._action("关键字高亮规则...", self.edit_highlight_rules))
        tools_menu.addAction(self._action("发送 BREAK（TX 中断）", self.send_break))

        help_menu = menu_bar.addMenu("帮助")
        help_menu.addAction(self._action("快捷键一览", self.show_shortcuts))
        help_menu.addAction(self._action("关于", self.show_about))

    def _action(self, text: str, slot, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_statusbar(self) -> None:
        """状态栏：连接 pill + 端口芯片 + 重连提示 + 临时提示 + 文件进度 + 统计。"""
        status = self.statusBar()
        status.setSizeGripEnabled(False)

        pill = QFrame()
        pill.setObjectName("pill")
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(8, 2, 10, 2)
        pill_layout.setSpacing(6)
        self.dot = QLabel()
        self.dot.setObjectName("dot")
        self.dot.setFixedSize(8, 8)
        self.lab_conn = QLabel("未连接")
        self.lab_conn.setMinimumWidth(150)
        pill_layout.addWidget(self.dot)
        pill_layout.addWidget(self.lab_conn)
        status.addWidget(pill)

        self.lab_port = QLabel("")
        self.lab_port.setObjectName("portChip")
        status.addWidget(self.lab_port)

        self.lab_reconnect = QLabel("")
        self.lab_reconnect.setObjectName("reconnectChip")
        self.lab_reconnect.setVisible(False)
        status.addWidget(self.lab_reconnect)

        self.lab_tip = QLabel("")
        self.lab_tip.setObjectName("hint")
        status.addWidget(self.lab_tip, 1)

        self.lab_file = QLabel("")
        self.lab_file.setObjectName("hint")
        self.lab_file.setVisible(False)
        status.addPermanentWidget(self.lab_file)

        self.lab_status = QLabel(self._fmt_status(0, 0, 0, 0))
        status.addPermanentWidget(self.lab_status)

        self._tip_timer = QTimer(self)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.timeout.connect(lambda: self.lab_tip.setText(""))

    def _build_shortcuts(self) -> None:
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(
                QKeySequence(sequence),
                self,
                activated=lambda: self.do_send_text(self.send_box.toPlainText(), True),
            )
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self.show_find_bar)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.clear_recv)
        QShortcut(QKeySequence("Esc"), self, activated=self.hide_find_bar)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.manual_reconnect)
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.send_box.setFocus())

    def _bind_signals(self) -> None:
        self.btn_link.clicked.connect(self.switch_serial)
        self.btn_refresh.clicked.connect(self.refresh_port_list)
        self.btn_recv_clear.clicked.connect(self.clear_recv)
        self.btn_wave.toggled.connect(self.on_wave_toggled)
        self.btn_find.clicked.connect(self.show_find_bar)
        self.btn_clearcnt.clicked.connect(self.clear_count)
        self.btn_timer.clicked.connect(self.toggle_timer_send)
        self.send_timer.timeout.connect(
            lambda: self.do_send_text(self.send_box.toPlainText(), False)
        )
        self.btn_send.clicked.connect(lambda: self.do_send_text(self.send_box.toPlainText(), True))
        self.btn_sendfile.clicked.connect(self._on_sendfile_clicked)
        self.btn_file_pick.clicked.connect(self.pick_send_file)
        self.btn_rst.clicked.connect(self.reset_target)
        self.btn_preset_send.clicked.connect(self.send_preset)
        self.btn_preset_add.clicked.connect(self.add_preset)
        self.cb_history.currentIndexChanged.connect(self.on_history_changed)
        self.cb_com.currentIndexChanged.connect(self._on_port_selected)
        self.ck_wrap.toggled.connect(self.on_auto_wrap)
        self.ck_dtr.toggled.connect(self.on_dtr_toggled)
        self.ck_rts.toggled.connect(self.on_rts_toggled)
        self.ck_log.toggled.connect(self.on_log_toggled)
        self.ck_highlight.toggled.connect(self.on_highlight_toggled)
        self.btn_autoreconnect.toggled.connect(self.on_autoreconnect_toggled)
        self.btn_rules.clicked.connect(self.edit_highlight_rules)
        self.rate_timer.timeout.connect(self.update_rate)
        self.port_timer.timeout.connect(self.check_ports)
        self.btn_find_next.clicked.connect(self.find_next)
        self.btn_find_close.clicked.connect(self.hide_find_bar)
        self.find_edit.returnPressed.connect(self.find_next)
        self.btn_ms_add.clicked.connect(self.ms_add)
        self.btn_ms_del.clicked.connect(self.ms_del)
        self.btn_ms_clear.clicked.connect(self.ms_clear)
        self.btn_ms_send.clicked.connect(self.ms_send_selected)
        self.btn_ms_loop.clicked.connect(self.ms_loop)
        self.recv_box.font_delta.connect(self.change_font_size)
        self.recv_box.customContextMenuRequested.connect(self._recv_context_menu)
        # 切换编码 / HEX 显示时丢弃解码器残留，避免半个字符串带到下一段
        self.cb_encoding.currentIndexChanged.connect(self._on_encoding_changed)
        self.cb_ts.currentIndexChanged.connect(self._on_ts_mode_changed)
        self.ck_hex.toggled.connect(lambda _: self._formatter.reset())
        self.fatal_error.connect(self._on_fatal_error)
        self.send_box.installEventFilter(self)

    def open_tools(self, tab: int = 0) -> None:
        """打开校验和 / 转换工具对话框（tab: 0 校验和, 1 HEX/ASCII 转换）。"""
        from .tools_dialog import ToolsDialog

        ToolsDialog(self, tab).exec()

    # =========================================================
    #  串口连接 / 断开 / 切换
    # =========================================================
    def restyle_link_btn(self, object_name: str, text: str) -> None:
        self.btn_link.setObjectName(object_name)
        self.btn_link.setText(text)
        style = self.btn_link.style()
        style.unpolish(self.btn_link)
        style.polish(self.btn_link)

    def _current_port(self) -> str:
        """下拉框当前设备名（显示文本里带了描述，真实设备名存在 userData）。"""
        data = self.cb_com.currentData()
        if data:
            return str(data)
        return self.cb_com.currentText().split(" ")[0]

    def _current_params(self) -> tuple[int, int, int, str, tuple[bool, bool, bool]] | None:
        """读取并校验界面上的线路参数；非法时返回 ``None``（已提示用户）。"""
        baud, err = utils.validate_baud(self.cb_baud.currentText())
        if err or baud is None:
            QMessageBox.warning(self, "参数错误", err or "波特率无效")
            return None
        try:
            bits = int(self.cb_data.currentText())
            stop = int(self.cb_stop.currentText())
            parity = config.PARITY_MAP[self.cb_check.currentText()]
            flow = config.FLOW_MAP[self.cb_flow.currentText()]
        except (ValueError, KeyError):
            QMessageBox.warning(self, "参数错误", "请检查数据位 / 停止位 / 校验位 / 流控设置")
            return None
        return baud, bits, stop, parity, flow

    def close_serial(self) -> None:
        self._user_close = True
        self.send_timer.stop()
        self.btn_timer.setChecked(False)
        self._reset_timer.stop()
        self._ms_stop_loop()
        if self._sending_file:
            self._finish_file_send(interrupted=True, reason="关闭串口")
        # 只要线程在跑就停，包含"连接中"的重试阶段：否则此时断开/关窗会留下
        # 仍在运行的 QThread（进程退出时 Qt 致命退出）。
        if self.worker.isRunning():
            self.worker.stop()
        self.worker.clear_cmds()  # 丢弃未发完的写入，避免下次连接时误发
        self._connected = False
        self._session_seconds = max(self._session_seconds, self._elapsed())

    def open_port(
        self, port: str, baud: int, bits: int, stop: int, parity: str, flow: tuple[bool, bool, bool]
    ) -> bool:
        self.current_port = port
        self.current_baud = baud
        self.current_bits = bits
        self.current_stop = stop
        self.current_parity = parity
        self.current_flow = flow
        self._port_info = self._port_map.get(port)
        self._user_close = False  # 新会话开始：之前的"主动断开"标记立即失效
        self._remember_port_profile()
        return self.worker.open(port, baud, bits, stop, parity, flow)

    def switch_serial(self) -> None:
        port = self._current_port()
        params = self._current_params()
        if params is None:
            return
        if not port:
            self.show_tip("未检测到可用串口，请点击「刷新」或检查驱动", 5000)
            return

        # 手动操作优先：正在等待重连时，点击连接直接接管
        self._stop_reconnect("", silent=True)

        baud, bits, stop, parity, flow = params
        if self._connected:
            changed = (
                port != self.current_port
                or baud != self.current_baud
                or bits != self.current_bits
                or stop != self.current_stop
                or parity != self.current_parity
                or flow != self.current_flow
            )
            # 参数与当前一致时，按钮语义是"断开"
            if not changed:
                self.close_serial()
                return
            self.close_serial()
        self.open_port(port, baud, bits, stop, parity, flow)

    def manual_reconnect(self) -> None:
        """Ctrl+R / 菜单：立即重连一次（不改动自动重连开关）。"""
        if self._connected:
            self.show_tip("当前已连接", 2000)
            return
        port = self._current_port() or self.current_port
        if not port:
            self.show_tip("没有可用端口", 3000)
            return
        params = self._current_params()
        if params is None:
            return
        self._stop_reconnect("", silent=True)
        self.open_port(port, *params)

    # =========================================================
    #  自动重连
    # =========================================================
    def on_autoreconnect_toggled(self, checked: bool) -> None:
        self.act_autoreconnect.setChecked(checked)
        if checked:
            if not self._loading:
                self.show_tip("已启用自动重连：掉线后按 VID/PID/SN 找回设备并重连", 5000)
            return
        self._stop_reconnect("已关闭自动重连", silent=not self._loading)

    def _start_reconnect(self, reason: str) -> None:
        """链路中断后进入重连流程（仅当开关打开且不是用户主动断开）。"""
        if self._closing or not self.btn_autoreconnect.isChecked():
            return
        if not self.current_port:
            return
        self._reconnect_reason = reason
        self._reconnector.start()
        self._reconnect_pending = False
        self.reconnect_timer.start()
        self._update_reconnect_label()
        total = "∞" if self._reconnector.policy.unbounded else self._reconnector.policy.max_attempts
        self.append_recv_text(
            f"= 自动重连已启动（最多 {total} 次，按设备身份找回端口）：{reason} =",
            config.WARN_COLOR,
        )
        log.warning("启动自动重连: %s | %s", self.current_port, reason)

    def _reconnect_tick(self) -> None:
        if self._closing:
            self.reconnect_timer.stop()
            return
        self._update_reconnect_label()
        if not self._reconnector.active:
            self.reconnect_timer.stop()
            return
        # 线程还在收尾时不发起新连接（否则会撞上"正在连接/断开中"）
        if self._reconnect_pending or self.worker.isRunning():
            return
        if self._reconnector.due():
            self.port_scanner.scan()

    def _try_reconnect(self, ports: list[PortInfo]) -> None:
        """在扫描结果里找回目标设备并尝试打开。"""
        if self._reconnect_pending or self.worker.isRunning():
            return
        info = pick_port(ports, self._port_info.identity if self._port_info else None)
        if info is None:
            attempt = self._reconnector.note_attempt()
            if not self._reconnector.active:
                self._reconnect_give_up(attempt)
                return
            wait = self._reconnector.seconds_until_due()
            self.append_recv_text(
                f"= 重连第 {attempt} 次：未发现目标设备（在线 {len(ports)} 个串口），"
                f"{wait:.1f}s 后重试 =",
                config.WARN_COLOR,
            )
            self._update_reconnect_label()
            return

        if info.device != self.current_port:
            self.append_recv_text(
                f"= 设备端口变化：{self.current_port} → {info.device}，按序列号/VID:PID 识别 =",
                config.INFO_COLOR,
            )
        self._port_info = info
        started = self.open_port(
            info.device,
            self.current_baud or 115200,
            self.current_bits,
            self.current_stop,
            self.current_parity,
            self.current_flow,
        )
        # 线程偶尔还在收尾：不把它算作一次失败，下一个 tick 再试
        self._reconnect_pending = started

    def _on_reconnect_failed(self, message: str) -> None:
        """一次重连尝试失败：记一次退避，次数用尽则收尾。"""
        self._reconnect_pending = False
        attempt = self._reconnector.note_attempt()
        if not self._reconnector.active:
            self._reconnect_give_up(attempt)
            return
        wait = self._reconnector.seconds_until_due()
        log.info("重连第 %d 次失败，%.1fs 后重试: %s", attempt, wait, message)
        self.append_recv_text(
            f"= 重连第 {attempt} 次失败（{message.splitlines()[0]}），{wait:.1f}s 后重试 =",
            config.WARN_COLOR,
        )
        self._update_reconnect_label()

    def _reconnect_give_up(self, attempt: int) -> None:
        self.reconnect_timer.stop()
        self._reconnect_pending = False
        self._update_reconnect_label()
        self.append_recv_text(
            f"= 自动重连已停止：已尝试 {attempt} 次仍未成功，请检查设备供电 / 驱动 / 端口占用 =",
            config.ERROR_COLOR,
        )
        self.show_tip("自动重连失败：已达最大尝试次数", 8000)
        log.error("自动重连放弃（%d 次）: %s", attempt, self.current_port)

    def _stop_reconnect(self, reason: str, *, silent: bool = False) -> None:
        was_active = self._reconnector.active
        if was_active or self._reconnect_pending:
            self._reconnector.stop()
            self._reconnect_pending = False
            if not silent and reason:
                self.append_recv_text(f"= {reason} =", config.INFO_COLOR)
        self.reconnect_timer.stop()
        self._update_reconnect_label()

    def _update_reconnect_label(self) -> None:
        text = self._reconnector.describe()
        self.lab_reconnect.setText(text)
        self.lab_reconnect.setVisible(bool(text))

    def on_link_lost(self, message: str) -> None:
        """设备拔出 / USB 链路失效（worker 判定，非用户操作）。"""
        if self._closing:
            return
        log.warning("链路中断: %s | %s", self.current_port, message)
        self.append_recv_text(f"!!! 链路中断：{message}", config.ERROR_COLOR)
        self.show_tip(
            "设备链路中断，正在尝试恢复…" if self._auto_reconnect_on() else "设备链路中断", 6000
        )

    def _auto_reconnect_on(self) -> bool:
        return self.btn_autoreconnect.isChecked()

    # ---------- 端口热插拔 / 枚举 ----------
    def check_ports(self) -> None:
        if self._closing:
            return
        self.port_scanner.scan()

    def _on_ports_scanned(self, ports: list[PortInfo]) -> None:
        if self._closing:
            return
        ports = self._normalize_ports(ports)
        devices = [info.device for info in ports]
        if set(devices) != self._port_set:
            self._apply_port_list(ports)  # 热插拔：列表自动同步（保留选择）
        else:
            self._port_map = {info.device: info for info in ports}

        if self._connected and self.current_port and self.current_port not in set(devices):
            if not self._port_missing:
                self._port_missing = True
                self.show_tip("提示：串口已拔出", 5000)
                log.warning("检测到串口拔出: %s", self.current_port)
        else:
            self._port_missing = False

        if self._reconnector.active and self._reconnector.due() and not self._reconnect_pending:
            self._try_reconnect(ports)

    def _on_scan_failed(self, message: str) -> None:
        self.show_tip(f"串口枚举失败：{message}", 5000)

    @staticmethod
    def _normalize_ports(ports: list) -> list[PortInfo]:
        """兼容 ``[(device, description)]`` 与 ``[PortInfo]`` 两种输入。"""
        out: list[PortInfo] = []
        for item in ports:
            if isinstance(item, PortInfo):
                out.append(item)
            else:
                device, description = item[0], (item[1] if len(item) > 1 else "")
                out.append(PortInfo(device=str(device), description=str(description or "")))
        return out

    def _apply_port_list(self, ports: list) -> None:
        """重建端口下拉项：优先保留当前选择，其次恢复上次保存的端口。"""
        infos = self._normalize_ports(ports)
        self._port_set = {info.device for info in infos}
        self._port_map = {info.device: info for info in infos}
        current = self._current_port()
        self.cb_com.blockSignals(True)
        self.cb_com.clear()
        for info in infos:
            self.cb_com.addItem(info.label, info.device)
        target = ""
        if current in self._port_set:
            target = current
        elif self._preferred_port in self._port_set:
            target = self._preferred_port
        elif len(infos) == 1:
            target = infos[0].device
        if target:
            index = self.cb_com.findData(target)
            if index >= 0:
                self.cb_com.setCurrentIndex(index)
        self.cb_com.blockSignals(False)
        self._update_port_info()

    def refresh_port_list(self) -> None:
        """手动刷新（启动 / F5 / 重连）：同步枚举一次，保证立即拿到可用列表。"""
        # 枚举失败与"真的没有串口"都返回空列表：一律按当前实情刷新，
        # 5s 的后台扫描会再校正一次，不会把用户卡在错误状态里。
        self._apply_port_list(enumerate_ports())

    def _update_port_info(self) -> None:
        """会话卡片：显示当前设备的 USB 身份（VID:PID/SN/厂商）。"""
        info = self._port_map.get(self._current_port()) or self._port_info
        self.lab_port.setText(info.device if info else "未选择")
        self.lab_detail.setText(info.short_identity if info else "—")
        self.lab_detail.setToolTip(info.detail_text() if info else "")
        if info is None:
            self.lab_driver.setText("—")
            self.lab_driver.setToolTip("")
        else:
            chips = " ".join(part for part in (info.manufacturer, info.product) if part)
            driver = chips or info.description or "—"
            self.lab_driver.setText(_shorten(driver, 16))
            self.lab_driver.setToolTip(driver)

    def _on_port_selected(self, _index: int) -> None:
        """用户在端口下拉里主动换口：套用该端口上次的参数并刷新信息卡。"""
        device = self._current_port()
        self._update_port_info()
        self._apply_port_profile(device)

    def _remember_port_profile(self) -> None:
        """记住每个端口上次用的线路参数（类似 PuTTY 的会话档案）。"""
        device = self.current_port
        if not device:
            return
        flow_name = self.cb_flow.currentText()
        value = "|".join(
            [
                str(self.current_baud),
                str(self.current_bits),
                str(self.current_stop),
                str(self.current_parity),
                flow_name,
            ]
        )
        self.store.set(f"profile/{device}", value)

    def _apply_port_profile(self, device: str) -> None:
        raw = self.store.get_str(f"profile/{device}")
        if not raw:
            return
        parts = raw.split("|")
        if len(parts) != 5:
            log.debug("端口档案格式非法，忽略: %s=%s", device, raw)
            return
        baud, bits, stop, parity, flow = parts
        if baud:
            self.cb_baud.setCurrentText(baud)
        if bits in ("5", "6", "7", "8"):
            self.cb_data.setCurrentText(bits)
        if stop in ("1", "2"):
            self.cb_stop.setCurrentText(stop)
        if parity in config.PARITY_MAP:
            self.cb_check.setCurrentText(parity)
        if flow in config.FLOW_MAP:
            self.cb_flow.setCurrentText(flow)

    # ---------- worker 信号槽 ----------
    def on_connected(self, info: str) -> None:
        was_reconnecting = self._reconnect_pending
        self._reconnect_pending = False
        self._connected = True
        self._user_close = False
        self._connect_t0 = time.monotonic()
        self._ts.reset(time.time())
        self._formatter.reset()  # 新会话不拼接上一次的残留字符
        self._paused_dropped = 0
        self.restyle_link_btn("danger", "断开")
        self.ck_dtr.setEnabled(True)
        self.ck_rts.setEnabled(True)
        self.lab_port.setText(f"{self.current_port} @ {self.current_baud}")
        self._port_info = self._port_map.get(self.current_port) or self._port_info
        self._update_port_info()

        if was_reconnecting:
            attempt = max(self._reconnector.attempt, 1)
            self._reconnector.stop()
            self.reconnect_timer.stop()
            self._update_reconnect_label()
            self._reconnect_count += 1
            self.append_recv_text(
                f"= 自动重连成功（第 {attempt} 次尝试，累计 {self._reconnect_count} 次）=",
                config.OK_COLOR,
            )
            log.info("自动重连成功: %s（第 %d 次）", self.current_port, attempt)
        else:
            self.append_recv_text(f"= 串口已连接 {info} =", config.INFO_COLOR)

        if self.ck_autolog.isChecked() and not self.ck_log.isChecked():
            self.ck_log.setChecked(True)  # on_log_toggled 会创建文件并提示
        self.update_status()
        self.update_conn_label()
        self.update_stats()

    def on_disconnected(self, reason: str) -> None:
        if self.worker.isRunning() and not self._closing:
            # 换参数重开时上一会话的延迟事件：新线程已接管，忽略旧事件
            log.debug("忽略过期的断开事件: %s", reason)
            return
        was_connected = self._connected
        self._connected = False
        self._ms_stop_loop()
        if self._closing:
            return
        self.restyle_link_btn("primary", "连接")
        self.ck_dtr.setEnabled(False)
        self.ck_rts.setEnabled(False)
        self.lab_port.setText("")
        self._session_seconds = max(self._session_seconds, self._elapsed())
        self.append_recv_text(f"= {reason} =", config.INFO_COLOR)
        self.update_status()
        self.update_conn_label()
        self.update_stats()

        user_close = self._user_close
        self._user_close = False
        if was_connected and not user_close and self._auto_reconnect_on():
            self._start_reconnect(self._reconnect_reason or "设备已断开")

    def on_worker_error(self, message: str) -> None:
        if self._closing:
            log.warning("退出过程中的串口错误: %s", message)
            return
        log.warning("串口错误: %s", message)
        self.append_recv_text(f"!!! {message}", config.ERROR_COLOR)
        self.update_stats()

    def on_open_failed(self, message: str) -> None:
        if self._closing:
            log.warning("打开失败（退出中）: %s", message)
            return
        if self._reconnect_pending:
            # 自动重连期间不弹模态框：否则用户离开时重连逻辑会被阻塞
            self._on_reconnect_failed(message)
            self.restyle_link_btn("primary", "连接")
            self.update_conn_label()
            return
        self._connected = False
        self.restyle_link_btn("primary", "连接")
        self.update_conn_label()
        QMessageBox.warning(self, "串口错误", message)

    def on_lines_state(self, dtr: bool, rts: bool) -> None:
        """把打开端口后的实际电平与界面状态对齐（pyserial 默认断言这两条线）。"""
        for button, real, setter in (
            (self.ck_dtr, dtr, self.worker.set_dtr),
            (self.ck_rts, rts, self.worker.set_rts),
        ):
            want = button.isChecked()
            if want != real:
                setter(want)

    # =========================================================
    #  接收处理
    # =========================================================
    def on_data_received(self, packets: list[tuple[bytes, float]]) -> None:
        if self._closing or not packets:
            return
        total = sum(len(chunk) for chunk, _ in packets)
        self.rx_count += total
        self.rx_frames += len(packets)

        # 原始字节无论是否暂停都要留存：HEX 导出针对"收到的所有数据"，
        # 暂停只影响显示，不应破坏导出内容的完整性。
        for chunk, _ts in packets:
            self._rx_raw.extend(chunk)
        if len(self._rx_raw) > config.RX_RAW_CAP:
            keep = config.RX_RAW_CAP // 8 * 7
            del self._rx_raw[: len(self._rx_raw) - keep]

        if self.btn_pause.isChecked():
            # 暂停冻结渲染；跳过量如实统计并展示，避免用户误判为丢数据故障
            self._paused_dropped += total
            return

        self._paused_dropped = 0
        hex_mode = self.ck_hex.isChecked()
        wave_enabled = self.btn_wave.isChecked()
        self._formatter.split_mode = SPLIT_MODE_MAP.get(
            self.cb_break.currentText(), SPLIT_BY_PACKET
        )
        texts: list[tuple[str, float]] = []
        for chunk, timestamp in packets:
            if wave_enabled:
                self.wave_chart.add_samples(chunk)
            text = self._formatter.format(chunk, hex_mode=hex_mode)
            if text:
                texts.append((text, timestamp))

        if self.log_file and texts:
            for text, timestamp in texts:
                stamp = datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[
                    :-3
                ]
                self._log_buf.append(f"[{stamp}] {text.rstrip()}")
        if self.ck_clear.isChecked():
            self.recv_box.clear()
        self.append_recv_batch(texts)

    def _on_encoding_changed(self, _index: int) -> None:
        self._formatter.encoding = self.encoding
        self._formatter.reset()

    def _on_ts_mode_changed(self, _index: int) -> None:
        mode = config.TIMESTAMP_MODES.get(self.cb_ts.currentText())
        if not mode:
            return
        self._ts.set_mode(mode)
        if not self.ck_time.isChecked():
            self.ck_time.setChecked(True)  # 选了格式却没开时间戳是常见困惑点

    def _recv_text_color(self) -> QColor:
        """接收区正文颜色跟随当前配色主题（深色底用浅色字，保证对比度）。"""
        return config.RECV_THEME_TEXT.get(
            self._recv_theme, config.RECV_THEME_TEXT[config.RECV_THEME_BLUE]
        )

    def _follow_tail(self) -> bool:
        """接收区是否停在底部：用户向上翻历史时不强行拉回。"""
        bar = self.recv_box.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def _format_for(self, color: str, cache: dict[str, QTextCharFormat]) -> QTextCharFormat:
        """按颜色取字符格式（同批次内复用，避免逐行 new 对象）。"""
        cached = cache.get(color)
        if cached is not None:
            return cached
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cache[color] = fmt
        return fmt

    def _recv_formats(self) -> tuple[QTextCharFormat, QTextCharFormat]:
        """正文 / 时间戳两套字符格式（必须分别取副本，共用会互相覆盖颜色）。"""
        cursor = self.recv_box.textCursor()
        body = cursor.charFormat()
        body.setForeground(self._recv_text_color())
        stamp = cursor.charFormat()
        stamp.setForeground(config.TIME_COLOR)
        return body, stamp

    def append_recv_batch(self, texts: list[tuple[str, float]]) -> None:
        """批量渲染：一次光标操作插入整批文本，减少重排。"""
        if not texts:
            return
        follow = self._follow_tail()
        cursor = self.recv_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        body_fmt, stamp_fmt = self._recv_formats()
        with_time = self.ck_time.isChecked()
        body_color = self._recv_text_color().name()
        highlight = self._highlighter if self.ck_highlight.isChecked() else None
        cache: dict[str, QTextCharFormat] = {body_color: body_fmt}

        for text, timestamp in texts:
            if with_time:
                cursor.setCharFormat(stamp_fmt)
                cursor.insertText(self._ts.stamp(timestamp))
            line = text.rstrip("\n")
            if highlight is not None and highlight.is_active:
                for segment, color in highlight.segments(line, body_color):
                    cursor.setCharFormat(self._format_for(color, cache))
                    cursor.insertText(segment)
            else:
                cursor.setCharFormat(body_fmt)
                cursor.insertText(line)
            cursor.setCharFormat(body_fmt)
            cursor.insertText("\n")
        if follow:
            self.recv_box.setTextCursor(cursor)
            self.recv_box.ensureCursorVisible()

    def append_recv_text(self, text: str, color: QColor) -> None:
        """单行追加（TX 回显 / 状态提示用）。"""
        if not text:
            return
        follow = self._follow_tail()
        cursor = self.recv_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        _, stamp_fmt = self._recv_formats()
        fmt = cursor.charFormat()
        fmt.setForeground(color)
        if self.ck_time.isChecked():
            cursor.setCharFormat(stamp_fmt)
            cursor.insertText(self._ts.stamp(time.time()))
        cursor.setCharFormat(fmt)
        cursor.insertText(text.rstrip("\n") + "\n")
        if follow:
            self.recv_box.setTextCursor(cursor)
            self.recv_box.ensureCursorVisible()

    @property
    def encoding(self) -> str:
        return config.ENCODING_MAP.get(self.cb_encoding.currentText(), "utf-8")

    # =========================================================
    #  发送处理
    # =========================================================
    def do_send_text(self, text: str, show_warn: bool = True) -> bool:
        """发送一段文本。

        Returns:
            ``True`` 表示已入队；``False`` 表示未发送（未连接 / 格式错误 / 空内容）。
        """
        if not self._connected:
            if show_warn:
                QMessageBox.warning(self, "提示", "请先打开串口")
            return False
        if self.send_hex.isChecked():
            data, err = utils.parse_hex(text)
            if data is None:
                if show_warn:
                    QMessageBox.warning(
                        self, "格式错误", f"HEX 格式错误，请用空格分隔，如: 01 A2 FF\n{err}"
                    )
                return False
        else:
            data = text.encode(self.encoding, errors="ignore")
        if self.ck_crc.isChecked() and data:
            data += utils.crc16_modbus(data).to_bytes(2, "little")
        eol = config.LINE_ENDINGS.get(self.cb_eol.currentText(), b"")
        data += eol
        if not data:
            return False
        self.worker.write(data)
        self.tx_count += len(data)
        self.add_history(text)
        self.update_status()
        self.update_stats()
        if self.ck_echo.isChecked():
            extra = ""
            if eol:
                extra += f" [{self.cb_eol.currentText()}]"
            if self.ck_crc.isChecked():
                extra += " [CRC16]"
            self.append_recv_text(f">>> TX{extra}: {text}", config.TX_COLOR)
        return True

    def send_break(self) -> None:
        """发送 BREAK：TX 保持低电平一段时间（部分 MCU 的 ISP/调试入口）。"""
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口")
            return
        self.worker.send_break(0.25)
        self.append_recv_text("= 已发送 BREAK（TX 低电平 250ms）=", config.TX_COLOR)

    def send_preset(self) -> None:
        self.do_send_text(self.cb_preset.currentText(), True)

    def add_preset(self) -> None:
        text = self.send_box.toPlainText().strip()
        if text and self.cb_preset.findText(text) < 0:
            self.cb_preset.addItem(text)
            self.cb_preset.setCurrentIndex(self.cb_preset.count() - 1)

    def add_history(self, text: str) -> None:
        if not text:
            return
        self._updating_history = True
        index = self.cb_history.findText(text)
        if index >= 0:
            self.cb_history.removeItem(index)
        self.cb_history.insertItem(0, text)
        while self.cb_history.count() > config.HISTORY_MAX:
            self.cb_history.removeItem(self.cb_history.count() - 1)
        self.cb_history.setCurrentIndex(-1)
        self._history = [self.cb_history.itemText(i) for i in range(self.cb_history.count())]
        self._history_index = -1
        self._updating_history = False

    def on_history_changed(self) -> None:
        if self._updating_history:
            return
        text = self.cb_history.currentText()
        if text:
            self.send_box.setPlainText(text)
            self._history_index = -1

    def _history_navigate(self, step: int) -> None:
        """输入框内 ↑/↓ 翻历史（仅在单行内容时生效，不干扰多行编辑）。"""
        if not self._history:
            return
        index = self._history_index + step
        if index < -1:
            index = -1
        if index >= len(self._history):
            index = len(self._history) - 1
        self._history_index = index
        if index < 0:
            self.send_box.clear()
            return
        self.send_box.setPlainText(self._history[index])
        cursor = self.send_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.send_box.setTextCursor(cursor)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt 命名)
        """发送框的 ↑/↓ 历史浏览。"""
        if (
            obj is self.send_box
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down)
            and "\n" not in self.send_box.toPlainText()
        ):
            self._history_navigate(-1 if event.key() == Qt.Key.Key_Up else 1)
            return True
        return super().eventFilter(obj, event)

    # ---------- 文件发送 ----------
    def pick_send_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件", "", "所有文件 (*)")
        if not path:
            return
        self._file_path = path
        size = 0
        with contextlib.suppress(OSError):
            size = os.path.getsize(path)
        self._file_size = size
        self.lab_file_path.setText(f"{os.path.basename(path)} · {utils.format_bytes(size)}")
        self.tabs.setCurrentIndex(2)

    def choose_send_file(self) -> None:
        """菜单入口：选文件并直接开始发送。"""
        self.pick_send_file()
        if self._file_path and not self._sending_file:
            self.send_file()

    def _on_sendfile_clicked(self) -> None:
        if self._sending_file:
            self._finish_file_send(interrupted=True, reason="用户取消")
            self.worker.clear_cmds()
            return
        if not self._file_path:
            self.pick_send_file()
            if not self._file_path:
                return
        self.send_file()

    def send_file(self) -> None:
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口")
            return
        if self._sending_file:
            self.show_tip("正在发送文件，请等待完成或点击「取消发送」", 3000)
            return
        path = self._file_path
        if not path:
            self.pick_send_file()
            path = self._file_path
            if not path:
                return
        try:
            # 句柄由发送泵持有并在 _finish_file_send 中关闭（流式发送，不能退出作用域即关）
            handle = open(path, "rb")  # noqa: SIM115
            size = os.path.getsize(path)
        except OSError as exc:
            QMessageBox.warning(self, "错误", f"读取文件失败: {exc}")
            return
        self._file_send = handle
        self._file_total = 0
        self._file_size = size
        self._sending_file = True
        self.btn_sendfile.setText("取消发送")
        self.file_progress.setValue(0)
        self.append_recv_text(
            f">>> 开始发送文件: {os.path.basename(path)} ({utils.format_bytes(size)})",
            config.TX_COLOR,
        )
        # 异步泵送：立即返回，由定时器按队列水位持续补水
        pace = self._pace_ms()
        self._file_timer.start(pace if pace > 0 else 5)
        self._pump_file_send()

    def _pace_ms(self) -> int:
        """分块间隔（毫秒）：0 表示按写队列水位全速发送。"""
        try:
            return max(int(self.edit_pace.text().strip() or 0), 0)
        except ValueError:
            return 0

    def _pump_file_send(self) -> None:
        """按写队列水位分块喂数据（背压），不阻塞 UI、不整文件驻留内存。"""
        if not self._sending_file:
            self._file_timer.stop()
            return
        if self._closing or not self._connected:
            self._finish_file_send(interrupted=True, reason="连接已断开")
            return
        handle = self._file_send
        if handle is None:
            self._finish_file_send()
            return
        pace = self._pace_ms()
        try:
            if pace > 0:
                # 限速模式：每个 tick 只送一块，给慢速 MCU 留出处理时间
                chunk = handle.read(config.SEND_CHUNK_SIZE)
                if not chunk:
                    self._finish_file_send()
                    return
                self.worker.write(chunk)
                self._file_total += len(chunk)
                self._file_timer.start(pace)
            else:
                while self.worker.pending_writes() < config.SEND_QUEUE_LIMIT:
                    chunk = handle.read(config.SEND_CHUNK_SIZE)
                    if not chunk:
                        self._finish_file_send()
                        return
                    self.worker.write(chunk)
                    self._file_total += len(chunk)
            self._update_file_progress()
        except OSError as exc:
            log.warning("文件发送失败: %s", exc)
            self._finish_file_send(interrupted=True, reason=str(exc))

    def _update_file_progress(self) -> None:
        if not self._file_size:
            return
        percent = min(self._file_total * 100 // self._file_size, 100)
        self.file_progress.setValue(percent)
        self.lab_file.setText(
            f"文件 {percent}%  {utils.format_bytes(self._file_total)}"
            f"/{utils.format_bytes(self._file_size)}"
        )
        self.lab_file.setVisible(True)

    def _finish_file_send(self, interrupted: bool = False, reason: str = "") -> None:
        self._file_timer.stop()
        if self._file_send is not None:
            with contextlib.suppress(OSError):
                self._file_send.close()
        self._file_send = None
        self._sending_file = False
        self.btn_sendfile.setText("发送文件")
        self.lab_file.setVisible(False)
        self.lab_file.setText("")
        total = self._file_total
        name = os.path.basename(self._file_path) if self._file_path else ""
        if total:
            self.tx_count += total
            self.update_status()
            self.update_stats()
        if interrupted:
            suffix = f"（{reason}）" if reason else ""
            self.append_recv_text(
                f">>> 文件发送中断: {name}{suffix}，已入队 {utils.format_bytes(total)}",
                config.ERROR_COLOR,
            )
        elif total:
            self.file_progress.setValue(100)
            self.append_recv_text(
                f">>> 文件已发送: {name} ({utils.format_bytes(total)})", config.TX_COLOR
            )

    # =========================================================
    #  多条发送列表
    # =========================================================
    def ms_add(self) -> None:
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

    def ms_del(self) -> None:
        rows = sorted({item.row() for item in self.ms_table.selectedItems()}, reverse=True)
        for row in rows:
            self.ms_table.removeRow(row)

    def ms_clear(self) -> None:
        self._ms_stop_loop()
        self.ms_table.setRowCount(0)

    def _ms_checked_rows(self) -> list[tuple[str, int]]:
        rows: list[tuple[str, int]] = []
        for row in range(self.ms_table.rowCount()):
            used = self.ms_table.item(row, 0)
            if not used or used.checkState() != Qt.CheckState.Checked:
                continue
            text_item = self.ms_table.item(row, 1)
            if text_item is None:
                continue
            delay_item = self.ms_table.item(row, 2)
            delay = 100
            if delay_item is not None:
                try:
                    delay = int(delay_item.text())
                except ValueError:
                    delay = 100
            rows.append((text_item.text(), max(delay, 0)))
        return rows

    def ms_send_selected(self) -> None:
        for text, _delay in self._ms_checked_rows():
            self.do_send_text(text, False)

    def ms_loop(self) -> None:
        if self._ms_looping:
            self._ms_stop_loop()
            return
        if not self._ms_checked_rows():
            QMessageBox.information(self, "提示", "请先勾选要循环发送的指令")
            self.btn_ms_loop.setChecked(False)
            return
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口")
            self.btn_ms_loop.setChecked(False)
            return
        self._ms_looping = True
        self._ms_loop_idx = 0
        self.btn_ms_loop.setText("停止循环")
        self._ms_loop_tick()

    def _ms_loop_tick(self) -> None:
        """循环发送一步：成员定时器链，_ms_stop_loop() 可确定性取消。"""
        if not self._ms_looping or not self._connected:
            self._ms_stop_loop()
            return
        rows = self._ms_checked_rows()
        if not rows:
            self._ms_stop_loop()
            return
        index = self._ms_loop_idx % len(rows)
        self._ms_loop_idx = index + 1
        text, delay = rows[index]
        self.do_send_text(text, False)
        self._ms_loop_timer.start(max(delay, 1))

    def _ms_stop_loop(self) -> None:
        self._ms_looping = False
        self._ms_loop_timer.stop()
        self.btn_ms_loop.setChecked(False)
        self.btn_ms_loop.setText("循环发送")

    # =========================================================
    #  复位 / 定时 / 速率 / 信号 / 日志
    # =========================================================
    def reset_target(self) -> None:
        """ESP32 复位：RTS 断言（低电平）保持 150ms 后释放。

        若板子 EN 接的是 DTR，把下方 rts 换成 dtr 即可。
        分步由 QTimer 驱动，UI 零阻塞。
        """
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口")
            return
        self._reset_timer.stop()
        self._reset_step = 0
        self.append_recv_text("= 复位脉冲：RTS 低电平 150ms =", config.TX_COLOR)
        self.worker.set_rts(False)  # 先释放：RTS 已勾选时也能产生真实跳变
        self._reset_timer.start(50)

    def _on_reset_tick(self) -> None:
        self._reset_step += 1
        if self._reset_step == 1:
            self.worker.set_rts(True)
            self.append_recv_text("= RTS 断言（低）→ 复位中 =", config.TX_COLOR)
            self._reset_timer.start(150)
        elif self._reset_step == 2:
            self.worker.set_rts(False)
            self.append_recv_text("= RTS 释放（高）→ 启动 =", config.TX_COLOR)
            self._reset_timer.stop()
            if self.ck_rts.isChecked():
                # 硬件已释放，同步取消勾选，避免"显示断言、实际已释放"
                self.ck_rts.setChecked(False)

    def toggle_timer_send(self) -> None:
        if self.send_timer.isActive():
            self.send_timer.stop()
            self.btn_timer.setChecked(False)
            return
        if not self._connected:
            QMessageBox.warning(self, "提示", "请先打开串口")
            self.btn_timer.setChecked(False)
            return
        try:
            period = int(self.edit_period.text())
        except ValueError:
            QMessageBox.warning(self, "参数错误", "请输入合法数字")
            self.btn_timer.setChecked(False)
            return
        if period <= 0:
            QMessageBox.warning(self, "参数错误", "周期必须大于 0")
            self.btn_timer.setChecked(False)
            return
        self.send_timer.start(period)
        self.btn_timer.setChecked(True)

    def _elapsed(self) -> float:
        return time.monotonic() - self._connect_t0 if self._connect_t0 else 0.0

    def update_rate(self) -> None:
        now = time.monotonic()
        elapsed = now - self._rate_t0
        if elapsed > 0:
            self.rx_rate = (self.rx_count - self._rate_rx) / elapsed
            self.tx_rate = (self.tx_count - self._rate_tx) / elapsed
        self._rate_t0 = now
        self._rate_rx = self.rx_count
        self._rate_tx = self.tx_count
        self.peak_rx = max(self.peak_rx, self.rx_rate)
        self.update_status()
        self.update_conn_label()
        self.update_recv_stat()
        self.update_stats()
        self._check_log_rollover()
        self._flush_log()
        if self._connected:
            self.rate_chart.add_sample(self.rx_rate, self.tx_rate)

    def update_status(self) -> None:
        self.lab_status.setText(
            self._fmt_status(self.rx_count, self.tx_count, self.rx_rate, self.tx_rate)
        )

    @staticmethod
    def _fmt_status(rx: int, tx: int, rx_rate: float, tx_rate: float) -> str:
        """统计文字按固定宽度对齐，避免每秒刷新时标签宽度伸缩产生残影。"""
        return f"RX:{rx:>8}  TX:{tx:>8}  收:{rx_rate:>6.0f} B/s 发:{tx_rate:>6.0f} B/s"

    def show_tip(self, text: str, msec: int = 5000) -> None:
        """状态栏临时提示：独立标签，不覆盖连接状态与统计。"""
        self.lab_tip.setText(text)
        self._tip_timer.start(msec)

    def update_recv_stat(self) -> None:
        text = (
            f"收 {utils.format_bytes(self.rx_count)}  发 {utils.format_bytes(self.tx_count)}"
            f"  |  行 {self.recv_box.blockCount()}"
        )
        if self._paused_dropped:
            text += f"  |  暂停中，已跳过 {utils.format_bytes(self._paused_dropped)}"
        self.lab_recv_stat.setText(text)

    def update_stats(self) -> None:
        """统计卡：累计收发 / 帧数 / 错误 / 峰值 / 时长。"""
        self.lab_rx.setText(utils.format_bytes(self.rx_count))
        self.lab_tx.setText(utils.format_bytes(self.tx_count))
        self.lab_frames.setText(str(self.rx_frames))
        errors = self.worker.read_errors + self.worker.write_errors
        self.lab_errors.setText(str(errors))
        self.lab_peak.setText(f"{utils.format_bytes(int(self.peak_rx))}/s")
        seconds = self._elapsed() if self._connected else self._session_seconds
        self.lab_duration.setText(utils.format_duration(seconds))

    def update_conn_label(self) -> None:
        if self._connected and self._connect_t0:
            elapsed = self._elapsed()
            self.lab_conn.setText(f"已连接 {utils.format_duration(elapsed)}")
            self.lab_session.setText(f"{self.current_port} · {self.current_baud}")
            state = "on"
        else:
            self.lab_conn.setText("未连接")
            self.lab_session.setText(
                f"空闲 · 重连 {self._reconnect_count}" if self._reconnect_count else "空闲"
            )
            state = "off"
        if state != self._dot_state:
            self._dot_state = state
            self.dot.setObjectName("dotGreen" if state == "on" else "dot")
            style = self.dot.style()
            style.unpolish(self.dot)
            style.polish(self.dot)

    def clear_count(self) -> None:
        self.rx_count = 0
        self.tx_count = 0
        self.rx_frames = 0
        self.rx_rate = 0.0
        self.tx_rate = 0.0
        self.peak_rx = 0.0
        self._paused_dropped = 0
        self.rate_chart.clear()
        self.update_status()
        self.update_recv_stat()
        self.update_stats()

    def clear_recv(self) -> None:
        """清空接收区（连同波形、原始缓冲与解码残留）。"""
        self.recv_box.clear()
        self.wave_chart.clear()
        self._rx_raw.clear()
        self._formatter.reset()

    def on_wave_toggled(self, checked: bool) -> None:
        self.wave_chart.setVisible(checked)
        if checked:
            self.wave_chart.update()

    def on_auto_wrap(self, checked: bool) -> None:
        mode = (
            QPlainTextEdit.LineWrapMode.WidgetWidth
            if checked
            else QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.recv_box.setLineWrapMode(mode)

    def on_dtr_toggled(self, checked: bool) -> None:
        self.worker.set_dtr(checked)

    def on_rts_toggled(self, checked: bool) -> None:
        self.worker.set_rts(checked)

    # ---------- 高亮 ----------
    def on_highlight_toggled(self, checked: bool) -> None:
        self._highlighter.set_enabled(checked)
        if checked:
            self.show_tip("已启用关键字高亮（只对新到达的数据生效）", 3000)

    def edit_highlight_rules(self) -> None:
        from .rules_dialog import run_rules_editor

        rules = run_rules_editor(self, self._highlighter.rules)
        if rules is None:
            return
        self._highlighter.set_rules(rules[:_MAX_HIGHLIGHT_RULES])
        self.ck_highlight.setChecked(True)
        self._highlighter.set_enabled(True)
        self.show_tip(f"高亮规则已更新（{len(rules)} 条）", 3000)

    # ---------- 接收区外观 ----------
    def _apply_recv_style(self) -> None:
        # 字体族与字号写在接收区自身样式表里：全局 QWidget 规则会覆盖 setFont
        css = config.RECV_THEME_STYLE[self._recv_theme]
        css += f"\nQPlainTextEdit{{font-size:{self._font_size}pt;}}"
        self.recv_box.setStyleSheet(css)
        font = QFont(config.RECV_FONT_FAMILY, self._font_size)
        font.setFamilies(config.RECV_FONT_FALLBACKS)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.recv_box.setFont(font)

    def set_recv_theme(self, theme: str) -> None:
        self._recv_theme = theme
        self._apply_recv_style()

    def change_font_size(self, delta: int) -> None:
        self._font_size = max(
            config.MIN_FONT_SIZE, min(config.MAX_FONT_SIZE, self._font_size + delta)
        )
        self._apply_recv_style()

    def _recv_context_menu(self, pos) -> None:
        menu = QMenu(self)
        action_clear = menu.addAction("清空显示")
        action_copy = menu.addAction("复制")
        action_copy.setEnabled(self.recv_box.textCursor().hasSelection())
        action_save = menu.addAction("保存接收日志...")
        action_export = menu.addAction("导出接收数据(HEX)...")
        menu.addSeparator()
        action_hex = menu.addAction("HEX 显示")
        action_hex.setCheckable(True)
        action_hex.setChecked(self.ck_hex.isChecked())
        action_time = menu.addAction("时间戳")
        action_time.setCheckable(True)
        action_time.setChecked(self.ck_time.isChecked())
        action_wrap = menu.addAction("自动换行")
        action_wrap.setCheckable(True)
        action_wrap.setChecked(self.ck_wrap.isChecked())
        action_highlight = menu.addAction("关键字高亮")
        action_highlight.setCheckable(True)
        action_highlight.setChecked(self.ck_highlight.isChecked())
        menu.addSeparator()
        theme_menu = menu.addMenu("接收配色")
        action_blue = theme_menu.addAction("蓝底白字")
        action_blue.setCheckable(True)
        action_blue.setChecked(self._recv_theme == config.RECV_THEME_BLUE)
        action_green = theme_menu.addAction("绿字黑底")
        action_green.setCheckable(True)
        action_green.setChecked(self._recv_theme == config.RECV_THEME_GREEN)
        menu.addSeparator()
        action_font_up = menu.addAction("字体放大")
        action_font_down = menu.addAction("字体缩小")
        action_font_reset = menu.addAction("恢复默认字号")

        chosen = menu.exec(self.recv_box.mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == action_clear:
            self.clear_recv()
        elif chosen == action_copy:
            self.recv_box.copy()
        elif chosen == action_save:
            self.save_log()
        elif chosen == action_export:
            self.export_log_hex()
        elif chosen == action_hex:
            self.ck_hex.setChecked(action_hex.isChecked())
        elif chosen == action_time:
            self.ck_time.setChecked(action_time.isChecked())
        elif chosen == action_wrap:
            self.ck_wrap.setChecked(action_wrap.isChecked())
        elif chosen == action_highlight:
            self.ck_highlight.setChecked(action_highlight.isChecked())
        elif chosen == action_blue:
            self.set_recv_theme(config.RECV_THEME_BLUE)
        elif chosen == action_green:
            self.set_recv_theme(config.RECV_THEME_GREEN)
        elif chosen == action_font_up:
            self.change_font_size(1)
        elif chosen == action_font_down:
            self.change_font_size(-1)
        elif chosen == action_font_reset:
            self._font_size = config.DEFAULT_FONT_SIZE
            self._apply_recv_style()

    # ---------- 日志 ----------
    def _flush_log(self) -> None:
        if self.log_file and self._log_buf:
            try:
                self.log_file.write("\n".join(self._log_buf) + "\n")
                self.log_file.flush()
            except OSError as exc:
                log.warning("日志写入失败: %s", exc)
            self._log_buf.clear()

    def on_log_toggled(self, checked: bool) -> None:
        if checked:
            try:
                name = self._open_log()
                self.append_recv_text(f">>> 开始记录: {os.path.basename(name)}", config.TX_COLOR)
            except OSError as exc:
                QMessageBox.warning(self, "错误", f"无法创建日志文件: {exc}")
                self.ck_log.blockSignals(True)
                self.ck_log.setChecked(False)
                self.ck_log.blockSignals(False)
                self.log_file = None
        else:
            if self.log_file:
                self._flush_log()
                self.log_file.close()
                self.log_file = None
                self.append_recv_text(">>> 停止记录", config.TX_COLOR)

    def _log_dir(self) -> str:
        directory = os.path.join(config.app_dir(), "logs")
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError:
            log.warning("日志目录不可写: %s", directory)
        return directory

    def _open_log(self) -> str:
        """按日期命名：logs/serial_YYYYMMDD.log（跨天自动滚动新文件）。"""
        today = datetime.date.today()
        path = os.path.join(self._log_dir(), f"serial_{today:%Y%m%d}.log")
        # 日志句柄跨事件循环长期持有，关闭由 on_log_toggled / closeEvent 负责
        self.log_file = open(path, "a", encoding="utf-8")  # noqa: SIM115
        self._log_date = today
        return path

    def _check_log_rollover(self) -> None:
        if self.log_file and datetime.date.today() != self._log_date:
            self._flush_log()
            with contextlib.suppress(OSError):
                self.log_file.close()
            name = self._open_log()
            self.append_recv_text(
                f">>> 跨天，日志已切换到: {os.path.basename(name)}", config.TX_COLOR
            )

    def save_log(self) -> None:
        self._flush_log()
        path, _ = QFileDialog.getSaveFileName(
            self, "保存接收日志", "recv_log.txt", "文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.recv_box.toPlainText())
            QMessageBox.information(self, "完成", f"已保存到:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "错误", f"保存失败: {exc}")

    def export_log_hex(self) -> None:
        """导出接收到的原始字节为 HEX 文本（每行 16 字节）。"""
        if not self._rx_raw:
            QMessageBox.information(self, "提示", "当前没有已接收的原始数据")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出接收数据(HEX)", "recv_log_hex.txt", "文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="ascii") as handle:
                for line in utils.hex_dump_lines(bytes(self._rx_raw)):
                    handle.write(line + "\n")
            QMessageBox.information(self, "完成", f"已导出 {len(self._rx_raw)} 字节到:\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "错误", f"导出失败: {exc}")

    def show_shortcuts(self) -> None:
        QMessageBox.information(
            self,
            "快捷键",
            "Ctrl+Enter  发送\n"
            "Ctrl+D      连接 / 断开\n"
            "Ctrl+R      立即重连一次\n"
            "Ctrl+F      查找（Esc 关闭）\n"
            "Ctrl+L      清空接收区\n"
            "Ctrl+S      保存接收日志\n"
            "F5          刷新端口\n"
            "↑ / ↓       在发送框内浏览历史\n"
            "Ctrl+滚轮   调整接收区字号",
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "关于",
            f"{config.APP_NAME}\n版本 {config.APP_VERSION}\n\n"
            "嵌入式串口调试终端：HEX/ASCII 收发、三种时间戳（时钟/相对/增量）、\n"
            "关键字高亮、自动重连（按 VID:PID/序列号找回端口）、\n"
            "端口参数记忆、定时发送、多条发送与循环、文件发送（进度/限速/取消）、\n"
            "CRC16 追加、DTR/RTS、BREAK、ESP32 复位、流控、日志自动记录、\n"
            "配置导入导出、接收区查找\n\n"
            "帮助 → 快捷键一览 查看完整按键。",
        )

    # =========================================================
    #  查找
    # =========================================================
    def show_find_bar(self) -> None:
        self.find_bar.setVisible(True)
        self.find_edit.setFocus()
        self.find_edit.selectAll()

    def hide_find_bar(self) -> None:
        self.find_bar.setVisible(False)
        self.recv_box.setFocus()

    def find_next(self) -> None:
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
    _BOOL_KEYS = (
        "ck_hex",
        "ck_time",
        "ck_clear",
        "ck_wrap",
        "ck_echo",
        "ck_crc",
        "ck_autolog",
        "ck_highlight",
        "btn_autoreconnect",
    )

    def load_settings(self) -> None:
        self._loading = True
        try:
            self._load_settings_inner()
        finally:
            self._loading = False

    def _load_settings_inner(self) -> None:
        geometry = self.store.get_bytes("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        self._preferred_port = self.store.get_str("port")
        if self._preferred_port:
            index = self.cb_com.findData(self._preferred_port)
            if index >= 0:
                self.cb_com.setCurrentIndex(index)
        self.cb_baud.setCurrentText(self.store.get_str("baud", "115200"))
        self.cb_data.setCurrentText(self.store.get_str("data", "8"))
        self.cb_stop.setCurrentText(self.store.get_str("stop", "1"))
        self.cb_check.setCurrentText(self.store.get_str("parity", "None"))
        self.cb_flow.setCurrentText(self.store.get_str("flow", "无"))
        self.cb_encoding.setCurrentText(self.store.get_str("encoding", "UTF-8"))
        self.cb_break.setCurrentIndex(
            self.store.get_int("break_mode", 0, lo=0, hi=self.cb_break.count() - 1)
        )
        # 行尾：v3 起用下拉框；老配置里的 ck_newline 迁移为 \r\n
        eol = self.store.get_str("eol")
        if not eol:
            eol = "\\r\\n" if self.store.get_bool("newline", False) else "无"
        self.cb_eol.setCurrentText(eol if eol in config.LINE_ENDINGS else "无")
        ts_mode = self.store.get_str("ts_mode")
        if ts_mode not in config.TS_MODE_LABELS:
            ts_mode = config.TS_MODE_CLOCK
        self.cb_ts.setCurrentText(config.TS_MODE_LABELS[ts_mode])
        self._ts.set_mode(ts_mode)

        if self.store.get_str("send_mode", "ascii") == "hex":
            self.send_hex.setChecked(True)
        else:
            self.send_ascii.setChecked(True)
        self.edit_period.setText(self.store.get_str("period", "100"))
        self.edit_pace.setText(self.store.get_str("pace", "0"))

        # 暂停状态与"记录到文件"不做持久化：避免下次启动界面正常但接收不刷新，
        # 或静默写入日志文件。
        for key in self._BOOL_KEYS:
            widget = getattr(self, key)
            if key == "btn_autoreconnect":
                widget.setChecked(self.store.get_bool(key, False))
            else:
                widget.setChecked(self.store.get_bool(key, widget.isChecked()))

        for text in self.store.get_str_list("presets"):
            if self.cb_preset.findText(text) < 0:
                self.cb_preset.addItem(text)

        # 高亮规则
        rules = parse_rules(self.store.get_str("highlight_rules"))[:_MAX_HIGHLIGHT_RULES]
        self._highlighter.set_rules(rules)
        self._highlighter.set_enabled(self.ck_highlight.isChecked())

        # 接收区外观
        self._font_size = self.store.get_int(
            "font_size", config.DEFAULT_FONT_SIZE, lo=config.MIN_FONT_SIZE, hi=config.MAX_FONT_SIZE
        )
        theme = self.store.get_str("recv_theme", config.RECV_THEME_BLUE)
        self._recv_theme = theme if theme in config.RECV_THEME_STYLE else config.RECV_THEME_BLUE

        self._restore_multisend()

        state = self.store.get_bytes("main_split")
        if state is not None:
            self.main_split.restoreState(state)
        state = self.store.get_bytes("v_split")
        if state is not None:
            self.v_split.restoreState(state)
        self._update_port_info()

    def _restore_multisend(self) -> None:
        used = self.store.get_bool_list("multisend_used")
        texts = self.store.get_str_list("multisend_text")
        delays = self.store.get_str_list("multisend_delay")
        for index, text in enumerate(texts):
            row = self.ms_table.rowCount()
            self.ms_table.insertRow(row)
            used_item = QTableWidgetItem()
            used_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            checked = index < len(used) and used[index]
            used_item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.ms_table.setItem(row, 0, used_item)
            self.ms_table.setItem(row, 1, QTableWidgetItem(text))
            delay = delays[index] if index < len(delays) else "100"
            self.ms_table.setItem(row, 2, QTableWidgetItem(delay or "100"))

    def save_settings(self) -> None:
        self.store.set("geometry", self.saveGeometry())
        self.store.set("port", self._current_port())
        self.store.set("baud", self.cb_baud.currentText())
        self.store.set("data", self.cb_data.currentText())
        self.store.set("stop", self.cb_stop.currentText())
        self.store.set("parity", self.cb_check.currentText())
        self.store.set("flow", self.cb_flow.currentText())
        self.store.set("encoding", self.cb_encoding.currentText())
        self.store.set("break_mode", self.cb_break.currentIndex())
        self.store.set("eol", self.cb_eol.currentText())
        self.store.set("ts_mode", config.TIMESTAMP_MODES.get(self.cb_ts.currentText(), "clock"))
        self.store.set("send_mode", "hex" if self.send_hex.isChecked() else "ascii")
        self.store.set("period", self.edit_period.text())
        self.store.set("pace", self.edit_pace.text())
        for key in self._BOOL_KEYS:
            self.store.set(key, bool(getattr(self, key).isChecked()))
        # 单次会话状态不落盘
        self.store.remove("ck_pause")
        self.store.remove("ck_log")
        self.store.set(
            "presets", [self.cb_preset.itemText(i) for i in range(self.cb_preset.count())]
        )
        self.store.set("highlight_rules", serialize_rules(self._highlighter.rules))
        self.store.set("font_size", self._font_size)
        self.store.set("recv_theme", self._recv_theme)

        used, texts, delays = [], [], []
        for row in range(self.ms_table.rowCount()):
            used_item = self.ms_table.item(row, 0)
            used.append(bool(used_item and used_item.checkState() == Qt.CheckState.Checked))
            text_item = self.ms_table.item(row, 1)
            texts.append(text_item.text() if text_item else "")
            delay_item = self.ms_table.item(row, 2)
            delays.append(delay_item.text() if delay_item else "100")
        self.store.set("multisend_used", used)
        self.store.set("multisend_text", texts)
        self.store.set("multisend_delay", delays)
        self.store.set("main_split", self.main_split.saveState())
        self.store.set("v_split", self.v_split.saveState())
        self.store.sync()

    # ---------- 配置导入 / 导出 ----------
    def export_settings(self) -> None:
        """把当前配置导出为 JSON，便于在另一台机器复现同一套参数。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置", "serial_tool_config.json", "JSON 文件 (*.json)"
        )
        if not path:
            return
        data = {
            key: self.store.raw().value(key)
            for key in self.store.raw().allKeys()
            if not key.startswith("geometry")
        }
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(_jsonable(data), handle, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "完成", f"配置已导出到:\n{path}")
        except (OSError, TypeError) as exc:
            QMessageBox.warning(self, "错误", f"导出失败: {exc}")

    def import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON 文件 (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "错误", f"读取配置失败: {exc}")
            return
        if not isinstance(data, dict):
            QMessageBox.warning(self, "错误", "配置文件格式不正确")
            return
        for key, value in data.items():
            if isinstance(key, str) and not key.startswith("geometry"):
                self.store.set(key, value)
        self.store.sync()
        # 部分项需要重建控件状态：多行发送表格先清空再按新配置重灌
        self.ms_table.setRowCount(0)
        self.load_settings()
        self._apply_recv_style()
        self.on_auto_wrap(self.ck_wrap.isChecked())
        self.show_tip("配置已导入", 4000)

    # =========================================================
    #  异常兜底 / 退出
    # =========================================================
    def _on_fatal_error(self, summary: str) -> None:
        """线程或全局异常钩子投递的致命错误：弹窗一次，避免用户只见窗口消失。"""
        if self._closing:
            return
        self.show_tip(f"内部错误：{summary}（详情见 logs/app.log）", 10000)
        QMessageBox.critical(
            self,
            "内部错误",
            f"发生未预期的错误：\n{summary}\n\n"
            "程序已记录详细日志到 logs/app.log，建议保存当前日志后重启。",
        )

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        self._closing = True
        self.rate_timer.stop()
        self.port_timer.stop()
        self.reconnect_timer.stop()
        self._tip_timer.stop()
        self.port_scanner.shutdown()
        self.close_serial()
        self._flush_log()
        if self.log_file:
            with contextlib.suppress(OSError):
                self.log_file.close()
        self.save_settings()
        log.info("窗口关闭，配置已保存")
        event.accept()


def _shorten(text: str, limit: int) -> str:
    """窄栏单行显示的兜底截断（超长文本进 tooltip，不靠折行）。"""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _jsonable(data: dict) -> dict:
    """QSettings 值转 JSON 可序列化类型（QByteArray 等转为字符串）。"""
    out = {}
    for key, value in data.items():
        if value is None or isinstance(value, (bool, int, float, str)):
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [str(item) for item in value]
        else:
            out[key] = str(value)
    return out
