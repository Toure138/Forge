"""Row level security: context, FORCE RLS, forge_app grants, forge_self_reader reads.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24

Explicit SQL only (ADR-0003). The API connects as forge_app: not a superuser, no BYPASSRLS,
owner of nothing, with the table-by-table grants below. Organization tables have RLS ENABLED
and FORCED; their policies apply TO forge_app only, so the owner (forge_owner) sees nothing.

Context (set per transaction by the API with set_config(name, value, true)):
  app.organization_id, app.user_id, app.user_email (normalized, lower case).
After a transaction-local setting ends, current_setting() returns '' (not NULL) on a reused
connection: NULLIF turns that into NULL, so no row matches instead of ''::uuid raising.

Cross-organization « my … » reads go through a closed list of SECURITY DEFINER functions owned
by forge_self_reader (NOLOGIN, no BYPASSRLS). Inside them RLS still applies, with
forge_self_reader's own read-only policies: the current user's memberships, the invitations
addressed to the current user's email, and the organizations they point to. Adding a SECURITY
DEFINER function means updating the whitelist test and justifying it in the spec's plan.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Organization tables -> policy expression (USING and WITH CHECK) for forge_app.
ORG_POLICIES = {
    "organizations": "id = app_current_org()",
    "memberships": "organization_id = app_current_org()",
    "invitations": "organization_id = app_current_org()",
    # A conversation is visible to its owner only, even within the organization (AC-40).
    "conversations": "organization_id = app_current_org() AND user_id = app_current_user()",
    "audit_events": "organization_id = app_current_org()",
}

# Plan, table "Droits forge_app". No DELETE on organizations (out of scope), invitations
# (history) and conversations; audit_events is append-only (AC-50).
APP_GRANTS = {
    "users": "SELECT, INSERT, UPDATE",
    "sessions": "SELECT, INSERT, UPDATE",
    "login_requests": "SELECT, INSERT, UPDATE, DELETE",
    "organizations": "SELECT, INSERT, UPDATE",
    "memberships": "SELECT, INSERT, UPDATE, DELETE",
    "invitations": "SELECT, INSERT, UPDATE",
    "conversations": "SELECT, INSERT",
    "audit_events": "SELECT, INSERT",
}

CONTEXT_FUNCTIONS = {
    "app_current_org": (
        "uuid",
        "SELECT NULLIF(current_setting('app.organization_id', true), '')::uuid",
    ),
    "app_current_user": (
        "uuid",
        "SELECT NULLIF(current_setting('app.user_id', true), '')::uuid",
    ),
    "app_current_user_email": (
        "text",
        "SELECT lower(NULLIF(current_setting('app.user_email', true), ''))",
    ),
}


# forge_self_reader: read-only policies limited to the current user (ADR-0003 point 3).
# The subqueries of the organizations policy are themselves under the policies above.
SELF_READER_POLICIES = {
    "memberships": "user_id = app_current_user()",
    "invitations": "email_normalized = app_current_user_email()",
    "organizations": """
        EXISTS (SELECT 1 FROM public.memberships m
                WHERE m.organization_id = organizations.id
                  AND m.user_id = public.app_current_user())
        OR EXISTS (SELECT 1 FROM public.invitations i
                   WHERE i.organization_id = organizations.id
                     AND i.status = 'pending'
                     AND i.email_normalized = public.app_current_user_email())
    """,
}

# The whitelist. Each body ALSO filters explicitly on the current user (defense in depth).
SECURITY_DEFINER_FUNCTIONS = {
    "app_my_memberships()": """
        RETURNS TABLE (organization_id uuid, organization_name text, role text)
        AS $$
            SELECT m.organization_id, o.name, m.role
            FROM public.memberships m
            JOIN public.organizations o ON o.id = m.organization_id
            WHERE m.user_id = public.app_current_user()
            ORDER BY o.name, m.organization_id
        $$""",
    "app_my_pending_invitations(p_now timestamptz)": """
        RETURNS TABLE (id uuid, organization_id uuid, organization_name text, role text,
                       invited_by_name text, expires_at timestamptz)
        AS $$
            SELECT i.id, i.organization_id, o.name, i.role, u.name, i.expires_at
            FROM public.invitations i
            JOIN public.organizations o ON o.id = i.organization_id
            JOIN public.users u ON u.id = i.invited_by
            WHERE i.email_normalized = public.app_current_user_email()
              AND i.status = 'pending'
              AND i.expires_at > p_now
            ORDER BY i.created_at, i.id
        $$""",
    "app_my_invitation(p_id uuid)": """
        RETURNS TABLE (organization_id uuid, email_normalized text, role text, status text,
                       expires_at timestamptz)
        AS $$
            SELECT i.organization_id, i.email_normalized, i.role, i.status, i.expires_at
            FROM public.invitations i
            WHERE i.id = p_id
              AND i.email_normalized = public.app_current_user_email()
        $$""",
}
SECURITY_DEFINER_SIGNATURES = {
    "app_my_memberships()": "app_my_memberships()",
    "app_my_pending_invitations(p_now timestamptz)": "app_my_pending_invitations(timestamptz)",
    "app_my_invitation(p_id uuid)": "app_my_invitation(uuid)",
}


def upgrade() -> None:
    for name, (returns, body) in CONTEXT_FUNCTIONS.items():
        op.execute(
            f"CREATE FUNCTION {name}() RETURNS {returns} LANGUAGE sql STABLE AS $$ {body} $$"
        )

    for table, expression in ORG_POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY app_org_isolation ON {table} TO forge_app "
            f"USING ({expression}) WITH CHECK ({expression})"
        )

    for table, privileges in APP_GRANTS.items():
        op.execute(f"GRANT {privileges} ON {table} TO forge_app")

    # --- forge_self_reader -------------------------------------------------------------------
    for table, expression in SELF_READER_POLICIES.items():
        op.execute(f"GRANT SELECT ON {table} TO forge_self_reader")
        op.execute(
            f"CREATE POLICY self_reader_own_rows ON {table} AS PERMISSIVE FOR SELECT "
            f"TO forge_self_reader USING ({expression})"
        )
    # users is global (no RLS): only the columns the functions return (inviter's name).
    op.execute("GRANT SELECT (id, name) ON users TO forge_self_reader")

    # ALTER ... OWNER TO needs the new owner to have CREATE on the schema: granted for the
    # hand-over only, then revoked (forge_owner may SET ROLE forge_self_reader, see initdb).
    op.execute("GRANT CREATE ON SCHEMA public TO forge_self_reader")
    for header, definition in SECURITY_DEFINER_FUNCTIONS.items():
        signature = SECURITY_DEFINER_SIGNATURES[header]
        op.execute(
            f"CREATE FUNCTION {header} {definition} "
            "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public"
        )
        # ACL first, while forge_owner still owns the function: after the hand-over its REVOKE
        # would be a silent no-op (only a warning). The ACL follows the new owner.
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO forge_app")
        op.execute(f"ALTER FUNCTION {signature} OWNER TO forge_self_reader")
    op.execute("REVOKE CREATE ON SCHEMA public FROM forge_self_reader")


def downgrade() -> None:
    # The functions belong to forge_self_reader: only its owner may drop them.
    op.execute("SET ROLE forge_self_reader")
    for signature in SECURITY_DEFINER_SIGNATURES.values():
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("RESET ROLE")
    for table in SELF_READER_POLICIES:
        op.execute(f"DROP POLICY self_reader_own_rows ON {table}")
        op.execute(f"REVOKE ALL ON {table} FROM forge_self_reader")
    op.execute("REVOKE ALL ON users FROM forge_self_reader")

    for table in APP_GRANTS:
        op.execute(f"REVOKE ALL ON {table} FROM forge_app")

    for table in ORG_POLICIES:
        op.execute(f"DROP POLICY app_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for name in CONTEXT_FUNCTIONS:
        op.execute(f"DROP FUNCTION {name}()")
