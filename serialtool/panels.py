# ====================== 界面面板 ======================
"""声明式面板构建：每个面板由 ``build()`` 返回一个控件集合。

主窗口通过 ``_bind_panel()`` 把集合里的控件挂到同名属性上（Qt ``setupUi`` 惯例），
因此编排逻辑仍可直接写 ``self.recv_box``，而布局代码集中在本模块。

布局分工（v3）：串口参数上移到顶部连接栏，左侧窄栏只放"会话 / 显示 / 统计"
三类只读或低频信息，右侧接收区占满主视野，发送区改为三页签
（单条 / 多条 / 文件），避免多条发送表格长期挤压输入框。
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import config
from .recv_pipeline import SPLIT_MODE_MAP
from .widgets import ClickComboBox, MiniRateChart, RecvPlainTextEdit, WaveChart

__all__ = [
    "ConnectionBar",
    "SessionCard",
    "DisplayCard",
    "StatsCard",
    "RecvPanel",
    "SendPanel",
    "make_card",
    "field_label",
]


def make_card(title: str) -> tuple[QFrame, QVBoxLayout]:
    """创建统一风格的卡片容器（渐变底 + 标题）。"""
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.setSpacing(9)
    label = QLabel(title)
    label.setObjectName("cardTitle")
    layout.addWidget(label)
    return frame, layout


def field_label(text: str) -> QLabel:
    """表单字段名（小号弱化色，避免与内容抢注意力）。"""
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


def _combo(items: list[str], current: str | None = None, width: int | None = None) -> ClickComboBox:
    """创建下拉框。

    ``width`` 是**下限**而不是固定值：固定宽度会让长选项（``XON/XOFF``、
    ``1500000``、``Space``）在选中后被省略号截断，用户看不到自己选了什么。
    这里取 ``max(指定下限, 内容所需宽度)``，既保留版面节奏，又保证选项可读。
    """
    box = ClickComboBox()
    box.addItems(items)
    if current is not None:
        box.setCurrentText(current)
    # sizeHint 已含箭头与内边距，按最长条目自适应
    box.setMinimumWidth(max(width or 0, box.sizeHint().width()))
    return box


@dataclass(slots=True)
class ConnectionBar:
    """顶部连接栏：端口 + 线路参数 + 连接/自动重连 + DTR/RTS/复位。"""

    frame: QFrame
    cb_com: ClickComboBox
    btn_refresh: QPushButton
    cb_baud: ClickComboBox
    cb_data: ClickComboBox
    cb_stop: ClickComboBox
    cb_check: ClickComboBox
    cb_flow: ClickComboBox
    btn_autoreconnect: QPushButton
    btn_link: QPushButton
    ck_dtr: QPushButton
    ck_rts: QPushButton
    btn_rst: QPushButton

    @classmethod
    def build(cls) -> ConnectionBar:
        frame = QFrame()
        frame.setObjectName("card")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        # ---- 第一行：视图级操作（选口 → 刷新 → 自动重连 → 连接） ----
        cb_com = ClickComboBox()
        cb_com.setMinimumWidth(240)
        cb_com.setToolTip("端口列表（名称 · 设备描述），热插拔自动刷新")
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("tool")
        btn_refresh.setToolTip("重新枚举串口（F5）")
        btn_autoreconnect = QPushButton("自动重连")
        btn_autoreconnect.setObjectName("toggle")
        btn_autoreconnect.setCheckable(True)
        btn_autoreconnect.setToolTip(
            "设备掉线后自动重新连接：按 VID:PID/序列号识别同一块板子，COM 号变化也能找回"
        )
        btn_link = QPushButton("连接")
        btn_link.setObjectName("primary")
        btn_link.setMinimumWidth(96)
        btn_link.setToolTip("连接 / 断开（Ctrl+D）")

        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("串口配置")
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        head.addSpacing(4)
        head.addWidget(field_label("端口"))
        head.addWidget(cb_com, 1)
        head.addWidget(btn_refresh)
        head.addWidget(btn_autoreconnect)
        head.addWidget(btn_link)
        outer.addLayout(head)

        sep = QFrame()
        sep.setObjectName("hsep")
        outer.addWidget(sep)

        # ---- 第二行：线路参数 + 信号线 ----
        cb_baud = _combo(config.BAUD_RATES, "115200", 104)
        cb_baud.setEditable(True)
        cb_baud.setToolTip("可直接输入自定义波特率（50 ~ 12000000）")
        cb_data = _combo(["5", "6", "7", "8"], "8", 60)
        cb_stop = _combo(["1", "2"], "1", 60)
        cb_check = _combo(list(config.PARITY_MAP.keys()), None, 88)
        cb_flow = _combo(list(config.FLOW_MAP.keys()), None, 96)
        cb_flow.setToolTip("硬件流控 RTS/CTS 或软件流控 XON/XOFF")

        ck_dtr = _signal_button("DTR", "DTR 信号线开关（连接后可用）")
        ck_rts = _signal_button("RTS", "RTS 信号线开关（连接后可用）")
        btn_rst = QPushButton("复位")
        btn_rst.setObjectName("tool")
        btn_rst.setToolTip("RTS 低电平 150ms 复位脉冲（ESP32/STM32 开发板通用）")

        params = QHBoxLayout()
        params.setSpacing(6)
        for text, widget in (
            ("波特率", cb_baud),
            ("数据位", cb_data),
            ("停止位", cb_stop),
            ("校验位", cb_check),
            ("流控", cb_flow),
        ):
            params.addWidget(field_label(text))
            params.addWidget(widget)
            params.addSpacing(6)
        params.addStretch()
        params.addWidget(ck_dtr)
        params.addWidget(ck_rts)
        params.addWidget(btn_rst)
        outer.addLayout(params)

        return cls(
            frame,
            cb_com,
            btn_refresh,
            cb_baud,
            cb_data,
            cb_stop,
            cb_check,
            cb_flow,
            btn_autoreconnect,
            btn_link,
            ck_dtr,
            ck_rts,
            btn_rst,
        )


def _signal_button(text: str, tooltip: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("toggle")
    button.setCheckable(True)
    button.setEnabled(False)  # 连接后才可用
    button.setMinimumWidth(52)
    button.setToolTip(tooltip)
    return button


@dataclass(slots=True)
class SessionCard:
    """会话信息卡片：当前设备、USB 身份、驱动、本次会话状态。"""

    frame: QFrame
    lab_port: QLabel
    lab_detail: QLabel
    lab_driver: QLabel
    lab_session: QLabel

    @classmethod
    def build(cls) -> SessionCard:
        frame, layout = make_card("会话")
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(1, 1)

        lab_port = QLabel("未选择")
        lab_port.setObjectName("mono")
        # 窄栏里折行的文本会被卡片高度裁掉，这里统一单行 + 省略号
        lab_detail = QLabel("—")
        lab_detail.setObjectName("hint")
        lab_detail.setWordWrap(False)
        lab_driver = QLabel("—")
        lab_driver.setObjectName("hint")
        lab_driver.setWordWrap(False)
        lab_session = QLabel("空闲")
        lab_session.setObjectName("hint")

        for row, (text, widget) in enumerate(
            (
                ("设备", lab_port),
                ("身份", lab_detail),
                ("芯片", lab_driver),
                ("会话", lab_session),
            )
        ):
            grid.addWidget(field_label(text), row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, row, 1)
        layout.addLayout(grid)
        layout.addStretch()
        return cls(frame, lab_port, lab_detail, lab_driver, lab_session)


@dataclass(slots=True)
class DisplayCard:
    """显示选项卡片：HEX/时间戳/断行/换行/日志/高亮。"""

    frame: QFrame
    ck_hex: QCheckBox
    ck_time: QCheckBox
    cb_ts: ClickComboBox
    cb_break: ClickComboBox
    ck_wrap: QCheckBox
    ck_clear: QCheckBox
    ck_log: QCheckBox
    ck_autolog: QCheckBox
    ck_highlight: QCheckBox
    btn_rules: QPushButton

    @classmethod
    def build(cls) -> DisplayCard:
        frame, layout = make_card("显示")

        ck_hex = QCheckBox("HEX 显示")
        ck_hex.setToolTip("按字节显示（空格分隔大写 HEX）")
        ck_time = QCheckBox("时间戳")
        ck_time.setToolTip("每行前加时间戳")
        ck_wrap = QCheckBox("自动换行")
        ck_wrap.setChecked(True)
        ck_wrap.setToolTip("关闭后超长行横向滚动，便于对齐列数据")
        ck_clear = QCheckBox("自动清除")
        ck_clear.setToolTip("每收到一批数据前清屏，只看最新输出")
        ck_log = QCheckBox("记录到文件")
        ck_autolog = QCheckBox("连接后自动记录")
        ck_autolog.setToolTip("连接成功即开始写 logs/serial_YYYYMMDD.log，掉线重连后继续")
        ck_highlight = QCheckBox("关键字高亮")
        ck_highlight.setChecked(True)
        ck_highlight.setToolTip("按规则给 error/ready 等关键字着色")

        two = QGridLayout()
        two.setSpacing(6)
        two.addWidget(ck_hex, 0, 0)
        two.addWidget(ck_time, 0, 1)
        two.addWidget(ck_wrap, 1, 0)
        two.addWidget(ck_clear, 1, 1)
        two.addWidget(ck_log, 2, 0)
        two.addWidget(ck_autolog, 2, 1)
        layout.addLayout(two)

        cb_ts = _combo(list(config.TIMESTAMP_MODES.keys()))
        ts_row = QHBoxLayout()
        ts_row.setSpacing(6)
        ts_row.addWidget(field_label("格式"))
        ts_row.addWidget(cb_ts, 1)
        layout.addLayout(ts_row)

        cb_break = _combo(list(SPLIT_MODE_MAP.keys()))
        br_row = QHBoxLayout()
        br_row.setSpacing(6)
        br_row.addWidget(field_label("断行"))
        br_row.addWidget(cb_break, 1)
        layout.addLayout(br_row)

        btn_rules = QPushButton("高亮规则...")
        btn_rules.setObjectName("small")
        btn_rules.setToolTip("编辑关键字着色规则（支持正则）")
        hl_row = QHBoxLayout()
        hl_row.setSpacing(6)
        hl_row.addWidget(ck_highlight)
        hl_row.addStretch()
        hl_row.addWidget(btn_rules)
        layout.addLayout(hl_row)
        layout.addStretch()

        return cls(
            frame,
            ck_hex,
            ck_time,
            cb_ts,
            cb_break,
            ck_wrap,
            ck_clear,
            ck_log,
            ck_autolog,
            ck_highlight,
            btn_rules,
        )


@dataclass(slots=True)
class StatsCard:
    """统计卡片：累计收发、帧数、错误、峰值速率、会话时长。"""

    frame: QFrame
    lab_rx: QLabel
    lab_tx: QLabel
    lab_frames: QLabel
    lab_errors: QLabel
    lab_peak: QLabel
    lab_duration: QLabel
    btn_clearcnt: QPushButton

    @classmethod
    def build(cls) -> StatsCard:
        frame, layout = make_card("统计")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        def tile(caption: str, value: str = "0") -> tuple[QLabel, QLabel]:
            value_label = QLabel(value)
            value_label.setObjectName("statValue")
            return field_label(caption), value_label

        lab_rx_cap, lab_rx = tile("接收")
        lab_tx_cap, lab_tx = tile("发送")
        lab_frames_cap, lab_frames = tile("帧数")
        lab_errors_cap, lab_errors = tile("错误")
        lab_peak_cap, lab_peak = tile("峰值")
        lab_duration_cap, lab_duration = tile("时长", "00:00:00")

        for col, (caption, value) in enumerate(
            (
                (lab_rx_cap, lab_rx),
                (lab_tx_cap, lab_tx),
                (lab_frames_cap, lab_frames),
                (lab_errors_cap, lab_errors),
                (lab_peak_cap, lab_peak),
                (lab_duration_cap, lab_duration),
            )
        ):
            grid.addWidget(caption, col // 2 * 2, col % 2)
            grid.addWidget(value, col // 2 * 2 + 1, col % 2)
        layout.addLayout(grid)

        btn_clearcnt = QPushButton("清空计数")
        btn_clearcnt.setObjectName("small")
        btn_clearcnt.setToolTip("清零收发统计与速率曲线（不影响接收区内容）")
        layout.addWidget(btn_clearcnt)
        layout.addStretch()
        return cls(
            frame,
            lab_rx,
            lab_tx,
            lab_frames,
            lab_errors,
            lab_peak,
            lab_duration,
            btn_clearcnt,
        )


def _tool_button(text: str, tooltip: str = "", *, checkable: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("tool")
    button.setCheckable(checkable)
    if tooltip:
        button.setToolTip(tooltip)
    return button


@dataclass(slots=True)
class RecvPanel:
    """接收区卡片：标题工具条 + 实时波形 + 控制台 + 查找条。"""

    frame: QFrame
    lab_recv_title: QLabel
    lab_recv_stat: QLabel
    rate_chart: MiniRateChart
    btn_wave: QPushButton
    btn_pause: QPushButton
    btn_find: QPushButton
    btn_recv_clear: QPushButton
    wave_chart: WaveChart
    recv_box: RecvPlainTextEdit
    find_bar: QWidget
    find_edit: QLineEdit
    btn_find_next: QPushButton
    btn_find_close: QPushButton

    @classmethod
    def build(cls) -> RecvPanel:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        lab_recv_title = QLabel("接收")
        lab_recv_title.setObjectName("sectionTitle")
        lab_recv_stat = QLabel("收 0 B  发 0 B")
        lab_recv_stat.setObjectName("hint")
        rate_chart = MiniRateChart()
        btn_wave = _tool_button("波形", "开关接收字节实时波形", checkable=True)
        btn_pause = _tool_button("暂停", "冻结显示（数据仍计数，恢复后继续渲染）", checkable=True)
        btn_find = _tool_button("查找", "在接收区查找（Ctrl+F）")
        btn_recv_clear = _tool_button("清空", "清空接收区（Ctrl+L）")

        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 12, 8)
        head.setSpacing(8)
        head.addWidget(lab_recv_title)
        head.addWidget(lab_recv_stat)
        head.addStretch()
        head.addWidget(rate_chart)
        for button in (btn_wave, btn_pause, btn_find, btn_recv_clear):
            head.addWidget(button)
        layout.addLayout(head)

        separator = QFrame()
        separator.setObjectName("hsep")
        layout.addWidget(separator)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(10, 8, 10, 10)
        body_layout.setSpacing(6)

        wave_chart = WaveChart()
        wave_chart.setVisible(False)
        body_layout.addWidget(wave_chart)

        recv_box = RecvPlainTextEdit()
        recv_box.setReadOnly(True)
        recv_box.setMaximumBlockCount(config.MAX_BLOCKS)
        recv_box.setPlaceholderText("等待数据 ...（Ctrl+F 查找 · Ctrl+滚轮 调整字号）")
        recv_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        body_layout.addWidget(recv_box, 1)

        find_bar = QWidget()
        find_layout = QHBoxLayout(find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        find_layout.setSpacing(6)
        find_edit = QLineEdit()
        find_edit.setPlaceholderText("查找 ...")
        btn_find_next = QPushButton("下一个")
        btn_find_next.setObjectName("small")
        btn_find_close = QPushButton("关闭")
        btn_find_close.setObjectName("small")
        find_layout.addWidget(find_edit, 1)
        find_layout.addWidget(btn_find_next)
        find_layout.addWidget(btn_find_close)
        find_bar.setVisible(False)
        body_layout.addWidget(find_bar)
        layout.addWidget(body, 1)

        return cls(
            frame,
            lab_recv_title,
            lab_recv_stat,
            rate_chart,
            btn_wave,
            btn_pause,
            btn_find,
            btn_recv_clear,
            wave_chart,
            recv_box,
            find_bar,
            find_edit,
            btn_find_next,
            btn_find_close,
        )


@dataclass(slots=True)
class SendPanel:
    """发送区卡片：三页签（单条 / 多条 / 文件）。

    三页签而不是"一个输入框 + 一张常驻表格"：多条发送表格在多数场景只是
    偶尔用一次，常驻会长期挤压单条发送的输入与按钮区。
    """

    frame: QFrame
    tabs: QTabWidget
    lab_send_title: QLabel
    # --- 单条 ---
    send_ascii: QRadioButton
    send_hex: QRadioButton
    cb_eol: ClickComboBox
    cb_encoding: ClickComboBox
    ck_echo: QCheckBox
    ck_crc: QCheckBox
    send_box: QPlainTextEdit
    cb_preset: ClickComboBox
    btn_preset_send: QPushButton
    btn_preset_add: QPushButton
    cb_history: ClickComboBox
    btn_send: QPushButton
    btn_timer: QPushButton
    edit_period: QLineEdit
    # --- 多条 ---
    ms_table: QTableWidget
    btn_ms_add: QPushButton
    btn_ms_del: QPushButton
    btn_ms_clear: QPushButton
    btn_ms_send: QPushButton
    btn_ms_loop: QPushButton
    # --- 文件 ---
    btn_file_pick: QPushButton
    lab_file_path: QLabel
    edit_pace: QLineEdit
    file_progress: QProgressBar
    btn_sendfile: QPushButton

    @classmethod
    def build(cls) -> SendPanel:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        lab_send_title = QLabel("发送")
        lab_send_title.setObjectName("sectionTitle")
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 12, 8)
        head.addWidget(lab_send_title)
        head.addStretch()
        layout.addLayout(head)

        separator = QFrame()
        separator.setObjectName("hsep")
        layout.addWidget(separator)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)

        # ---------------- 页签 1：单条发送 ----------------
        send_ascii = QRadioButton("ASCII")
        send_ascii.setChecked(True)
        send_hex = QRadioButton("HEX")
        send_hex.setToolTip("按字节发送，如 01 03 00 00 00 02")

        cb_eol = _combo(list(config.LINE_ENDINGS.keys()), width=88)
        cb_eol.setToolTip("自动追加的行尾（二进制协议请选「无」）")
        cb_encoding = _combo(list(config.ENCODING_MAP.keys()), width=100)
        ck_echo = QCheckBox("TX 回显")
        ck_crc = QCheckBox("追加CRC16")
        ck_crc.setToolTip("在数据尾部追加 Modbus CRC16（低字节在前）")

        opts = QHBoxLayout()
        opts.setSpacing(8)
        opts.addWidget(send_ascii)
        opts.addWidget(send_hex)
        opts.addSpacing(8)
        opts.addWidget(field_label("行尾"))
        opts.addWidget(cb_eol)
        opts.addSpacing(6)
        opts.addWidget(field_label("编码"))
        opts.addWidget(cb_encoding)
        opts.addSpacing(6)
        opts.addWidget(ck_echo)
        opts.addWidget(ck_crc)
        opts.addStretch()

        send_box = QPlainTextEdit()
        send_box.setPlaceholderText("输入要发送的内容 ...（Ctrl+Enter 发送 · ↑/↓ 浏览历史）")
        send_box.setMinimumHeight(64)

        cb_preset = _combo(list(config.PRESET_DEFAULT))
        btn_preset_send = QPushButton("发送")
        btn_preset_send.setObjectName("small")
        btn_preset_send.setToolTip("发送选中的预设指令")
        btn_preset_add = QPushButton("保存")
        btn_preset_add.setObjectName("small")
        btn_preset_add.setToolTip("把当前输入保存为预设")
        cb_history = ClickComboBox()
        cb_history.setToolTip(f"最近发送过的内容（最多 {config.HISTORY_MAX} 条）")

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        preset_row.addWidget(field_label("预设"))
        preset_row.addWidget(cb_preset, 2)
        preset_row.addWidget(btn_preset_send)
        preset_row.addWidget(btn_preset_add)
        preset_row.addSpacing(6)
        preset_row.addWidget(field_label("历史"))
        preset_row.addWidget(cb_history, 2)

        btn_send = QPushButton("发送")
        btn_send.setObjectName("primary")
        btn_send.setMinimumWidth(88)
        btn_timer = _tool_button("定时发送", "按周期重复发送输入框内容", checkable=True)
        edit_period = QLineEdit("100")
        edit_period.setFixedWidth(64)
        edit_period.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit_period.setToolTip("定时发送周期（毫秒）")

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(btn_send)
        actions.addSpacing(6)
        actions.addWidget(btn_timer)
        actions.addWidget(field_label("周期ms"))
        actions.addWidget(edit_period)
        actions.addStretch()

        page_single = QWidget()
        single_layout = QVBoxLayout(page_single)
        single_layout.setContentsMargins(10, 8, 10, 10)
        single_layout.setSpacing(6)
        single_layout.addLayout(opts)
        single_layout.addWidget(send_box, 1)
        single_layout.addLayout(preset_row)
        single_layout.addLayout(actions)
        tabs.addTab(page_single, "单条发送")

        # ---------------- 页签 2：多条发送 ----------------
        ms_table = QTableWidget(0, 3)
        ms_table.setHorizontalHeaderLabels(["使用", "指令内容", "延迟ms"])
        ms_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        ms_table.setColumnWidth(0, 50)
        ms_table.setColumnWidth(2, 70)
        ms_table.verticalHeader().setVisible(False)
        ms_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        ms_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        ms_table.setMinimumHeight(80)

        btn_ms_add = QPushButton("添加当前输入")
        btn_ms_add.setObjectName("small")
        btn_ms_add.setToolTip("把单条发送页签里的内容加入列表")
        btn_ms_del = QPushButton("删除选中")
        btn_ms_del.setObjectName("small")
        btn_ms_clear = QPushButton("清空")
        btn_ms_clear.setObjectName("small")
        btn_ms_send = QPushButton("发送选中")
        btn_ms_send.setObjectName("small")
        btn_ms_loop = QPushButton("循环发送")
        btn_ms_loop.setObjectName("small")
        btn_ms_loop.setCheckable(True)
        btn_ms_loop.setToolTip("按行内延迟循环发送所有勾选指令，再次点击停止")

        ms_row = QHBoxLayout()
        ms_row.setSpacing(6)
        for button in (btn_ms_add, btn_ms_del, btn_ms_clear):
            ms_row.addWidget(button)
        ms_row.addStretch()
        ms_row.addWidget(btn_ms_send)
        ms_row.addWidget(btn_ms_loop)

        page_multi = QWidget()
        multi_layout = QVBoxLayout(page_multi)
        multi_layout.setContentsMargins(10, 8, 10, 10)
        multi_layout.setSpacing(6)
        multi_layout.addWidget(ms_table, 1)
        multi_layout.addLayout(ms_row)
        tabs.addTab(page_multi, "多条发送")

        # ---------------- 页签 3：文件发送 ----------------
        btn_file_pick = QPushButton("选择文件...")
        lab_file_path = QLabel("未选择文件")
        lab_file_path.setObjectName("hint")
        lab_file_path.setWordWrap(True)
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        pick_row.addWidget(btn_file_pick)
        pick_row.addWidget(lab_file_path, 1)

        edit_pace = QLineEdit("0")
        edit_pace.setFixedWidth(64)
        edit_pace.setAlignment(Qt.AlignmentFlag.AlignRight)
        edit_pace.setToolTip("每 4KB 分块之间的间隔（毫秒）。0 = 全速；慢速 MCU 建议 5~50ms")
        pace_row = QHBoxLayout()
        pace_row.setSpacing(8)
        pace_row.addWidget(field_label("分块间隔ms"))
        pace_row.addWidget(edit_pace)
        pace_hint = QLabel("间隔过大只会更慢，不会丢数据；0 表示按写队列水位全速灌入")
        pace_hint.setObjectName("hint")
        pace_row.addWidget(pace_hint, 1)

        file_progress = QProgressBar()
        file_progress.setRange(0, 100)
        file_progress.setValue(0)
        file_progress.setTextVisible(True)
        file_progress.setFormat("%p%")

        btn_sendfile = QPushButton("发送文件")
        btn_sendfile.setObjectName("primary")
        btn_sendfile.setMinimumWidth(110)
        send_row = QHBoxLayout()
        send_row.addWidget(btn_sendfile)
        send_row.addStretch()

        page_file = QWidget()
        file_layout = QVBoxLayout(page_file)
        file_layout.setContentsMargins(10, 8, 10, 10)
        file_layout.setSpacing(8)
        file_layout.addLayout(pick_row)
        file_layout.addLayout(pace_row)
        file_layout.addWidget(file_progress)
        file_layout.addLayout(send_row)
        file_layout.addStretch()
        tabs.addTab(page_file, "文件发送")

        layout.addWidget(tabs, 1)

        return cls(
            frame,
            tabs,
            lab_send_title,
            send_ascii,
            send_hex,
            cb_eol,
            cb_encoding,
            ck_echo,
            ck_crc,
            send_box,
            cb_preset,
            btn_preset_send,
            btn_preset_add,
            cb_history,
            btn_send,
            btn_timer,
            edit_period,
            ms_table,
            btn_ms_add,
            btn_ms_del,
            btn_ms_clear,
            btn_ms_send,
            btn_ms_loop,
            btn_file_pick,
            lab_file_path,
            edit_pace,
            file_progress,
            btn_sendfile,
        )
