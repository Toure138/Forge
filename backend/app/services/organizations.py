"""Organizations: creation, the user's own list, rename (FR-13, FR-13 bis, FR-16)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text, update

from app.core.authz import CREATOR_ROLE, Role
from app.db import tables
from app.db.context import RequestDb
from app.services import audit

# SECURITY DEFINER function (migration 0002): the current user's memberships, across
# organizations, and nothing else (R1). Already sorted by organization name.
_MY_MEMBERSHIPS = text("SELECT organization_id, organization_name, role FROM app_my_memberships()")


@dataclass(frozen=True)
class Organization:
    """An organization as seen by one of its members."""

    id: UUID
    name: str
    role: Role


async def create(db: RequestDb, *, name: str, now: datetime) -> Organization:
    """New organization whose sole Owner is the current user, and `organization.created` in its
    audit log, in the request's transaction.

    The id is generated here and the context set on it BEFORE the INSERT: the RLS `WITH CHECK`
    only accepts rows of the active organization."""
    if db.user_id is None:
        raise RuntimeError("creating an organization needs the current user (set_user)")
    organization_id = uuid4()
    await db.set_organization(organization_id)
    await db.connection.execute(
        insert(tables.organizations).values(id=organization_id, name=name, created_at=now)
    )
    await db.connection.execute(
        insert(tables.memberships).values(
            organization_id=organization_id,
            user_id=db.user_id,
            role=CREATOR_ROLE.value,
            created_at=now,
            updated_at=now,
        )
    )
    await audit.record(
        db,
        action="organization.created",
        outcome="success",
        now=now,
        target_type="organization",
        target_id=str(organization_id),
        details={"name": name},
    )
    return Organization(organization_id, name, CREATOR_ROLE)


async def list_mine(db: RequestDb) -> list[Organization]:
    """The current user's organizations and role in each (FR-16): only their memberships."""
    if db.user_id is None:
        raise RuntimeError("listing one's organizations needs the current user (set_user)")
    rows = (await db.connection.execute(_MY_MEMBERSHIPS)).all()
    return [
        Organization(row.organization_id, row.organization_name, Role(row.role)) for row in rows
    ]


async def rename(db: RequestDb, *, name: str, now: datetime) -> None:
    """Rename the active organization (FR-13 bis); `organization.renamed` with both names."""
    if db.organization_id is None:
        raise RuntimeError("renaming needs the active organization (set_organization)")
    o = tables.organizations
    current = select(o.c.name).where(o.c.id == db.organization_id).with_for_update()
    old_name: str = (await db.connection.execute(current)).scalar_one()
    await db.connection.execute(update(o).where(o.c.id == db.organization_id).values(name=name))
    await audit.record(
        db,
        action="organization.renamed",
        outcome="success",
        now=now,
        target_type="organization",
        target_id=str(db.organization_id),
        details={"from_name": old_name, "to_name": name},
    )
