"""AC-47 (FR-37): an id from another organization gets exactly the answer of an id that does not
exist, and changes nothing there."""

import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.factories import World

NOT_FOUND = {"code": "not_found", "message": "Not found."}

Operation = Callable[[TestClient, uuid.UUID], httpx.Response]
OPERATIONS: dict[str, tuple[str, Operation]] = {
    "patch_member": (
        "member",
        lambda c, target: c.patch(f"/api/organization/members/{target}", json={"role": "guest"}),
    ),
    "delete_member": (
        "member",
        lambda c, target: c.delete(f"/api/organization/members/{target}"),
    ),
    "revoke_invitation": (
        "invitation",
        lambda c, target: c.delete(f"/api/organization/invitations/{target}"),
    ),
}


def snapshot(response: httpx.Response) -> tuple[int, list[tuple[str, str]], bytes]:
    return response.status_code, list(response.headers.multi_items()), response.content


def globex_state(world: World) -> dict[str, Any]:
    org = world.globex
    return {
        "memberships": world.query(
            org, "SELECT * FROM memberships WHERE organization_id = %s ORDER BY user_id", org
        ),
        "invitations": world.query(
            org, "SELECT * FROM invitations WHERE organization_id = %s ORDER BY id", org
        ),
        "audit": world.audit(org),
    }


@pytest.mark.parametrize("actor", ["bob", "alice"], ids=["admin", "owner"])
@pytest.mark.parametrize("operation", list(OPERATIONS))
def test_foreign_ids_look_like_missing_ids(world: World, operation: str, actor: str) -> None:
    """An Admin (and an Owner) of Acme aims at a member or an invitation of Globex: status,
    headers and body are those of a random uuid; nothing changes in Globex, nothing is written in
    Acme's audit log either."""
    hugo = world.add_person("hugo", "Hugo Faure", "hugo@globex.example")
    world.add_member(world.globex, hugo, "member")
    invitation = world.add_invitation(world.globex, "ivy@globex.example", "member", world.gina)
    kind, call = OPERATIONS[operation]
    foreign_id = hugo.id if kind == "member" else invitation
    globex_before = globex_state(world)
    acme_audit_before = world.audit(world.acme)
    client = world.client_as(world.people[actor], world.acme)

    foreign = call(client, foreign_id)
    missing = call(client, uuid.uuid4())

    assert foreign.status_code == 404
    assert foreign.json() == NOT_FOUND
    assert snapshot(foreign) == snapshot(missing)
    assert globex_state(world) == globex_before
    assert world.audit(world.acme) == acme_audit_before
