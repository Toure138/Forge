"""Invitations (AC-19 to AC-26, AC-60, AC-61; FR-18 to FR-22): created, listed and revoked by the
organization; listed, accepted and declined by the invitee."""

import ast
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import login
from tests.factories import Person, World
from tests.fakes.google import GoogleAccount

PERMISSION_DENIED = {"code": "permission_denied", "message": "Permission denied."}
NOT_FOUND = {"code": "not_found", "message": "Not found."}
ALREADY_MEMBER = {
    "code": "already_member",
    "message": "This person is already a member of the organization.",
}
ALREADY_PENDING = {
    "code": "invitation_already_pending",
    "message": "A pending invitation already exists for this email.",
}
TTL = timedelta(days=7)  # INVITATION_TTL_DAYS default


def iso(value: Any) -> str:
    return str(value.isoformat()).replace("+00:00", "Z")


def invite(world: World, actor: Person, email: str, role: str) -> httpx.Response:
    client = world.client_as(actor, world.acme)
    return client.post("/api/organization/invitations", json={"email": email, "role": role})


def invitations_of(world: World, org: uuid.UUID) -> list[dict[str, Any]]:
    return world.query(
        org, "SELECT * FROM invitations WHERE organization_id = %s ORDER BY created_at", org
    )


def last_event(world: World, org: uuid.UUID) -> dict[str, Any]:
    return world.audit(org)[-1]


# --- organization side (T032) ---


def test_admin_invites_member(world: World) -> None:
    """AC-19 (FR-18): Bob (Admin) invites bob@exemple.com as Member: pending, audited, email
    normalized to lower case (FR-19)."""
    response = invite(world, world.bob, "Eve.Laurent@Exemple.com", "member")

    assert response.status_code == 201
    body = response.json()
    invitation_id = uuid.UUID(body["id"])
    assert body == {
        "id": str(invitation_id),
        "email": "eve.laurent@exemple.com",
        "role": "member",
        "invited_by": {"user_id": str(world.bob.id), "name": "Bob Durand"},
        "created_at": iso(world.now),
        "expires_at": iso(world.now + TTL),
    }
    [row] = invitations_of(world, world.acme)[-1:]
    assert row["id"] == invitation_id
    assert (row["email_normalized"], row["role"], row["status"]) == (
        "eve.laurent@exemple.com",
        "member",
        "pending",
    )
    assert (row["invited_by"], row["expires_at"]) == (world.bob.id, world.now + TTL)
    event = last_event(world, world.acme)
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        world.bob.id,
        "invitation.created",
        "success",
    )
    assert (event["target_type"], event["target_id"]) == ("invitation", str(invitation_id))
    assert event["details"] == {"role": "member"}  # no email in the audit log (minimization)


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_cannot_invite_admin_or_owner(world: World, role: str) -> None:
    """AC-20 (FR-18, FR-30): refused, nothing created, refusal audited."""
    before = invitations_of(world, world.acme)

    response = invite(world, world.bob, "eve@exemple.com", role)

    assert response.status_code == 403
    assert response.json() == PERMISSION_DENIED
    assert invitations_of(world, world.acme) == before
    event = last_event(world, world.acme)
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        world.bob.id,
        "permission.denied",
        "denied",
    )
    assert event["details"] == {"permission": f"{role}s:manage", "role": role}


def test_owner_invites_any_role(world: World) -> None:
    for index, role in enumerate(["owner", "admin", "member", "guest"]):
        assert invite(world, world.alice, f"p{index}@exemple.com", role).status_code == 201


def test_member_cannot_invite(world: World) -> None:
    response = invite(world, world.carole, "eve@exemple.com", "guest")

    assert response.status_code == 403
    assert last_event(world, world.acme)["details"] == {"permission": "members:manage"}


def test_admin_cannot_revoke_admin_invitation(world: World) -> None:
    """AC-25 (FR-20): an Admin invitation created by the Owner cannot be revoked by an Admin."""
    invitation = world.add_invitation(world.acme, "eve@exemple.com", "admin", world.alice)

    response = world.client_as(world.bob, world.acme).delete(
        f"/api/organization/invitations/{invitation}"
    )

    assert response.status_code == 403
    assert response.json() == PERMISSION_DENIED
    assert world.invitation(world.acme, invitation)["status"] == "pending"  # type: ignore[index]
    event = last_event(world, world.acme)
    assert (event["action"], event["outcome"]) == ("permission.denied", "denied")
    assert (event["target_type"], event["target_id"]) == ("invitation", str(invitation))
    assert event["details"] == {"permission": "admins:manage", "role": "admin"}


def test_owner_revokes_invitation(world: World) -> None:
    invitation = world.add_invitation(world.acme, "eve@exemple.com", "admin", world.alice)

    response = world.client_as(world.alice, world.acme).delete(
        f"/api/organization/invitations/{invitation}"
    )

    assert response.status_code == 204
    row = world.invitation(world.acme, invitation)
    assert row is not None
    assert (row["status"], row["decided_by"], row["decided_at"]) == (
        "revoked",
        world.alice.id,
        world.now,
    )
    event = last_event(world, world.acme)
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        world.alice.id,
        "invitation.revoked",
        "success",
    )
    assert (event["target_type"], event["target_id"]) == ("invitation", str(invitation))
    assert event["details"] == {"role": "admin"}
    # Revoked: no longer listed, cannot be revoked twice.
    alice = world.client_as(world.alice, world.acme)
    assert alice.get("/api/organization/invitations").json() == []
    again = alice.delete(f"/api/organization/invitations/{invitation}")
    assert (again.status_code, again.json()) == (404, NOT_FOUND)


def test_admin_revokes_member_invitation(world: World) -> None:
    invitation = world.add_invitation(world.acme, "eve@exemple.com", "guest", world.alice)
    client = world.client_as(world.bob, world.acme)

    assert client.delete(f"/api/organization/invitations/{invitation}").status_code == 204


def test_revoking_unknown_invitation_is_404(world: World) -> None:
    response = world.client_as(world.alice, world.acme).delete(
        f"/api/organization/invitations/{uuid.uuid4()}"
    )
    assert (response.status_code, response.json()) == (404, NOT_FOUND)


def test_inviting_existing_member_is_refused(world: World) -> None:
    """AC-26 (FR-21): Carole is already a Member; inviting her as Guest is refused with an
    explicit message and her role does not change (email case ignored)."""
    response = invite(world, world.bob, "CAROLE@acme.example", "guest")

    assert response.status_code == 409
    assert response.json() == ALREADY_MEMBER
    assert world.role_of(world.acme, world.carole) == "member"
    assert invitations_of(world, world.acme) == []


def test_member_of_another_organization_can_be_invited(world: World) -> None:
    """Membership is checked in the active organization only (Gina belongs to Globex)."""
    assert invite(world, world.bob, world.gina.email, "member").status_code == 201


def test_duplicate_pending_invitation_is_409(world: World) -> None:
    """R5: one pending invitation per email and organization; a new one is allowed once the
    previous one was declined or has expired."""
    assert invite(world, world.bob, "eve@exemple.com", "member").status_code == 201

    duplicate = invite(world, world.bob, "EVE@exemple.com", "guest")
    assert duplicate.status_code == 409
    assert duplicate.json() == ALREADY_PENDING

    world.execute(
        world.acme,
        "UPDATE invitations SET status = 'declined' WHERE organization_id = %s",
        world.acme,
    )
    assert invite(world, world.bob, "eve@exemple.com", "member").status_code == 201

    world.clock.advance(days=7)  # expired (now >= expires_at)
    world.renew_sessions()
    renewed = invite(world, world.bob, "eve@exemple.com", "member")
    assert renewed.status_code == 201
    statuses = [row["status"] for row in invitations_of(world, world.acme)]
    assert statuses == ["declined", "expired", "pending"]


def test_list_pending_invitations_excludes_expired(world: World) -> None:
    old = world.add_invitation(world.acme, "old@exemple.com", "member", world.alice)
    world.clock.advance(days=4)
    world.renew_sessions()
    recent = world.add_invitation(world.acme, "recent@exemple.com", "guest", world.alice)
    world.add_invitation(world.acme, "gone@exemple.com", "guest", world.alice, status="revoked")
    world.add_invitation(world.globex, "other@exemple.com", "guest", world.gina)
    bob = world.client_as(world.bob, world.acme)

    before_expiry = bob.get("/api/organization/invitations")
    assert before_expiry.status_code == 200
    assert [i["id"] for i in before_expiry.json()] == [str(old), str(recent)]

    world.clock.advance(days=3)  # `old` reaches its expiry date
    world.renew_sessions()
    bob = world.client_as(world.bob, world.acme)
    listed = bob.get("/api/organization/invitations").json()
    assert listed == [
        {
            "id": str(recent),
            "email": "recent@exemple.com",
            "role": "guest",
            "invited_by": {"user_id": str(world.alice.id), "name": "Alice Martin"},
            "created_at": iso(world.now - timedelta(days=3)),
            "expires_at": iso(world.now - timedelta(days=3) + TTL),
        }
    ]


def test_member_cannot_list_invitations(world: World) -> None:
    response = world.client_as(world.carole, world.acme).get("/api/organization/invitations")

    assert response.status_code == 403
    assert last_event(world, world.acme)["details"] == {"permission": "members:read"}


@pytest.mark.parametrize(
    "email",
    ["", "bob", "bob@exemple", "@exemple.com", "bob @exemple.com", "a" * 310 + "@exemple.com"],
    ids=["empty", "no_at", "no_dot", "no_local", "space", "too_long"],
)
def test_invalid_email_is_422(world: World, email: str) -> None:
    response = invite(world, world.bob, email, "member")

    assert response.status_code == 422
    assert invitations_of(world, world.acme) == []


# --- invitee side (T033) ---

EXPIRED = {"code": "invitation_expired", "message": "This invitation has expired."}
APP_DIR = Path(__file__).resolve().parents[2] / "app"


def respond(client: TestClient, invitation: uuid.UUID, answer: str) -> httpx.Response:
    return client.post(f"/api/invitations/{invitation}/{answer}")


def test_invitee_accepts_with_case_insensitive_email(world: World) -> None:
    """AC-21 (FR-19): an invitation for bob@exemple.com; the invitee signed in as
    Bob@Exemple.com accepts it: member with the planned role; the invitation cannot be reused."""
    invitee = world.add_person("invitee", "Bob Exemple", "Bob@Exemple.com")
    invitation = world.add_invitation(world.acme, "bob@exemple.com", "member", world.bob)
    client = world.client_as(invitee)

    mine = client.get("/api/invitations/mine")
    assert mine.status_code == 200
    assert mine.json() == [
        {
            "id": str(invitation),
            "organization_id": str(world.acme),
            "organization_name": "Acme",
            "role": "member",
            "invited_by_name": "Bob Durand",
            "expires_at": iso(world.now + TTL),
        }
    ]

    accepted = respond(client, invitation, "accept")

    assert accepted.status_code == 200
    assert accepted.json() == {"id": str(world.acme), "name": "Acme", "role": "member"}
    assert world.role_of(world.acme, invitee) == "member"
    row = world.invitation(world.acme, invitation)
    assert row is not None
    assert (row["status"], row["decided_by"], row["decided_at"]) == (
        "accepted",
        invitee.id,
        world.now,
    )
    event = world.audit(world.acme)[-1]
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        invitee.id,
        "invitation.accepted",
        "success",
    )
    assert (event["target_type"], event["target_id"]) == ("invitation", str(invitation))
    assert event["details"] == {"role": "member"}
    # Usable once: a second use answers like an unknown invitation.
    again = respond(client, invitation, "accept")
    assert (again.status_code, again.json()) == (404, NOT_FOUND)
    assert client.get("/api/invitations/mine").json() == []
    active = client.get("/api/organization", headers={"X-Organization-Id": str(world.acme)})
    assert active.json()["role"] == "member"


def test_other_email_cannot_accept_invitation(world: World) -> None:
    """AC-22 (FR-19): anyone else gets the answer of an unknown invitation, and stays out (even an
    Owner of the organization)."""
    invitation = world.add_invitation(world.acme, "bob@exemple.com", "admin", world.alice)
    outsider = world.add_person("outsider", "Out Sider", "out@exemple.com")
    audit_before = world.audit(world.acme)

    for person in (outsider, world.gina, world.carole, world.alice):
        client = world.client_as(person)
        foreign = respond(client, invitation, "accept")
        missing = respond(client, uuid.uuid4(), "accept")
        assert (foreign.status_code, foreign.json()) == (404, NOT_FOUND)
        assert (foreign.status_code, foreign.headers.multi_items(), foreign.content) == (
            missing.status_code,
            missing.headers.multi_items(),
            missing.content,
        )
        assert respond(client, invitation, "decline").status_code == 404
        assert client.get("/api/invitations/mine").json() == []

    assert world.role_of(world.acme, outsider) is None
    assert world.role_of(world.acme, world.carole) == "member"
    assert world.invitation(world.acme, invitation)["status"] == "pending"  # type: ignore[index]
    assert world.audit(world.acme) == audit_before


def test_invitee_declines_invitation(world: World) -> None:
    """AC-23 (FR-19): declined: not a member, no longer usable, audited."""
    invitee = world.add_person("invitee", "Bob Exemple", "bob@exemple.com")
    invitation = world.add_invitation(world.acme, "bob@exemple.com", "member", world.bob)
    client = world.client_as(invitee)

    declined = respond(client, invitation, "decline")

    assert declined.status_code == 204
    assert declined.content == b""
    assert world.role_of(world.acme, invitee) is None
    row = world.invitation(world.acme, invitation)
    assert row is not None
    assert (row["status"], row["decided_by"]) == ("declined", invitee.id)
    event = world.audit(world.acme)[-1]
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        invitee.id,
        "invitation.declined",
        "success",
    )
    assert event["details"] == {"role": "member"}
    assert respond(client, invitation, "accept").status_code == 404
    assert client.get("/api/invitations/mine").json() == []


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_expired_or_revoked_invitation_cannot_be_accepted(world: World, state: str) -> None:
    """AC-24 (FR-20): 7 days later the invitation is refused with 410, and marked expired with an
    `invitation.expired_on_use` entry committed despite the error; a revoked one is unknown."""
    invitee = world.add_person("invitee", "Bob Exemple", "bob@exemple.com")
    invitation = world.add_invitation(world.acme, "bob@exemple.com", "member", world.bob)
    if state == "expired":
        world.clock.advance(days=7)
        world.renew_sessions()
        invitee = world.people["invitee"]
    else:
        world.execute(
            world.acme,
            "UPDATE invitations SET status = 'revoked' WHERE id = %s",
            invitation,
        )
    client = world.client_as(invitee)

    response = respond(client, invitation, "accept")

    assert world.role_of(world.acme, invitee) is None
    row = world.invitation(world.acme, invitation)
    assert row is not None
    if state == "revoked":
        assert (response.status_code, response.json()) == (404, NOT_FOUND)
        assert row["status"] == "revoked"
        return
    assert (response.status_code, response.json()) == (410, EXPIRED)
    assert (row["status"], row["decided_at"]) == ("expired", world.now)
    event = world.audit(world.acme)[-1]
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        invitee.id,
        "invitation.expired_on_use",
        "denied",
    )
    assert (event["target_type"], event["target_id"]) == ("invitation", str(invitation))
    assert event["details"] == {"role": "member"}
    # Now expired: unknown from then on.
    assert respond(client, invitation, "accept").status_code == 404


def test_expired_invitation_cannot_be_declined(world: World) -> None:
    world.add_person("invitee", "Bob Exemple", "bob@exemple.com")
    invitation = world.add_invitation(world.acme, "bob@exemple.com", "member", world.bob)
    world.clock.advance(days=7, seconds=1)
    world.renew_sessions()

    response = respond(world.client_as(world.people["invitee"]), invitation, "decline")

    assert (response.status_code, response.json()) == (410, EXPIRED)
    assert world.invitation(world.acme, invitation)["status"] == "expired"  # type: ignore[index]


def test_invitation_sends_no_email_and_appears_at_next_login(
    world: World, client: TestClient
) -> None:
    """AC-60 (FR-22): creating an invitation sends nothing (no outbound connection: the network
    guard of the test suite would fail the test; no mail module in the application), and the
    invitation shows up when the invitee signs in with that address (case ignored)."""
    created = invite(world, world.bob, "Erin.Blanc@Exemple.com", "guest")
    assert created.status_code == 201
    assert world.fake_google.requests == []  # inviting talks to nobody

    login(client, world.fake_google, GoogleAccount("sub-erin", "erin.blanc@exemple.com", "Erin"))
    mine = client.get("/api/invitations/mine")

    assert mine.status_code == 200
    assert [
        (i["id"], i["organization_name"], i["role"], i["invited_by_name"]) for i in mine.json()
    ] == [(created.json()["id"], "Acme", "guest", "Bob Durand")]
    assert client.get("/api/organizations").json() == []  # not a member before accepting
    assert mail_imports(APP_DIR) == []


def test_same_domain_without_invitation_gets_no_membership(
    world: World, client: TestClient
) -> None:
    """AC-60 (FR-22): an address of a domain whose other addresses are members of Acme gets no
    membership and no invitation (no automatic membership by domain)."""
    login(client, world.fake_google, GoogleAccount("sub-zed", "zed@acme.example", "Zed"))

    assert client.get("/api/organizations").json() == []
    assert client.get("/api/invitations/mine").json() == []
    response = client.get("/api/organization", headers={"X-Organization-Id": str(world.acme)})
    assert response.status_code == 404


def test_accept_when_already_member_is_409(world: World) -> None:
    """FR-21: an invitation addressed to someone who became a member in the meantime is refused
    (409), the role is unchanged and the invitation stays pending."""
    invitation = world.add_invitation(world.acme, world.carole.email, "admin", world.alice)

    response = respond(world.client_as(world.carole), invitation, "accept")

    assert (response.status_code, response.json()) == (409, ALREADY_MEMBER)
    assert world.role_of(world.acme, world.carole) == "member"
    assert world.invitation(world.acme, invitation)["status"] == "pending"  # type: ignore[index]


def mail_imports(root: Path) -> list[str]:
    """`module: imported name` for every import of `smtplib` or `email` under `root`."""
    found = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in {"smtplib", "email"}:
                    found.append(f"{path.relative_to(root)}: {name}")
    return found


def test_mail_import_finder_flags_mail_modules(tmp_path: Path) -> None:
    """The guard above, seen red on a synthetic package first."""
    (tmp_path / "notify.py").write_text("import smtplib\nfrom email.message import EmailMessage\n")
    (tmp_path / "ok.py").write_text("from app.auth.users import normalize_email\n")

    assert mail_imports(tmp_path) == ["notify.py: smtplib", "notify.py: email.message"]
