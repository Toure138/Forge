"""Invitations: API contract.

Organization side (`/api/organization/invitations`): `InvitationCreate`, `Invitation`.
Invitee side (`/api/invitations/...`): `MyInvitation`.

The email is checked by a minimal pattern, not `EmailStr` (which needs `email-validator`): only a
Google user whose VERIFIED address is the same can accept (plan, « Contrat API »).
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import Role


class InvitationCreate(BaseModel):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role


class UserRef(BaseModel):
    user_id: UUID
    name: str


class Invitation(BaseModel):
    """A pending invitation of the active organization. `email` is the normalized address."""

    id: UUID
    email: str
    role: Role
    invited_by: UserRef
    created_at: datetime
    expires_at: datetime


class MyInvitation(BaseModel):
    """A pending invitation addressed to the signed-in user (`GET /api/invitations/mine`)."""

    id: UUID
    organization_id: UUID
    organization_name: str
    role: Role
    invited_by_name: str
    expires_at: datetime
