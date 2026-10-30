"""analysis 模块（FFT / 直方图 / 统计）纯逻辑测试。"""

from __future__ import annotations

import math

import pytest

from serialtool.analysis import (
    channel_stats,
    estimate_sample_rate,
    fft,
    fft_spectrum,
    histogram,
)


class TestFFT:
    def test_rejects_non_pow2(self):
        with pytest.raises(ValueError):
            fft([1.0, 2.0, 3.0])

    def test_dc_component(self):
        """全直流信号 FFT 后只有 bin0 有能量。"""
        spec = fft([1.0] * 8)
        assert abs(spec[0]) == pytest.approx(8.0)
        for k in range(1, 8):
            assert abs(spec[k]) < 1e-9

    def test_single_sine_peak(self):
        """50Hz 正弦波在 1000Hz 采样下，FFT 峰值应落在 50Hz 附近。"""
        fs = 1000.0
        n = 1024
        f0 = 50.0
        samples = [math.sin(2 * math.pi * f0 * i / fs) for i in range(n)]
        freqs, mags = fft_spectrum(samples, fs)
        peak_freq = freqs[max(range(len(mags)), key=lambda i: mags[i])]
        # 频率分辨率 = fs / n = 0.9766Hz，峰值应接近 50Hz（±1 bin）
        assert abs(peak_freq - f0) <= fs / n * 1.5

    def test_spectrum_noise_rejection(self):
        """含直流偏置的 50Hz 信号，去直流后 0Hz 附近不应成为主峰。"""
        fs = 1000.0
        n = 1024
        f0 = 50.0
        samples = [5.0 + math.sin(2 * math.pi * f0 * i / fs) for i in range(n)]
        _, mags = fft_spectrum(samples, fs)
        peak = max(range(len(mags)), key=lambda i: mags[i])
        assert peak > 10  # 主峰在 50Hz 附近，而不是 0Hz


class TestSampleRate:
    def test_uniform(self):
        times = [0.0, 0.01, 0.02, 0.03]
        assert estimate_sample_rate(times) == pytest.approx(100.0)

    def test_too_few_points(self):
        assert estimate_sample_rate([]) == 0.0
        assert estimate_sample_rate([1.0]) == 0.0

    def test_non_positive_dt(self):
        assert estimate_sample_rate([2.0, 1.0]) == 0.0  # 时间倒流


class TestHistogram:
    def test_uniform_distribution(self):
        centers, counts = histogram([0.0, 1.0, 2.0, 3.0], bins=4)
        assert counts == [1, 1, 1, 1]

    def test_all_equal(self):
        centers, counts = histogram([5.0, 5.0, 5.0], bins=4)
        assert sum(counts) == 3

    def test_skips_nan(self):
        centers, counts = histogram([float("nan"), 1.0, 2.0], bins=2)
        assert sum(counts) == 2


class TestChannelStats:
    def test_basic(self):
        s = channel_stats([1.0, 2.0, 3.0, 4.0])
        assert s["min"] == 1.0
        assert s["max"] == 4.0
        assert s["mean"] == pytest.approx(2.5)
        assert s["p2p"] == 3.0

    def test_rms(self):
        s = channel_stats([0.0, 2.0])
        # RMS = sqrt((0 + 4)/2) = sqrt(2)
        assert s["rms"] == pytest.approx(math.sqrt(2.0))

    def test_empty(self):
        s = channel_stats([])
        assert math.isnan(s["mean"])

    def test_skips_nan(self):
        s = channel_stats([float("nan"), 2.0, 4.0])
        assert s["min"] == 2.0
        assert s["max"] == 4.0
