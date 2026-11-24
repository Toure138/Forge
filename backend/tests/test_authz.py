"""Pure authorization model (`app/core/authz.py`): permissions, roles, role-management rules.

The expected tables below are copied from the plan (« Autorisation »), not computed from
`authz.py`: a change of the model must show up here.
"""

from typing import get_args

import pytest

from app.core import authz
from app.core.authz import Role
from app.db import tables
from app.schemas import common

ORG_PERMISSIONS = {
    "organization:read",
    "organization:leave",
    "organization:rename",
    "agents:use",
    "members:read",
    "members:manage",
    "admins:manage",
    "owners:manage",
    "audit:read",
}
USER_PERMISSIONS = {
    "session:read",
    "session:logout",
    "organization:list",
    "organization:create",
    "invitation:respond",
}

# Plan, « Rôles → permissions ».
EXPECTED_ROLE_PERMISSIONS = {
    "guest": {"organization:read", "organization:leave"},
    "member": {"organization:read", "organization:leave", "agents:use"},
    "admin": {
        "organization:read",
        "organization:leave",
        "agents:use",
        "members:read",
        "members:manage",
        "audit:read",
    },
    "owner": ORG_PERMISSIONS,
}


@pytest.mark.parametrize(
    ("actor", "from_role", "to_role", "allowed"),
    [
        ("admin", "member", "guest", True),
        ("admin", "guest", "member", True),
        ("admin", "member", "admin", False),
        ("admin", "admin", "member", False),
        ("admin", "owner", "member", False),
        ("owner", "member", "admin", True),
        ("owner", "admin", "owner", True),
        ("owner", "owner", "member", True),
        ("member", "guest", "member", False),
        ("member", "member", "owner", False),
        ("guest", "guest", "member", False),
    ],
    ids=lambda v: str(v),
)
def test_can_change_role(actor: str, from_role: str, to_role: str, allowed: bool) -> None:
    perms = authz.permissions_for(Role(actor))
    assert authz.can_change_role(perms, Role(from_role), Role(to_role)) is allowed


# Can the actor (row) invite, remove or revoke someone holding the target role (column)?
MANAGE_MATRIX = {
    "owner": {"owner": True, "admin": True, "member": True, "guest": True},
    "admin": {"owner": False, "admin": False, "member": True, "guest": True},
    "member": {"owner": False, "admin": False, "member": False, "guest": False},
    "guest": {"owner": False, "admin": False, "member": False, "guest": False},
}


@pytest.mark.parametrize(
    ("actor", "target", "allowed"),
    [(a, t, ok) for a, row in MANAGE_MATRIX.items() for t, ok in row.items()],
)
def test_can_manage_role(actor: str, target: str, allowed: bool) -> None:
    assert authz.can_manage_role(authz.permissions_for(Role(actor)), Role(target)) is allowed


def test_role_permissions_follow_plan_table() -> None:
    assert {r.value: set(authz.permissions_for(r)) for r in Role} == EXPECTED_ROLE_PERMISSIONS


def test_no_permission_spans_organizations() -> None:
    """AC-46: the permission set is frozen; nothing grants access to every organization."""
    assert set(authz.ORGANIZATION_PERMISSIONS) == ORG_PERMISSIONS
    assert set(authz.USER_PERMISSIONS) == USER_PERMISSIONS
    assert set(authz.ALL_PERMISSIONS) == ORG_PERMISSIONS | USER_PERMISSIONS
    # Organization permissions are only ever obtained through a membership (a role)...
    granted_by_roles = set().union(*authz.ROLE_PERMISSIONS.values())
    assert granted_by_roles == ORG_PERMISSIONS
    # ...and what any signed-in user gets reads or writes no organization's data.
    assert not set(authz.USER_PERMISSIONS) & ORG_PERMISSIONS
    for perm in authz.ALL_PERMISSIONS:
        assert not {"*", "all", "any", "platform", "superadmin"} & set(perm.split(":"))


def test_role_management_permissions() -> None:
    assert {r.value: p for r, p in authz.ROLE_MANAGEMENT_PERMISSION.items()} == {
        "guest": "members:manage",
        "member": "members:manage",
        "admin": "admins:manage",
        "owner": "owners:manage",
    }


def test_permissions_for_reads_the_definition_at_each_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-level AC-28: changing the single definition changes the outcome, nothing else."""
    perms = frozenset({*authz.ROLE_PERMISSIONS[Role.MEMBER], "organization:rename"})
    monkeypatch.setitem(authz.ROLE_PERMISSIONS, Role.MEMBER, perms)
    assert "organization:rename" in authz.permissions_for(Role.MEMBER)


def test_schema_literals_match_authz() -> None:
    """The API contract and the DB CHECK constraint mirror `authz.py` exactly."""
    roles = {r.value for r in Role}
    assert set(get_args(common.Role)) == roles
    assert set(get_args(common.Permission)) == set(authz.ORGANIZATION_PERMISSIONS)
    assert set(tables.ROLE_VALUES) == roles
    assert len(tables.ROLE_VALUES) == len(roles)


def test_public_routes_are_health_and_login_only() -> None:
    assert {"health", "auth_login", "auth_callback"} == authz.PUBLIC_ROUTES


def test_declare_permission_marks_dependency() -> None:
    async def dependency() -> None:
        return None

    assert authz.declared_permission(dependency) is None
    assert authz.declare_permission(dependency, "members:read") is dependency
    assert authz.declared_permission(dependency) == "members:read"
    assert getattr(dependency, "__forge_permission__") == "members:read"  # noqa: B009


def test_declare_permission_rejects_unknown_permission() -> None:
    async def dependency() -> None:
        return None

    with pytest.raises(ValueError, match="unknown permission"):
        authz.declare_permission(dependency, "everything:all")  # type: ignore[arg-type]


def test_require_authenticated_declares_its_permission() -> None:
    marker = authz.require_authenticated("session:read")
    assert authz.declared_permission(marker.dependency) == "session:read"


def test_require_authenticated_refuses_organization_permission() -> None:
    """An organization permission needs a membership: `require(...)`, never a bare session."""
    with pytest.raises(ValueError, match="every signed-in user"):
        authz.require_authenticated("members:manage")  # type: ignore[arg-type]
