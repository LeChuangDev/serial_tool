"""示波器抽稀函数测试（纯逻辑，不依赖 Qt 渲染）。"""

from __future__ import annotations

import pytest

from serialtool.scope import decimate


class TestDecimate:
    def test_partitions_into_columns(self):
        times = [0.0, 0.1, 0.2, 0.3]
        values = [1.0, 2.0, 3.0, 4.0]
        out = decimate(times, values, 0.0, 0.4, 2)
        assert out == [(0, 1.0, 2.0), (1, 3.0, 4.0)]

    def test_min_max_envelope(self):
        times = [0.0, 0.05, 0.1, 0.15]
        values = [5.0, -1.0, 3.0, 2.0]
        assert decimate(times, values, 0.0, 0.2, 1) == [(0, -1.0, 5.0)]

    def test_skips_nan(self):
        times = [0.0, 0.1]
        values = [float("nan"), 7.0]
        assert decimate(times, values, 0.0, 0.2, 1) == [(0, 7.0, 7.0)]

    def test_out_of_window_ignored(self):
        times = [0.0, 1.0, 2.0]
        values = [1.0, 2.0, 3.0]
        assert decimate(times, values, 0.5, 2.5, 2) == [(0, 2.0, 2.0), (1, 3.0, 3.0)]

    def test_invalid_arguments(self):
        assert decimate([], [], 0.0, 1.0, 10) == []
        assert decimate([0.0], [1.0], 1.0, 1.0, 10) == []  # t1 <= t0
        assert decimate([0.0], [1.0], 0.0, 1.0, 0) == []  # width <= 0


class TestScopeAnalysis:
    """示波器分析功能（FFT / 直方图 / 统计 / 游标）测试。"""

    @staticmethod
    def _make(qapp, n=2048):
        import math

        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        fs = 1000.0
        for i in range(n):
            t = i / fs
            scope.add_frame(t, [math.sin(2 * math.pi * 50 * i / fs)])
        return scope

    @staticmethod
    def _cleanup(scope):
        scope._repaint_timer.stop()
        scope._fps_timer.stop()
        scope.deleteLater()

    def test_fft_data_finds_50hz(self, qapp):
        scope = self._make(qapp)
        try:
            freqs, mags, fs = scope.fft_data(0)
            assert fs == pytest.approx(1000.0, rel=0.02)
            peak = freqs[max(range(len(mags)), key=lambda i: mags[i])]
            assert abs(peak - 50.0) < 5.0
        finally:
            self._cleanup(scope)

    def test_channel_stats(self, qapp):
        scope = self._make(qapp)
        try:
            s = scope.channel_stats(0)
            assert s["min"] < -0.9
            assert s["max"] > 0.9
            assert abs(s["mean"]) < 0.05
            assert abs(s["rms"] - 0.7071) < 0.05  # 正弦 RMS = 1/sqrt(2)
        finally:
            self._cleanup(scope)

    def test_histogram_data(self, qapp):
        scope = self._make(qapp)
        try:
            centers, counts = scope.histogram_data(0, bins=16)
            assert len(centers) == 16
            assert sum(counts) == 2048
        finally:
            self._cleanup(scope)

    def test_nearest_index(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.00, [1.0])
            scope.add_frame(0.01, [2.0])
            scope.add_frame(0.02, [3.0])
            assert scope._nearest_index(-1.0) == 0
            assert scope._nearest_index(0.004) == 0  # 更接近 0.00
            assert scope._nearest_index(0.016) == 2  # 更接近 0.02
            assert scope._nearest_index(1.0) == 2
        finally:
            self._cleanup(scope)

    def test_set_view(self, qapp):
        from serialtool.scope import VIEW_HISTOGRAM, VIEW_SPECTRUM, VIEW_WAVE, ScopeWidget

        scope = ScopeWidget()
        try:
            assert scope.view() == VIEW_WAVE
            scope.set_view(VIEW_SPECTRUM)
            assert scope.view() == VIEW_SPECTRUM
            scope.set_view(VIEW_HISTOGRAM)
            assert scope.view() == VIEW_HISTOGRAM
        finally:
            self._cleanup(scope)


class TestAnalysisCache:
    """分析结果缓存：30FPS 下每帧重算 FFT / 直方图会把一个核吃满。"""

    @staticmethod
    def _cleanup(scope):
        scope._repaint_timer.stop()
        scope._fps_timer.stop()
        scope.deleteLater()

    def test_fft_reused_while_data_unchanged(self, qapp):
        scope = TestScopeAnalysis._make(qapp, n=1024)
        try:
            first = scope.fft_data(0)
            assert scope.fft_data(0) is first  # 无新数据 → 同一份结果
            scope.add_frame(2.0, [0.5])
            assert scope.fft_data(0) is not first  # 新数据 → 重算
        finally:
            self._cleanup(scope)

    def test_histogram_reused(self, qapp):
        scope = TestScopeAnalysis._make(qapp, n=512)
        try:
            assert scope.histogram_data(0) is scope.histogram_data(0)
            scope.add_frame(2.0, [0.5])
            first = scope.histogram_data(0)
            assert scope.histogram_data(0) is first
            assert scope.histogram_data(0, bins=16) is not first  # 不同参数分别缓存
        finally:
            self._cleanup(scope)

    def test_calibration_invalidates_cache(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0])
            scope.add_frame(0.1, [3.0])
            before = scope.channel_stats(0)
            scope.set_channel_scale(0, 10.0)
            after = scope.channel_stats(0)
            assert after["max"] == pytest.approx(before["max"] * 10)
        finally:
            self._cleanup(scope)

    def test_fft_points_change_invalidates_cache(self, qapp):
        scope = TestScopeAnalysis._make(qapp, n=1024)
        try:
            first = scope.fft_data(0)
            scope.set_fft_points(256)
            assert scope.fft_data(0) is not first
        finally:
            self._cleanup(scope)


class TestYRange:
    """Y 轴量程：自动跟随 vs 手动固定。"""

    @staticmethod
    def _cleanup(scope):
        scope._repaint_timer.stop()
        scope._fps_timer.stop()
        scope.deleteLater()

    def test_default_is_auto(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            _, _, auto = scope.y_range()
            assert auto is True
        finally:
            self._cleanup(scope)

    def test_fixed_range(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.set_y_range(-5.0, 5.0)
            assert scope.y_range() == (-5.0, 5.0, False)
        finally:
            self._cleanup(scope)

    def test_rejects_inverted_range(self, qapp):
        """上下限颠倒会让绘制除零（span<=0），必须拒绝而不是照单全收。"""
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.set_y_range(0.0, 10.0)
            scope.set_y_range(10.0, 0.0)
            assert scope.y_range() == (0.0, 10.0, False)
            scope.set_y_range(1.0, 1.0)
            assert scope.y_range() == (0.0, 10.0, False)
        finally:
            self._cleanup(scope)

    def test_back_to_auto(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.set_y_range(-1.0, 1.0)
            scope.set_auto_range(True)
            assert scope.y_range()[2] is True
        finally:
            self._cleanup(scope)

    def test_fixed_range_renders(self, qapp):
        """固定量程下实际绘制一次，确认不会因 span 异常崩掉。"""
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            for i in range(50):
                scope.add_frame(i * 0.01, [float(i)])
            scope.set_y_range(-100.0, 100.0)  # 数据远在量程内 → 压成近似直线
            scope.resize(320, 240)
            scope.grab()  # 触发一次真实 paintEvent
        finally:
            self._cleanup(scope)


class TestChannelMeta:
    """通道元数据（名称 / 颜色 / 标定 / 小数位）测试。"""

    @staticmethod
    def _cleanup(scope):
        scope._repaint_timer.stop()
        scope._fps_timer.stop()
        scope.deleteLater()

    def test_transform_value(self):
        from serialtool.scope import transform_value

        assert transform_value(2.0, 1.0, 0.0) == 2.0  # 快路径
        assert transform_value(2.0, 10.0, 1.0) == 21.0
        assert transform_value(0.0, 2.0, -3.0) == -3.0

    def test_default_meta(self, qapp):
        from serialtool import config
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0, 2.0])
            assert scope.channel_name(0) == "CH1"
            assert scope.channel_name(1) == "CH2"
            assert scope.channel_color(0) == config.SCOPE_COLORS[0]
            meta = scope.channel_meta(0)
            assert meta.scale == 1.0 and meta.offset == 0.0 and meta.decimals == 4
        finally:
            self._cleanup(scope)

    def test_setters(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0])
            scope.set_channel_name(0, "温度")
            scope.set_channel_color(0, "#ff0000")
            scope.set_channel_scale(0, 2.0)
            scope.set_channel_offset(0, 1.0)
            scope.set_channel_decimals(0, 2)
            assert scope.channel_name(0) == "温度"
            assert scope.channel_color(0) == "#ff0000"
            assert scope.channel_meta(0).scale == 2.0
            assert scope.channel_meta(0).offset == 1.0
            assert scope.channel_meta(0).decimals == 2
        finally:
            self._cleanup(scope)

    def test_channel_values_applies_transform(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0, 2.0])
            scope.add_frame(0.1, [3.0, 4.0])
            scope.add_frame(0.2, [5.0, 6.0])
            scope.set_channel_scale(0, 10.0)
            scope.set_channel_offset(0, 5.0)
            assert list(scope.channel_values(0)) == [15.0, 35.0, 55.0]
            # 未标定的通道走快路径，返回原值
            assert list(scope.channel_values(1)) == [2.0, 4.0, 6.0]
        finally:
            self._cleanup(scope)

    def test_decimals_clamped(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0])
            scope.set_channel_decimals(0, -3)
            assert scope.channel_meta(0).decimals == 0
            scope.set_channel_decimals(0, 99)
            assert scope.channel_meta(0).decimals == 9
        finally:
            self._cleanup(scope)


class TestChannelMetaPersistence:
    """通道元数据落盘：名称 / 颜色 / 标定重启后必须还在。"""

    @staticmethod
    def _cleanup(scope):
        scope._repaint_timer.stop()
        scope._fps_timer.stop()
        scope.deleteLater()

    def test_roundtrip(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.import_meta(
                [{"name": "温度", "color": "#ff0000", "scale": 2.0, "offset": 1.0, "decimals": 2}]
            )
            assert scope.channel_name(0) == "温度"
            assert scope.channel_color(0) == "#ff0000"
            assert scope.channel_meta(0).scale == 2.0
            assert scope.channel_meta(0).offset == 1.0
            assert scope.channel_meta(0).decimals == 2
            assert scope.export_meta()[0]["name"] == "温度"
        finally:
            self._cleanup(scope)

    def test_bad_values_sanitized(self, qapp):
        """配置可能被手改坏：坏值要收敛，不能崩、不能画出黑线。"""
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.import_meta(
                [
                    {
                        "name": "x" * 100,
                        "color": "red",
                        "scale": "nan",
                        "offset": None,
                        "decimals": 99,
                    }
                ]
            )
            assert len(scope.channel_name(0)) == 32  # 超长名截断
            assert scope.channel_color(0).startswith("#")  # 非法颜色回退默认色板
            assert scope.channel_meta(0).scale == 1.0
            assert scope.channel_meta(0).offset == 0.0
            assert scope.channel_meta(0).decimals == 9
        finally:
            self._cleanup(scope)

    def test_non_list_ignored(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.import_meta("not a list")
            scope.import_meta([42, None])
            assert scope.export_meta() == []
        finally:
            self._cleanup(scope)

    def test_preset_applies_to_new_frames(self, qapp):
        """无数据时预置的元数据，新帧到达后按通道序号套用。"""
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.import_meta([{"name": "电压"}, {"name": "电流"}])
            assert scope.channel_count() == 0  # 还没有数据
            scope.add_frame(0.0, [1.0, 2.0])
            assert scope.channel_name(0) == "电压"
            assert scope.channel_name(1) == "电流"
        finally:
            self._cleanup(scope)

    def test_clear_keeps_meta(self, qapp):
        """“清空”清的是曲线，不是用户调好的通道名与标定。"""
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0])
            scope.set_channel_name(0, "转速")
            scope.set_channel_scale(0, 5.0)
            scope.clear()
            assert scope.channel_count() == 0
            scope.add_frame(0.1, [2.0])
            assert scope.channel_name(0) == "转速"
            assert scope.channel_meta(0).scale == 5.0
        finally:
            self._cleanup(scope)


class TestRenderBudget:
    """渲染预算：抽样抽稀 / 自适应降帧 / 空闲不重绘。

    这几条是"16 通道长窗口会不会把 UI 线程吃满"的防线，改动绘制路径时必须守。
    """

    @staticmethod
    def _cleanup(scope):
        scope._repaint_timer.stop()
        scope._fps_timer.stop()
        scope.deleteLater()

    def test_step_sampling_is_subset(self):
        """抽样是等距取点，包络只能比全量窄，不会凭空造出更大/更小的值。"""
        from serialtool.scope import decimate

        times = [i * 0.001 for i in range(100)]
        values = [float(i) for i in range(100)]
        full = decimate(times, values, 0.0, 0.1, 10)
        sampled = decimate(times, values, 0.0, 0.1, 10, 4)
        assert sampled and len(sampled) == len(full)
        for (col, vmin, vmax), (fcol, fmin, fmax) in zip(sampled, full, strict=True):
            assert col == fcol  # 列映射不变，只是每列参与聚合的点变少
            assert vmin >= fmin and vmax <= fmax

    def test_decimate_step_scales_with_points(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            for i in range(5000):
                scope.add_frame(i * 0.001, [1.0])
            assert scope._decimate_step(100) > 1  # 点远多于列 → 抽样
            assert scope._decimate_step(100_000) == 1  # 列足够多 → 逐点
        finally:
            self._cleanup(scope)

    def test_tick_skips_unchanged_data(self, qapp):
        """数据没变且没有游标：一次都不画（挂机/暂停时 CPU 归零）。"""
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            calls: list[int] = []
            scope.update = lambda: calls.append(1)  # 拦截重绘
            scope._painted_revision = scope._revision  # 假装刚画完
            scope._tick()
            assert calls == []
            scope.add_frame(0.0, [1.0])
            scope._tick()
            assert len(calls) == 1
        finally:
            self._cleanup(scope)

    def test_adapt_lowers_fps_when_heavy(self, qapp):
        import time

        from serialtool import config
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            base = scope._repaint_timer.interval()
            for _ in range(6):
                scope._adapt(time.perf_counter() - 0.08)  # 假装单帧画了 80ms
            interval = scope._repaint_timer.interval()
            assert base < interval <= 1000 / config.SCOPE_MIN_FPS + 1
        finally:
            self._cleanup(scope)

    def test_adapt_keeps_target_fps_when_cheap(self, qapp):
        import time

        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            for _ in range(6):
                scope._adapt(time.perf_counter() - 0.001)  # 1ms
            assert scope._repaint_timer.interval() == 1000 // 30
        finally:
            self._cleanup(scope)


class TestExportRows:
    """CSV 导出必须走标定后值，否则曲线和文件对不上。"""

    @staticmethod
    def _cleanup(scope):
        scope._repaint_timer.stop()
        scope._fps_timer.stop()
        scope.deleteLater()

    def test_applies_calibration(self, qapp):
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0, 2.0])
            scope.set_channel_scale(0, 10.0)
            names, rows = scope.export_rows()
            assert names == ["CH1", "CH2"]
            assert rows[0][1][0] == pytest.approx(10.0)
            assert rows[0][1][1] == pytest.approx(2.0)
        finally:
            self._cleanup(scope)

    def test_missing_channel_is_nan(self, qapp):
        """后出现的通道用 NaN 补齐历史，导出时对应格子留空。"""
        from serialtool.scope import ScopeWidget

        scope = ScopeWidget()
        try:
            scope.add_frame(0.0, [1.0])
            scope.add_frame(0.1, [2.0, 3.0])
            _, rows = scope.export_rows()
            assert rows[0][1][1] != rows[0][1][1]  # NaN
            assert rows[1][1][1] == pytest.approx(3.0)
        finally:
            self._cleanup(scope)
