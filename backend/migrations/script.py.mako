"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Rules (ADR-0003): RLS policies, grants and SQL functions are explicit SQL (op.execute), never
generated. Every organization table gets, in the SAME migration: organization_id, ENABLE + FORCE
ROW LEVEL SECURITY, a policy TO forge_app and minimal grants. Every upgrade has its downgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
