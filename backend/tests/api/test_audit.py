"""Organization audit log through the API: `GET /api/organization/audit` (AC-48, AC-49;
FR-38, FR-40)."""

from typing import Any

import pytest

from tests.factories import World

PERMISSION_DENIED = {"code": "permission_denied", "message": "Permission denied."}


def iso(value: Any) -> str:
    return str(value.isoformat()).replace("+00:00", "Z")


def read_audit(world: World, **params: Any) -> dict[str, Any]:
    response = world.client_as(world.bob, world.acme).get("/api/organization/audit", params=params)
    assert response.status_code == 200, response.text
    page: dict[str, Any] = response.json()
    return page


def test_audit_log_records_sequence_without_other_org(world: World) -> None:
    """AC-48: invitation, acceptance, role change, removal and a permission refusal in Acme, plus
    activity in Globex: an Admin of Acme reads each Acme action with its actor, target, date and
    outcome, and nothing from Globex."""
    eve = world.add_person("eve", "Eve Laurent", "eve@exemple.com")
    first = len(world.audit(world.acme))
    gina = world.client_as(world.gina, world.globex)
    assert gina.patch("/api/organization", json={"name": "Globex SA"}).status_code == 200
    invited_in_globex = gina.post(
        "/api/organization/invitations", json={"email": "x@globex.example", "role": "member"}
    )
    assert invited_in_globex.status_code == 201

    steps: list[tuple[str, Any]] = []
    world.clock.advance(minutes=1)
    invited = world.client_as(world.bob, world.acme).post(
        "/api/organization/invitations", json={"email": eve.email, "role": "member"}
    )
    invitation = invited.json()["id"]
    steps.append(("invite", world.now))
    world.clock.advance(minutes=1)
    assert world.client_as(eve).post(f"/api/invitations/{invitation}/accept").status_code == 200
    steps.append(("accept", world.now))
    world.clock.advance(minutes=1)
    alice = world.client_as(world.alice, world.acme)
    assert alice.patch(f"/api/organization/members/{eve.id}", json={"role": "guest"}).is_success
    steps.append(("role", world.now))
    world.clock.advance(minutes=1)
    bob = world.client_as(world.bob, world.acme)
    assert bob.delete(f"/api/organization/members/{eve.id}").status_code == 204
    steps.append(("remove", world.now))
    world.clock.advance(minutes=1)
    carole = world.client_as(world.carole, world.acme)
    assert carole.get("/api/organization/members").status_code == 403
    steps.append(("denied", world.now))

    page = read_audit(world)

    items = page["items"]
    assert page["next_before_id"] is None
    assert [item["id"] for item in items] == sorted((i["id"] for i in items), reverse=True)
    newest = [
        {key: item[key] for key in ("actor", "action", "target_type", "target_id", "outcome")}
        | {"occurred_at": item["occurred_at"], "details": item["details"]}
        for item in items[:5]
    ]
    times = {name: iso(when) for name, when in steps}
    assert newest == [
        {
            "actor": {"user_id": str(world.carole.id), "name": "Carole Petit"},
            "action": "permission.denied",
            "target_type": "organization",
            "target_id": str(world.acme),
            "outcome": "denied",
            "occurred_at": times["denied"],
            "details": {"permission": "members:read"},
        },
        {
            "actor": {"user_id": str(world.bob.id), "name": "Bob Durand"},
            "action": "member.removed",
            "target_type": "member",
            "target_id": str(eve.id),
            "outcome": "success",
            "occurred_at": times["remove"],
            "details": {"role": "guest"},
        },
        {
            "actor": {"user_id": str(world.alice.id), "name": "Alice Martin"},
            "action": "member.role_changed",
            "target_type": "member",
            "target_id": str(eve.id),
            "outcome": "success",
            "occurred_at": times["role"],
            "details": {"from_role": "member", "to_role": "guest"},
        },
        {
            "actor": {"user_id": str(eve.id), "name": "Eve Laurent"},
            "action": "invitation.accepted",
            "target_type": "invitation",
            "target_id": invitation,
            "outcome": "success",
            "occurred_at": times["accept"],
            "details": {"role": "member"},
        },
        {
            "actor": {"user_id": str(world.bob.id), "name": "Bob Durand"},
            "action": "invitation.created",
            "target_type": "invitation",
            "target_id": invitation,
            "outcome": "success",
            "occurred_at": times["invite"],
            "details": {"role": "member"},
        },
    ]
    assert len(items) == first + 5
    # Nothing from Globex: neither its actor, nor its organization, nor its invitation.
    assert str(world.gina.id) not in str(items)
    assert str(world.globex) not in str(items)
    assert invited_in_globex.json()["id"] not in str(items)
    assert "Globex" not in str(items)


@pytest.mark.parametrize("who", ["carole", "dave"], ids=["member", "guest"])
def test_member_and_guest_cannot_read_audit(world: World, who: str) -> None:
    """AC-49 (FR-40): only Owners and Admins read the audit log; the refusal is itself audited."""
    person = world.people[who]

    response = world.client_as(person, world.acme).get("/api/organization/audit")

    assert response.status_code == 403
    assert response.json() == PERMISSION_DENIED
    last = world.audit(world.acme)[-1]
    assert (last["actor_user_id"], last["action"], last["details"]) == (
        person.id,
        "permission.denied",
        {"permission": "audit:read"},
    )


def test_owner_reads_audit(world: World) -> None:
    response = world.client_as(world.alice, world.acme).get("/api/organization/audit")
    assert response.status_code == 200


def test_audit_pagination_with_before_id(world: World) -> None:
    """Newest first; `next_before_id` gives the next (older) page, None on the last one."""
    alice = world.client_as(world.alice, world.acme)
    for index in range(5):
        assert alice.patch("/api/organization", json={"name": f"Acme {index}"}).is_success
    every_id = [row["id"] for row in reversed(world.audit(world.acme))]
    assert len(every_id) == 5

    seen: list[int] = []
    before_id: int | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if before_id is not None:
            params["before_id"] = before_id
        page = read_audit(world, **params)
        pages += 1
        assert len(page["items"]) <= 2
        seen += [item["id"] for item in page["items"]]
        before_id = page["next_before_id"]
        if before_id is None:
            break
        assert before_id == page["items"][-1]["id"]

    assert seen == every_id
    assert pages == 3
    first_page = read_audit(world, limit=2)
    assert [i["details"]["to_name"] for i in first_page["items"]] == ["Acme 4", "Acme 3"]


def test_audit_default_page_size_is_50(world: World) -> None:
    alice = world.client_as(world.alice, world.acme)
    for _ in range(51):
        world.execute(
            world.acme,
            "INSERT INTO audit_events (organization_id, occurred_at, actor_user_id, action,"
            " target_type, target_id, outcome, details)"
            " VALUES (%s, %s, %s, 'organization.renamed', 'organization', %s, 'success', '{}')",
            world.acme,
            world.now,
            world.alice.id,
            str(world.acme),
        )
    page = alice.get("/api/organization/audit").json()
    assert len(page["items"]) == 50
    assert page["next_before_id"] == page["items"][-1]["id"]


@pytest.mark.parametrize("limit", [0, 201])
def test_audit_limit_is_bounded(world: World, limit: int) -> None:
    response = world.client_as(world.bob, world.acme).get(
        "/api/organization/audit", params={"limit": limit}
    )
    assert response.status_code == 422


def test_audit_limit_bounds_are_accepted(world: World) -> None:
    bob = world.client_as(world.bob, world.acme)
    for limit in (1, 200):
        assert bob.get("/api/organization/audit", params={"limit": limit}).status_code == 200


def test_audit_actor_may_be_unknown(world: World) -> None:
    """`actor_user_id` is nullable: such an entry is rendered with a null actor."""
    world.execute(
        world.acme,
        "INSERT INTO audit_events (organization_id, occurred_at, actor_user_id, action,"
        " outcome, details) VALUES (%s, %s, NULL, 'member.left', 'success', '{}')",
        world.acme,
        world.now,
    )
    [item] = read_audit(world)["items"]
    assert item["actor"] == {"user_id": None, "name": None}
    assert (item["target_type"], item["target_id"]) == (None, None)
