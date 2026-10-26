# ====================== 串口工作线程 ======================
import queue
import threading
import time

import serial
from PyQt6.QtCore import QThread, pyqtSignal

from . import config


class SerialWorker(QThread):
    """串口后台线程：阻塞读取 + 命令队列。

    UI 线程不直接接触 serial 对象，所有操作（写/DTR/RTS）通过命令队列
    提交，接收数据聚合后经信号送回 UI 线程，避免高频信号轰炸。
    """

    data_received = pyqtSignal(list)   # list of (bytes, float)：包 + 到达时间戳
    connected = pyqtSignal(str)        # "port @ baud"
    disconnected = pyqtSignal(str)     # 关闭原因
    error = pyqtSignal(str)            # 打开失败 / 读异常 / 命令执行失败
    lines_state = pyqtSignal(bool, bool)   # 打开端口后的实际 DTR / RTS 电平

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
        # 上一轮线程可能仍在收尾（如"连接中"被断开、大包写入中）：先停干净。
        # 否则 QThread.start() 对运行中的线程无效，表现为点了连接一直连不上。
        if self.isRunning():
            self.stop()
            if self.isRunning():
                self.error.emit(f"串口线程仍在收尾，请稍后重试（{self._port}）")
                return
        self._stop.clear()      # 清掉上一轮遗留的停止标志，避免新线程启动即退出
        self.start()

    def write(self, data):
        self._cmd_queue.put(("write", data))

    def set_dtr(self, on):
        self._cmd_queue.put(("dtr", bool(on)))

    def set_rts(self, on):
        self._cmd_queue.put(("rts", bool(on)))

    def clear_cmds(self):
        """丢弃未执行的命令（断开时调用：避免残留写入在下次连接时误发）。"""
        while True:
            try:
                self._cmd_queue.get_nowait()
            except queue.Empty:
                return

    def stop(self, timeout=2.0):
        """请求停止并等待线程结束（期间端口被释放，可安全重开）。"""
        self._stop.set()
        ser = self._ser
        if ser is not None:
            try:
                ser.cancel_read()
            except Exception:
                pass
        # 轮询等待线程结束。不使用 QThread.wait()：在 Python3.14 + PyQt6 环境下，
        # wait() 在线程收尾返回的瞬间会触发原生崩溃（0xC0000409）。
        # cancel_read 后读超时必然很快返回，线程随即退出，轮询即可安全等完。
        deadline = time.time() + timeout
        while not self.isFinished() and time.time() < deadline:
            time.sleep(0.005)

    # ---------- 后台线程 ----------
    def run(self):
        ser = None
        announced = False       # 是否真正连上过：只有连上过才发"已断开"，避免打开失败时误报
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
            announced = True
            try:
                # pyserial 打开端口时默认断言 DTR/RTS，把实际电平同步给界面
                self.lines_state.emit(bool(ser.dtr), bool(ser.rts))
            except Exception:
                pass

            # 接收循环：命令 → 阻塞读 → 聚合发信号
            packets = []
            times = []
            buf_len = 0
            batch_start = 0.0
            err_backoff = 0.0
            while not self._stop.is_set():
                # 1) 优先执行命令队列；单轮写入量有上限，保证大文件发送时
                #    接收仍能持续进行（不会整包写完才读一次）
                wrote = 0
                while wrote < config.SEND_CHUNK_SIZE:
                    try:
                        kind, payload = self._cmd_queue.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        if kind == "write":
                            ser.write(payload)
                            wrote += len(payload)
                        elif kind == "dtr":
                            ser.dtr = payload
                        elif kind == "rts":
                            ser.rts = payload
                    except Exception as e:
                        self.error.emit(f"命令执行失败: {e}")
                # 2) 阻塞读（timeout 内返回，线程休眠不占 CPU）
                try:
                    chunk = ser.read(config.READ_CHUNK_SIZE)
                    err_backoff = 0.0
                except serial.SerialException as e:
                    # 去重告警一次，不中断循环：保留"拔出不断开"语义
                    # （避免 ESP32 复位时设备短暂消失被误判为断开）
                    if not self._warned_read_error:
                        self._warned_read_error = True
                        self.error.emit(f"串口读取异常: {e}")
                    if self._stop.is_set():
                        break
                    # 设备拔出后 read 会立即抛异常，必须退避，否则空转打满 CPU
                    err_backoff = min(err_backoff * 2 or 0.02, 0.4)
                    time.sleep(err_backoff)
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
            if announced:
                self.disconnected.emit("串口已断开")
