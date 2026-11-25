"""Members of the active organization: API contract (`/api/organization/members`)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.schemas.common import Role


class Member(BaseModel):
    user_id: UUID
    name: str
    email: str
    picture_url: str | None
    role: Role
    joined_at: datetime


class MemberRoleUpdate(BaseModel):
    role: Role
