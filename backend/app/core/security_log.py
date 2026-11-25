"""Platform security log (plan, « Journalisation »; FR-39, FR-41).

Events that belong to no organization (sign-in, sign-out, rejected sessions, access attempts to
an organization one is not a member of) go to the `forge.security` logger: the console locally,
Cloud Logging in the target (ADR-0002 D14).

`security_event` has a closed signature (name, user id, organization id, reason), and names and
reasons come from closed lists: a token, a cookie, a code or an email cannot be passed by mistake.

`RedactQueryFilter` hides the query string of `/api/auth/*` in uvicorn's access log (the callback
URL carries Google's `code` and the `state`). `create_app` installs it.
"""

import logging
import re
from typing import Final, Literal, get_args
from uuid import UUID

logger = logging.getLogger("forge.security")

SecurityEventName = Literal[
    "login_succeeded",
    "login_failed",
    "logout",
    "session_rejected",
    "organization_access_denied",
]
LoginFailureReason = Literal[
    "email_not_verified",
    "state_mismatch",
    "missing_login_cookie",
    "replayed",
    "invalid_id_token",
    "provider_error",
    "cancelled",
]
SessionRejectionReason = Literal["unknown", "revoked", "idle_expired", "absolute_expired"]
SecurityReason = LoginFailureReason | SessionRejectionReason

# Reasons allowed per event; an event absent from this table takes no reason.
_REASONS: Final[dict[str, frozenset[str]]] = {
    "login_failed": frozenset(get_args(LoginFailureReason)),
    "session_rejected": frozenset(get_args(SessionRejectionReason)),
}
_NAMES: Final = frozenset(get_args(SecurityEventName))
_WARNINGS: Final = frozenset({"login_failed", "session_rejected", "organization_access_denied"})


def security_event(
    name: SecurityEventName,
    *,
    user_id: UUID | None = None,
    organization_id: UUID | None = None,
    reason: SecurityReason | None = None,
) -> None:
    """Log a platform security event. Only Forge identifiers, never an email (minimization)."""
    if name not in _NAMES:
        raise ValueError(f"unknown security event {name!r}")
    allowed = _REASONS.get(name)
    if allowed is None and reason is not None:
        raise ValueError(f"security event {name!r} takes no reason")
    if allowed is not None and reason not in allowed:
        raise ValueError(f"invalid reason {reason!r} for security event {name!r}")
    fields = {
        "name": name,
        "user_id": str(user_id) if user_id else None,
        "organization_id": str(organization_id) if organization_id else None,
        "reason": reason,
    }
    logger.log(
        logging.WARNING if name in _WARNINGS else logging.INFO,
        "security_event %s",
        " ".join(f"{key}={value}" for key, value in fields.items() if value is not None),
        extra={"security_event": fields},
    )


_AUTH_QUERY = re.compile(r"^(/api/auth/[^?\s]*)\?\S*$")


class RedactQueryFilter(logging.Filter):
    """Replace the query string of `/api/auth/*` paths in access-log records by `?[redacted]`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _AUTH_QUERY.sub(r"\1?[redacted]", arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True
