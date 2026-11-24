"""Members of the active organization: leave, list, change a role, remove (AC-31 to AC-37,
AC-61 to AC-63; FR-26 to FR-30)."""

import uuid

import httpx
import pytest

from tests.factories import Person, World

ORGANIZATION_NOT_FOUND = {"code": "organization_not_found", "message": "Organization not found."}
LAST_OWNER = {"code": "last_owner", "message": "An organization must keep at least one owner."}
PERMISSION_DENIED = {"code": "permission_denied", "message": "Permission denied."}
NOT_FOUND = {"code": "not_found", "message": "Not found."}


def last_event(world: World, org: uuid.UUID) -> dict[str, object]:
    return world.audit(org)[-1]


# --- leave (T028) ---


def test_member_leaves_organization(world: World) -> None:
    """AC-36 (FR-29): Carole leaves Acme; her next request in Acme is refused; the departure is
    in the audit log."""
    carole = world.client_as(world.carole, world.acme)

    response = carole.post("/api/organization/leave")

    assert response.status_code == 204
    assert response.content == b""
    assert world.role_of(world.acme, world.carole) is None
    following = carole.get("/api/organization")
    assert following.status_code == 404
    assert following.json() == ORGANIZATION_NOT_FOUND
    assert carole.get("/api/organizations").json() == []
    event = last_event(world, world.acme)
    assert event["actor_user_id"] == world.carole.id
    assert (event["action"], event["outcome"]) == ("member.left", "success")
    assert (event["target_type"], event["target_id"]) == ("member", str(world.carole.id))
    assert event["details"] == {"role": "member"}


def test_last_owner_cannot_leave(world: World) -> None:
    """AC-35 (FR-28): Alice, sole Owner of Acme, cannot leave it; the refusal is audited."""
    alice = world.client_as(world.alice, world.acme)

    response = alice.post("/api/organization/leave")

    assert response.status_code == 409
    assert response.json() == LAST_OWNER
    assert world.role_of(world.acme, world.alice) == "owner"
    event = last_event(world, world.acme)
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        world.alice.id,
        "member.left",
        "denied",
    )
    assert event["details"] == {"role": "owner", "reason": "last_owner"}


def test_owner_can_leave_when_another_owner_remains(world: World) -> None:
    world.execute(
        world.acme,
        "UPDATE memberships SET role = 'owner' WHERE organization_id = %s AND user_id = %s",
        world.acme,
        world.bob.id,
    )

    response = world.client_as(world.alice, world.acme).post("/api/organization/leave")

    assert response.status_code == 204
    assert world.roles(world.acme) == {
        world.bob.id: "owner",
        world.carole.id: "member",
        world.dave.id: "guest",
    }


def test_leaving_keeps_other_memberships(world: World) -> None:
    world.add_member(world.globex, world.carole, "guest")

    world.client_as(world.carole, world.acme).post("/api/organization/leave")

    assert world.role_of(world.globex, world.carole) == "guest"


# --- list and change a role (T029) ---

ROLE_OF_KEY = {"owner": "alice", "admin": "bob", "member": "carole", "guest": "dave"}
PERMISSIONS_BY_ROLE = {  # plan, « Rôles → permissions » (copied, not computed)
    "member": sorted(["organization:read", "organization:leave", "agents:use"]),
    "admin": sorted(
        [
            "organization:read",
            "organization:leave",
            "agents:use",
            "members:read",
            "members:manage",
            "audit:read",
        ]
    ),
}


def set_role(world: World, person: Person, role: str) -> None:
    world.execute(
        world.acme,
        "UPDATE memberships SET role = %s WHERE organization_id = %s AND user_id = %s",
        role,
        world.acme,
        person.id,
    )


def permissions_of(world: World, person: Person) -> list[str]:
    response = world.client_as(person, world.acme).get("/api/organization")
    assert response.status_code == 200
    permissions: list[str] = response.json()["permissions"]
    return permissions


def change_role(world: World, actor: Person, target: uuid.UUID, role: str) -> httpx.Response:
    client = world.client_as(actor, world.acme)
    return client.patch(f"/api/organization/members/{target}", json={"role": role})


def iso(world: World) -> str:
    """`world.now` as the API serializes it."""
    return world.now.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("role", ["owner", "admin", "member", "guest"])
def test_list_members_requires_members_read(world: World, role: str) -> None:
    """FR-23: Owners and Admins see the members; Members and Guests are refused (audited)."""
    actor = world.people[ROLE_OF_KEY[role]]

    response = world.client_as(actor, world.acme).get("/api/organization/members")

    if role in ("member", "guest"):
        assert response.status_code == 403
        assert response.json() == PERMISSION_DENIED
        event = last_event(world, world.acme)
        assert (event["actor_user_id"], event["action"]) == (actor.id, "permission.denied")
        assert event["details"] == {"permission": "members:read"}
        return
    assert response.status_code == 200
    members = response.json()
    assert {m["user_id"]: m["role"] for m in members} == {
        str(world.alice.id): "owner",
        str(world.bob.id): "admin",
        str(world.carole.id): "member",
        str(world.dave.id): "guest",
    }
    bob = next(m for m in members if m["user_id"] == str(world.bob.id))
    assert bob == {
        "user_id": str(world.bob.id),
        "name": "Bob Durand",
        "email": "bob@acme.example",
        "picture_url": None,
        "role": "admin",
        "joined_at": iso(world),
    }
    assert "Gina" not in response.text  # Globex's members are not listed


@pytest.mark.parametrize("target_role", ["admin", "owner"])
def test_admin_cannot_modify_admin_or_owner(world: World, target_role: str) -> None:
    """AC-33 (FR-27): Bob (Admin) cannot demote another Admin nor an Owner; the refusal is
    audited and nothing changes."""
    erin = world.add_person("erin", "Erin Blanc", "erin@acme.example")
    world.add_member(world.acme, erin, "admin")
    target = erin if target_role == "admin" else world.alice

    response = change_role(world, world.bob, target.id, "member")

    assert response.status_code == 403
    assert response.json() == PERMISSION_DENIED
    assert world.role_of(world.acme, target) == target_role
    event = last_event(world, world.acme)
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        world.bob.id,
        "permission.denied",
        "denied",
    )
    assert (event["target_type"], event["target_id"]) == ("member", str(target.id))
    assert event["details"] == {
        "permission": f"{target_role}s:manage",
        "from_role": target_role,
        "to_role": "member",
    }


def test_admin_manages_members_and_guests(world: World) -> None:
    """FR-27: an Admin moves a Member to Guest and back."""
    assert change_role(world, world.bob, world.carole.id, "guest").status_code == 200
    assert world.role_of(world.acme, world.carole) == "guest"
    assert change_role(world, world.bob, world.carole.id, "member").status_code == 200
    assert world.role_of(world.acme, world.carole) == "member"


def test_owner_promotes_then_demotes_admin(world: World) -> None:
    """AC-34 (FR-27): Alice makes Bob (Member) Admin, then Member again; Bob's permissions follow
    each change at his next request, and both changes are audited."""
    set_role(world, world.bob, "member")
    assert permissions_of(world, world.bob) == PERMISSIONS_BY_ROLE["member"]

    promoted = change_role(world, world.alice, world.bob.id, "admin")
    assert promoted.status_code == 200
    assert promoted.json() == {
        "user_id": str(world.bob.id),
        "name": "Bob Durand",
        "email": "bob@acme.example",
        "picture_url": None,
        "role": "admin",
        "joined_at": iso(world),
    }
    assert permissions_of(world, world.bob) == PERMISSIONS_BY_ROLE["admin"]

    assert change_role(world, world.alice, world.bob.id, "member").status_code == 200
    assert permissions_of(world, world.bob) == PERMISSIONS_BY_ROLE["member"]

    changes = [e for e in world.audit(world.acme) if e["action"] == "member.role_changed"]
    summary = [(e["actor_user_id"], e["target_id"], e["outcome"], e["details"]) for e in changes]
    assert summary == [
        (world.alice.id, str(world.bob.id), "success", {"from_role": "member", "to_role": "admin"}),
        (world.alice.id, str(world.bob.id), "success", {"from_role": "admin", "to_role": "member"}),
    ]


def test_last_owner_cannot_be_demoted(world: World) -> None:
    """AC-35 (FR-28): Alice, sole Owner, cannot demote herself; refusal audited."""
    response = change_role(world, world.alice, world.alice.id, "admin")

    assert response.status_code == 409
    assert response.json() == LAST_OWNER
    assert world.role_of(world.acme, world.alice) == "owner"
    event = last_event(world, world.acme)
    assert (event["action"], event["outcome"]) == ("member.role_changed", "denied")
    assert event["details"] == {"from_role": "owner", "to_role": "admin", "reason": "last_owner"}


@pytest.mark.parametrize(
    ("who", "to_role"),
    [("bob", "owner"), ("carole", "admin"), ("carole", "owner")],
    ids=["admin_to_owner", "member_to_admin", "member_to_owner"],
)
def test_no_self_elevation(world: World, who: str, to_role: str) -> None:
    """AC-37 (FR-30): nobody gives themselves a higher role."""
    person = world.people[who]
    before = world.role_of(world.acme, person)

    response = change_role(world, person, person.id, to_role)

    assert response.status_code == 403
    assert response.json() == PERMISSION_DENIED
    assert world.role_of(world.acme, person) == before
    assert last_event(world, world.acme)["action"] == "permission.denied"


def test_ownership_transfer_promote_then_leave(world: World) -> None:
    """AC-62 (FR-27 bis, FR-28): Alice names Bob Owner, then leaves: Bob is the sole Owner, both
    events are audited."""
    set_role(world, world.bob, "member")

    assert change_role(world, world.alice, world.bob.id, "owner").status_code == 200
    left = world.client_as(world.alice, world.acme).post("/api/organization/leave")

    assert left.status_code == 204
    roles = world.roles(world.acme)
    assert [uid for uid, role in roles.items() if role == "owner"] == [world.bob.id]
    assert world.alice.id not in roles
    events = [(e["actor_user_id"], e["action"], e["outcome"]) for e in world.audit(world.acme)]
    assert events[-2:] == [
        (world.alice.id, "member.role_changed", "success"),
        (world.alice.id, "member.left", "success"),
    ]


def test_owner_demotes_coowner_then_cannot_demote_self_as_last_owner(world: World) -> None:
    """AC-63 (FR-27 bis): Alice removes Bob's Owner role (he keeps his membership with the given
    role); then, sole Owner, she cannot drop her own."""
    set_role(world, world.bob, "owner")

    demoted = change_role(world, world.alice, world.bob.id, "admin")
    assert demoted.status_code == 200
    assert world.role_of(world.acme, world.bob) == "admin"

    response = change_role(world, world.alice, world.alice.id, "admin")
    assert response.status_code == 409
    assert response.json() == LAST_OWNER
    assert world.role_of(world.acme, world.alice) == "owner"


def test_unknown_member_is_404(world: World) -> None:
    response = change_role(world, world.alice, uuid.uuid4(), "member")

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


def test_change_role_validates_the_role(world: World) -> None:
    response = change_role(world, world.alice, world.carole.id, "superadmin")
    assert response.status_code == 422
    assert world.role_of(world.acme, world.carole) == "member"


# --- remove (T031) ---


def remove(world: World, actor: Person, target: uuid.UUID) -> httpx.Response:
    return world.client_as(actor, world.acme).delete(f"/api/organization/members/{target}")


def test_removed_member_loses_access_keeps_other_memberships(world: World) -> None:
    """AC-32 (FR-26): Bob (Admin) removes Carole from Acme: her next request in Acme is refused,
    her membership of Globex is intact, the removal is audited."""
    world.add_member(world.globex, world.carole, "guest")
    carole = world.client_as(world.carole, world.acme)
    assert carole.get("/api/organization").status_code == 200

    response = remove(world, world.bob, world.carole.id)

    assert response.status_code == 204
    assert response.content == b""
    following = carole.get("/api/organization")
    assert following.status_code == 404
    assert following.json() == ORGANIZATION_NOT_FOUND
    assert world.role_of(world.acme, world.carole) is None
    assert world.role_of(world.globex, world.carole) == "guest"
    listed = carole.get("/api/organizations").json()
    assert listed == [{"id": str(world.globex), "name": "Globex", "role": "guest"}]
    event = last_event(world, world.acme)
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        world.bob.id,
        "member.removed",
        "success",
    )
    assert (event["target_type"], event["target_id"]) == ("member", str(world.carole.id))
    assert event["details"] == {"role": "member"}


@pytest.mark.parametrize("target_role", ["admin", "owner"])
def test_admin_cannot_remove_admin_or_owner(world: World, target_role: str) -> None:
    """AC-33 (FR-27): Bob (Admin) cannot remove an Admin nor an Owner; refusal audited."""
    erin = world.add_person("erin", "Erin Blanc", "erin@acme.example")
    world.add_member(world.acme, erin, "admin")
    target = erin if target_role == "admin" else world.alice

    response = remove(world, world.bob, target.id)

    assert response.status_code == 403
    assert response.json() == PERMISSION_DENIED
    assert world.role_of(world.acme, target) == target_role
    event = last_event(world, world.acme)
    assert (event["actor_user_id"], event["action"], event["outcome"]) == (
        world.bob.id,
        "permission.denied",
        "denied",
    )
    assert (event["target_type"], event["target_id"]) == ("member", str(target.id))
    assert event["details"] == {"permission": f"{target_role}s:manage", "role": target_role}


def test_last_owner_cannot_be_removed(world: World) -> None:
    """AC-35 (FR-28): the sole Owner cannot be removed (here by herself); refusal audited."""
    response = remove(world, world.alice, world.alice.id)

    assert response.status_code == 409
    assert response.json() == LAST_OWNER
    assert world.role_of(world.acme, world.alice) == "owner"
    event = last_event(world, world.acme)
    assert (event["action"], event["outcome"]) == ("member.removed", "denied")
    assert event["details"] == {"role": "owner", "reason": "last_owner"}


def test_owner_removes_a_coowner(world: World) -> None:
    set_role(world, world.bob, "owner")

    assert remove(world, world.alice, world.bob.id).status_code == 204
    assert world.role_of(world.acme, world.bob) is None


def test_member_cannot_remove_anyone(world: World) -> None:
    response = remove(world, world.carole, world.dave.id)

    assert response.status_code == 403
    assert world.role_of(world.acme, world.dave) == "guest"
    assert last_event(world, world.acme)["details"] == {"permission": "members:manage"}


def test_removing_unknown_member_is_404(world: World) -> None:
    response = remove(world, world.alice, uuid.uuid4())

    assert response.status_code == 404
    assert response.json() == NOT_FOUND


# --- second Owner through an invitation (T033) ---


def test_owner_invites_second_owner(world: World) -> None:
    """AC-61 (FR-18, FR-27 bis): Alice invites Carole as Owner, Carole accepts: Acme has two
    Owners, with the same permissions."""
    world.execute(
        world.acme,
        "DELETE FROM memberships WHERE organization_id = %s AND user_id = %s",
        world.acme,
        world.carole.id,
    )
    alice = world.client_as(world.alice, world.acme)
    created = alice.post(
        "/api/organization/invitations", json={"email": world.carole.email, "role": "owner"}
    )
    assert created.status_code == 201

    carole = world.client_as(world.carole)
    accepted = carole.post(f"/api/invitations/{created.json()['id']}/accept")

    assert accepted.status_code == 200
    owners = sorted(uid for uid, role in world.roles(world.acme).items() if role == "owner")
    assert owners == sorted([world.alice.id, world.carole.id])
    assert permissions_of(world, world.carole) == permissions_of(world, world.alice)


# --- the role is read at each request, agents included (T036) ---


def test_demotion_to_guest_takes_effect_on_next_request(world: World) -> None:
    """AC-31 (US-7, FR-26): Carole, Member of Acme, uses the agents; Bob (Admin) makes her a
    Guest; her very next agent request is refused, with the same session (no new sign-in)."""
    carole = world.client_as(world.carole, world.acme, replies=["Bonjour !"])
    assert carole.get("/api/agents").status_code == 200
    first = carole.post("/api/agents/assistant/invoke", json={"message": "Salut"})
    assert first.status_code == 200
    assert first.json()["output"] == "Bonjour !"

    demoted = change_role(world, world.bob, world.carole.id, "guest")
    assert demoted.status_code == 200

    for response in (
        carole.get("/api/agents"),
        carole.post("/api/agents/assistant/invoke", json={"message": "Encore"}),
    ):
        assert response.status_code == 403
        assert response.json() == PERMISSION_DENIED
