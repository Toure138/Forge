"""Injectable clock (plan T14).

Every expiration decision (sessions, login requests, invitations) uses a `now` that comes from a
`Clock`, never from the database's `now()`: tests replace the clock and move time forward without
waiting. The application's clock lives in `app.state.clock`.
"""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant, timezone-aware, in UTC."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
