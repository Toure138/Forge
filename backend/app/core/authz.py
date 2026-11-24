"""Authorization model: permissions, roles and role-management rules (constitution XI).

This module is the ONLY place that gives role names a meaning. The rest of the code checks
permissions (`require("members:manage")`, `can_manage_role`), never role names. The only other
mentions of role names are mirrors checked by `test_schema_literals_match_authz` in
`tests/test_authz.py` (`app/schemas/common.py` for the API contract, `app/db/tables.py` for the
CHECK constraint) and the "at least one Owner" business invariant (`app/services/memberships.py`).

Route dependencies: `require_authenticated(perm)` (any valid session) and `require(perm)` (a
permission in the active organization, `X-Organization-Id`). Each one carries
`__forge_permission__`, which the route-protection test looks for (FR-25).
"""

from collections.abc import Callable, Set
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal, get_args
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select

from app.auth.dependencies import CurrentSessionDep, Principal
from app.core.clock import Clock
from app.core.errors import ApiError
from app.core.security_log import security_event
from app.db import tables
from app.db.context import RequestDb, RequestDbDep


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


# --- organization context ---

ORGANIZATION_HEADER: Final = "X-Organization-Id"

# FR-13: whoever creates an organization becomes its Owner.
CREATOR_ROLE: Final = Role.OWNER


def organization_required() -> ApiError:
    return ApiError(400, "organization_required", "A valid X-Organization-Id header is required.")


def organization_not_found() -> ApiError:
    """Same answer for an organization that does not exist and for one the user is not a member
    of (FR-15, FR-37)."""
    return ApiError(404, "organization_not_found", "Organization not found.")


def permission_denied() -> ApiError:
    return ApiError(403, "permission_denied", "Permission denied.")


@dataclass(frozen=True)
class OrgContext:
    """The active organization of a request, as proven by the user's membership."""

    principal: Principal
    organization_id: UUID
    organization_name: str
    role: Role
    permissions: frozenset[OrganizationPermission]


def _organization_id(request: Request) -> UUID:
    raw = request.headers.get(ORGANIZATION_HEADER)
    try:
        return UUID(raw or "")
    except ValueError:
        raise organization_required() from None


async def _membership(db: RequestDb, organization_id: UUID, user_id: UUID) -> Any:
    """(organization name, role) of the user in the organization, or None. Filtered explicitly
    on the organization and the user, with RLS underneath (constitution X)."""
    m, o = tables.memberships, tables.organizations
    statement = (
        select(o.c.name, m.c.role)
        .join(o, o.c.id == m.c.organization_id)
        .where(m.c.organization_id == organization_id)
        .where(m.c.user_id == user_id)
    )
    return (await db.connection.execute(statement)).one_or_none()


def require(permission: OrganizationPermission) -> Any:
    """Route dependency for an action in the active organization.

    Valid session (401) -> CSRF on unsafe methods (403) -> `X-Organization-Id` (400) -> membership
    of the current user in that organization, read at each request (otherwise 404
    `organization_not_found`, logged on the platform log only, R2) -> `permission` among the
    role's permissions (`permissions_for`, read at each call), otherwise `permission.denied` in the
    organization's audit log and 403. Returns the `OrgContext`; the database context stays on the
    organization for the rest of the request.

    Usage: `ctx: Annotated[OrgContext, require("members:read")]`."""
    if permission not in ORGANIZATION_PERMISSIONS:
        raise ValueError(f"{permission!r} is not an organization permission")

    async def check(request: Request, principal: CurrentSessionDep, db: RequestDbDep) -> OrgContext:
        organization_id = _organization_id(request)
        await db.set_organization(organization_id)
        membership = await _membership(db, organization_id, principal.user_id)
        if membership is None:
            security_event(
                "organization_access_denied",
                user_id=principal.user_id,
                organization_id=organization_id,
            )
            raise organization_not_found()
        role = Role(membership.role)
        permissions = permissions_for(role)
        if permission not in permissions:
            clock: Clock = request.app.state.clock
            await db.record_denial(
                permission_denied(),
                action="permission.denied",
                now=clock.now(),
                target_type="organization",
                target_id=str(organization_id),
                details={"permission": permission},
            )
        return OrgContext(principal, organization_id, membership.name, role, permissions)

    check.__name__ = f"require_{permission.replace(':', '_')}"
    return Depends(declare_permission(check, permission))


def missing_role_permission(perms: Set[str], *roles: Role) -> OrganizationPermission | None:
    """The first permission `perms` lack to manage holders of `roles` (None: all manageable)."""
    for role in roles:
        needed = ROLE_MANAGEMENT_PERMISSION[Role(role)]
        if needed not in perms:
            return needed
    return None
