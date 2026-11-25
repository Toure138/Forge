"""Invitations (FR-18 to FR-22).

Organization side (active organization, `require(...)` already passed): create, list the pending
ones, revoke. Invitee side (no organization yet): `list_mine`, `accept`, `decline`, through the
SECURITY DEFINER functions limited to the current user; the invitation's organization becomes the
context only once the invitation is proven to be addressed to the user.

Emails are compared in their normalized form (`normalize_email`: case ignored, R6). Every query of
the organization side filters explicitly on the active organization, with RLS underneath.
Expiry: an invitation is expired when `now >= expires_at` (injected clock).
"""

from collections.abc import Set
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Row, exists, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.auth.users import normalize_email
from app.core.authz import Role, missing_role_permission, permission_denied
from app.core.errors import ApiError
from app.db import tables
from app.db.context import RequestDb
from app.services import audit

PENDING = "pending"


def already_member() -> ApiError:
    return ApiError(409, "already_member", "This person is already a member of the organization.")


def already_pending() -> ApiError:
    return ApiError(
        409, "invitation_already_pending", "A pending invitation already exists for this email."
    )


def invitation_not_found() -> ApiError:
    """Same answer for an unknown invitation, one of another organization (FR-37), one that is no
    longer pending, or one addressed to someone else (FR-19)."""
    return ApiError(404, "not_found", "Not found.")


@dataclass(frozen=True)
class InvitationInfo:
    id: UUID
    email: str
    role: Role
    invited_by_id: UUID
    invited_by_name: str
    created_at: datetime
    expires_at: datetime


def _organization(db: RequestDb) -> UUID:
    if db.organization_id is None:
        raise RuntimeError("invitations need the active organization (set_organization)")
    return db.organization_id


def _info(row: Row[Any]) -> InvitationInfo:
    return InvitationInfo(
        id=row.id,
        email=row.email_normalized,
        role=Role(row.role),
        invited_by_id=row.invited_by,
        invited_by_name=row.invited_by_name,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


def _with_inviter() -> Any:
    i, u = tables.invitations, tables.users
    return select(
        i.c.id,
        i.c.email_normalized,
        i.c.role,
        i.c.invited_by,
        u.c.name.label("invited_by_name"),
        i.c.created_at,
        i.c.expires_at,
    ).join(u, u.c.id == i.c.invited_by)


async def _is_member(db: RequestDb, email_normalized: str) -> bool:
    m, u = tables.memberships, tables.users
    statement = select(
        exists()
        .where(m.c.organization_id == _organization(db))
        .where(m.c.user_id == u.c.id)
        .where(u.c.email_normalized == email_normalized)
    )
    return bool((await db.connection.execute(statement)).scalar_one())


async def create(
    db: RequestDb,
    *,
    permissions: Set[str],
    email: str,
    role: Role,
    ttl: timedelta,
    now: datetime,
) -> InvitationInfo:
    """Invite `email` with `role` into the active organization (FR-18, FR-20, FR-21, R5).

    403 unless `permissions` allow managing `role` (refusal audited); 409 `already_member`; 409
    `invitation_already_pending` while a non-expired one exists for that email (an expired one is
    marked `expired` first). No email is sent (FR-22)."""
    organization_id = _organization(db)
    email_normalized = normalize_email(email)
    missing = missing_role_permission(permissions, role)
    if missing is not None:
        await db.record_denial(
            permission_denied(),
            action="permission.denied",
            now=now,
            target_type="invitation",
            details={"permission": missing, "role": role.value},
        )
    if await _is_member(db, email_normalized):
        raise already_member()
    i = tables.invitations
    await db.connection.execute(
        update(i)
        .where(i.c.organization_id == organization_id)
        .where(i.c.email_normalized == email_normalized)
        .where(i.c.status == PENDING)
        .where(i.c.expires_at <= now)
        .values(status="expired", decided_at=now)
    )
    invitation_id = uuid4()
    statement = (
        insert(i)
        .values(
            id=invitation_id,
            organization_id=organization_id,
            email_normalized=email_normalized,
            role=role.value,
            invited_by=db.user_id,
            created_at=now,
            expires_at=now + ttl,
            status=PENDING,
        )
        # The partial unique index « one pending invitation per (organization, email) » decides,
        # even between two concurrent requests.
        .on_conflict_do_nothing(
            index_elements=[i.c.organization_id, i.c.email_normalized],
            index_where=i.c.status == PENDING,
        )
        .returning(i.c.id)
    )
    if (await db.connection.execute(statement)).scalar_one_or_none() is None:
        raise already_pending()
    await audit.record(
        db,
        action="invitation.created",
        outcome="success",
        now=now,
        target_type="invitation",
        target_id=str(invitation_id),
        details={"role": role.value},
    )
    created = await _pending(db, invitation_id, now=now)
    assert created is not None
    return created


async def _pending(db: RequestDb, invitation_id: UUID, *, now: datetime) -> InvitationInfo | None:
    i = tables.invitations
    statement = (
        _with_inviter()
        .where(i.c.organization_id == _organization(db))
        .where(i.c.id == invitation_id)
        .where(i.c.status == PENDING)
        .where(i.c.expires_at > now)
    )
    row = (await db.connection.execute(statement)).one_or_none()
    return None if row is None else _info(row)


async def list_pending(db: RequestDb, *, now: datetime) -> list[InvitationInfo]:
    """Pending, non-expired invitations of the active organization, oldest first."""
    i = tables.invitations
    statement = (
        _with_inviter()
        .where(i.c.organization_id == _organization(db))
        .where(i.c.status == PENDING)
        .where(i.c.expires_at > now)
        .order_by(i.c.created_at, i.c.id)
    )
    return [_info(row) for row in (await db.connection.execute(statement)).all()]


async def revoke(
    db: RequestDb, *, permissions: Set[str], invitation_id: UUID, now: datetime
) -> None:
    """Revoke a pending, non-expired invitation of the active organization (FR-20).

    404 otherwise (also for another organization's invitation, FR-37); 403 unless `permissions`
    allow managing its role (AC-25; refusal audited)."""
    i = tables.invitations
    locked = (
        select(i.c.role)
        .where(i.c.organization_id == _organization(db))
        .where(i.c.id == invitation_id)
        .where(i.c.status == PENDING)
        .where(i.c.expires_at > now)
        .with_for_update()
    )
    role_value = (await db.connection.execute(locked)).scalar_one_or_none()
    if role_value is None:
        raise invitation_not_found()
    role = Role(role_value)
    missing = missing_role_permission(permissions, role)
    if missing is not None:
        await db.record_denial(
            permission_denied(),
            action="permission.denied",
            now=now,
            target_type="invitation",
            target_id=str(invitation_id),
            details={"permission": missing, "role": role.value},
        )
    await db.connection.execute(
        update(i)
        .where(i.c.organization_id == _organization(db))
        .where(i.c.id == invitation_id)
        .values(status="revoked", decided_at=now, decided_by=db.user_id)
    )
    await audit.record(
        db,
        action="invitation.revoked",
        outcome="success",
        now=now,
        target_type="invitation",
        target_id=str(invitation_id),
        details={"role": role.value},
    )


# --- invitee side: the current user's own invitations, across organizations ---

# SECURITY DEFINER functions (migration 0002): they only ever return invitations addressed to
# the current user's email (`app.user_email`), whatever the active organization (R1).
_MY_PENDING = text(
    "SELECT id, organization_id, organization_name, role, invited_by_name, expires_at"
    " FROM app_my_pending_invitations(:now)"
)
_MY_INVITATION = text(
    "SELECT organization_id, email_normalized, role, status, expires_at"
    " FROM app_my_invitation(:invitation_id)"
)


def invitation_expired() -> ApiError:
    return ApiError(410, "invitation_expired", "This invitation has expired.")


@dataclass(frozen=True)
class MyInvitationInfo:
    id: UUID
    organization_id: UUID
    organization_name: str
    role: Role
    invited_by_name: str
    expires_at: datetime


@dataclass(frozen=True)
class JoinedOrganization:
    id: UUID
    name: str
    role: Role


async def list_mine(db: RequestDb, *, now: datetime) -> list[MyInvitationInfo]:
    """Pending, non-expired invitations addressed to the current user (FR-22), soonest expiry
    first."""
    if db.user_id is None or db.user_email is None:
        raise RuntimeError("one's invitations need the current user (set_user)")
    rows = (await db.connection.execute(_MY_PENDING, {"now": now})).all()
    return [
        MyInvitationInfo(
            id=row.id,
            organization_id=row.organization_id,
            organization_name=row.organization_name,
            role=Role(row.role),
            invited_by_name=row.invited_by_name,
            expires_at=row.expires_at,
        )
        for row in sorted(rows, key=lambda r: (r.expires_at, r.id))
    ]


async def _prove_mine(db: RequestDb, invitation_id: UUID) -> Row[Any]:
    """The pending invitation `invitation_id` IF it is addressed to the current user; otherwise
    404, the same answer as for an unknown invitation (FR-19, FR-37). No organization context is
    set yet: the function reads across organizations, for the current user only."""
    if db.user_id is None or db.user_email is None:
        raise RuntimeError("answering an invitation needs the current user (set_user)")
    result = await db.connection.execute(_MY_INVITATION, {"invitation_id": invitation_id})
    row = result.one_or_none()
    # The function already filters on the user's email; checked again here (defense in depth).
    if row is None or row.status != PENDING or row.email_normalized != db.user_email:
        raise invitation_not_found()
    return row


async def _decide(
    db: RequestDb, invitation_id: UUID, *, accept: bool, now: datetime
) -> tuple[UUID, Role]:
    """Shared by accept and decline: proof, THEN organization context, expiry, single use."""
    proof = await _prove_mine(db, invitation_id)
    organization_id: UUID = proof.organization_id
    # Only now that the invitation is proven to be the user's does its organization become the
    # context (forge_app then sees that organization's rows for the rest of the request).
    await db.set_organization(organization_id)
    i = tables.invitations
    pending = (
        update(i)
        .where(i.c.organization_id == organization_id)
        .where(i.c.id == invitation_id)
        .where(i.c.status == PENDING)
    )
    role = Role(proof.role)
    if now >= proof.expires_at:
        await db.connection.execute(pending.values(status="expired", decided_at=now))
        await audit.record(
            db,
            action="invitation.expired_on_use",
            outcome="denied",
            now=now,
            target_type="invitation",
            target_id=str(invitation_id),
            details={"role": role.value},
        )
        await db.commit_and_raise(invitation_expired())  # stays expired despite the 410
    status = "accepted" if accept else "declined"
    decided = pending.values(status=status, decided_at=now, decided_by=db.user_id)
    if (await db.connection.execute(decided.returning(i.c.id))).scalar_one_or_none() is None:
        raise invitation_not_found()  # answered concurrently: single use
    return organization_id, role


async def accept(db: RequestDb, invitation_id: UUID, *, now: datetime) -> JoinedOrganization:
    """Accept an invitation addressed to the current user: member with the planned role (FR-19).

    404 if unknown, not pending or not the user's; 410 if expired (marked expired and audited);
    409 `already_member` (nothing changes: the invitation stays pending)."""
    organization_id, role = await _decide(db, invitation_id, accept=True, now=now)
    m = tables.memberships
    joined = (
        insert(m)
        .values(
            organization_id=organization_id,
            user_id=db.user_id,
            role=role.value,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[m.c.organization_id, m.c.user_id])
        .returning(m.c.user_id)
    )
    if (await db.connection.execute(joined)).scalar_one_or_none() is None:
        raise already_member()
    await audit.record(
        db,
        action="invitation.accepted",
        outcome="success",
        now=now,
        target_type="invitation",
        target_id=str(invitation_id),
        details={"role": role.value},
    )
    o = tables.organizations
    name_query = select(o.c.name).where(o.c.id == organization_id)
    name: str = (await db.connection.execute(name_query)).scalar_one()
    return JoinedOrganization(organization_id, name, role)


async def decline(db: RequestDb, invitation_id: UUID, *, now: datetime) -> None:
    """Decline an invitation addressed to the current user (FR-19): no longer usable, audited.
    Same errors as `accept` (404, 410)."""
    _, role = await _decide(db, invitation_id, accept=False, now=now)
    await audit.record(
        db,
        action="invitation.declined",
        outcome="success",
        now=now,
        target_type="invitation",
        target_id=str(invitation_id),
        details={"role": role.value},
    )
