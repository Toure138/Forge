"""SQLAlchemy Core tables (no ORM, ADR-0003).

The schema itself is created by the Alembic migrations (`migrations/versions/`), which also hold
RLS, policies and grants. These objects are what the code queries with; a test checks that they
match the migrated schema.

Scopes (constitution X): `users`, `sessions` and `login_requests` are global; every other table
belongs to an organization (`organization_id`, or `id` for `organizations`) and is protected by
RLS (migration 0002).

No timestamp has a server default: dates come from the injected clock (`now` passed by services),
so that expirations are testable and never decided by the database's `now()`.
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

# Values allowed by the CHECK constraints. Role names mirror the authorization model
# (`app/core/authz.py`), which remains the only place that gives them meaning; the test
# `tests/test_authz.py::test_schema_literals_match_authz` keeps them identical. Not imported from
# `authz.py`: `authz` will depend on the request context (`app/db/context.py`), which queries these
# tables, so importing it here would create an import cycle.
ROLE_VALUES = ("owner", "admin", "member", "guest")
INVITATION_STATUS_VALUES = ("pending", "accepted", "declined", "revoked", "expired")
AUDIT_OUTCOME_VALUES = ("success", "denied")
AUDIT_TARGET_TYPE_VALUES = ("organization", "member", "invitation")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _ts(name: str, *, nullable: bool = False) -> Column[object]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("google_sub", Text, nullable=False, unique=True),
    Column("email", Text, nullable=False),
    Column("email_normalized", Text, nullable=False, index=True),
    Column("name", Text, nullable=False),
    Column("picture_url", Text),
    _ts("created_at"),
    _ts("last_login_at"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("token_hash", LargeBinary, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
    Column("csrf_token", Text, nullable=False),
    _ts("created_at"),
    _ts("last_seen_at"),
    _ts("revoked_at", nullable=True),
)

login_requests = Table(
    "login_requests",
    metadata,
    Column("token_hash", LargeBinary, primary_key=True),
    Column("state", Text, nullable=False),
    Column("nonce", Text, nullable=False),
    Column("code_verifier", Text, nullable=False),
    _ts("created_at"),
    _ts("expires_at"),
    _ts("consumed_at", nullable=True),
)

organizations = Table(
    "organizations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("name", Text, nullable=False),
    _ts("created_at"),
    # Names are NOT unique: a unique constraint would reveal that an organization exists (FR-16).
    CheckConstraint(
        "char_length(name) BETWEEN 1 AND 100 AND btrim(name) <> ''", name="name_length"
    ),
)

memberships = Table(
    "memberships",
    metadata,
    Column("organization_id", Uuid, ForeignKey("organizations.id"), nullable=False),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False, index=True),
    Column("role", Text, nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
    PrimaryKeyConstraint("organization_id", "user_id"),
    CheckConstraint(_in("role", ROLE_VALUES), name="role"),
)

invitations = Table(
    "invitations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("organization_id", Uuid, ForeignKey("organizations.id"), nullable=False),
    Column("email_normalized", Text, nullable=False, index=True),
    Column("role", Text, nullable=False),
    Column("invited_by", Uuid, ForeignKey("users.id"), nullable=False),
    _ts("created_at"),
    _ts("expires_at"),
    Column("status", Text, nullable=False),
    _ts("decided_at", nullable=True),
    Column("decided_by", Uuid, ForeignKey("users.id")),
    CheckConstraint(_in("role", ROLE_VALUES), name="role"),
    CheckConstraint(_in("status", INVITATION_STATUS_VALUES), name="status"),
    # At most one pending invitation per (organization, email); decided ones are history.
    Index(
        "uq_invitations_pending_email",
        "organization_id",
        "email_normalized",
        unique=True,
        postgresql_where=text("status = 'pending'"),
    ),
)

conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),  # = LangGraph thread_id
    Column("organization_id", Uuid, ForeignKey("organizations.id"), nullable=False),
    Column("user_id", Uuid, ForeignKey("users.id"), nullable=False),
    Column("agent_name", Text, nullable=False),
    _ts("created_at"),
    Index("ix_conversations_organization_id_user_id", "organization_id", "user_id"),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("organization_id", Uuid, ForeignKey("organizations.id"), nullable=False),
    _ts("occurred_at"),
    Column("actor_user_id", Uuid, ForeignKey("users.id")),
    Column("action", Text, nullable=False),
    Column("target_type", Text),
    Column("target_id", Text),
    Column("outcome", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    CheckConstraint(_in("outcome", AUDIT_OUTCOME_VALUES), name="outcome"),
    CheckConstraint(
        f"target_type IS NULL OR {_in('target_type', AUDIT_TARGET_TYPE_VALUES)}",
        name="target_type",
    ),
    # Newest-first pagination within an organization (`before_id`).
    Index("ix_audit_events_organization_id_id", "organization_id", "id"),
)
