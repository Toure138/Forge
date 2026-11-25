"""Memberships of the active organization: leave, list, change a role, remove (FR-26 to FR-30).

Access control is done with permissions (`authz.can_change_role`, `authz.can_manage_role`). The
only role name used here is `Role.OWNER`, for the business invariant « an organization always has
at least one Owner » (FR-28), which is not an access rule.

Concurrency (AC-35): every operation that could take away an Owner first locks the rows of the
organization's Owners (`SELECT … FOR UPDATE`, always in the same order), then reads its target.
Two Owners demoting each other at the same time are therefore serialized: the second one sees the
result of the first and is refused with `last_owner`. Locking the Owners FIRST, then the target,
in every operation, avoids lock-order deadlocks.

Every query filters explicitly on the active organization, with RLS underneath (constitution X).
"""

from collections.abc import Set
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Select, delete, select, update

from app.core.authz import Role, missing_role_permission, permission_denied
from app.core.errors import ApiError
from app.db import tables
from app.db.context import RequestDb
from app.services import audit


def last_owner() -> ApiError:
    return ApiError(409, "last_owner", "An organization must keep at least one owner.")


def member_not_found() -> ApiError:
    """Same answer for a user who is not a member and for a member of another organization
    (invisible under RLS): FR-37."""
    return ApiError(404, "not_found", "Not found.")


@dataclass(frozen=True)
class _Target:
    user_id: UUID
    role: Role


def _organization(db: RequestDb) -> UUID:
    if db.organization_id is None:
        raise RuntimeError("memberships need the active organization (set_organization)")
    return db.organization_id


async def _lock_owners(db: RequestDb) -> set[UUID]:
    """Lock the Owners' membership rows (FR-28 invariant); returns their user ids."""
    m = tables.memberships
    statement = (
        select(m.c.user_id)
        .where(m.c.organization_id == _organization(db))
        .where(m.c.role == Role.OWNER.value)
        .order_by(m.c.user_id)
        .with_for_update()
    )
    return set((await db.connection.execute(statement)).scalars())


async def _lock_member(db: RequestDb, user_id: UUID) -> _Target | None:
    m = tables.memberships
    statement = (
        select(m.c.user_id, m.c.role)
        .where(m.c.organization_id == _organization(db))
        .where(m.c.user_id == user_id)
        .with_for_update()
    )
    row = (await db.connection.execute(statement)).one_or_none()
    return None if row is None else _Target(row.user_id, Role(row.role))


def _would_remove_last_owner(target: _Target, owners: set[UUID]) -> bool:
    return target.role is Role.OWNER and owners <= {target.user_id}


async def _delete(db: RequestDb, user_id: UUID) -> None:
    m = tables.memberships
    await db.connection.execute(
        delete(m).where(m.c.organization_id == _organization(db)).where(m.c.user_id == user_id)
    )


async def leave(db: RequestDb, *, now: datetime) -> None:
    """The current user leaves the active organization (FR-29), unless they are its last Owner
    (409 `last_owner`, refusal audited)."""
    if db.user_id is None:
        raise RuntimeError("leaving needs the current user (set_user)")
    owners = await _lock_owners(db)
    me = await _lock_member(db, db.user_id)
    if me is None:  # left concurrently: the membership proven by `require` is gone
        raise member_not_found()
    if _would_remove_last_owner(me, owners):
        await db.record_denial(
            last_owner(),
            action="member.left",
            now=now,
            target_type="member",
            target_id=str(me.user_id),
            details={"role": me.role.value, "reason": "last_owner"},
        )
    await _delete(db, me.user_id)
    await audit.record(
        db,
        action="member.left",
        outcome="success",
        now=now,
        target_type="member",
        target_id=str(me.user_id),
        details={"role": me.role.value},
    )


@dataclass(frozen=True)
class MemberInfo:
    user_id: UUID
    name: str
    email: str
    picture_url: str | None
    role: Role
    joined_at: datetime


def _members_query(db: RequestDb) -> Select[Any]:
    """Members of the active organization with their profile (`users` is global: it is only read
    through the organization's memberships)."""
    m, u = tables.memberships, tables.users
    return (
        select(m.c.user_id, u.c.name, u.c.email, u.c.picture_url, m.c.role, m.c.created_at)
        .join(u, u.c.id == m.c.user_id)
        .where(m.c.organization_id == _organization(db))
    )


def _member_info(row: Row[Any]) -> MemberInfo:
    return MemberInfo(
        user_id=row.user_id,
        name=row.name,
        email=row.email,
        picture_url=row.picture_url,
        role=Role(row.role),
        joined_at=row.created_at,
    )


async def list_members(db: RequestDb) -> list[MemberInfo]:
    """Members of the active organization, by name."""
    m, u = tables.memberships, tables.users
    statement = _members_query(db).order_by(u.c.name, m.c.user_id)
    return [_member_info(row) for row in (await db.connection.execute(statement)).all()]


async def get_member(db: RequestDb, user_id: UUID) -> MemberInfo | None:
    statement = _members_query(db).where(tables.memberships.c.user_id == user_id)
    row = (await db.connection.execute(statement)).one_or_none()
    return None if row is None else _member_info(row)


async def change_role(
    db: RequestDb, *, permissions: Set[str], user_id: UUID, role: Role, now: datetime
) -> MemberInfo:
    """Give `role` to the member `user_id` (FR-27, FR-27 bis, FR-30).

    404 if not a member of the active organization (also for another organization's member,
    FR-37); 403 unless `permissions` allow managing both the current and the new role
    (`can_change_role`; refusal audited); 409 `last_owner` if the last Owner would lose the role
    (refusal audited)."""
    owners = await _lock_owners(db)
    target = await _lock_member(db, user_id)
    if target is None:
        raise member_not_found()
    roles = {"from_role": target.role.value, "to_role": role.value}
    missing = missing_role_permission(permissions, target.role, role)
    if missing is not None:
        await db.record_denial(
            permission_denied(),
            action="permission.denied",
            now=now,
            target_type="member",
            target_id=str(user_id),
            details={"permission": missing, **roles},
        )
    if role is not target.role:
        if _would_remove_last_owner(target, owners):
            await db.record_denial(
                last_owner(),
                action="member.role_changed",
                now=now,
                target_type="member",
                target_id=str(user_id),
                details={**roles, "reason": "last_owner"},
            )
        m = tables.memberships
        await db.connection.execute(
            update(m)
            .where(m.c.organization_id == _organization(db))
            .where(m.c.user_id == user_id)
            .values(role=role.value, updated_at=now)
        )
        await audit.record(
            db,
            action="member.role_changed",
            outcome="success",
            now=now,
            target_type="member",
            target_id=str(user_id),
            details=roles,
        )
    member = await get_member(db, user_id)
    assert member is not None  # locked above
    return member


async def remove(db: RequestDb, *, permissions: Set[str], user_id: UUID, now: datetime) -> None:
    """Remove the member `user_id` from the active organization (FR-26, FR-27).

    404 if not a member (FR-37); 403 unless `permissions` allow managing the member's role
    (`can_manage_role`; refusal audited); 409 `last_owner` for the last Owner (refusal audited).
    Their other memberships are untouched (other organizations are not even visible here)."""
    owners = await _lock_owners(db)
    target = await _lock_member(db, user_id)
    if target is None:
        raise member_not_found()
    missing = missing_role_permission(permissions, target.role)
    if missing is not None:
        await db.record_denial(
            permission_denied(),
            action="permission.denied",
            now=now,
            target_type="member",
            target_id=str(user_id),
            details={"permission": missing, "role": target.role.value},
        )
    if _would_remove_last_owner(target, owners):
        await db.record_denial(
            last_owner(),
            action="member.removed",
            now=now,
            target_type="member",
            target_id=str(user_id),
            details={"role": target.role.value, "reason": "last_owner"},
        )
    await _delete(db, user_id)
    await audit.record(
        db,
        action="member.removed",
        outcome="success",
        now=now,
        target_type="member",
        target_id=str(user_id),
        details={"role": target.role.value},
    )
