"""串口枚举与设备身份匹配测试（自动重连的基础）。

重点覆盖"USB 转串口换了 COM 号"这个真实场景：设备重插后 COM3 → COM15，
只有按 VID/PID/序列号匹配才能找回同一块板子。
"""

from __future__ import annotations

import pytest

from serialtool.ports import (
    PortIdentity,
    PortInfo,
    is_virtual_port,
    match_score,
    pick_port,
)


def _port(device: str, vid=0x1A86, pid=0x7523, sn=None, desc="USB-SERIAL CH340") -> PortInfo:
    return PortInfo(
        device=device,
        description=desc,
        vid=vid,
        pid=pid,
        serial_number=sn,
        hwid=f"USB VID:PID={vid:04X}:{pid:04X}",
    )


class TestLabel:
    def test_label_includes_description(self):
        assert _port("COM3").label == "COM3 · USB-SERIAL CH340"

    def test_label_without_description(self):
        assert PortInfo(device="COM9").label == "COM9"

    def test_detail_text_has_usb_ids(self):
        detail = _port("COM3").detail_text()
        assert "1A86" in detail and "7523" in detail

    def test_detail_text_falls_back_to_hwid(self):
        info = PortInfo(device="COM1", hwid="ACPI\\PNP0501")
        assert info.detail_text() == "ACPI\\PNP0501"


class TestMatchScore:
    def test_same_device_and_ids_is_exact(self):
        identity = _port("COM3").identity
        assert match_score(identity, _port("COM3")) == 100

    def test_new_com_number_same_serial_scores_high(self):
        identity = PortIdentity(device="COM3", vid=0x10C4, pid=0xEA60, serial_number="SN123")
        moved = _port("COM15", vid=0x10C4, pid=0xEA60, sn="SN123")
        assert match_score(identity, moved) == 80

    def test_same_vid_pid_without_serial_still_matches(self):
        identity = PortIdentity(device="COM3", vid=0x1A86, pid=0x7523)
        assert match_score(identity, _port("COM7")) == 60

    def test_different_device_is_zero(self):
        identity = PortIdentity(device="COM3", vid=0x0403, pid=0x6001)
        assert match_score(identity, _port("COM8")) == 0

    def test_description_match_as_last_resort(self):
        identity = PortIdentity(device="COM3", description="USB-SERIAL CH340")
        other = PortInfo(device="COM9", description="USB-SERIAL CH340")
        assert match_score(identity, other) == 40

    def test_same_name_without_usb_identity(self):
        identity = PortIdentity(device="COM1")
        assert match_score(identity, PortInfo(device="com1")) == 90


class TestPickPort:
    def test_prefers_exact_device(self):
        identity = _port("COM3").identity
        ports = [_port("COM7"), _port("COM3"), _port("COM5")]
        assert pick_port(ports, identity).device == "COM3"

    def test_finds_device_on_new_port(self):
        identity = PortIdentity(device="COM3", vid=0x10C4, pid=0xEA60, serial_number="SN1")
        ports = [
            _port("COM9", vid=0x1A86, pid=0x7523),
            _port("COM15", vid=0x10C4, pid=0xEA60, sn="SN1"),
        ]
        assert pick_port(ports, identity).device == "COM15"

    def test_returns_none_when_nothing_matches(self):
        identity = PortIdentity(device="COM3", vid=0x0403, pid=0x6001, serial_number="X")
        assert pick_port([_port("COM9")], identity) is None

    def test_empty_list(self):
        assert pick_port([], PortIdentity(device="COM3")) is None

    def test_no_identity_takes_first(self):
        ports = [_port("COM2"), _port("COM4")]
        assert pick_port(ports, None).device == "COM2"


class TestVirtualPortFilter:
    @pytest.mark.parametrize(
        "description",
        ["Bluetooth Serial Port", "蓝牙链接上的标准串行", "Standard Serial over Bluetooth link"],
    )
    def test_bluetooth_filtered(self, description):
        assert is_virtual_port(description) is True

    @pytest.mark.parametrize("description", ["USB-SERIAL CH340", "Silicon Labs CP210x", "", None])
    def test_normal_kept(self, description):
        assert is_virtual_port(description) is False
