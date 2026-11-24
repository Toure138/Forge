"""Invitations.

Organization side (active organization, `X-Organization-Id`):
GET    /organization/invitations                  -> pending, non-expired (`members:read`)
POST   /organization/invitations                  -> invite (`members:manage` + `can_manage_role`)
DELETE /organization/invitations/{invitation_id}  -> revoke (`members:manage` + `can_manage_role`)

Invitee side (no active organization; `invitation:respond`, any signed-in user):
GET    /invitations/mine                          -> my pending invitations
POST   /invitations/{invitation_id}/accept        -> join (404, 409 already_member, 410)
POST   /invitations/{invitation_id}/decline       -> decline (404, 410)
"""

from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response

from app.api.dependencies import ClockDep, SettingsDep
from app.auth.dependencies import Principal
from app.core.authz import OrgContext, Role, require, require_authenticated
from app.db.context import RequestDbDep
from app.schemas.invitations import Invitation, InvitationCreate, MyInvitation, UserRef
from app.schemas.organizations import OrganizationSummary
from app.services import invitations

RespondDep = Annotated[Principal, require_authenticated("invitation:respond")]

router = APIRouter(tags=["invitations"])


def _invitation(info: invitations.InvitationInfo) -> Invitation:
    return Invitation(
        id=info.id,
        email=info.email,
        role=info.role.value,
        invited_by=UserRef(user_id=info.invited_by_id, name=info.invited_by_name),
        created_at=info.created_at,
        expires_at=info.expires_at,
    )


@router.get("/organization/invitations", name="list_invitations", response_model=list[Invitation])
async def list_invitations(
    _: Annotated[OrgContext, require("members:read")], db: RequestDbDep, clock: ClockDep
) -> list[Invitation]:
    return [_invitation(info) for info in await invitations.list_pending(db, now=clock.now())]


@router.post(
    "/organization/invitations",
    name="create_invitation",
    response_model=Invitation,
    status_code=201,
)
async def create_invitation(
    body: InvitationCreate,
    ctx: Annotated[OrgContext, require("members:manage")],
    db: RequestDbDep,
    clock: ClockDep,
    settings: SettingsDep,
) -> Invitation:
    info = await invitations.create(
        db,
        permissions=ctx.permissions,
        email=body.email,
        role=Role(body.role),
        ttl=timedelta(days=settings.invitation_ttl_days),
        now=clock.now(),
    )
    return _invitation(info)


@router.delete(
    "/organization/invitations/{invitation_id}",
    name="revoke_invitation",
    status_code=204,
    response_class=Response,
)
async def revoke_invitation(
    invitation_id: UUID,
    ctx: Annotated[OrgContext, require("members:manage")],
    db: RequestDbDep,
    clock: ClockDep,
) -> Response:
    await invitations.revoke(
        db, permissions=ctx.permissions, invitation_id=invitation_id, now=clock.now()
    )
    return Response(status_code=204)


@router.get("/invitations/mine", name="list_my_invitations", response_model=list[MyInvitation])
async def list_my_invitations(
    _: RespondDep, db: RequestDbDep, clock: ClockDep
) -> list[MyInvitation]:
    return [
        MyInvitation(
            id=info.id,
            organization_id=info.organization_id,
            organization_name=info.organization_name,
            role=info.role.value,
            invited_by_name=info.invited_by_name,
            expires_at=info.expires_at,
        )
        for info in await invitations.list_mine(db, now=clock.now())
    ]


@router.post(
    "/invitations/{invitation_id}/accept",
    name="accept_invitation",
    response_model=OrganizationSummary,
)
async def accept_invitation(
    invitation_id: UUID, _: RespondDep, db: RequestDbDep, clock: ClockDep
) -> OrganizationSummary:
    joined = await invitations.accept(db, invitation_id, now=clock.now())
    return OrganizationSummary(id=joined.id, name=joined.name, role=joined.role.value)


@router.post(
    "/invitations/{invitation_id}/decline",
    name="decline_invitation",
    status_code=204,
    response_class=Response,
)
async def decline_invitation(
    invitation_id: UUID, _: RespondDep, db: RequestDbDep, clock: ClockDep
) -> Response:
    await invitations.decline(db, invitation_id, now=clock.now())
    return Response(status_code=204)
