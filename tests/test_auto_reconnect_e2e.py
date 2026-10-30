"""自动重连端到端测试：设备掉线 → 换 COM 号重新出现 → 自动连回。

用假 Serial 对象模拟 USB 转串口，验证的是完整链路：
``读失败 → link_lost → disconnected → 重连调度 → 按 VID:PID 找回新端口 → connected``。
这条链路任何一环断掉，用户看到的就是"插回去还得手动点连接"。
"""

from __future__ import annotations

import time

import pytest
import serial

from serialtool.ports import PortInfo
from serialtool.reconnect import ReconnectPolicy, ReconnectScheduler

PORT = "COM3"
NEW_PORT = "COM15"


class FakeSerial:
    """可注入"拔出"状态的最小 pyserial 替身。"""

    unplugged = False
    instances: list[FakeSerial] = []

    def __init__(self, port, baudrate, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.kwargs = kwargs
        self.timeout = kwargs.get("timeout", 0.05)
        self.written = bytearray()
        self.closed = False
        self.dtr = True
        self.rts = True
        self.in_waiting = 0
        FakeSerial.instances.append(self)

    def read(self, size=1):
        if FakeSerial.unplugged:
            raise serial.SerialException("device reports readiness to read but returned no data")
        time.sleep(min(self.timeout or 0.01, 0.01))
        return b""

    def cancel_read(self):
        pass

    def write(self, data):
        self.written.extend(data)
        return len(data)

    def send_break(self, duration=0.25):
        return None

    def reset_input_buffer(self):
        return None

    def reset_output_buffer(self):
        return None

    def close(self):
        self.closed = True


def _wait_for(predicate, app, timeout: float = 6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    app.processEvents()
    return predicate()


@pytest.fixture()
def window(qapp, monkeypatch):
    from serialtool import config
    from serialtool.main_window import LecoSerial

    monkeypatch.setattr(config, "LINK_LOSS_TIMEOUT", 0.2)
    FakeSerial.unplugged = False
    FakeSerial.instances.clear()
    monkeypatch.setattr("serial.Serial", FakeSerial)

    main = LecoSerial()
    # 测试用短退避：把 1.2s 冷却和 0.8s 基础间隔压到毫秒级
    main._reconnector = ReconnectScheduler(
        ReconnectPolicy(max_attempts=5, base_delay=0.1, max_delay=0.2, jitter=0.0, cooldown=0.05)
    )
    main.btn_autoreconnect.setChecked(True)
    main.show()
    qapp.processEvents()
    try:
        yield main
    finally:
        main._closing = True
        main.rate_timer.stop()
        main.port_timer.stop()
        main.reconnect_timer.stop()
        main._tip_timer.stop()
        main.port_scanner.shutdown()
        main.close_serial()
        main.deleteLater()
        qapp.processEvents()
        FakeSerial.unplugged = False


DEVICE = PortInfo(
    device=PORT, description="USB-SERIAL CH340", vid=0x1A86, pid=0x7523, serial_number="A1B2C3"
)
NEW_DEVICE = PortInfo(
    device=NEW_PORT, description="USB-SERIAL CH340", vid=0x1A86, pid=0x7523, serial_number="A1B2C3"
)
OTHER_DEVICE = PortInfo(device="COM7", description="FT232R", vid=0x0403, pid=0x6001)


def _connect(window, qapp, port=PORT) -> None:
    # 先让窗口知道这个端口的 USB 身份（真实环境里来自枚举，重连靠它找回设备）
    window._apply_port_list([DEVICE])
    window.open_port(port, 115200, 8, 1, "N", (False, False, False))
    assert _wait_for(lambda: window._connected, qapp), "首次连接未成功"


def _feed_when_due(window, qapp, ports, timeout: float = 4.0) -> None:
    """等到重连调度到点、线程也退出后，再把扫描结果喂进去（模拟一次扫描）。"""
    ready = _wait_for(
        lambda: window._reconnector.due() and not window.worker.isRunning(), qapp, timeout
    )
    assert ready, "重连调度一直没到点"
    window._on_ports_scanned(ports)
    qapp.processEvents()


class TestAutoReconnectEndToEnd:
    def test_reconnects_to_new_com_port(self, window, qapp):
        _connect(window, qapp)
        assert window.current_port == PORT

        # 拔出：读操作开始抛异常
        FakeSerial.unplugged = True
        assert _wait_for(lambda: window._reconnector.active, qapp, timeout=6.0)
        window.reconnect_timer.stop()  # 关掉后台轮询，测试里手工驱动扫描
        assert _wait_for(lambda: not window._connected, qapp)

        # 重新插入，但被系统分配到新的 COM 号
        FakeSerial.unplugged = False
        _feed_when_due(window, qapp, [OTHER_DEVICE, NEW_DEVICE])

        assert _wait_for(lambda: window._connected, qapp, timeout=6.0), "未自动重连"
        assert window.current_port == NEW_PORT
        assert window._reconnect_count == 1
        assert window._reconnector.active is False

    def test_waits_when_device_absent_then_recovers(self, window, qapp):
        _connect(window, qapp)
        FakeSerial.unplugged = True
        assert _wait_for(lambda: window._reconnector.active, qapp, timeout=6.0)
        window.reconnect_timer.stop()
        FakeSerial.unplugged = False

        # 设备还没回来：每次扫描都记一次尝试，且保持等待
        _feed_when_due(window, qapp, [OTHER_DEVICE])
        assert window._reconnector.attempt >= 1
        assert window._reconnector.active is True
        assert window._connected is False

        _feed_when_due(window, qapp, [NEW_DEVICE])
        assert _wait_for(lambda: window._connected, qapp, timeout=6.0)

    def test_gives_up_after_max_attempts(self, window, qapp):
        _connect(window, qapp)
        FakeSerial.unplugged = True
        assert _wait_for(lambda: window._reconnector.active, qapp, timeout=6.0)
        window.reconnect_timer.stop()

        for _ in range(10):
            if not window._reconnector.active:
                break
            _feed_when_due(window, qapp, [])

        assert window._reconnector.active is False
        assert window._reconnector.attempt == 5
        assert window.reconnect_timer.isActive() is False

    def test_manual_disconnect_does_not_reconnect(self, window, qapp):
        _connect(window, qapp)
        window.close_serial()
        assert _wait_for(lambda: not window._connected, qapp)
        time.sleep(0.3)
        qapp.processEvents()
        assert window._reconnector.active is False
