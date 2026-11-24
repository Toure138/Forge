"""Authorization model: permissions, roles and role-management rules (constitution XI).

This module is the ONLY place that gives role names a meaning. The rest of the code checks
permissions (`require("members:manage")`, `can_manage_role`), never role names. The only other
mentions of role names are mirrors checked by `test_schema_literals_match_authz` in
`tests/test_authz.py` (`app/schemas/common.py` for the API contract, `app/db/tables.py` for the
CHECK constraint) and the "at least one Owner" business invariant (`app/services/memberships.py`).

Route dependencies: `require_authenticated(perm)` (any valid session) and, with the organization
context (spec 001, T026), `require(perm)`. Each one carries `__forge_permission__`, which the
route-protection test looks for (FR-25).
"""

from collections.abc import Callable, Set
from enum import StrEnum
from typing import Any, Final, Literal, get_args

from fastapi import Depends

from app.auth.dependencies import CurrentSessionDep, Principal
from app.core.errors import ApiError


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    GUEST = "guest"


# Granted to any valid session, whatever the organization: none of them reads or writes the data
# of an organization (the cross-organization reads « mine » are limited to the current user).
UserPermission = Literal[
    "session:read",
    "session:logout",
    "organization:list",
    "organization:create",
    "invitation:respond",
]

# Granted by a role, inside the active organization only (obtained through a membership).
OrganizationPermission = Literal[
    "organization:read",
    "organization:leave",
    "organization:rename",
    "agents:use",
    "members:read",
    "members:manage",
    "admins:manage",
    "owners:manage",
    "audit:read",
]

Permission = Literal[UserPermission, OrganizationPermission]

USER_PERMISSIONS: Final[frozenset[UserPermission]] = frozenset(get_args(UserPermission))
ORGANIZATION_PERMISSIONS: Final[frozenset[OrganizationPermission]] = frozenset(
    get_args(OrganizationPermission)
)
ALL_PERMISSIONS: Final[frozenset[Permission]] = USER_PERMISSIONS | ORGANIZATION_PERMISSIONS

# Role -> permissions (plan, « Rôles → permissions »). Read at each call by `permissions_for`:
# changing a role here changes every action that depends on it (AC-28).
ROLE_PERMISSIONS: dict[Role, frozenset[OrganizationPermission]] = {
    Role.OWNER: frozenset(
        {
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
    ),
    Role.ADMIN: frozenset(
        {
            "organization:read",
            "organization:leave",
            "agents:use",
            "members:read",
            "members:manage",
            "audit:read",
        }
    ),
    Role.MEMBER: frozenset({"organization:read", "organization:leave", "agents:use"}),
    Role.GUEST: frozenset({"organization:read", "organization:leave"}),
}

# "To invite, remove, revoke or change the role of a holder of role X, you need..."
ROLE_MANAGEMENT_PERMISSION: dict[Role, OrganizationPermission] = {
    Role.GUEST: "members:manage",
    Role.MEMBER: "members:manage",
    Role.ADMIN: "admins:manage",
    Role.OWNER: "owners:manage",
}

# Route names (FastAPI operation names) exempted from authentication: the ONLY exemption list.
PUBLIC_ROUTES: Final[frozenset[str]] = frozenset({"health", "auth_login", "auth_callback"})

PERMISSION_ATTRIBUTE: Final = "__forge_permission__"


def permissions_for(role: Role) -> frozenset[OrganizationPermission]:
    """Permissions of `role`, looked up in `ROLE_PERMISSIONS` at each call."""
    return ROLE_PERMISSIONS[Role(role)]


def can_manage_role(perms: Set[str], role: Role) -> bool:
    """Whether `perms` allow inviting, removing or revoking a holder of `role`."""
    return ROLE_MANAGEMENT_PERMISSION[Role(role)] in perms


def can_change_role(perms: Set[str], from_role: Role, to_role: Role) -> bool:
    """FR-27, FR-30: manage the current role of the target AND the role it is given.

    Self-elevation falls out of it: an Admin lacks `owners:manage`, a Member lacks
    `members:manage`.
    """
    return can_manage_role(perms, from_role) and can_manage_role(perms, to_role)


def declare_permission[D: Callable[..., Any]](dependency: D, permission: Permission) -> D:
    """Mark a FastAPI dependency as checking `permission` (read by the route-protection test)."""
    if permission not in ALL_PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}")
    setattr(dependency, PERMISSION_ATTRIBUTE, permission)
    return dependency


def declared_permission(dependency: object) -> Permission | None:
    """The permission declared on `dependency` by `declare_permission`, if any."""
    permission: Permission | None = getattr(dependency, PERMISSION_ATTRIBUTE, None)
    return permission


def require_authenticated(permission: UserPermission) -> Any:
    """Route dependency: a valid session (401 otherwise) holding `permission`, a permission of
    every signed-in user (`USER_PERMISSIONS`, read at each call). Returns the `Principal`.

    Usage: `principal: Annotated[Principal, require_authenticated("session:read")]`."""
    if permission not in USER_PERMISSIONS:
        raise ValueError(f"{permission!r} is not a permission of every signed-in user")

    async def check(principal: CurrentSessionDep) -> Principal:
        if permission not in USER_PERMISSIONS:
            raise ApiError(403, "permission_denied", "Permission denied.")
        return principal

    check.__name__ = f"require_authenticated_{permission.replace(':', '_')}"
    return Depends(declare_permission(check, permission))
