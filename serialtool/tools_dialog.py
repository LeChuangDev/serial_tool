# ====================== 工具对话框：校验和 / HEX-ASCII 转换 ======================
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QRadioButton, QCheckBox, QTabWidget, QMessageBox,
)

from . import styles
from . import utils


class ToolsDialog(QDialog):
    def __init__(self, parent=None, tab=0):
        super().__init__(parent)
        self._main = parent
        self.setWindowTitle("工具")
        self.resize(580, 460)
        self.setStyleSheet(styles.GLOBAL_QSS)

        tabs = QTabWidget()
        tabs.addTab(self._make_checksum_tab(), "校验和")
        tabs.addTab(self._make_convert_tab(), "HEX / ASCII 转换")
        tabs.setCurrentIndex(tab)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.addWidget(tabs)

        self._last_results = None   # [(名称, 数值)]，供"追加到发送区"

    # =========================================================
    #  Tab1 校验和计算
    # =========================================================
    def _make_checksum_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        v.addWidget(QLabel("输入内容（默认取发送区文本）："))
        self.cs_input = QPlainTextEdit()
        if self._main is not None:
            self.cs_input.setPlainText(self._main.send_box.toPlainText())
        v.addWidget(self.cs_input, 1)

        row = QHBoxLayout(); row.setSpacing(12)
        self.cs_ascii = QRadioButton("ASCII / 文本"); self.cs_ascii.setChecked(True)
        self.cs_hex = QRadioButton("HEX")
        row.addWidget(self.cs_ascii); row.addWidget(self.cs_hex); row.addStretch()
        v.addLayout(row)

        row2 = QHBoxLayout(); row2.setSpacing(12)
        self.cs_crc8 = QCheckBox("CRC8"); self.cs_crc8.setChecked(True)
        self.cs_crc16 = QCheckBox("CRC16-Modbus"); self.cs_crc16.setChecked(True)
        self.cs_xor = QCheckBox("异或")
        self.cs_sum = QCheckBox("累加和")
        row2.addWidget(self.cs_crc8); row2.addWidget(self.cs_crc16)
        row2.addWidget(self.cs_xor); row2.addWidget(self.cs_sum); row2.addStretch()
        v.addLayout(row2)

        h = QHBoxLayout(); h.setSpacing(8)
        btn_calc = QPushButton("计算"); btn_calc.setObjectName("primary")
        btn_calc.clicked.connect(self.calc_checksum)
        self.btn_append = QPushButton("追加到发送区"); self.btn_append.setObjectName("small")
        self.btn_append.clicked.connect(self.append_result)
        h.addWidget(btn_calc); h.addWidget(self.btn_append); h.addStretch()
        v.addLayout(h)

        self.cs_result = QPlainTextEdit()
        self.cs_result.setReadOnly(True)
        self.cs_result.setPlaceholderText("校验结果将显示在这里 …")
        v.addWidget(self.cs_result, 1)
        return w

    def calc_checksum(self):
        text = self.cs_input.toPlainText()
        if self.cs_hex.isChecked():
            data, err = utils.parse_hex(text)
            if data is None:
                QMessageBox.warning(self, "HEX 格式错误", err)
                return
        else:
            enc = "utf-8"
            if self._main is not None:
                enc = self._main.encoding
            data = text.encode(enc, errors="ignore")
        if not data:
            QMessageBox.information(self, "提示", "输入内容为空")
            return

        lines = [f"数据长度: {len(data)} 字节"]
        results = []
        if self.cs_crc8.isChecked():
            v = utils.crc8(data)
            lines.append(f"CRC8          = 0x{v:02X}  ({v})")
            results.append(("CRC8", v))
        if self.cs_crc16.isChecked():
            v = utils.crc16_modbus(data)
            lines.append(f"CRC16-Modbus  = 0x{v:04X}"
                         f"   （发送低字节在前: {v & 0xFF:02X} {v >> 8:02X}）")
            results.append(("CRC16", v))
        if self.cs_xor.isChecked():
            v = utils.xor_sum(data)
            lines.append(f"异或          = 0x{v:02X}  ({v})")
            results.append(("XOR", v))
        if self.cs_sum.isChecked():
            v = utils.byte_sum(data)
            lines.append(f"累加和        = 0x{v:02X}  ({v})")
            results.append(("SUM", v))
        self._last_results = results
        self.cs_result.setPlainText("\n".join(lines))

    def append_result(self):
        if not self._last_results:
            QMessageBox.information(self, "提示", "请先点击「计算」")
            return
        if self._main is None:
            return
        parts = []
        for name, v in self._last_results:
            parts.append(f"{v & 0xFF:02X}" if name != "CRC16"
                         else f"{v & 0xFF:02X} {v >> 8:02X}")
        cur = self._main.send_box.toPlainText()
        new = (cur.rstrip() + " " if cur.strip() else "") + " ".join(parts)
        self._main.send_box.setPlainText(new)

    # =========================================================
    #  Tab2 HEX ⇄ ASCII 转换
    # =========================================================
    def _make_convert_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        v.addWidget(QLabel("HEX（空格分隔，如 48 65 6C 6C 6F）："))
        self.cv_hex = QPlainTextEdit()
        v.addWidget(self.cv_hex, 1)

        h = QHBoxLayout(); h.setSpacing(8)
        b1 = QPushButton("HEX → ASCII"); b1.setObjectName("primary")
        b1.clicked.connect(self.hex_to_ascii)
        b2 = QPushButton("ASCII → HEX"); b2.clicked.connect(self.ascii_to_hex)
        b3 = QPushButton("清空"); b3.setObjectName("tool")
        h.addWidget(b1); h.addWidget(b2); h.addStretch(); h.addWidget(b3)
        v.addLayout(h)

        v.addWidget(QLabel("ASCII："))
        self.cv_ascii = QPlainTextEdit()
        v.addWidget(self.cv_ascii, 1)
        b3.clicked.connect(self.cv_ascii.clear)
        return w

    def hex_to_ascii(self):
        data, err = utils.parse_hex(self.cv_hex.toPlainText())
        if data is None:
            QMessageBox.warning(self, "HEX 格式错误", err)
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        self.cv_ascii.setPlainText(text)

    def ascii_to_hex(self):
        self.cv_hex.setPlainText(utils.hex_str(self.cv_ascii.toPlainText().encode("utf-8")))
