from datetime import UTC, datetime, timedelta

from app.core.clock import Clock, SystemClock
from tests.fakes.clock import FakeClock


def test_system_clock_is_timezone_aware_utc() -> None:
    clock: Clock = SystemClock()
    before = datetime.now(UTC)
    now = clock.now()
    after = datetime.now(UTC)
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert before <= now <= after


def test_fake_clock_advances() -> None:
    start = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    clock: Clock = FakeClock(start)
    assert clock.now() == start
    assert clock.now() == start  # frozen until advanced

    assert isinstance(clock, FakeClock)
    clock.advance(hours=23, minutes=59)
    assert clock.now() == start + timedelta(hours=23, minutes=59)
    clock.advance(days=7)
    assert clock.now() == start + timedelta(days=7, hours=23, minutes=59)
    assert clock.now().utcoffset() == timedelta(0)


def test_fake_clock_defaults_to_a_fixed_utc_instant() -> None:
    assert FakeClock().now() == FakeClock().now()
    assert FakeClock().now().utcoffset() == timedelta(0)
