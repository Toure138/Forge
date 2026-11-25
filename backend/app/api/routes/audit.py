"""Audit log of the active organization (`X-Organization-Id`), read only (FR-40).

GET /organization/audit?before_id=&limit=  -> `AuditPage`, newest first (`audit:read`)

No route modifies or deletes an audit entry (AC-50).
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.authz import OrgContext, require
from app.db.context import RequestDbDep
from app.schemas.audit import AuditActor, AuditEvent, AuditPage
from app.services import audit

router = APIRouter(tags=["audit"])


@router.get("/organization/audit", name="list_audit_events", response_model=AuditPage)
async def list_audit_events(
    _: Annotated[OrgContext, require("audit:read")],
    db: RequestDbDep,
    before_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    entries, next_before_id = await audit.list_events(db, limit=limit, before_id=before_id)
    items = [
        AuditEvent(
            id=entry.id,
            occurred_at=entry.occurred_at,
            actor=AuditActor(user_id=entry.actor_user_id, name=entry.actor_name),
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            outcome=entry.outcome,
            details=entry.details,
        )
        for entry in entries
    ]
    return AuditPage(items=items, next_before_id=next_before_id)
