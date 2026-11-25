"""AC-27 (FR-23): every cell of the role matrix, through the API.

The matrix is the spec's table, copied AS IS below (not computed from `app/core/authz.py`): each
row is tried by one or more API actions, by a user holding each role. ✅ → 2xx, ❌ → 403
`permission_denied`.

The world is rebuilt for each case (empty database, then one organization with the acting user,
one target of each role and one pending invitation of each role), so no case depends on another.
To keep the suite fast (96 cases), the cases share ONE application, started once for the module,
and each world is written in a single transaction by `build_case` (local to this file): only the
acting user gets a session, and the browser switches to it.
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth.sessions import SESSION_COOKIE, hash_token, new_token
from tests.conftest import client_for, connect_test_database, create_test_app
from tests.db.conftest import insert, set_context
from tests.factories import CSRF_HEADER, ORG_HEADER
from tests.fakes.clock import FakeClock
from tests.fakes.google import FakeGoogle

# specs/001-organisations-auth-rbac/spec.md, FR-23, copied as is.
FR_23 = """
  | Action | Propriétaire | Admin | Membre | Invité |
  |---|:-:|:-:|:-:|:-:|
  | Voir ses organisations et son propre rôle, choisir l'organisation active | ✅ | ✅ | ✅ | ✅ |
  | Quitter l'organisation (FR-29) | ✅ (sauf dernier Propriétaire) | ✅ | ✅ | ✅ |
  | Utiliser les agents intégrés (spec 000) | ✅ | ✅ | ✅ | ❌ (FR-31) |
  | Voir les membres et les invitations en attente | ✅ | ✅ | ❌ | ❌ |
  | Inviter, retirer, changer de rôle des Membres et des Invités ; révoquer leurs invitations | ✅ | ✅ | ❌ | ❌ |
  | Nommer ou retirer des Admins | ✅ | ❌ | ❌ | ❌ |
  | Consulter le journal d'audit de l'organisation | ✅ | ✅ | ❌ | ❌ |
  | Nommer ou retirer des Propriétaires (FR-27 bis) | ✅ | ❌ | ❌ | ❌ |
  | Renommer l'organisation (FR-13 bis) | ✅ | ❌ | ❌ | ❌ |
"""  # noqa: E501

ROLES = ("owner", "admin", "member", "guest")  # the table's columns, in order
PERMISSION_DENIED = {"code": "permission_denied", "message": "Permission denied."}


def parse_matrix(table: str) -> dict[str, dict[str, bool]]:
    """{row label: {role: allowed}}; a cell is allowed when it starts with ✅."""
    rows = [line.strip() for line in table.strip().splitlines()][2:]  # header, separator
    matrix = {}
    for row in rows:
        label, *cells = (cell.strip() for cell in row.strip("|").split("|"))
        assert len(cells) == len(ROLES), row
        assert all(cell[0] in "✅❌" for cell in cells), row
        matrix[label] = {
            role: cell.startswith("✅") for role, cell in zip(ROLES, cells, strict=True)
        }
    return matrix


MATRIX = parse_matrix(FR_23)


@dataclass(frozen=True)
class Case:
    """The world of one case: `actor` holds the role under test in `org`; `targets[role]` is
    another member holding `role`; `invitations[role]` is a pending invitation for `role`."""

    org: uuid.UUID
    actor: uuid.UUID
    session_token: str
    csrf_token: str
    targets: dict[str, uuid.UUID]
    invitations: dict[str, uuid.UUID]


Action = Callable[[TestClient, Case], httpx.Response]


def set_role(target: str, role: str) -> Action:
    return lambda c, case: c.patch(
        f"/api/organization/members/{case.targets[target]}", json={"role": role}
    )


def remove(target: str) -> Action:
    return lambda c, case: c.delete(f"/api/organization/members/{case.targets[target]}")


def invite(role: str) -> Action:
    return lambda c, case: c.post(
        "/api/organization/invitations", json={"email": f"new-{role}@acme.example", "role": role}
    )


def revoke(role: str) -> Action:
    return lambda c, case: c.delete(f"/api/organization/invitations/{case.invitations[role]}")


# The API actions that try each row of the matrix (labels exactly as in FR_23).
ACTIONS: dict[str, dict[str, Action]] = {
    "Voir ses organisations et son propre rôle, choisir l'organisation active": {
        "list_my_organizations": lambda c, _: c.get("/api/organizations"),
        "get_active_organization": lambda c, _: c.get("/api/organization"),
    },
    "Quitter l'organisation (FR-29)": {
        "leave": lambda c, _: c.post("/api/organization/leave"),
    },
    "Utiliser les agents intégrés (spec 000)": {
        "list_agents": lambda c, _: c.get("/api/agents"),
    },
    "Voir les membres et les invitations en attente": {
        "list_members": lambda c, _: c.get("/api/organization/members"),
        "list_invitations": lambda c, _: c.get("/api/organization/invitations"),
    },
    "Inviter, retirer, changer de rôle des Membres et des Invités ; révoquer leurs invitations": {
        "invite_member": invite("member"),
        "invite_guest": invite("guest"),
        "remove_member": remove("member"),
        "remove_guest": remove("guest"),
        "change_member_to_guest": set_role("member", "guest"),
        "change_guest_to_member": set_role("guest", "member"),
        "revoke_member_invitation": revoke("member"),
        "revoke_guest_invitation": revoke("guest"),
    },
    "Nommer ou retirer des Admins": {
        "invite_admin": invite("admin"),
        "promote_member_to_admin": set_role("member", "admin"),
        "demote_admin_to_member": set_role("admin", "member"),
        "remove_admin": remove("admin"),
    },
    "Consulter le journal d'audit de l'organisation": {
        "read_audit": lambda c, _: c.get("/api/organization/audit"),
    },
    "Nommer ou retirer des Propriétaires (FR-27 bis)": {
        "invite_owner": invite("owner"),
        "promote_member_to_owner": set_role("member", "owner"),
        "demote_owner_to_admin": set_role("owner", "admin"),
        "remove_owner": remove("owner"),
    },
    "Renommer l'organisation (FR-13 bis)": {
        "rename": lambda c, _: c.patch("/api/organization", json={"name": "Acme Renamed"}),
    },
}

CASES = [
    (role, label, name) for label, actions in ACTIONS.items() for name in actions for role in ROLES
]


def test_every_row_of_the_matrix_is_tried() -> None:
    assert list(ACTIONS) == list(MATRIX)
    assert len(MATRIX) == 9
    assert all(ACTIONS[label] for label in MATRIX)


# --- one application for the module; one world per case ---


@pytest.fixture(scope="module")
def module_clock() -> FakeClock:
    return FakeClock()  # never advanced here: every session stays valid


@pytest.fixture(scope="module")
def module_google(module_clock: FakeClock) -> FakeGoogle:
    return FakeGoogle(module_clock)


@pytest.fixture(scope="module")
def browser(module_clock: FakeClock, module_google: FakeGoogle) -> Iterator[TestClient]:
    with client_for(create_test_app(module_clock, module_google)) as client:
        yield client


def _user(conn: psycopg.Connection, key: str, now: datetime) -> uuid.UUID:
    user = uuid.uuid4()
    email = f"{key.replace('_', '-')}@acme.example"
    row = {
        "id": user,
        "google_sub": f"sub-{user}",
        "email": email,
        "email_normalized": email,
        "name": key.replace("_", " ").title(),
        "picture_url": None,
        "created_at": now,
        "last_login_at": now,
    }
    insert(conn, "users", row)
    return user


def build_case(conn: psycopg.Connection, role: str, now: datetime) -> Case:
    """Acme with the actor (`role`), one other member of each role and one pending invitation of
    each role, in one transaction. Two Owners at least: leaving or demoting an Owner never meets
    the last-Owner rule (FR-28), which is not what this matrix is about."""
    actor = _user(conn, "actor", now)
    session_token, csrf_token = new_token(), new_token()
    session = {
        "token_hash": hash_token(session_token),
        "user_id": actor,
        "csrf_token": csrf_token,
        "created_at": now,
        "last_seen_at": now,
    }
    insert(conn, "sessions", session)
    targets = {r: _user(conn, f"target_{r}", now) for r in ROLES}
    org = uuid.uuid4()
    set_context(conn, org=org)
    insert(conn, "organizations", {"id": org, "name": "Acme", "created_at": now})
    for user, member_role in [(actor, role), *((t, r) for r, t in targets.items())]:
        row = {
            "organization_id": org,
            "user_id": user,
            "role": member_role,
            "created_at": now,
            "updated_at": now,
        }
        insert(conn, "memberships", row)
    invitations = {r: uuid.uuid4() for r in ROLES}
    for r, invitation in invitations.items():
        row = {
            "id": invitation,
            "organization_id": org,
            "email_normalized": f"pending-{r}@acme.example",
            "role": r,
            "invited_by": targets["owner"],
            "created_at": now,
            "expires_at": now + timedelta(days=7),
            "status": "pending",
        }
        insert(conn, "invitations", row)
    conn.commit()
    return Case(org, actor, session_token, csrf_token, targets, invitations)


@pytest.mark.parametrize(
    ("role", "label", "action"), CASES, ids=[f"{role}-{action}" for role, _, action in CASES]
)
def test_role_permission_matrix(
    clean_db: None,
    module_clock: FakeClock,
    browser: TestClient,
    role: str,
    label: str,
    action: str,
) -> None:
    with connect_test_database("TEST_DATABASE_URL") as conn:
        case = build_case(conn, role, module_clock.now())
    browser.cookies.set(SESSION_COOKIE, case.session_token)
    browser.headers[CSRF_HEADER] = case.csrf_token
    browser.headers[ORG_HEADER] = str(case.org)

    response = ACTIONS[label][action](browser, case)

    if MATRIX[label][role]:
        assert 200 <= response.status_code < 300, (response.status_code, response.text)
    else:
        assert response.status_code == 403, (response.status_code, response.text)
        assert response.json() == PERMISSION_DENIED
