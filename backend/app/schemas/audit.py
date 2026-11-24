"""Organization audit log: API contract (`GET /api/organization/audit`).

The literals below are also the closed lists the audit service accepts (`app/services/audit.py`).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

AuditAction = Literal[
    "organization.created",
    "organization.renamed",
    "invitation.created",
    "invitation.accepted",
    "invitation.declined",
    "invitation.revoked",
    "invitation.expired_on_use",
    "member.role_changed",
    "member.removed",
    "member.left",
    "permission.denied",
]
AuditTargetType = Literal["organization", "member", "invitation"]
AuditOutcome = Literal["success", "denied"]
AuditDetailValue = str | int | bool | None


class AuditActor(BaseModel):
    """Who acted (null when unknown)."""

    user_id: UUID | None
    name: str | None


class AuditEvent(BaseModel):
    id: int
    occurred_at: datetime
    actor: AuditActor
    action: AuditAction
    target_type: AuditTargetType | None
    target_id: str | None
    outcome: AuditOutcome
    details: dict[str, AuditDetailValue]


class AuditPage(BaseModel):
    """Newest first. `next_before_id`: pass it as `before_id` for the next (older) page; null on
    the last page."""

    items: list[AuditEvent]
    next_before_id: int | None
