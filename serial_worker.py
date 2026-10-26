# ====================== 串口工作线程 ======================
import queue
import threading
import time

import serial
from PyQt6.QtCore import QThread, pyqtSignal

import config


class SerialWorker(QThread):
    """串口后台线程：阻塞读取 + 命令队列。

    UI 线程不直接接触 serial 对象，所有操作（写/DTR/RTS）通过命令队列
    提交，接收数据聚合后经信号送回 UI 线程，避免高频信号轰炸。
    """

    data_received = pyqtSignal(list)   # list of (bytes, float)：包 + 到达时间戳
    connected = pyqtSignal(str)        # "port @ baud"
    disconnected = pyqtSignal(str)     # 关闭原因
    error = pyqtSignal(str)            # 打开失败 / 读异常 / 命令执行失败

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cmd_queue = queue.Queue()
        self._stop = threading.Event()
        self._ser = None
        self._warned_read_error = False
        self._port = self._baud = self._bits = self._stopbits = self._parity = None

    # ---------- UI 线程调用（非阻塞入队） ----------
    def open(self, port, baud, bits, stop, parity):
        self._port = port
        self._baud = baud
        self._bits = bits
        self._stopbits = stop
        self._parity = parity
        self._warned_read_error = False
        self.start()

    def write(self, data):
        self._cmd_queue.put(("write", data))

    def set_dtr(self, on):
        self._cmd_queue.put(("dtr", bool(on)))

    def set_rts(self, on):
        self._cmd_queue.put(("rts", bool(on)))

    def stop(self, timeout=2.0):
        """请求停止并等待线程结束（期间端口被释放，可安全重开）。"""
        self._stop.set()
        ser = self._ser
        if ser is not None:
            try:
                ser.cancel_read()
            except Exception:
                pass
        self.wait(timeout)

    # ---------- 后台线程 ----------
    def run(self):
        ser = None
        try:
            # 打开阶段：后台重试 5 次，不阻塞 UI
            last_err = ""
            for _ in range(5):
                if self._stop.is_set():
                    return
                try:
                    ser = serial.Serial(
                        self._port, self._baud, bytesize=self._bits,
                        stopbits=self._stopbits, parity=self._parity,
                        timeout=config.READ_TIMEOUT,
                        write_timeout=config.WRITE_TIMEOUT,
                        dsrdtr=False)
                    break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(0.2)
            else:
                self.error.emit(
                    f"串口打开失败：{self._port}\n原因：{last_err}\n"
                    f"请确认端口未被占用且设备已插入")
                return

            self._ser = ser
            try:
                ser.reset_input_buffer()
                ser.reset_output_buffer()
            except Exception:
                pass
            self.connected.emit(f"{self._port} @ {self._baud}")

            # 接收循环：命令 → 阻塞读 → 聚合发信号
            packets = []
            times = []
            buf_len = 0
            batch_start = 0.0
            while not self._stop.is_set():
                # 1) 优先执行命令队列
                while True:
                    try:
                        kind, payload = self._cmd_queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if kind == "write":
                            ser.write(payload)
                        elif kind == "dtr":
                            ser.dtr = payload
                        elif kind == "rts":
                            ser.rts = payload
                    except Exception as e:
                        self.error.emit(f"命令执行失败: {e}")
                # 2) 阻塞读（timeout 内返回，线程休眠不占 CPU）
                try:
                    chunk = ser.read(config.READ_CHUNK_SIZE)
                except serial.SerialException as e:
                    # 去重告警一次，不中断循环：保留"拔出不断开"语义
                    # （避免 ESP32 复位时设备短暂消失被误判为断开）
                    if not self._warned_read_error:
                        self._warned_read_error = True
                        self.error.emit(f"串口读取异常: {e}")
                    if self._stop.is_set():
                        break
                    continue
                if chunk:
                    if not packets:
                        batch_start = time.time()
                    packets.append(chunk)
                    times.append(time.time())
                    buf_len += len(chunk)
                # 3) 聚合：定量或定时发一批，保留包边界
                if buf_len and (buf_len >= config.AGG_BYTES
                                or time.time() - batch_start >= config.AGG_INTERVAL):
                    self.data_received.emit(list(zip(packets, times)))
                    packets = []
                    times = []
                    buf_len = 0
        finally:
            if ser is not None:
                try:
                    ser.reset_input_buffer()
                except Exception:
                    pass
                try:
                    ser.close()
                except Exception:
                    pass
            self._ser = None
            self._stop.clear()
            self.disconnected.emit("串口已断开")
