from uuid import UUID

from pydantic import BaseModel


class UserOut(BaseModel):
    id: UUID
    email: str
    name: str
    picture_url: str | None


class SessionOut(BaseModel):
    """`GET /api/auth/session`: the signed-in user, and the CSRF token to send back in
    `X-CSRF-Token` on every unsafe method (POST, PATCH, PUT, DELETE)."""

    user: UserOut
    csrf_token: str
