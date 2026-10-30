# ====================== 串口工作线程 ======================
"""串口后台线程：阻塞读 + 命令队列。

职责边界：
- UI 线程**永不**直接接触 ``serial.Serial`` 对象；所有操作（写数据、DTR/RTS、
  BREAK）通过命令队列提交，串口异常通过信号回报。
- 接收数据按"定量或定时"聚合后一次性发出，避免高波特率下信号轰炸 UI。
- 链路中断（USB 拔出）由"连续读失败超过 :data:`config.LINK_LOSS_TIMEOUT`"判定，
  发出 :attr:`SerialWorker.link_lost` 后退出循环；是否重连由主窗口决定
  （重连策略见 ``reconnect.py``）。
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from typing import Any

import serial
from PyQt6.QtCore import QThread, pyqtSignal

from . import config
from .ports import PortScanner, is_virtual_port  # noqa: F401  (对外保持旧导入路径)

__all__ = ["SerialWorker", "PortScanner", "is_virtual_port"]

log = logging.getLogger(__name__)

# 单轮发送预算：一次循环最多写多少字节。过小会限制高波特率下的吞吐
# （因为每轮之后要等一次读超时），过大则影响接收实时性。
_WRITE_BUDGET = 64 * 1024
# 有写积压时，读超时的等待粒度（秒）：让写优先但不长时间饿死接收
_WRITE_PRIORITY_NAP = 0.002
_STOP_POLL_INTERVAL = 0.005


class SerialWorker(QThread):
    """串口读写线程。"""

    data_received = pyqtSignal(list)  # [(bytes, float)]：数据包 + 到达时间戳
    connected = pyqtSignal(str)  # "port @ baud"
    disconnected = pyqtSignal(str)  # 关闭原因
    error = pyqtSignal(str)  # 运行期错误（读异常 / 命令执行失败）
    open_failed = pyqtSignal(str)  # 打开失败（UI 需要弹窗的致命错误）
    link_lost = pyqtSignal(str)  # 设备拔出 / 链路失效（区别于用户主动断开）
    lines_state = pyqtSignal(bool, bool)  # 打开端口后的实际 DTR / RTS 电平

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._cmd_queue: queue.Queue[bytes] = queue.Queue()
        self._ctrl_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._ser: serial.Serial | None = None
        self._warned_read_error = False
        self._port = ""
        self._baud = 115200
        self._bits = 8
        self._stopbits = 1
        self._parity = serial.PARITY_NONE
        self._flow: tuple[bool, bool, bool] = (False, False, False)  # (rtscts, xonxoff, dsrdtr)
        self.read_errors = 0  # 读异常累计次数（统计卡展示）
        self.write_errors = 0

    # ---------------------------------------------------------
    #  UI 线程调用（全部非阻塞入队）
    # ---------------------------------------------------------
    def open(
        self,
        port: str,
        baud: int,
        bits: int,
        stop: int,
        parity: str,
        flow: tuple[bool, bool, bool] = (False, False, False),
    ) -> bool:
        """请求打开串口。

        Returns:
            ``True`` 表示已启动连接流程；``False`` 表示线程仍在收尾（已发 error 信号）。
        """
        self._port = port
        self._baud = baud
        self._bits = bits
        self._stopbits = stop
        self._parity = parity
        self._flow = flow
        self._warned_read_error = False
        self.read_errors = 0
        self.write_errors = 0
        # 已在连接/断开过程中就不再重启线程：重复点击只给一条提示，
        # 避免出现"停掉正在重试的线程 → 再重启"的多余连接循环。
        if self.isRunning():
            self.error.emit(f"正在连接/断开中，请稍候再试（{port}）")
            return False
        self._stop.clear()  # 清掉上一轮遗留的停止标志
        self.start()
        return True

    def write(self, data: bytes) -> None:
        if data:
            self._cmd_queue.put(data)

    def pending_writes(self) -> int:
        """排队待写的块数（供发送文件做背压，避免内存膨胀）。"""
        return self._cmd_queue.qsize()

    def set_dtr(self, on: bool) -> None:
        self._ctrl_queue.put(("dtr", bool(on)))

    def set_rts(self, on: bool) -> None:
        self._ctrl_queue.put(("rts", bool(on)))

    def send_break(self, duration: float = 0.25) -> None:
        """发送 BREAK（TX 拉低），部分 MCU 的 ISP/调试模式靠它进入。"""
        self._ctrl_queue.put(("break", max(float(duration), 0.01)))

    def clear_cmds(self) -> None:
        """丢弃未执行的命令（断开时调用：避免残留写入在下次连接时误发）。"""
        for pending in (self._ctrl_queue, self._cmd_queue):
            while True:
                try:
                    pending.get_nowait()
                except queue.Empty:
                    break

    @property
    def is_open(self) -> bool:
        """串口对象是否已建立（连接中/重试阶段为 False）。"""
        return self._ser is not None

    def stop(self, timeout: float = 2.0) -> bool:
        """请求停止并等待线程结束（期间端口被释放，可安全重开）。

        Returns:
            ``True`` 表示线程已在超时内退出。

        注意：不使用 ``QThread.wait()``——在 Python 3.14 + PyQt6 环境下，
        线程收尾返回的瞬间会触发原生崩溃（0xC0000409）。``cancel_read()``
        后阻塞读必然很快返回，轮询即可安全等完。
        """
        self._stop.set()
        if not self.isRunning():
            # 线程从未启动或已退出：isFinished() 对未启动的 QThread 恒为 False，
            # 直接返回，避免在关闭路径上空等满超时。
            return True
        ser = self._ser
        if ser is not None:
            # 部分平台/驱动不支持 cancel_read：失败时按读超时自然退出
            with contextlib.suppress(Exception):
                ser.cancel_read()
        deadline = time.monotonic() + timeout
        while not self.isFinished() and time.monotonic() < deadline:
            time.sleep(_STOP_POLL_INTERVAL)
        finished = self.isFinished()
        if not finished:
            log.warning("串口线程在 %.1fs 内未退出（端口 %s）", timeout, self._port)
        return finished

    # ---------------------------------------------------------
    #  后台线程
    # ---------------------------------------------------------
    def run(self) -> None:  # noqa: C901 (线程主循环，集中表达状态机)
        ser: serial.Serial | None = None
        announced = False  # 只有真正连上过才发"已断开"
        packets: list[bytes] = []
        times: list[float] = []
        buf_len = 0
        batch_start = 0.0
        dead_since = 0.0  # 首次读失败的时刻（0 表示当前正常）
        try:
            ser = self._open_with_retry()
            if ser is None:
                return
            self._ser = ser
            self._reset_buffers(ser)
            self.connected.emit(f"{self._port} @ {self._baud}")
            announced = True
            self._emit_lines_state(ser)
            log.info(
                "串口已打开: %s @ %d (bits=%d stop=%s parity=%s flow=%s)",
                self._port,
                self._baud,
                self._bits,
                self._stopbits,
                self._parity,
                self._flow,
            )

            read_backoff = 0.0
            while not self._stop.is_set():
                self._drain_ctrl(ser)
                self._drain_writes(ser)

                # 有写积压时让写优先：短暂让步后再读，避免"每写 4KB 等一次读超时"
                if self._cmd_queue.qsize() and not self._in_waiting(ser):
                    self._stop.wait(_WRITE_PRIORITY_NAP)
                    continue

                try:
                    chunk = ser.read(config.READ_CHUNK_SIZE)
                    read_backoff = 0.0
                    dead_since = 0.0
                    if self._warned_read_error:
                        # 恢复正常：重新武装告警，下次真掉线还能提示
                        self._warned_read_error = False
                        log.info("串口读取恢复正常: %s", self._port)
                except serial.SerialException as exc:
                    self.read_errors += 1
                    # 去重告警：一次掉线只提示一次，避免刷屏
                    if not self._warned_read_error:
                        self._warned_read_error = True
                        log.warning("串口读取异常: %s", exc)
                        self.error.emit(f"串口读取异常: {exc}")
                    if self._stop.is_set():
                        break
                    # 设备拔出后 read 会立即抛异常，必须退避，否则空转打满 CPU
                    now = time.monotonic()
                    if not dead_since:
                        dead_since = now
                    elif now - dead_since >= config.LINK_LOSS_TIMEOUT:
                        # 持续读失败 → 判定链路中断，退出线程交给重连策略处理
                        log.warning(
                            "链路中断（连续读失败 %.1fs）: %s", now - dead_since, self._port
                        )
                        self.link_lost.emit(str(exc))
                        break
                    read_backoff = min(read_backoff * 2 or 0.02, 0.4)
                    self._stop.wait(read_backoff)
                    continue

                if chunk:
                    if not packets:
                        batch_start = time.monotonic()
                    packets.append(chunk)
                    times.append(time.time())
                    buf_len += len(chunk)
                # 聚合：定量或定时发一批，保留包边界
                if buf_len and (
                    buf_len >= config.AGG_BYTES
                    or time.monotonic() - batch_start >= config.AGG_INTERVAL
                ):
                    self.data_received.emit(list(zip(packets, times, strict=True)))
                    packets, times, buf_len = [], [], 0
        except Exception:  # 线程内兜底：记录并保证 finally 释放端口
            log.exception("串口线程异常退出（端口 %s）", self._port)
            self.error.emit("串口线程异常，已停止；详情见 logs/app.log")
        finally:
            if packets:  # 退出前把聚合缓冲区里的残留数据送出去，避免丢尾包
                self.data_received.emit(list(zip(packets, times, strict=True)))
            self._close_serial(ser)
            self._ser = None
            if announced:
                self.disconnected.emit("串口已断开")
                log.info("串口已关闭: %s", self._port)

    # ---------- run() 的分步实现 ----------
    def _open_with_retry(self, attempts: int = 5) -> serial.Serial | None:
        """后台重试打开：设备刚插入/复位期间占用常见，重试比直接报错友好。"""
        rtscts, xonxoff, dsrdtr = self._flow
        last_error = ""
        for _ in range(attempts):
            if self._stop.is_set():
                return None
            try:
                return serial.Serial(
                    self._port,
                    self._baud,
                    bytesize=self._bits,
                    stopbits=self._stopbits,
                    parity=self._parity,
                    timeout=config.READ_TIMEOUT,
                    write_timeout=config.WRITE_TIMEOUT,
                    rtscts=rtscts,
                    xonxoff=xonxoff,
                    dsrdtr=dsrdtr,
                )
            except Exception as exc:
                last_error = str(exc)
                self._stop.wait(0.2)
        message = f"串口打开失败：{self._port}\n原因：{last_error}\n请确认端口未被占用且设备已插入"
        log.error("串口打开失败: %s | %s", self._port, last_error)
        self.open_failed.emit(message)
        return None

    @staticmethod
    def _reset_buffers(ser: serial.Serial) -> None:
        for action in (ser.reset_input_buffer, ser.reset_output_buffer):
            try:
                action()
            except Exception as exc:  # 部分虚拟串口不支持
                log.debug("缓冲复位失败: %s", exc)

    def _emit_lines_state(self, ser: serial.Serial) -> None:
        try:
            # pyserial 打开端口时默认断言 DTR/RTS，把实际电平同步给界面
            self.lines_state.emit(bool(ser.dtr), bool(ser.rts))
        except Exception as exc:
            log.debug("读取信号线状态失败: %s", exc)

    def _drain_ctrl(self, ser: serial.Serial) -> None:
        """信号线控制优先处理：文件发送期间复位脉冲也能立即生效。"""
        while True:
            try:
                kind, payload = self._ctrl_queue.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "dtr":
                    ser.dtr = payload
                elif kind == "rts":
                    ser.rts = payload
                elif kind == "break":
                    ser.send_break(payload)
            except Exception as exc:
                log.warning("信号线命令执行失败(%s=%s): %s", kind, payload, exc)
                self.error.emit(f"命令执行失败: {exc}")

    def _drain_writes(self, ser: serial.Serial) -> None:
        """单轮有上限地写数据，保证大文件发送时接收仍能持续进行。"""
        written = 0
        while written < _WRITE_BUDGET:
            try:
                payload = self._cmd_queue.get_nowait()
            except queue.Empty:
                return
            try:
                ser.write(payload)
                written += len(payload)
            except Exception as exc:
                self.write_errors += 1
                log.warning("串口写入失败: %s", exc)
                self.error.emit(f"串口写入失败: {exc}")
                return

    @staticmethod
    def _in_waiting(ser: serial.Serial) -> int:
        try:
            return int(getattr(ser, "in_waiting", 0) or 0)
        except Exception:
            return 0

    @staticmethod
    def _close_serial(ser: serial.Serial | None) -> None:
        if ser is None:
            return
        with contextlib.suppress(Exception):
            ser.reset_input_buffer()
        try:
            ser.close()
        except Exception as exc:
            log.warning("关闭串口失败: %s", exc)
