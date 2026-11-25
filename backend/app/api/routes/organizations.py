"""Organizations.

GET  /organizations  -> the user's organizations, role in each (`organization:list`)
POST /organizations  -> create one; the user becomes its sole Owner (`organization:create`)
GET  /organization   -> the active organization, role and permissions (`organization:read`)
PATCH /organization  -> rename it (`organization:rename`)
POST /organization/leave -> the user leaves it, unless last Owner (`organization:leave`)

Routes under `/organization` act in the organization named by `X-Organization-Id`, once
`authz.require(...)` has proven the user's membership.
"""

from typing import Annotated

from fastapi import APIRouter, Response

from app.api.dependencies import ClockDep
from app.auth.dependencies import Principal
from app.core.authz import OrgContext, require, require_authenticated
from app.db.context import RequestDbDep
from app.schemas.organizations import (
    ActiveOrganization,
    OrganizationCreate,
    OrganizationRename,
    OrganizationSummary,
)
from app.services import memberships, organizations

router = APIRouter(tags=["organizations"])


def _summary(organization: organizations.Organization) -> OrganizationSummary:
    return OrganizationSummary(
        id=organization.id, name=organization.name, role=organization.role.value
    )


def active_organization(ctx: OrgContext, name: str | None = None) -> ActiveOrganization:
    return ActiveOrganization(
        id=ctx.organization_id,
        name=ctx.organization_name if name is None else name,
        role=ctx.role.value,
        permissions=sorted(ctx.permissions),
    )


@router.get(
    "/organizations", name="list_my_organizations", response_model=list[OrganizationSummary]
)
async def list_my_organizations(
    _: Annotated[Principal, require_authenticated("organization:list")], db: RequestDbDep
) -> list[OrganizationSummary]:
    return [_summary(org) for org in await organizations.list_mine(db)]


@router.post(
    "/organizations",
    name="create_organization",
    response_model=OrganizationSummary,
    status_code=201,
)
async def create_organization(
    body: OrganizationCreate,
    _: Annotated[Principal, require_authenticated("organization:create")],
    db: RequestDbDep,
    clock: ClockDep,
) -> OrganizationSummary:
    return _summary(await organizations.create(db, name=body.name, now=clock.now()))


@router.get("/organization", name="get_active_organization", response_model=ActiveOrganization)
async def get_active_organization(
    ctx: Annotated[OrgContext, require("organization:read")],
) -> ActiveOrganization:
    return active_organization(ctx)


@router.patch("/organization", name="rename_organization", response_model=ActiveOrganization)
async def rename_organization(
    body: OrganizationRename,
    ctx: Annotated[OrgContext, require("organization:rename")],
    db: RequestDbDep,
    clock: ClockDep,
) -> ActiveOrganization:
    await organizations.rename(db, name=body.name, now=clock.now())
    return active_organization(ctx, name=body.name)


@router.post(
    "/organization/leave", name="leave_organization", status_code=204, response_class=Response
)
async def leave_organization(
    _: Annotated[OrgContext, require("organization:leave")], db: RequestDbDep, clock: ClockDep
) -> Response:
    await memberships.leave(db, now=clock.now())
    return Response(status_code=204)
