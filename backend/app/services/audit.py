"""Organization audit log (plan, « Journalisation »; FR-38, FR-40, FR-41).

`record` is the ONLY way the application writes to `audit_events` (the table is append-only for
`forge_app`: SELECT and INSERT only; `test_no_route_mutates_audit` checks the code). `list_events`
reads it, a page at a time. The entry belongs to the active organization of the request
and its actor is the current user; both come from the `RequestDb` context, never from the caller.

`details` is a flat mapping of scalars (roles, permission names, organization names): a structured
value (a token payload, a cookie jar...) cannot be written by construction (FR-41).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final, get_args
from uuid import UUID

from sqlalchemy import insert, select

from app.db import tables
from app.schemas.audit import AuditAction, AuditDetailValue, AuditOutcome, AuditTargetType

if TYPE_CHECKING:
    from app.db.context import RequestDb

AUDIT_ACTIONS: Final = frozenset(get_args(AuditAction))
AUDIT_OUTCOMES: Final = frozenset(get_args(AuditOutcome))
AUDIT_TARGET_TYPES: Final = frozenset(get_args(AuditTargetType))


def check_details(details: Mapping[str, AuditDetailValue] | None) -> dict[str, AuditDetailValue]:
    """A copy of `details`, or TypeError unless every key is a str and every value a str, int,
    bool or None (no float, bytes, list or mapping)."""
    clean: dict[str, AuditDetailValue] = {}
    for key, value in (details or {}).items():
        if not isinstance(key, str):
            raise TypeError(f"audit details keys must be str, got {type(key).__name__}")
        if value is not None and type(value) not in (str, int, bool):
            raise TypeError(
                f"audit details values must be str, int, bool or None; {key!r} is "
                f"{type(value).__name__}"
            )
        clean[key] = value
    return clean


async def record(
    db: "RequestDb",
    *,
    action: AuditAction,
    outcome: AuditOutcome,
    now: datetime,
    target_type: AuditTargetType | None = None,
    target_id: str | None = None,
    details: Mapping[str, AuditDetailValue] | None = None,
) -> int:
    """Append an entry to the audit log of the request's active organization; returns its id."""
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"unknown audit action {action!r}")
    if outcome not in AUDIT_OUTCOMES:
        raise ValueError(f"unknown audit outcome {outcome!r}")
    if target_type is not None and target_type not in AUDIT_TARGET_TYPES:
        raise ValueError(f"unknown audit target type {target_type!r}")
    if db.organization_id is None:
        raise RuntimeError("an audit entry needs an active organization (set_organization)")
    a = tables.audit_events
    statement = (
        insert(a)
        .values(
            organization_id=db.organization_id,
            occurred_at=now,
            actor_user_id=db.user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            details=check_details(details),
        )
        .returning(a.c.id)
    )
    event_id: int = (await db.connection.execute(statement)).scalar_one()
    return event_id


@dataclass(frozen=True)
class AuditEntry:
    id: int
    occurred_at: datetime
    actor_user_id: UUID | None
    actor_name: str | None
    action: AuditAction
    target_type: AuditTargetType | None
    target_id: str | None
    outcome: AuditOutcome
    details: dict[str, AuditDetailValue]


async def list_events(
    db: "RequestDb", *, limit: int, before_id: int | None = None
) -> tuple[list[AuditEntry], int | None]:
    """A page of the active organization's audit log, newest first: up to `limit` entries older
    than `before_id`, and the `before_id` of the next page (None on the last one)."""
    if db.organization_id is None:
        raise RuntimeError("reading the audit log needs an active organization (set_organization)")
    a, u = tables.audit_events, tables.users
    statement = (
        select(
            a.c.id,
            a.c.occurred_at,
            a.c.actor_user_id,
            u.c.name.label("actor_name"),
            a.c.action,
            a.c.target_type,
            a.c.target_id,
            a.c.outcome,
            a.c.details,
        )
        .outerjoin(u, u.c.id == a.c.actor_user_id)
        .where(a.c.organization_id == db.organization_id)
        .order_by(a.c.id.desc())
        .limit(limit + 1)  # one more tells whether an older page exists
    )
    if before_id is not None:
        statement = statement.where(a.c.id < before_id)
    rows = (await db.connection.execute(statement)).all()
    entries = [
        AuditEntry(
            id=row.id,
            occurred_at=row.occurred_at,
            actor_user_id=row.actor_user_id,
            actor_name=row.actor_name,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            outcome=row.outcome,
            details=row.details,
        )
        for row in rows[:limit]
    ]
    next_before_id = entries[-1].id if len(rows) > limit else None
    return entries, next_before_id
