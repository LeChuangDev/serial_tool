# ====================== 导入依赖库 ======================
import sys
import os
import time
import datetime
import serial
import serial.tools.list_ports
from PyQt6.QtWidgets import *
from PyQt6.QtCore import QTimer, Qt, QDateTime, QSettings
from PyQt6.QtGui import QIcon, QColor, QTextCursor, QShortcut, QKeySequence, QAction

# ====================== 全局配置 ======================
APP_NAME = "Leeco‑Serial 樂創串口调试终端"
WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 660
WINDOW_ICON_NAME = "icon.png"
EXE_ICON_NAME = "app.ico"

if sys.platform == "win32":
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)


# ====================== 自定义下拉控件（点击即展开） ======================
class ClickComboBox(QComboBox):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("""
        QComboBox{background-color:#222730;color:#ffffff;border:1px solid #363c48;
            border-radius:4px;padding:6px 8px;font-size:14px;}
        QComboBox::drop-down{border-width:0px;}
        QComboBox:hover{border-color:#4086ff;}
        QComboBox:on{background-color:#2b323d;}
        QComboBox QAbstractItemView{background-color:#181c23;color:white;
            border:1px solid #363c48;selection-background-color:#2568ee;}
        """)
    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self.showPopup()


# ====================== 主程序窗口 ======================
class LecoSerial(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)

        # 图标
        icon_full_path = resource_path(WINDOW_ICON_NAME)
        if os.path.exists(icon_full_path):
            self.setWindowIcon(QIcon(icon_full_path))

        # ---------- 串口与状态变量 ----------
        self.ser = serial.Serial()
        self.recv_timer = QTimer()          # 接收轮询（稳定）
        self.recv_timer.timeout.connect(self.read_data)
        self.send_timer = QTimer()
        self.rate_timer = QTimer()
        self.port_timer = QTimer()          # 热插拔检测
        self.rx_count = 0
        self.tx_count = 0
        self.rx_rate = 0.0
        self.tx_rate = 0.0
        self._rate_t0 = time.time()
        self._rate_rx = 0
        self._rate_tx = 0
        self.recv_color = QColor("#3478d8")
        self.tx_color = QColor("#e6a23c")
        self.current_port = ""
        self.current_baud = 0
        self.current_bits = 8
        self.current_stop = 1
        self.current_parity = serial.PARITY_NONE
        self._allow_autoreconnect = False
        self._closing = False
        self._warned_read_error = False
        self.log_file = None
        self.line_buf = ""
        self._updating_history = False
        self.qs = QSettings("Leeco", "SerialTool")

        # ---------- 全局样式 ----------
        self.setStyleSheet("""
        QMainWindow{background-color:#0d0f14;}
        QWidget{color:#e2e6ed;font-family:Microsoft YaHei;}
        QLabel{font-size:14px;}
        QPushButton{background-color:#222730;color:#ffffff;border:none;
            border-radius:4px;padding:8px 16px;font-size:14px;}
        QPushButton:hover{background-color:#2c333f;}
        QPushButton#blueBtn{background-color:#2568ee;}
        QPushButton#blueBtn:hover{background-color:#3b7bff;}
        QPushButton#redBtn{background-color:#dd2e2e;}
        QPushButton#redBtn:hover{background-color:#f04444;}
        QLineEdit,QTextEdit{background-color:#181c23;border:1px solid #363c48;
            border-radius:4px;color:#fff;padding:6px;font-size:14px;}
        QCheckBox{spacing:6px;font-size:13px;}
        QCheckBox::indicator{width:16px;height:16px;border:1px solid #2a303b;
            border-left:1px solid #222830;border-radius:3px;background-color:#181c23;}
        QCheckBox::indicator:checked{background-color:#2568ee;}
        QRadioButton{spacing:6px;font-size:13px;}
        QMenuBar{background:#0d0f14;color:#e2e6ed;}
        QMenuBar::item:selected{background:#2568ee;}
        QMenu{background:#181c23;color:#e2e6ed;border:1px solid #222730;}
        QMenu::item:selected{background:#2568ee;}
        """)

        self._build_ui()
        self._build_menu()
        self._build_statusbar()
        self._build_shortcuts()

        # ---------- 信号绑定 ----------
        self.btn_link.clicked.connect(self.switch_serial)
        self.btn_refresh.clicked.connect(self.refresh_port_list)
        self.btn_clear.clicked.connect(self.recv_box.clear)
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

        self.refresh_port_list()
        self.load_settings()

        self.rate_timer.start(1000)
        self.port_timer.start(2000)
        self.on_auto_wrap(self.ck_wrap.isChecked())
        self.update_status()

    # =========================================================
    #  界面构建
    # =========================================================
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        # ---- 第1行：串口参数 + DTR/RTS + 复位 + 刷新/连接 ----
        top = QHBoxLayout(); top.setSpacing(10)
        g = QVBoxLayout(); g.setSpacing(3); g.addWidget(QLabel("串口"))
        self.cb_com = ClickComboBox(); g.addWidget(self.cb_com); top.addLayout(g)

        g = QVBoxLayout(); g.setSpacing(3); g.addWidget(QLabel("波特率"))
        self.cb_baud = ClickComboBox(); self.cb_baud.setEditable(True)
        self.cb_baud.addItems(["9600","115200","38400","57600","256000","512000"])
        self.cb_baud.setCurrentText("115200"); self.cb_baud.setMinimumWidth(110)
        g.addWidget(self.cb_baud); top.addLayout(g)

        g = QVBoxLayout(); g.setSpacing(3); g.addWidget(QLabel("数据位"))
        self.cb_data = ClickComboBox(); self.cb_data.addItems(["5","6","7","8"])
        self.cb_data.setCurrentText("8"); g.addWidget(self.cb_data); top.addLayout(g)

        g = QVBoxLayout(); g.setSpacing(3); g.addWidget(QLabel("停止位"))
        self.cb_stop = ClickComboBox(); self.cb_stop.addItems(["1","2"])
        self.cb_stop.setCurrentText("1"); g.addWidget(self.cb_stop); top.addLayout(g)

        g = QVBoxLayout(); g.setSpacing(3); g.addWidget(QLabel("校验位"))
        self.cb_check = ClickComboBox(); self.cb_check.addItems(["None","Even","Odd"])
        g.addWidget(self.cb_check); top.addLayout(g)

        g = QVBoxLayout(); g.setSpacing(3); g.addWidget(QLabel("信号线"))
        h = QHBoxLayout()
        self.ck_dtr = QCheckBox("DTR"); self.ck_rts = QCheckBox("RTS")
        self.ck_dtr.setEnabled(False); self.ck_rts.setEnabled(False)
        self.btn_rst = QPushButton("复位")
        self.btn_rst.setToolTip("RTS 低电平复位脉冲，用于 ESP32 复位刷新\n若 EN 接 DTR，请改 reset_target() 里的 rts 为 dtr")
        h.addWidget(self.ck_dtr); h.addWidget(self.ck_rts); h.addWidget(self.btn_rst)
        g.addLayout(h); top.addLayout(g)

        g = QVBoxLayout(); g.setSpacing(3)
        h = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新端口"); self.btn_link = QPushButton("连接")
        self.btn_link.setObjectName("blueBtn")
        h.addWidget(self.btn_refresh); h.addWidget(self.btn_link)
        g.addLayout(h); top.addLayout(g)
        top.addStretch()
        lay.addLayout(top)

        # ---- 第2行：显示选项 ----
        disp = QHBoxLayout(); disp.setSpacing(10)
        self.ck_hex = QCheckBox("HEX 显示")
        self.ck_time = QCheckBox("时间戳")
        self.ck_clear = QCheckBox("自动清除")
        self.ck_wrap = QCheckBox("自动换行"); self.ck_wrap.setChecked(True)
        self.ck_pause = QCheckBox("暂停显示")
        self.ck_log = QCheckBox("记录到文件")
        for w in (self.ck_hex, self.ck_time, self.ck_clear, self.ck_wrap,
                  self.ck_pause, self.ck_log):
            disp.addWidget(w)
        disp.addWidget(QLabel("断行:"))
        self.cb_break = ClickComboBox()
        self.cb_break.addItems(["按包", "按行(\\n)"])
        disp.addWidget(self.cb_break)
        disp.addStretch()
        lay.addLayout(disp)

        # ---- 第3行：发送选项 ----
        send = QHBoxLayout(); send.setSpacing(10)
        self.send_ascii = QRadioButton("ASCII"); self.send_ascii.setChecked(True)
        self.send_hex = QRadioButton("HEX")
        self.ck_newline = QCheckBox("发送新行\\r\\n")
        self.ck_echo = QCheckBox("TX 回显")
        self.ck_crc = QCheckBox("追加CRC16")
        send.addWidget(self.send_ascii); send.addWidget(self.send_hex)
        send.addWidget(self.ck_newline); send.addWidget(self.ck_echo); send.addWidget(self.ck_crc)
        send.addWidget(QLabel("编码:"))
        self.cb_encoding = ClickComboBox()
        self.cb_encoding.addItems(["UTF-8", "GBK", "ASCII"])
        send.addWidget(self.cb_encoding)
        send.addStretch()
        self.btn_savelog = QPushButton("保存日志")
        self.btn_clearcnt = QPushButton("清空计数")
        self.btn_clear = QPushButton("清空显示")
        send.addWidget(self.btn_savelog); send.addWidget(self.btn_clearcnt); send.addWidget(self.btn_clear)
        lay.addLayout(send)

        # ---- 主体：接收区 + 发送面板 ----
        main_h = QHBoxLayout(); main_h.setSpacing(12)
        self.recv_box = QTextEdit(); self.recv_box.setReadOnly(True)
        self.recv_box.setPlaceholderText("等待数据 ...")
        main_h.addWidget(self.recv_box, stretch=3)

        right = QVBoxLayout(); right.setSpacing(8)
        p = QHBoxLayout(); p.addWidget(QLabel("预设:"))
        self.cb_preset = ClickComboBox()
        self.cb_preset.addItems(["AT", "AT+RST", "AT+GMR", "AT+CWMODE=1"])
        p.addWidget(self.cb_preset, 1)
        self.btn_preset_send = QPushButton("发送"); self.btn_preset_add = QPushButton("添加")
        p.addWidget(self.btn_preset_send); p.addWidget(self.btn_preset_add)
        right.addLayout(p)

        p = QHBoxLayout(); p.addWidget(QLabel("历史:"))
        self.cb_history = ClickComboBox()
        p.addWidget(self.cb_history, 1)
        right.addLayout(p)

        self.send_box = QTextEdit()
        self.send_box.setPlaceholderText("输入要发送的内容 ...")
        right.addWidget(self.send_box, 1)

        p = QHBoxLayout(); p.addWidget(QLabel("周期ms:"))
        self.edit_period = QLineEdit("100"); self.edit_period.setMinimumWidth(60)
        p.addWidget(self.edit_period)
        self.btn_timer = QPushButton("定时发送")
        p.addWidget(self.btn_timer)
        right.addLayout(p)

        p = QHBoxLayout()
        self.btn_send = QPushButton("发送"); self.btn_send.setObjectName("blueBtn")
        self.btn_sendfile = QPushButton("发送文件")
        p.addWidget(self.btn_send); p.addWidget(self.btn_sendfile)
        right.addLayout(p)

        main_h.addLayout(right, stretch=1)
        lay.addLayout(main_h, stretch=1)

    def _build_menu(self):
        mb = self.menuBar()
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
        m_help = mb.addMenu("帮助")
        a6 = QAction("关于", self); a6.triggered.connect(self.show_about)
        m_help.addAction(a6)

    def _build_statusbar(self):
        self.lab_status = QLabel("RX:0  TX:0")
        self.statusBar().addPermanentWidget(self.lab_status)
        self.statusBar().showMessage("未连接")

    def _build_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Return"), self,
                  activated=lambda: self.do_send_text(self.send_box.toPlainText(), True))
        QShortcut(QKeySequence("Ctrl+Enter"), self,
                  activated=lambda: self.do_send_text(self.send_box.toPlainText(), True))

    # =========================================================
    #  串口连接 / 断开 / 切换
    # =========================================================
    def restyle_link_btn(self, obj):
        self.btn_link.setObjectName(obj)
        st = self.btn_link.style(); st.unpolish(self.btn_link); st.polish(self.btn_link)

    def close_serial(self):
        self.recv_timer.stop()
        self.send_timer.stop()
        self.btn_timer.setText("定时发送")
        try:
            if self.ser.is_open:
                try: self.ser.cancel_read()
                except Exception: pass
                try: self.ser.flush()
                except Exception: pass
                self.ser.close()
                print("【日志】串口已关闭")
        except Exception as e:
            print(f"【警告】关闭串口异常: {e}")
        self.restyle_link_btn("blueBtn")
        self.ck_dtr.setEnabled(False); self.ck_rts.setEnabled(False)
        self.statusBar().showMessage("未连接")
        self.append_recv_text("= 串口已断开 =", self.recv_color)
        self.update_status()

    def open_port(self, port, baud, bits, stop, par):
        if self.ser.is_open:
            self.close_serial()
            time.sleep(0.3)          # Windows 释放端口需要时间
        last_err = ""
        for attempt in range(5):
            try:
                self.ser = serial.Serial(port, baud, bytesize=bits, stopbits=stop,
                                         parity=par, timeout=0.05, dsrdtr=False)
                print(f"【日志】串口打开成功: {port} @ {baud}")
                break
            except Exception as e:
                last_err = str(e)
                time.sleep(0.2)
        else:
            QMessageBox.warning(self, "串口错误",
                                f"串口打开失败：{port}\n原因：{last_err}\n"
                                f"请确认端口未被占用且设备已插入")
            return False
        try:
            self.ser.reset_input_buffer(); self.ser.reset_output_buffer()
        except Exception: pass
        self.current_port = port; self.current_baud = baud
        self.current_bits = bits; self.current_stop = stop; self.current_parity = par
        self._warned_read_error = False
        self.recv_timer.start(30)   # 启动接收轮询
        self.restyle_link_btn("redBtn")
        self.ck_dtr.setEnabled(True); self.ck_rts.setEnabled(True)
        self.statusBar().showMessage(f"已连接 {port} @ {baud}")
        self.append_recv_text(f"= 串口已连接 {port} {baud} =", self.recv_color)
        self.update_status()
        return True

    def switch_serial(self):
        try:
            port = self.cb_com.currentText()
            baud = int(self.cb_baud.currentText())
            bits = int(self.cb_data.currentText())
            stop = int(self.cb_stop.currentText())
            par = {"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN,
                   "Odd": serial.PARITY_ODD}[self.cb_check.currentText()]
        except (ValueError, KeyError):
            QMessageBox.warning(self, "参数错误", "请检查串口参数是否正确填写")
            return
        if self.ser.is_open:
            changed = (port != self.current_port or baud != self.current_baud
                       or bits != self.current_bits or stop != self.current_stop
                       or par != self.current_parity)
            if not changed:
                self._allow_autoreconnect = False
                self.close_serial()
                return
            self._allow_autoreconnect = False
            self.close_serial()
            time.sleep(0.3)
        self.open_port(port, baud, bits, stop, par)

    # 热插拔检测：只提示，不自动断开（避免 ESP32 复位时设备短暂消失被误判）
    def check_ports(self):
        if self._closing:
            return
        try:
            now = set(p.device for p in serial.tools.list_ports.comports()
                      if "bluetooth" not in p.description.lower()
                      and "蓝牙" not in p.description.lower())
        except Exception:
            return
        if self.ser.is_open and self.current_port and self.current_port not in now:
            self.statusBar().showMessage("提示：串口已拔出，请重新连接", 5000)
            print("【日志】检测到串口拔出")

    def refresh_port_list(self):
        self.cb_com.clear()
        ports = serial.tools.list_ports.comports()
        usb = []
        for p in ports:
            d = p.description.lower()
            if "bluetooth" in d or "蓝牙" in d:
                continue
            usb.append(p.device); self.cb_com.addItem(p.device)
        if len(usb) == 1:
            self.cb_com.setCurrentText(usb[0])

    # =========================================================
    #  接收处理
    # =========================================================
    def read_data(self):
        if not self.ser.is_open:
            return
        try:
            buf = self.ser.read_all()
        except Exception as e:
            if not self._warned_read_error:
                self._warned_read_error = True
                print(f"【警告】读取串口异常: {e}")
                self.append_recv_text(f"!!! 串口读取异常: {e}", QColor("#f5222d"))
            return
        if not buf:
            return
        self.rx_count += len(buf)
        self.update_status()
        res = self.format_received(buf)
        if self.log_file and res:
            self.log_file.write(res + "\n"); self.log_file.flush()
        if not self.ck_pause.isChecked():
            if self.ck_clear.isChecked():
                self.recv_box.clear()
            if res:
                self.append_recv_text(res, self.recv_color)

    def format_received(self, buf):
        if self.ck_hex.isChecked():
            return " ".join(f"{i:02X}" for i in buf)
        text = buf.decode(self.encoding, errors="replace")
        if self.cb_break.currentText().startswith("按行"):
            self.line_buf += text
            if "\n" in self.line_buf:
                parts = self.line_buf.split("\n")
                self.line_buf = parts.pop()
                return "\n".join(p for p in parts if p != "")
            return ""
        return text

    def append_recv_text(self, text, color: QColor):
        if not text:
            return
        cursor = self.recv_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = cursor.charFormat(); fmt.setForeground(color); cursor.setCharFormat(fmt)
        if self.ck_time.isChecked():
            t = QDateTime.currentDateTime().toString("HH:mm:ss.zzz")
            cursor.insertText(f"[{t}] ")
        cursor.insertText(text.rstrip("\n") + "\n")
        self.recv_box.setTextCursor(cursor)
        self.recv_box.ensureCursorVisible()

    @property
    def encoding(self):
        return {"UTF-8": "utf-8", "GBK": "gbk", "ASCII": "latin-1"}[self.cb_encoding.currentText()]

    # =========================================================
    #  发送处理
    # =========================================================
    def parse_hex(self, text):
        t = (text.replace("0x", "").replace("0X", "").replace(",", " ")
             .replace("，", " ").replace("\n", " ").replace("\r", " "))
        parts = t.split()
        if not parts:
            return b""
        try:
            return bytes(int(x, 16) for x in parts)
        except ValueError:
            return None

    def crc16_modbus(self, data):
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        return crc

    def do_send_text(self, text, show_warn=True):
        if not self.ser.is_open:
            if show_warn:
                QMessageBox.warning(self, "提示", "请先打开串口")
            return False
        if self.send_hex.isChecked():
            data = self.parse_hex(text)
            if data is None:
                if show_warn:
                    QMessageBox.warning(self, "格式错误", "HEX 格式错误，请用空格分隔，如: 01 A2 FF")
                return False
        else:
            data = text.encode(self.encoding, errors="ignore")
        if self.ck_crc.isChecked() and data:
            data += self.crc16_modbus(data).to_bytes(2, "little")
        if self.ck_newline.isChecked():
            data += b"\r\n"
        if not data:
            return False
        self.ser.write(data)
        self.tx_count += len(data)
        self.add_history(text)
        self.update_status()
        if self.ck_echo.isChecked():
            extra = ""
            if self.ck_newline.isChecked(): extra += " [\\r\\n]"
            if self.ck_crc.isChecked(): extra += " [CRC16]"
            self.append_recv_text(f">>> TX{extra}: {text}", self.tx_color)
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
        while self.cb_history.count() > 20:
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
        if not self.ser.is_open:
            QMessageBox.warning(self, "提示", "请先打开串口"); return
        path, _ = QFileDialog.getOpenFileName(self, "选择要发送的文件", "", "所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"读取文件失败: {e}"); return
        if not data:
            return
        for i in range(0, len(data), 4096):
            self.ser.write(data[i:i + 4096])
            QApplication.processEvents()
        self.tx_count += len(data)
        self.update_status()
        self.append_recv_text(f">>> 文件已发送: {os.path.basename(path)} ({len(data)} 字节)", self.tx_color)

    # =========================================================
    #  复位 / 定时 / 速率 / 信号 / 日志
    # =========================================================
    def reset_target(self):
        """ESP32 复位：RTS 低电平脉冲（多数开发板 EN 经三极管接 RTS）
        若你的板子 EN 接的是 DTR，把下面所有 rts 换成 dtr。"""
        if not self.ser.is_open:
            QMessageBox.warning(self, "提示", "请先打开串口"); return
        try:
            self.append_recv_text("= 复位脉冲: RTS 低 150ms =", self.tx_color)
            self.ser.rts = True
            time.sleep(0.05)
            self.ser.rts = False
            time.sleep(0.15)
            self.ser.rts = True
        except Exception as e:
            print(f"【警告】复位失败: {e}")
            self.append_recv_text(f"!!! 复位失败: {e}", QColor("#f5222d"))

    def toggle_timer_send(self):
        if self.send_timer.isActive():
            self.send_timer.stop(); self.btn_timer.setText("定时发送"); return
        if not self.ser.is_open:
            QMessageBox.warning(self, "提示", "请先打开串口"); return
        try:
            t = int(self.edit_period.text())
        except ValueError:
            QMessageBox.warning(self, "参数错误", "请输入合法数字"); return
        if t <= 0:
            QMessageBox.warning(self, "参数错误", "周期必须大于 0"); return
        self.send_timer.start(t)
        self.btn_timer.setText("关闭定时")

    def update_rate(self):
        now = time.time()
        dt = now - self._rate_t0
        if dt > 0:
            self.rx_rate = (self.rx_count - self._rate_rx) / dt
            self.tx_rate = (self.tx_count - self._rate_tx) / dt
        self._rate_t0 = now; self._rate_rx = self.rx_count; self._rate_tx = self.tx_count
        self.update_status()

    def update_status(self):
        self.lab_status.setText(f"RX:{self.rx_count}  TX:{self.tx_count}"
                                f"  收:{self.rx_rate:.0f} B/s  发:{self.tx_rate:.0f} B/s")

    def clear_count(self):
        self.rx_count = 0; self.tx_count = 0
        self.rx_rate = 0; self.tx_rate = 0
        self.update_status()

    def on_auto_wrap(self, checked):
        self.recv_box.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth
                                      if checked else QTextEdit.LineWrapMode.NoWrap)

    def on_dtr_toggled(self, checked):
        if self.ser.is_open:
            try: self.ser.dtr = bool(checked)
            except Exception as e: print(f"DTR 设置失败: {e}")

    def on_rts_toggled(self, checked):
        if self.ser.is_open:
            try: self.ser.rts = bool(checked)
            except Exception as e: print(f"RTS 设置失败: {e}")

    def on_log_toggled(self, checked):
        if checked:
            try:
                name = "log_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"
                self.log_file = open(name, "a", encoding="utf-8")
                self.append_recv_text(f">>> 开始记录: {name}", self.tx_color)
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法创建日志文件: {e}")
                self.ck_log.blockSignals(True); self.ck_log.setChecked(False); self.ck_log.blockSignals(False)
                self.log_file = None
        else:
            if self.log_file:
                self.log_file.close(); self.log_file = None
                self.append_recv_text(">>> 停止记录", self.tx_color)

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存接收日志", "recv_log.txt", "文本文件 (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.recv_box.toPlainText())
            QMessageBox.information(self, "完成", f"已保存到:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败: {e}")

    def show_about(self):
        QMessageBox.about(self, "关于", f"{APP_NAME}\n版本 2.0\n\n"
                          "串口调试助手：HEX/ASCII 收发、时间戳、定时发送、发送文件、\n"
                          "CRC16、DTR/RTS、ESP32 复位、预设命令、多编码、日志记录、\n"
                          "设置记忆、速率显示\n\n快捷键：Ctrl+Enter 发送 · F5 刷新端口")

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
        for name, w in [("ck_hex", self.ck_hex), ("ck_time", self.ck_time),
                        ("ck_clear", self.ck_clear), ("ck_wrap", self.ck_wrap),
                        ("ck_pause", self.ck_pause), ("ck_log", self.ck_log),
                        ("ck_newline", self.ck_newline), ("ck_echo", self.ck_echo),
                        ("ck_crc", self.ck_crc)]:
            w.setChecked(self.qs.value(name, False, type=bool))
        presets = self.qs.value("presets", [])
        if not isinstance(presets, list):
            presets = [presets]
        for it in presets:
            if it and self.cb_preset.findText(it) < 0:
                self.cb_preset.addItem(it)

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
                        ("ck_pause", self.ck_pause), ("ck_log", self.ck_log),
                        ("ck_newline", self.ck_newline), ("ck_echo", self.ck_echo),
                        ("ck_crc", self.ck_crc)]:
            self.qs.setValue(name, w.isChecked())
        self.qs.setValue("presets",
                         [self.cb_preset.itemText(i) for i in range(self.cb_preset.count())])

    def closeEvent(self, e):
        self._closing = True
        self.close_serial()
        self.save_settings()
        if self.log_file:
            try: self.log_file.close()
            except Exception: pass
        e.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = LecoSerial()
    win.show()
    sys.exit(app.exec())