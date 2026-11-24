"""Members of the active organization (`X-Organization-Id`).

GET   /organization/members            -> list (`members:read`)
PATCH /organization/members/{user_id}  -> change a role (`members:manage`, then `can_change_role`)
DELETE /organization/members/{user_id} -> remove (`members:manage`, then `can_manage_role`)
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Response

from app.api.dependencies import ClockDep
from app.core.authz import OrgContext, Role, require
from app.db.context import RequestDbDep
from app.schemas.members import Member, MemberRoleUpdate
from app.services import memberships

router = APIRouter(prefix="/organization/members", tags=["members"])


def _member(info: memberships.MemberInfo) -> Member:
    return Member(
        user_id=info.user_id,
        name=info.name,
        email=info.email,
        picture_url=info.picture_url,
        role=info.role.value,
        joined_at=info.joined_at,
    )


@router.get("", name="list_members", response_model=list[Member])
async def list_members(
    _: Annotated[OrgContext, require("members:read")], db: RequestDbDep
) -> list[Member]:
    return [_member(info) for info in await memberships.list_members(db)]


@router.patch("/{user_id}", name="change_member_role", response_model=Member)
async def change_member_role(
    user_id: UUID,
    body: MemberRoleUpdate,
    ctx: Annotated[OrgContext, require("members:manage")],
    db: RequestDbDep,
    clock: ClockDep,
) -> Member:
    info = await memberships.change_role(
        db, permissions=ctx.permissions, user_id=user_id, role=Role(body.role), now=clock.now()
    )
    return _member(info)


@router.delete("/{user_id}", name="remove_member", status_code=204, response_class=Response)
async def remove_member(
    user_id: UUID,
    ctx: Annotated[OrgContext, require("members:manage")],
    db: RequestDbDep,
    clock: ClockDep,
) -> Response:
    await memberships.remove(db, permissions=ctx.permissions, user_id=user_id, now=clock.now())
    return Response(status_code=204)
