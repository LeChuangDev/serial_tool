# ====================== 绘图数据分析（FFT / 直方图 / 统计） ======================
"""纯逻辑信号分析，不依赖 Qt / numpy，方便单测与复用。

对标 VOFA+ 波形控件的"直方统计 + 傅里叶变换"卖点，但用纯 Python 实现：
- FFT 用迭代 radix-2（Cooley-Tukey），1024 点约几毫秒，绘图模式低频刷新完全够用；
- 采样率由数据时间戳自动估算，无需用户手填；
- 直方图 / 通道统计供示波器叠加显示。
"""

from __future__ import annotations

import math

__all__ = [
    "fft",
    "fft_spectrum",
    "estimate_sample_rate",
    "histogram",
    "channel_stats",
]


def _next_pow2(n: int) -> int:
    """返回 ``>= n`` 的最小 2 的幂。"""
    p = 1
    while p < n:
        p <<= 1
    return p


def fft(samples: list[float]) -> list[complex]:
    """迭代 radix-2 FFT（Cooley-Tukey），长度必须是 2 的幂。

    纯 Python 复现教科书蝶形结构，供 ``fft_spectrum`` 使用；对非 2 的幂长度
    会抛 ``ValueError``（调用方应先用 ``_next_pow2`` 补零对齐）。
    """
    n = len(samples)
    if n == 0:
        return []
    if n & (n - 1):
        raise ValueError("FFT 长度必须是 2 的幂")
    a = [complex(x, 0.0) for x in samples]

    # 位反转置换
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    # 蝶形运算
    length = 2
    while length <= n:
        ang = -2.0 * math.pi / length
        wlen = complex(math.cos(ang), math.sin(ang))
        half = length // 2
        for i in range(0, n, length):
            w = 1.0 + 0.0j
            for k in range(half):
                u = a[i + k]
                v = a[i + k + half] * w
                a[i + k] = u + v
                a[i + k + half] = u - v
                w *= wlen
        length <<= 1
    return a


def fft_spectrum(samples: list[float], sample_rate: float) -> tuple[list[float], list[float]]:
    """返回 ``(频率列表, 幅值列表)``，只保留正频率半部分（到奈奎斯特频率）。

    - 去直流分量 + 汉宁窗抑制频谱泄漏；
    - 补零到最近的 2 的幂；
    - 幅值做相对归一化（``2/p``），用于观察频率成分的相对强弱，不做绝对物理标定。
    """
    n = len(samples)
    if n < 2 or sample_rate <= 0:
        return [], []
    mean = sum(samples) / n
    if n > 1:
        windowed = [
            (s - mean) * (0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)))
            for i, s in enumerate(samples)
        ]
    else:
        windowed = [0.0]
    p = _next_pow2(n)
    if p > n:
        windowed.extend([0.0] * (p - n))
    spec = fft(windowed)
    half = p // 2
    freqs = [sample_rate * i / p for i in range(half)]
    mags = [abs(spec[i]) * 2.0 / p for i in range(half)]
    return freqs, mags


def estimate_sample_rate(times: list[float]) -> float:
    """从时间戳序列估算平均采样率（Hz）。不足 2 点返回 0。"""
    if len(times) < 2:
        return 0.0
    dt = (times[-1] - times[0]) / (len(times) - 1)
    if dt <= 0:
        return 0.0
    return 1.0 / dt


def histogram(values: list[float], bins: int = 32) -> tuple[list[float], list[int]]:
    """等宽直方图，返回 ``(bin 中心列表, 计数列表)``。跳过 NaN。"""
    clean = [v for v in values if v == v]
    if not clean or bins <= 0:
        return [], []
    lo = min(clean)
    hi = max(clean)
    if lo == hi:
        lo, hi = lo - 1.0, hi + 1.0
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in clean:
        idx = int((v - lo) / width)
        if idx >= bins:
            idx = bins - 1
        elif idx < 0:
            idx = 0
        counts[idx] += 1
    centers = [lo + (i + 0.5) * width for i in range(bins)]
    return centers, counts


def channel_stats(values: list[float]) -> dict[str, float]:
    """通道统计：min / max / mean / rms / 峰峰值。跳过 NaN，空序列返回 NaN。"""
    clean = [v for v in values if v == v]
    if not clean:
        nan = float("nan")
        return {"min": nan, "max": nan, "mean": nan, "rms": nan, "p2p": nan}
    lo = min(clean)
    hi = max(clean)
    mean = sum(clean) / len(clean)
    rms = math.sqrt(sum(v * v for v in clean) / len(clean))
    return {"min": lo, "max": hi, "mean": mean, "rms": rms, "p2p": hi - lo}
