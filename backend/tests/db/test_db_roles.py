"""Postgres roles and databases (AC-45, AC-46): introspection against the real test database."""

import psycopg
import pytest

from tests.db.conftest import ORG_TABLES

# OID of the bootstrap superuser created by initdb (the image's POSTGRES_USER).
BOOTSTRAP_SUPERUSER_OID = 10


def test_no_role_bypasses_rls(pg_app: psycopg.Connection) -> None:
    rows = pg_app.execute(
        """
        SELECT rolname FROM pg_roles
        WHERE (rolbypassrls OR rolsuper) AND oid <> %s
        ORDER BY rolname
        """,
        (BOOTSTRAP_SUPERUSER_OID,),
    ).fetchall()
    assert rows == []


def test_roles_and_databases_exist(pg_app: psycopg.Connection) -> None:
    roles = {
        row[0]: row[1:]
        for row in pg_app.execute(
            """
            SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole
            FROM pg_roles
            WHERE rolname IN ('forge_owner', 'forge_app', 'forge_self_reader')
            """
        ).fetchall()
    }
    # (login, superuser, bypassrls, createdb, createrole)
    assert roles["forge_owner"][:3] == (True, False, False)
    assert roles["forge_app"] == (True, False, False, False, False)
    assert roles["forge_self_reader"][:3] == (False, False, False)

    owners = dict(
        pg_app.execute(
            """
            SELECT d.datname, pg_get_userbyid(d.datdba) FROM pg_database d
            WHERE d.datname IN ('forge', 'forge_test')
            """
        ).fetchall()
    )
    assert owners == {"forge": "forge_owner", "forge_test": "forge_owner"}

    # forge_owner may SET ROLE forge_self_reader (to give it functions) without inheriting it.
    grant = pg_app.execute(
        """
        SELECT m.inherit_option, m.set_option, m.admin_option FROM pg_auth_members m
        WHERE m.roleid = 'forge_self_reader'::regrole AND m.member = 'forge_owner'::regrole
        """
    ).fetchall()
    assert grant == [(False, True, False)]


# --- T004: RLS and minimal grants of forge_app (migration 0002) ----------------------------

# Global tables (constitution X whitelist): no organization, hence no RLS.
GLOBAL_TABLES = {"users", "sessions", "login_requests", "alembic_version"}

# Exactly the "Droits forge_app" table of the plan.
APP_PRIVILEGES = {
    "users": {"SELECT", "INSERT", "UPDATE"},
    "sessions": {"SELECT", "INSERT", "UPDATE"},
    "login_requests": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "organizations": {"SELECT", "INSERT", "UPDATE"},
    "memberships": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "invitations": {"SELECT", "INSERT", "UPDATE"},
    "conversations": {"SELECT", "INSERT"},
    "audit_events": {"SELECT", "INSERT"},
}


def unprotected_tables(conn: psycopg.Connection) -> list[str]:
    """Problems that break the rule « global (whitelisted) or organization-protected »."""
    rows = conn.execute(
        """
        SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
               EXISTS (SELECT 1 FROM pg_attribute a
                       WHERE a.attrelid = c.oid AND a.attname = 'organization_id'
                         AND NOT a.attisdropped),
               EXISTS (SELECT 1 FROM pg_policy p
                       WHERE p.polrelid = c.oid AND 'forge_app'::regrole = ANY (p.polroles))
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm')
        ORDER BY c.relname
        """
    ).fetchall()
    problems = []
    for name, kind, rls, force, has_org, app_policy in rows:
        if kind in ("v", "m"):
            problems.append(f"{name}: views bypass the RLS of their tables")
            continue
        if name in GLOBAL_TABLES:
            continue
        if not (has_org or name == "organizations"):
            problems.append(f"{name}: no organization_id column")
        if not (rls and force):
            problems.append(f"{name}: row level security not enabled AND forced")
        if not app_policy:
            problems.append(f"{name}: no policy TO forge_app")
    return problems


def test_app_role_cannot_bypass_rls(migrated_db: object, pg_app: psycopg.Connection) -> None:
    attrs = pg_app.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert pg_app.info.user == "forge_app"
    assert attrs == (False, False)
    owned = pg_app.execute(
        """
        SELECT 'class ' || relname FROM pg_class WHERE relowner = 'forge_app'::regrole
        UNION ALL SELECT 'function ' || proname FROM pg_proc WHERE proowner = 'forge_app'::regrole
        UNION ALL SELECT 'schema ' || nspname FROM pg_namespace
                  WHERE nspowner = 'forge_app'::regrole
        UNION ALL SELECT 'type ' || typname FROM pg_type WHERE typowner = 'forge_app'::regrole
        UNION ALL SELECT 'database ' || datname FROM pg_database
                  WHERE datdba = 'forge_app'::regrole
        """
    ).fetchall()
    assert owned == []
    # No role membership either (it could carry another role's ownership or attributes).
    memberships = pg_app.execute(
        "SELECT roleid::regrole::text FROM pg_auth_members WHERE member = 'forge_app'::regrole"
    ).fetchall()
    assert memberships == []


def test_every_table_is_global_or_org_protected(
    migrated_db: object, pg_owner: psycopg.Connection
) -> None:
    # The guard first proves it catches an unprotected table (synthetic, rolled back).
    with pg_owner.transaction(force_rollback=True):
        pg_owner.execute("CREATE TABLE guard_probe (id uuid, organization_id uuid)")
        pg_owner.execute("CREATE TABLE guard_probe_no_org (id uuid)")
        pg_owner.execute("CREATE VIEW guard_probe_view AS SELECT 1 AS one")
        flagged = unprotected_tables(pg_owner)
    assert "guard_probe: row level security not enabled AND forced" in flagged
    assert "guard_probe: no policy TO forge_app" in flagged
    assert "guard_probe_no_org: no organization_id column" in flagged
    assert "guard_probe_view: views bypass the RLS of their tables" in flagged

    assert unprotected_tables(pg_owner) == []


def test_app_table_privileges_are_exactly_the_plan(
    migrated_db: object, pg_app: psycopg.Connection
) -> None:
    rows = pg_app.execute(
        """
        SELECT table_name, privilege_type FROM information_schema.role_table_grants
        WHERE grantee = 'forge_app' AND table_schema = 'public'
        """
    ).fetchall()
    granted: dict[str, set[str]] = {}
    for table, privilege in rows:
        granted.setdefault(table, set()).add(privilege)
    assert granted == APP_PRIVILEGES
    # No column-level grant sneaking around the table-level ones.
    columns = pg_app.execute(
        """
        SELECT table_name, column_name, privilege_type FROM information_schema.column_privileges
        WHERE grantee = 'forge_app' AND table_schema = 'public'
          AND NOT has_table_privilege(
              'forge_app', quote_ident(table_schema) || '.' || quote_ident(table_name),
              privilege_type
          )
        """
    ).fetchall()
    assert columns == []


def test_audit_table_is_append_only_for_app(
    migrated_db: object, pg_app: psycopg.Connection
) -> None:
    privileges = {
        privilege: pg_app.execute(
            "SELECT has_table_privilege('audit_events', %s)", (privilege,)
        ).fetchone()[0]  # type: ignore[index]
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER")
    }
    assert privileges == {
        "SELECT": True,
        "INSERT": True,
        "UPDATE": False,
        "DELETE": False,
        "TRUNCATE": False,
        "TRIGGER": False,
    }
    for statement in (
        "UPDATE audit_events SET outcome = 'success'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="permission denied"):
            pg_app.execute(statement)
        pg_app.rollback()


def test_test_database_is_postgres_with_forced_rls(
    migrated_db: object, pg_app: psycopg.Connection
) -> None:
    database, version = pg_app.execute(
        "SELECT current_database(), current_setting('server_version_num')::int"
    ).fetchone()  # type: ignore[misc]
    assert database == "forge_test"
    assert version >= 180000
    forced = dict(
        pg_app.execute(
            """
            SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relname = ANY (%s)
            """,
            (sorted(ORG_TABLES),),
        ).fetchall()
    )
    assert forced == dict.fromkeys(ORG_TABLES, True)


# --- T005: SECURITY DEFINER whitelist (forge_self_reader) ---------------------------------

SECURITY_DEFINER_WHITELIST = {
    "app_my_memberships",
    "app_my_pending_invitations",
    "app_my_invitation",
}


def security_definer_functions(conn: psycopg.Connection) -> dict[str, tuple[object, ...]]:
    """Every SECURITY DEFINER function outside the system schemas, with its safety settings."""
    rows = conn.execute(
        """
        SELECT p.proname, pg_get_userbyid(p.proowner), p.proconfig, p.provolatile,
               has_function_privilege('public', p.oid, 'EXECUTE'),
               has_function_privilege('forge_app', p.oid, 'EXECUTE')
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        """
    ).fetchall()
    return {row[0]: row[1:] for row in rows}


def test_security_definer_functions_are_whitelisted(
    migrated_db: object, pg_owner: psycopg.Connection
) -> None:
    # The guard first proves it sees a synthetic SECURITY DEFINER function (rolled back).
    with pg_owner.transaction(force_rollback=True):
        pg_owner.execute(
            "CREATE FUNCTION guard_probe() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'"
        )
        assert "guard_probe" in security_definer_functions(pg_owner)

    expected = (
        "forge_self_reader",  # owner: NOLOGIN, no BYPASSRLS, its own read-only policies
        ["search_path=pg_catalog, public"],  # frozen search_path: no hijack by a homonym
        "s",  # STABLE
        False,  # EXECUTE revoked from PUBLIC
        True,  # EXECUTE granted to forge_app
    )
    assert security_definer_functions(pg_owner) == dict.fromkeys(
        SECURITY_DEFINER_WHITELIST, expected
    )


def test_self_reader_is_read_only(migrated_db: object, pg_owner: psycopg.Connection) -> None:
    """forge_self_reader can read what its policies allow, write nothing, create nothing."""
    writes = pg_owner.execute(
        """
        SELECT c.relname, w.privilege FROM pg_class c
        CROSS JOIN (VALUES ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE')) AS w(privilege)
        WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
          AND has_table_privilege('forge_self_reader', c.oid, w.privilege)
        """
    ).fetchall()
    assert writes == []
    can_create = pg_owner.execute(
        "SELECT has_schema_privilege('forge_self_reader', 'public', 'CREATE')"
    ).fetchone()
    assert can_create == (False,)
    policies = pg_owner.execute(
        """
        SELECT c.relname, p.polcmd FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
        WHERE 'forge_self_reader'::regrole = ANY (p.polroles)
        """
    ).fetchall()
    assert sorted(policies) == [("invitations", "r"), ("memberships", "r"), ("organizations", "r")]
