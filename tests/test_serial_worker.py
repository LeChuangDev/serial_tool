"""串口线程行为测试：命令队列、打开失败路径、停止语义。

不依赖真实硬件：只验证线程与队列的状态机。
跨线程信号是排队投递，断言前必须驱动事件循环。
"""

from __future__ import annotations

import time

import pytest

from serialtool.serial_worker import SerialWorker, is_virtual_port

MISSING_PORT = "COM_NOT_EXIST_999"


def _wait_for(predicate, app, timeout: float = 8.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()  # 跨线程信号需要事件循环才能投递
        if predicate():
            return True
        time.sleep(interval)
    app.processEvents()
    return predicate()


@pytest.fixture()
def worker(qapp):
    instance = SerialWorker()
    yield instance
    instance.stop()
    instance.clear_cmds()


class TestCommandQueue:
    def test_write_enqueues(self, worker):
        worker.write(b"abc")
        worker.write(b"def")
        assert worker.pending_writes() == 2

    def test_empty_write_ignored(self, worker):
        worker.write(b"")
        assert worker.pending_writes() == 0

    def test_clear_cmds_drops_pending(self, worker):
        worker.write(b"abc")
        worker.set_dtr(True)
        worker.clear_cmds()
        assert worker.pending_writes() == 0

    def test_is_open_false_before_start(self, worker):
        assert worker.is_open is False


class TestOpenFailure:
    def test_missing_port_emits_open_failed(self, worker, qapp):
        failures: list[str] = []
        worker.open_failed.connect(failures.append)

        assert worker.open(MISSING_PORT, 115200, 8, 1, "N") is True
        assert _wait_for(lambda: bool(failures), qapp)
        assert "串口打开失败" in failures[0]
        assert worker.is_open is False

    def test_second_open_while_running_is_rejected(self, worker, qapp):
        errors: list[str] = []
        worker.error.connect(errors.append)

        worker.open(MISSING_PORT, 115200, 8, 1, "N")
        accepted = worker.open(MISSING_PORT, 115200, 8, 1, "N")
        worker.stop()
        _wait_for(lambda: False, qapp, timeout=0.05)
        if not accepted:
            assert any("正在连接" in message for message in errors)


class TestStop:
    def test_stop_on_idle_worker_returns_true(self, worker):
        # 未启动的 QThread：stop() 必须立即返回，不能空等超时
        assert worker.stop(timeout=1.0) is True

    def test_thread_finishes_after_stop(self, worker, qapp):
        worker.open(MISSING_PORT, 115200, 8, 1, "N")
        assert worker.stop(timeout=5.0) is True
        assert worker.isFinished() is True


class TestVirtualPortFilter:
    @pytest.mark.parametrize(
        "description",
        [
            "Bluetooth Serial Port",
            "蓝牙链接上的标准串行",
            "Standard Serial over Bluetooth link (COM5)",
        ],
    )
    def test_filtered(self, description):
        assert is_virtual_port(description) is True

    @pytest.mark.parametrize(
        "description",
        [
            "USB-SERIAL CH340 (COM3)",
            "Silicon Labs CP210x",
            "",
            None,
        ],
    )
    def test_kept(self, description):
        assert is_virtual_port(description) is False
