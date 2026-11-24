"""Organizations: API contract (`/api/organizations`, `/api/organization`)."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, StringConstraints

from app.schemas.common import Permission, Role

# Leading and trailing spaces are removed BEFORE the length check: a blank name is refused.
OrganizationName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class OrganizationCreate(BaseModel):
    name: OrganizationName


class OrganizationRename(OrganizationCreate):
    pass


class OrganizationSummary(BaseModel):
    """An organization the user is a member of, with their role in it."""

    id: UUID
    name: str
    role: Role


class ActiveOrganization(OrganizationSummary):
    """The active organization (`X-Organization-Id`) and what the user may do in it."""

    permissions: list[Permission]
