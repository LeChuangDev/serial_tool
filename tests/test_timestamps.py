"""时间戳格式化测试：时钟 / 相对 / 增量三种模式。"""

from __future__ import annotations

import time

from serialtool.timestamps import (
    MODE_CLOCK,
    MODE_DELTA,
    MODE_ELAPSED,
    TimestampFormatter,
    format_delta,
)


class TestFormatDelta:
    def test_milliseconds(self):
        assert format_delta(0.012) == "12ms"

    def test_seconds(self):
        assert format_delta(1.234) == "1.234s"

    def test_minutes(self):
        assert format_delta(62.5) == "1m02.500s"

    def test_hours(self):
        assert format_delta(3725.0) == "1h02m05.000s"

    def test_negative_clamped(self):
        assert format_delta(-1.0) == "0ms"


class TestClockMode:
    def test_prefix_contains_clock(self):
        fmt = TimestampFormatter(MODE_CLOCK)
        stamp = fmt.stamp(time.mktime((2026, 10, 30, 12, 34, 56, 0, 0, -1)) + 0.789)
        assert stamp.startswith("[12:34:56.7")
        assert stamp.endswith("] ")

    def test_clock_mode_ignores_origin(self):
        fmt = TimestampFormatter(MODE_CLOCK)
        first = fmt.stamp(100.0)
        second = fmt.stamp(200.0)
        assert first != second


class TestElapsedMode:
    def test_relative_to_origin(self):
        fmt = TimestampFormatter(MODE_ELAPSED)
        fmt.reset(origin=1000.0)
        assert fmt.stamp(1000.5) == "[+500ms] "
        assert fmt.stamp(1002.0) == "[+2.000s] "

    def test_first_packet_sets_origin_if_unset(self):
        fmt = TimestampFormatter(MODE_ELAPSED)
        assert fmt.stamp(5000.0) == "[+0ms] "
        assert fmt.stamp(5001.0) == "[+1.000s] "

    def test_reset_restarts(self):
        fmt = TimestampFormatter(MODE_ELAPSED)
        fmt.reset(origin=10.0)
        fmt.stamp(15.0)
        fmt.reset(origin=100.0)
        assert fmt.stamp(100.25) == "[+250ms] "


class TestDeltaMode:
    def test_interval_between_packets(self):
        fmt = TimestampFormatter(MODE_DELTA)
        fmt.reset(origin=100.0)
        assert fmt.stamp(100.0) == "[+0ms] "
        assert fmt.stamp(100.05) == "[+50ms] "
        assert fmt.stamp(100.10) == "[+50ms] "

    def test_reset_clears_last(self):
        fmt = TimestampFormatter(MODE_DELTA)
        fmt.reset(origin=0.0)
        fmt.stamp(10.0)
        fmt.reset(origin=20.0)
        assert fmt.stamp(20.0) == "[+0ms] "


class TestModeFallback:
    def test_unknown_mode_falls_back_to_clock(self):
        assert TimestampFormatter("bogus").mode == MODE_CLOCK

    def test_set_mode_ignores_unknown(self):
        fmt = TimestampFormatter(MODE_CLOCK)
        fmt.set_mode("bogus")
        assert fmt.mode == MODE_CLOCK
