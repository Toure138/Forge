"""Identity and organizations: the 8 tables of spec 001.

Revision ID: 0001
Revises:
Create Date: 2026-11-24

Columns are written out here (not imported from app/db/tables.py) so that this revision stays
frozen in history; tests/db/test_migrations.py checks that both agree.
RLS, policies, functions and grants come in 0002.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = "role IN ('owner', 'admin', 'member', 'guest')"


def ts(name: str, *, nullable: bool = False) -> sa.Column[object]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    # --- global tables -------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("google_sub", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_normalized", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("picture_url", sa.Text(), nullable=True),
        ts("created_at"),
        ts("last_login_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
    )
    op.create_index("ix_users_email_normalized", "users", ["email_normalized"])

    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("csrf_token", sa.Text(), nullable=False),
        ts("created_at"),
        ts("last_seen_at"),
        ts("revoked_at", nullable=True),
        sa.PrimaryKeyConstraint("token_hash", name="pk_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "login_requests",
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        ts("created_at"),
        ts("expires_at"),
        ts("consumed_at", nullable=True),
        sa.PrimaryKeyConstraint("token_hash", name="pk_login_requests"),
    )

    # --- organization tables (RLS in 0002) --------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100 AND btrim(name) <> ''",
            name="ck_organizations_name_length",
        ),
    )

    op.create_table(
        "memberships",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        ts("created_at"),
        ts("updated_at"),
        sa.PrimaryKeyConstraint("organization_id", "user_id", name="pk_memberships"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_memberships_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_memberships_user_id_users"),
        sa.CheckConstraint(ROLES, name="ck_memberships_role"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("email_normalized", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("invited_by", sa.Uuid(), nullable=False),
        ts("created_at"),
        ts("expires_at"),
        sa.Column("status", sa.Text(), nullable=False),
        ts("decided_at", nullable=True),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_invitations_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"], ["users.id"], name="fk_invitations_invited_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_invitations_decided_by_users"
        ),
        sa.CheckConstraint(ROLES, name="ck_invitations_role"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'declined', 'revoked', 'expired')",
            name="ck_invitations_status",
        ),
    )
    op.create_index("ix_invitations_email_normalized", "invitations", ["email_normalized"])
    op.create_index(
        "uq_invitations_pending_email",
        "invitations",
        ["organization_id", "email_normalized"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_name", sa.Text(), nullable=False),
        ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_conversations_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_conversations_user_id_users"),
    )
    op.create_index(
        "ix_conversations_organization_id_user_id",
        "conversations",
        ["organization_id", "user_id"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        ts("occurred_at"),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_audit_events_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_audit_events_actor_user_id_users"
        ),
        sa.CheckConstraint("outcome IN ('success', 'denied')", name="ck_audit_events_outcome"),
        sa.CheckConstraint(
            "target_type IS NULL OR target_type IN ('organization', 'member', 'invitation')",
            name="ck_audit_events_target_type",
        ),
    )
    op.create_index("ix_audit_events_organization_id_id", "audit_events", ["organization_id", "id"])


def downgrade() -> None:
    for table in (
        "audit_events",
        "conversations",
        "invitations",
        "memberships",
        "organizations",
        "login_requests",
        "sessions",
        "users",
    ):
        op.drop_table(table)
