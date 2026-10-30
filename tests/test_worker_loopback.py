"""串口线程端到端测试（用假 Serial 对象模拟设备，无需真实硬件）。

覆盖：连接握手 → 收数据聚合 → 写路径 → DTR/RTS → 停止时残留数据不丢。
"""

from __future__ import annotations

import time

import pytest
import serial

from serialtool.serial_worker import SerialWorker

PORT = "COM_FAKE"


class FakeSerial:
    """最小可用的 pyserial 替身：可注入接收数据、记录写入内容。"""

    instances: list[FakeSerial] = []

    def __init__(self, port, baudrate, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.kwargs = kwargs
        self.rx = bytearray()
        self.written = bytearray()
        self.closed = False
        self.dtr = True
        self.rts = True
        self.timeout = kwargs.get("timeout", 0.05)
        self.in_waiting = 0
        FakeSerial.instances.append(self)

    # ---- 读取 ----
    def read(self, size=1):
        deadline = time.monotonic() + (self.timeout or 0)
        while time.monotonic() < deadline:
            if self.rx:
                chunk = bytes(self.rx[:size])
                del self.rx[:size]
                self.in_waiting = len(self.rx)
                return chunk
            time.sleep(0.001)
        return b""

    def cancel_read(self):
        pass

    # ---- 写入 ----
    def write(self, data):
        self.written.extend(data)
        return len(data)

    # ---- 缓冲 / 关闭 ----
    def send_break(self, duration=0.25):
        self.breaks = getattr(self, "breaks", [])
        self.breaks.append(duration)

    def reset_input_buffer(self):
        self.rx.clear()

    def reset_output_buffer(self):
        pass

    def close(self):
        self.closed = True


def _wait_for(predicate, app, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


@pytest.fixture()
def fake_serial(monkeypatch):
    FakeSerial.instances.clear()
    monkeypatch.setattr("serial.Serial", FakeSerial)
    return FakeSerial


@pytest.fixture()
def live_worker(qapp, fake_serial):
    worker = SerialWorker()
    received: list[tuple[bytes, float]] = []
    states: list[tuple[bool, bool]] = []
    worker.data_received.connect(lambda packets: received.extend(packets))
    worker.lines_state.connect(lambda dtr, rts: states.append((dtr, rts)))
    assert worker.open(PORT, 115200, 8, 1, "N") is True
    assert _wait_for(lambda: worker.is_open, qapp)
    yield worker, received, states
    worker.stop()
    worker.clear_cmds()


class TestConnect:
    def test_reports_lines_state(self, live_worker, qapp):
        _worker, _received, states = live_worker
        assert _wait_for(lambda: bool(states), qapp)
        assert states[0] == (True, True)

    def test_port_parameters_passed(self, live_worker, fake_serial):
        serial_obj = fake_serial.instances[0]
        assert serial_obj.port == PORT
        assert serial_obj.baudrate == 115200
        assert serial_obj.kwargs["bytesize"] == 8
        assert serial_obj.kwargs["rtscts"] is False


class TestReceive:
    def test_data_aggregated_and_delivered(self, live_worker, qapp):
        worker, received, _states = live_worker
        fake = FakeSerial.instances[0]
        fake.rx.extend(b"hello")
        assert _wait_for(lambda: bool(received), qapp)
        joined = b"".join(chunk for chunk, _ts in received)
        assert b"hello" in joined
        assert all(isinstance(ts, float) for _chunk, ts in received)

    def test_packet_boundaries_preserved(self, live_worker, qapp):
        _worker, received, _states = live_worker
        fake = FakeSerial.instances[0]
        fake.rx.extend(b"a")
        assert _wait_for(lambda: bool(received), qapp)
        count_before = len(received)
        fake.rx.extend(b"b")
        assert _wait_for(lambda: len(received) > count_before, qapp)


class TestWrite:
    def test_write_reaches_device(self, live_worker, qapp):
        worker, _received, _states = live_worker
        fake = FakeSerial.instances[0]
        worker.write(b"AT+RST")
        assert _wait_for(lambda: bytes(fake.written) == b"AT+RST", qapp)
        assert worker.pending_writes() == 0

    def test_backlog_does_not_starve_receive(self, live_worker, qapp):
        worker, received, _states = live_worker
        fake = FakeSerial.instances[0]
        for _ in range(50):
            worker.write(b"x" * 1024)
        fake.rx.extend(b"ping")
        assert _wait_for(lambda: bool(received), qapp, timeout=5.0)
        assert _wait_for(lambda: len(fake.written) >= 50 * 1024, qapp, timeout=5.0)

    def test_dtr_rts_commands_applied(self, live_worker, qapp):
        worker, _received, _states = live_worker
        fake = FakeSerial.instances[0]
        worker.set_dtr(False)
        worker.set_rts(False)
        assert _wait_for(lambda: fake.dtr is False and fake.rts is False, qapp)


class TestStop:
    def test_stop_closes_port(self, live_worker, qapp):
        worker, _received, _states = live_worker
        fake = FakeSerial.instances[0]
        assert worker.stop() is True
        assert fake.closed is True
        assert worker.is_open is False
        assert _wait_for(lambda: worker.isFinished(), qapp)

    def test_buffered_packets_flushed_on_stop(self, qapp, fake_serial):
        worker = SerialWorker()
        received: list[tuple[bytes, float]] = []
        worker.data_received.connect(lambda packets: received.extend(packets))
        worker.open(PORT, 115200, 8, 1, "N")
        assert _wait_for(lambda: worker.is_open, qapp)
        fake_serial.instances[0].rx.extend(b"tail")
        # 不等待聚合阈值，直接停止：finally 必须把残留数据送出去
        worker.stop()
        _wait_for(lambda: False, qapp, timeout=0.2)
        assert b"".join(chunk for chunk, _ts in received) == b"tail"


class TestBreak:
    def test_break_reaches_device(self, live_worker, qapp):
        worker, _received, _states = live_worker
        fake = FakeSerial.instances[0]
        worker.send_break(0.05)
        assert _wait_for(lambda: getattr(fake, "breaks", []) == [0.05], qapp)


class TestLinkLoss:
    """USB 拔出：连续读失败超过阈值 → link_lost + 线程退出（自动重连的触发点）。"""

    def test_link_lost_emitted_and_thread_exits(self, qapp, fake_serial, monkeypatch):
        from serialtool import config

        monkeypatch.setattr(config, "LINK_LOSS_TIMEOUT", 0.2)

        class UnpluggableSerial(FakeSerial):
            unplugged = False

            def read(self, size=1):
                if UnpluggableSerial.unplugged:
                    raise serial.SerialException("clearcommerror: device unplugged")
                return super().read(size)

        FakeSerial.instances.clear()
        monkeypatch.setattr("serial.Serial", UnpluggableSerial)

        worker = SerialWorker()
        lost: list[str] = []
        closed: list[str] = []
        worker.link_lost.connect(lost.append)
        worker.disconnected.connect(closed.append)
        try:
            assert worker.open(PORT, 115200, 8, 1, "N") is True
            assert _wait_for(lambda: worker.is_open, qapp)
            UnpluggableSerial.unplugged = True
            assert _wait_for(lambda: bool(lost), qapp, timeout=4.0)
            assert "unplugged" in lost[0]
            assert _wait_for(lambda: worker.isFinished(), qapp, timeout=3.0)
            assert _wait_for(lambda: bool(closed), qapp)
            assert worker.read_errors > 0
        finally:
            worker.stop()
            worker.clear_cmds()
