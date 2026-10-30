# ====================== 工具对话框：校验和 / HEX-ASCII 转换 ======================
"""辅助工具窗口。

- Tab1 校验和：ASCII/HEX 输入，一次算出 CRC8 / CRC16-Modbus / 异或 / 累加和，
  并可一键把结果以 HEX 追加到主窗口发送区。
- Tab2 转换：HEX ⇄ ASCII 双向转换。
"""

from __future__ import annotations

import logging

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import styles, utils

log = logging.getLogger(__name__)

__all__ = ["ToolsDialog"]


class ToolsDialog(QDialog):
    """校验和计算 / HEX 转换工具窗口。"""

    def __init__(self, parent: QWidget | None = None, tab: int = 0) -> None:
        super().__init__(parent)
        self._main = parent
        self._last_results: list[tuple[str, int]] | None = None
        self.setWindowTitle("工具")
        self.resize(580, 460)
        self.setStyleSheet(styles.GLOBAL_QSS)

        tabs = QTabWidget()
        tabs.addTab(self._make_checksum_tab(), "校验和")
        tabs.addTab(self._make_convert_tab(), "HEX / ASCII 转换")
        tabs.setCurrentIndex(max(0, min(tab, tabs.count() - 1)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(tabs)

    # =========================================================
    #  Tab1 校验和计算
    # =========================================================
    def _make_checksum_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        layout.addWidget(QLabel("输入内容（默认取发送区文本）："))
        self.cs_input = QPlainTextEdit()
        if self._main is not None:
            self.cs_input.setPlainText(self._main.send_box.toPlainText())
        layout.addWidget(self.cs_input, 1)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        self.cs_ascii = QRadioButton("ASCII / 文本")
        self.cs_ascii.setChecked(True)
        self.cs_hex = QRadioButton("HEX")
        mode_row.addWidget(self.cs_ascii)
        mode_row.addWidget(self.cs_hex)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        algo_row = QHBoxLayout()
        algo_row.setSpacing(12)
        self.cs_crc8 = QCheckBox("CRC8 (0x07)")
        self.cs_crc8.setChecked(True)
        self.cs_crc8.setToolTip("多项式 0x07，初值 0x00，无反射、无最终异或（CRC-8/ATM）")
        self.cs_crc16 = QCheckBox("CRC16-Modbus")
        self.cs_crc16.setChecked(True)
        self.cs_crc16.setToolTip("多项式 0xA001（反射），初值 0xFFFF，低字节在前")
        self.cs_xor = QCheckBox("异或")
        self.cs_sum = QCheckBox("累加和")
        for widget in (self.cs_crc8, self.cs_crc16, self.cs_xor, self.cs_sum):
            algo_row.addWidget(widget)
        algo_row.addStretch()
        layout.addLayout(algo_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        calc_button = QPushButton("计算")
        calc_button.setObjectName("primary")
        calc_button.clicked.connect(self.calc_checksum)
        self.btn_append = QPushButton("追加到发送区")
        self.btn_append.setObjectName("small")
        self.btn_append.clicked.connect(self.append_result)
        button_row.addWidget(calc_button)
        button_row.addWidget(self.btn_append)
        button_row.addStretch()
        layout.addLayout(button_row)

        self.cs_result = QPlainTextEdit()
        self.cs_result.setReadOnly(True)
        self.cs_result.setPlaceholderText("校验结果将显示在这里 …")
        layout.addWidget(self.cs_result, 1)
        return page

    def _payload(self, text: str) -> bytes | None:
        """按当前输入模式把文本转成字节；HEX 格式错误时提示并返回 None。"""
        if not self.cs_hex.isChecked():
            encoding = self._main.encoding if self._main is not None else "utf-8"
            return text.encode(encoding, errors="ignore")
        data, err = utils.parse_hex(text)
        if data is None:
            QMessageBox.warning(self, "HEX 格式错误", err)
        return data

    def calc_checksum(self) -> None:
        text = self.cs_input.toPlainText()
        data = self._payload(text)
        if data is None:
            return
        if not data:
            QMessageBox.information(self, "提示", "输入内容为空")
            return

        lines = [f"数据长度: {len(data)} 字节"]
        results: list[tuple[str, int]] = []
        if self.cs_crc8.isChecked():
            value = utils.crc8(data)
            lines.append(f"CRC8 (poly 0x07, init 0x00) = 0x{value:02X}  ({value})")
            results.append(("CRC8", value))
        if self.cs_crc16.isChecked():
            value = utils.crc16_modbus(data)
            lines.append(
                f"CRC16-Modbus  = 0x{value:04X}"
                f"   （发送低字节在前: {value & 0xFF:02X} {value >> 8:02X}）"
            )
            results.append(("CRC16", value))
        if self.cs_xor.isChecked():
            value = utils.xor_sum(data)
            lines.append(f"异或          = 0x{value:02X}  ({value})")
            results.append(("XOR", value))
        if self.cs_sum.isChecked():
            value = utils.byte_sum(data)
            lines.append(f"累加和        = 0x{value:02X}  ({value})")
            results.append(("SUM", value))

        self._last_results = results
        self.cs_result.setPlainText("\n".join(lines))
        log.debug("校验和计算完成: %d 字节, %d 项", len(data), len(results))

    def append_result(self) -> None:
        if not self._last_results:
            QMessageBox.information(self, "提示", "请先点击「计算」")
            return
        if self._main is None:
            return
        parts = [
            f"{value & 0xFF:02X}" if name != "CRC16" else f"{value & 0xFF:02X} {value >> 8:02X}"
            for name, value in self._last_results
        ]
        current = self._main.send_box.toPlainText()
        prefix = current.rstrip() + " " if current.strip() else ""
        self._main.send_box.setPlainText(prefix + " ".join(parts))

    # =========================================================
    #  Tab2 HEX ⇄ ASCII 转换
    # =========================================================
    def _make_convert_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        layout.addWidget(QLabel("HEX（空格分隔，如 48 65 6C 6C 6F）："))
        self.cv_hex = QPlainTextEdit()
        layout.addWidget(self.cv_hex, 1)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        to_ascii = QPushButton("HEX → ASCII")
        to_ascii.setObjectName("primary")
        to_ascii.clicked.connect(self.hex_to_ascii)
        to_hex = QPushButton("ASCII → HEX")
        to_hex.clicked.connect(self.ascii_to_hex)
        clear = QPushButton("清空")
        clear.setObjectName("tool")
        clear.clicked.connect(self.clear_convert)
        button_row.addWidget(to_ascii)
        button_row.addWidget(to_hex)
        button_row.addStretch()
        button_row.addWidget(clear)
        layout.addLayout(button_row)

        layout.addWidget(QLabel("ASCII："))
        self.cv_ascii = QPlainTextEdit()
        layout.addWidget(self.cv_ascii, 1)
        return page

    def clear_convert(self) -> None:
        """清空转换页的两个输入框。"""
        self.cv_hex.clear()
        self.cv_ascii.clear()

    def hex_to_ascii(self) -> None:
        data, err = utils.parse_hex(self.cv_hex.toPlainText())
        if data is None:
            QMessageBox.warning(self, "HEX 格式错误", err)
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")  # 非 UTF-8 时按字节直译，不丢信息
        self.cv_ascii.setPlainText(text)

    def ascii_to_hex(self) -> None:
        self.cv_hex.setPlainText(utils.hex_str(self.cv_ascii.toPlainText().encode("utf-8")))
