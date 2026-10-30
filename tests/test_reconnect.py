"""自动重连时序测试：用假时钟精确断言退避行为。

真实场景里"第 3 次重试应该等 3.2 秒"这种要求只能靠注入时钟验证——
手工插拔 10 次既不可靠也无法断言时间点。
"""

from __future__ import annotations

import pytest

from serialtool.reconnect import ReconnectPolicy, ReconnectScheduler


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return FakeClock()


class TestDelayFor:
    def test_exponential_growth(self):
        policy = ReconnectPolicy(base_delay=1.0, max_delay=60.0, jitter=0.0)
        assert [policy.delay_for(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]

    def test_capped_at_max_delay(self):
        policy = ReconnectPolicy(base_delay=1.0, max_delay=5.0, jitter=0.0)
        assert policy.delay_for(10) == 5.0

    def test_zero_attempt_uses_floor(self):
        policy = ReconnectPolicy(base_delay=1.0, jitter=0.0)
        assert policy.delay_for(0) == pytest.approx(0.05)

    def test_jitter_bounds(self):
        policy = ReconnectPolicy(base_delay=4.0, max_delay=60.0, jitter=0.25)
        assert policy.delay_for(1, random_value=0.0) == pytest.approx(3.0)
        assert policy.delay_for(1, random_value=0.5) == pytest.approx(4.0)
        assert policy.delay_for(1, random_value=0.999) == pytest.approx(5.0, rel=1e-2)

    def test_unbounded_when_max_attempts_zero(self):
        assert ReconnectPolicy(max_attempts=0).unbounded is True
        assert ReconnectPolicy(max_attempts=3).unbounded is False


class TestScheduler:
    def test_starts_idle(self, clock):
        scheduler = ReconnectScheduler(clock=clock)
        assert scheduler.active is False
        assert scheduler.describe() == ""

    def test_first_attempt_waits_for_cooldown(self, clock):
        scheduler = ReconnectScheduler(ReconnectPolicy(cooldown=1.0), clock=clock)
        scheduler.start()
        assert scheduler.active is True
        assert scheduler.due() is False
        clock.advance(0.9)
        assert scheduler.due() is False
        clock.advance(0.2)
        assert scheduler.due() is True

    def test_note_attempt_schedules_backoff(self, clock):
        policy = ReconnectPolicy(max_attempts=5, base_delay=1.0, max_delay=10.0, jitter=0.0)
        scheduler = ReconnectScheduler(policy, clock=clock)
        scheduler.start()
        clock.advance(2.0)

        assert scheduler.note_attempt() == 1
        assert scheduler.seconds_until_due() == pytest.approx(1.0)
        clock.advance(1.01)
        assert scheduler.due() is True

        assert scheduler.note_attempt() == 2
        assert scheduler.seconds_until_due() == pytest.approx(2.0)

    def test_stops_after_max_attempts(self, clock):
        policy = ReconnectPolicy(max_attempts=2, base_delay=0.1, jitter=0.0, cooldown=0.1)
        scheduler = ReconnectScheduler(policy, clock=clock)
        scheduler.start()
        clock.advance(0.2)

        assert scheduler.note_attempt() == 1
        assert scheduler.active is True
        assert scheduler.note_attempt() == 2
        assert scheduler.active is False
        assert scheduler.exhausted() is True

    def test_manual_stop_is_not_exhausted(self, clock):
        scheduler = ReconnectScheduler(ReconnectPolicy(max_attempts=3), clock=clock)
        scheduler.start()
        scheduler.stop()
        assert scheduler.active is False
        assert scheduler.exhausted() is False
        assert scheduler.attempt == 0

    def test_unbounded_never_exhausts(self, clock):
        policy = ReconnectPolicy(max_attempts=0, base_delay=0.1, jitter=0.0, max_delay=0.1)
        scheduler = ReconnectScheduler(policy, clock=clock)
        scheduler.start()
        for _ in range(50):
            clock.advance(1.0)
            scheduler.note_attempt()
        assert scheduler.active is True
        assert scheduler.exhausted() is False

    def test_describe_shows_progress_and_countdown(self, clock):
        policy = ReconnectPolicy(max_attempts=10, base_delay=2.0, jitter=0.0, cooldown=0.0)
        scheduler = ReconnectScheduler(policy, clock=clock)
        scheduler.start()
        scheduler.note_attempt()
        assert "1/10" in scheduler.describe()
        assert "2.0s" in scheduler.describe()

    def test_restart_resets_attempts(self, clock):
        policy = ReconnectPolicy(max_attempts=3, base_delay=0.1, jitter=0.0, cooldown=0.0)
        scheduler = ReconnectScheduler(policy, clock=clock)
        scheduler.start()
        scheduler.note_attempt()
        scheduler.note_attempt()
        scheduler.start()
        assert scheduler.attempt == 0
        assert scheduler.active is True
        assert scheduler.exhausted() is False
