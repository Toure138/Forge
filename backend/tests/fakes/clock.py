"""Frozen clock for tests: time only moves when the test says so."""

from datetime import UTC, datetime, timedelta

DEFAULT_START = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self, start: datetime = DEFAULT_START) -> None:
        if start.utcoffset() != timedelta(0):
            raise ValueError("FakeClock needs a timezone-aware UTC start")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        """Move time forward, e.g. `clock.advance(hours=24, minutes=1)` (`timedelta` arguments)."""
        self._now += timedelta(**delta)
