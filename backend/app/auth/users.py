"""Forge users, identified by Google's stable `sub` (FR-3). Table `users` is global (FR-33)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert

from app.auth.provider import VerifiedIdentity
from app.db import tables
from app.db.context import RequestDb


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    email_normalized: str
    name: str
    picture_url: str | None


def normalize_email(email: str) -> str:
    """Comparison form of an email: case is ignored, nothing else (R6: no Gmail alias folding)."""
    return email.strip().lower()


async def upsert_user(db: RequestDb, identity: VerifiedIdentity, *, now: datetime) -> User:
    """Create the user of `identity.sub`, or refresh its email, name and picture (same id)."""
    u = tables.users
    profile = {
        "email": identity.email,
        "email_normalized": normalize_email(identity.email),
        "name": identity.name,
        "picture_url": identity.picture,
        "last_login_at": now,
    }
    statement = (
        insert(u)
        .values(id=uuid4(), google_sub=identity.sub, created_at=now, **profile)
        .on_conflict_do_update(index_elements=[u.c.google_sub], set_=profile)
        .returning(u.c.id, u.c.email, u.c.email_normalized, u.c.name, u.c.picture_url)
    )
    row = (await db.connection.execute(statement)).one()
    return User(
        id=row.id,
        email=row.email,
        email_normalized=row.email_normalized,
        name=row.name,
        picture_url=row.picture_url,
    )
