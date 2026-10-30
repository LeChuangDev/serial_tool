"""纯函数层单元测试：HEX 解析、校验算法、格式化。"""

from __future__ import annotations

import pytest

from serialtool import utils


class TestParseHex:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("01 A2 FF", b"\x01\xa2\xff"),
            ("0x01,0xA2", b"\x01\xa2"),
            ("01，02；03", b"\x01\x02\x03"),  # 中文标点
            ("01\n02\t03", b"\x01\x02\x03"),  # 任意空白
            ("ff", b"\xff"),
        ],
    )
    def test_valid(self, text, expected):
        data, err = utils.parse_hex(text)
        assert data == expected
        assert err is None

    def test_empty(self):
        assert utils.parse_hex("") == (b"", None)
        assert utils.parse_hex("   ") == (b"", None)

    def test_invalid_token_reports_position(self):
        data, err = utils.parse_hex("01 ZZ 03")
        assert data is None
        assert "第 2 个元素" in err and "ZZ" in err

    def test_out_of_range(self):
        data, err = utils.parse_hex("01 1FF")
        assert data is None
        assert "超出单字节范围" in err

    def test_redundant_prefix_only(self):
        # 只有前缀没有值：清洗后为空，返回空字节而非报错
        assert utils.parse_hex("0x") == (b"", None)


class TestChecksums:
    def test_crc16_modbus_check_value(self):
        # CRC-16/MODBUS 标准校验值
        assert utils.crc16_modbus(b"123456789") == 0x4B37

    def test_crc8_check_value(self):
        assert utils.crc8(b"123456789") == 0xF4

    def test_xor_and_sum(self):
        assert utils.xor_sum(b"\x01\x02\x03") == 0
        assert utils.byte_sum(b"\xff\x02") == 0x01  # 超 8 位取低字节

    def test_empty_payload(self):
        assert utils.crc16_modbus(b"") == 0xFFFF
        assert utils.crc8(b"") == 0
        assert utils.xor_sum(b"") == 0
        assert utils.byte_sum(b"") == 0


class TestFormatting:
    def test_hex_str(self):
        assert utils.hex_str(b"\x00\xab\xff") == "00 AB FF"

    def test_hex_dump_lines(self):
        lines = utils.hex_dump_lines(bytes(range(20)))
        assert len(lines) == 2
        assert lines[0].count(" ") == 15
        assert lines[1] == " ".join(["10", "11", "12", "13"])

    def test_hex_dump_invalid_width(self):
        with pytest.raises(ValueError):
            utils.hex_dump_lines(b"abc", width=0)

    @pytest.mark.parametrize(
        "count, expected",
        [
            (0, "0B"),
            (1023, "1023B"),
            (1024, "1.0KB"),
            (1536, "1.5KB"),
            (1048576, "1.0MB"),
            (1024**3, "1.0GB"),
        ],
    )
    def test_format_bytes(self, count, expected):
        assert utils.format_bytes(count) == expected

    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0, "00:00:00"),
            (-5, "00:00:00"),
            (61, "00:01:01"),
            (3661, "01:01:01"),
        ],
    )
    def test_format_duration(self, seconds, expected):
        assert utils.format_duration(seconds) == expected


class TestValidateBaud:
    def test_valid(self):
        assert utils.validate_baud("115200") == (115200, None)
        assert utils.validate_baud(" 9600 ") == (9600, None)

    @pytest.mark.parametrize("text", ["", "abc", "1.5", "0", "99999999999"])
    def test_invalid(self, text):
        baud, err = utils.validate_baud(text)
        assert baud is None
        assert err
