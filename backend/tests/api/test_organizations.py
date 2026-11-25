"""Organizations through the API: active organization (`X-Organization-Id`), list, creation,
rename (AC-14 to AC-17, AC-59; FR-13 to FR-16, FR-37)."""

import logging
import uuid
from typing import Annotated, Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.authz import OrgContext, require
from tests.conftest import client_for, create_test_app
from tests.factories import CSRF_HEADER, ORG_HEADER, World

ORGANIZATION_NOT_FOUND = {"code": "organization_not_found", "message": "Organization not found."}
ORGANIZATION_REQUIRED = {
    "code": "organization_required",
    "message": "A valid X-Organization-Id header is required.",
}
PERMISSION_DENIED = {"code": "permission_denied", "message": "Permission denied."}

# Plan, « Rôles → permissions » (copied, not computed from authz.py).
ADMIN_PERMISSIONS = sorted(
    [
        "organization:read",
        "organization:leave",
        "agents:use",
        "members:read",
        "members:manage",
        "audit:read",
    ]
)
MEMBER_PERMISSIONS = sorted(["organization:read", "organization:leave", "agents:use"])
OWNER_PERMISSIONS = [
    *ADMIN_PERMISSIONS,
    "admins:manage",
    "owners:manage",
    "organization:rename",
]


@pytest.fixture(autouse=True)
def _capture_security_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="forge.security")


def security_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        r.security_event  # type: ignore[attr-defined]
        for r in caplog.records
        if r.name == "forge.security"
    ]


def snapshot(response: httpx.Response) -> tuple[int, list[tuple[str, str]], bytes]:
    """Everything the client receives: status, headers (in order) and body bytes."""
    return response.status_code, list(response.headers.multi_items()), response.content


# --- active organization (T026) ---


def test_get_active_organization_returns_role_and_permissions(world: World) -> None:
    response = world.client_as(world.bob, world.acme).get("/api/organization")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(world.acme),
        "name": "Acme",
        "role": "admin",
        "permissions": ADMIN_PERMISSIONS,
    }


def test_cannot_activate_organization_without_membership(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-16, FR-37: Alice (Acme only) naming Globex gets exactly the answer of an organization
    that does not exist. The attempt goes to the platform log, not to Globex's audit log (R2)."""
    globex_audit = world.audit(world.globex)
    alice = world.client_as(world.alice)

    foreign = alice.get("/api/organization", headers={ORG_HEADER: str(world.globex)})
    missing = alice.get("/api/organization", headers={ORG_HEADER: str(uuid.uuid4())})

    assert foreign.status_code == 404
    assert foreign.json() == ORGANIZATION_NOT_FOUND
    assert snapshot(foreign) == snapshot(missing)
    denied = [e for e in security_events(caplog) if e["name"] == "organization_access_denied"]
    assert denied[0] == {
        "name": "organization_access_denied",
        "user_id": str(world.alice.id),
        "organization_id": str(world.globex),
        "reason": None,
    }
    assert world.audit(world.globex) == globex_audit
    # Her active organization did not change: Acme still answers with her role.
    acme = alice.get("/api/organization", headers={ORG_HEADER: str(world.acme)})
    assert acme.json()["role"] == "owner"


@pytest.mark.parametrize("header", [None, "", "not-a-uuid", "1234"], ids=repr)
def test_missing_or_malformed_org_header_is_400(world: World, header: str | None) -> None:
    alice = world.client_as(world.alice)
    headers = {} if header is None else {ORG_HEADER: header}

    response = alice.get("/api/organization", headers=headers)

    assert response.status_code == 400
    assert response.json() == ORGANIZATION_REQUIRED


def test_require_refuses_missing_permission_and_audits_it(world: World) -> None:
    """`require(perm)`: a member without `perm` gets 403, and the refusal is in the audit log of
    the organization (committed despite the rollback of the request)."""
    app = create_test_app(world.clock, world.fake_google)

    @app.get("/api/probe", name="probe")
    async def probe(ctx: Annotated[OrgContext, require("audit:read")]) -> dict[str, str]:
        return {"role": ctx.role}

    with client_for(app) as carole:
        carole.cookies.set("forge_session", world.carole.session_token)
        headers = {ORG_HEADER: str(world.acme), CSRF_HEADER: world.carole.csrf_token}
        denied = carole.get("/api/probe", headers=headers)

    assert denied.status_code == 403
    assert denied.json() == PERMISSION_DENIED
    last = world.audit(world.acme)[-1]
    assert last["actor_user_id"] == world.carole.id
    assert (last["action"], last["outcome"]) == ("permission.denied", "denied")
    assert (last["target_type"], last["target_id"]) == ("organization", str(world.acme))
    assert last["details"] == {"permission": "audit:read"}


def test_require_reads_membership_at_each_request(world: World) -> None:
    """FR-26: the role is read at each request (no cache in the session)."""
    carole: TestClient = world.client_as(world.carole, world.acme)
    assert carole.get("/api/organization").json()["permissions"] == MEMBER_PERMISSIONS
    world.execute(
        world.acme,
        "UPDATE memberships SET role = 'admin' WHERE organization_id = %s AND user_id = %s",
        world.acme,
        world.carole.id,
    )
    assert carole.get("/api/organization").json()["permissions"] == ADMIN_PERMISSIONS


# --- list and create (T027) ---


def test_create_organization_makes_creator_sole_owner(world: World) -> None:
    """AC-14 (FR-13): a signed-in user with no organization creates Acme: sole Owner, event in
    Acme's audit log."""
    eve = world.add_person("eve", "Eve Laurent", "eve@example.com")
    client = world.client_as(eve)

    response = client.post("/api/organizations", json={"name": "  Acme  "})

    assert response.status_code == 201
    body = response.json()
    org = uuid.UUID(body["id"])
    assert body == {"id": str(org), "name": "Acme", "role": "owner"}  # name stripped
    assert world.roles(org) == {eve.id: "owner"}
    [event] = world.audit(org)
    assert event["actor_user_id"] == eve.id
    assert (event["action"], event["outcome"]) == ("organization.created", "success")
    assert (event["target_type"], event["target_id"]) == ("organization", str(org))
    assert event["details"] == {"name": "Acme"}
    assert event["occurred_at"] == world.now
    # The new organization is usable at once.
    active = client.get("/api/organization", headers={ORG_HEADER: str(org)})
    assert active.json()["role"] == "owner"


def test_list_my_organizations_shows_only_memberships(world: World) -> None:
    """AC-17 (FR-16): Alice, member of Acme only, sees Acme and nothing about Globex."""
    response = world.client_as(world.alice).get("/api/organizations")

    assert response.status_code == 200
    assert response.json() == [{"id": str(world.acme), "name": "Acme", "role": "owner"}]
    assert "Globex" not in response.text and str(world.globex) not in response.text
    gina = world.client_as(world.gina).get("/api/organizations").json()
    assert gina == [{"id": str(world.globex), "name": "Globex", "role": "owner"}]


def test_list_my_organizations_is_empty_without_membership(world: World) -> None:
    eve = world.add_person("eve", "Eve Laurent", "eve@example.com")
    assert world.client_as(eve).get("/api/organizations").json() == []


def test_switch_active_organization_applies_new_role(world: World) -> None:
    """AC-15 (FR-14, FR-15): Alice, Member of Acme and Admin of Globex: changing the header is
    enough, the permissions follow, no new sign-in."""
    world.execute(
        world.acme,
        "UPDATE memberships SET role = 'member' WHERE organization_id = %s AND user_id = %s",
        world.acme,
        world.alice.id,
    )
    world.add_member(world.globex, world.alice, "admin")
    alice = world.client_as(world.alice)

    mine = alice.get("/api/organizations").json()
    assert sorted((o["name"], o["role"]) for o in mine) == [("Acme", "member"), ("Globex", "admin")]
    in_acme = alice.get("/api/organization", headers={ORG_HEADER: str(world.acme)}).json()
    assert (in_acme["name"], in_acme["permissions"]) == ("Acme", MEMBER_PERMISSIONS)
    in_globex = alice.get("/api/organization", headers={ORG_HEADER: str(world.globex)}).json()
    assert (in_globex["name"], in_globex["role"]) == ("Globex", "admin")
    assert in_globex["permissions"] == ADMIN_PERMISSIONS


@pytest.mark.parametrize("name", ["", "   ", "x" * 101], ids=["empty", "blank", "too_long"])
def test_organization_name_is_validated(world: World, name: str) -> None:
    eve = world.add_person("eve", "Eve Laurent", "eve@example.com")
    client = world.client_as(eve)

    response = client.post("/api/organizations", json={"name": name})

    assert response.status_code == 422
    assert client.get("/api/organizations").json() == []


def test_organization_name_of_100_characters_is_accepted(world: World) -> None:
    response = world.client_as(world.alice).post("/api/organizations", json={"name": "é" * 100})
    assert response.status_code == 201
    assert response.json()["name"] == "é" * 100


def test_organization_names_are_not_unique(world: World) -> None:
    """FR-16: a uniqueness check would reveal that an organization of that name exists."""
    response = world.client_as(world.gina).post("/api/organizations", json={"name": "Acme"})

    assert response.status_code == 201
    assert response.json()["name"] == "Acme"
    assert uuid.UUID(response.json()["id"]) != world.acme


# --- rename (T028) ---


def test_owner_renames_organization(world: World) -> None:
    """AC-59 (FR-13 bis): the new name is what every member gets at their next request (R11),
    and the rename is in the audit log."""
    alice = world.client_as(world.alice, world.acme)

    response = alice.patch("/api/organization", json={"name": " Acme SA "})

    assert response.status_code == 200
    assert response.json() == {
        "id": str(world.acme),
        "name": "Acme SA",
        "role": "owner",
        "permissions": sorted(OWNER_PERMISSIONS),
    }
    for person in (world.bob, world.carole, world.dave):
        active = world.client_as(person, world.acme).get("/api/organization").json()
        assert active["name"] == "Acme SA"
    listed = world.client_as(world.carole).get("/api/organizations").json()
    assert listed == [{"id": str(world.acme), "name": "Acme SA", "role": "member"}]
    last = world.audit(world.acme)[-1]
    assert last["actor_user_id"] == world.alice.id
    assert (last["action"], last["outcome"]) == ("organization.renamed", "success")
    assert (last["target_type"], last["target_id"]) == ("organization", str(world.acme))
    assert last["details"] == {"from_name": "Acme", "to_name": "Acme SA"}


@pytest.mark.parametrize("who", ["bob", "carole", "dave"], ids=["admin", "member", "guest"])
def test_non_owner_cannot_rename(world: World, who: str) -> None:
    """AC-59: an Admin, a Member or a Guest is refused (403), and the refusal is audited."""
    person = world.people[who]

    response = world.client_as(person, world.acme).patch(
        "/api/organization", json={"name": "Hijacked"}
    )

    assert response.status_code == 403
    assert response.json() == PERMISSION_DENIED
    names = world.query(world.acme, "SELECT name FROM organizations")
    assert names == [{"name": "Acme"}]
    last = world.audit(world.acme)[-1]
    assert (last["actor_user_id"], last["action"], last["outcome"]) == (
        person.id,
        "permission.denied",
        "denied",
    )
    assert last["details"] == {"permission": "organization:rename"}


@pytest.mark.parametrize("name", ["", "  ", "x" * 101], ids=["empty", "blank", "too_long"])
def test_rename_validates_the_name(world: World, name: str) -> None:
    response = world.client_as(world.alice, world.acme).patch(
        "/api/organization", json={"name": name}
    )
    assert response.status_code == 422


# --- agents need an active organization (T036) ---


def test_user_without_organization_cannot_use_agents(world: World) -> None:
    """AC-18 (FR-17, FR-31): Eve, signed in but member of no organization, cannot list nor call
    an agent: without the header, 400; with any organization (here Acme), the answer of an
    organization that does not exist."""
    eve = world.add_person("eve", "Eve Laurent", "eve@example.com")
    client = world.client_as(eve)

    no_header = client.get("/api/agents")
    assert no_header.status_code == 400
    assert no_header.json() == ORGANIZATION_REQUIRED

    headers = {ORG_HEADER: str(world.acme)}
    for response in (
        client.get("/api/agents", headers=headers),
        client.post("/api/agents/assistant/invoke", json={"message": "hi"}, headers=headers),
        client.post("/api/agents/assistant/stream", json={"message": "hi"}, headers=headers),
    ):
        assert response.status_code == 404
        assert response.json() == ORGANIZATION_NOT_FOUND
