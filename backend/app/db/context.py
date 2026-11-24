"""Per-request database access and security context (ADR-0003, decision 4).

One HTTP request = one connection and ONE transaction. The RLS context is set inside that
transaction with `SELECT set_config(name, value, true)` (the parameterized equivalent of
`SET LOCAL`): it disappears at COMMIT or ROLLBACK, so nothing is left on the connection when the
pool hands it to the next request. Never use a session-level `SET`.

Every query is parameterized: values are always bound, never concatenated into SQL.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_SET_USER = text(
    "SELECT set_config('app.user_id', :user_id, true),"
    " set_config('app.user_email', :user_email, true)"
)
_SET_ORGANIZATION = text("SELECT set_config('app.organization_id', :organization_id, true)")


class RequestDb:
    """The request's connection, inside its transaction, with the context it carries."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection
        self.user_id: UUID | None = None
        self.user_email: str | None = None
        self.organization_id: UUID | None = None

    async def set_user(self, user_id: UUID, email_normalized: str) -> None:
        """Current user, for the RLS policies and the self-reader functions.

        Both the id (`app.user_id`: memberships, conversations) and the normalized email
        (`app.user_email`: invitations addressed to the user) are needed.
        """
        await self.connection.execute(
            _SET_USER, {"user_id": str(user_id), "user_email": email_normalized}
        )
        self.user_id, self.user_email = user_id, email_normalized

    async def set_organization(self, organization_id: UUID) -> None:
        """Active organization: the organization tables only show (and accept) its rows."""
        await self.connection.execute(_SET_ORGANIZATION, {"organization_id": str(organization_id)})
        self.organization_id = organization_id


@asynccontextmanager
async def request_transaction(engine: AsyncEngine) -> AsyncIterator[RequestDb]:
    """Connect, BEGIN, yield; COMMIT on success, ROLLBACK on exception (then re-raise)."""
    async with engine.connect() as connection:
        await connection.begin()
        try:
            yield RequestDb(connection)
        except Exception:
            await connection.rollback()
            raise
        await connection.commit()


async def request_db(request: Request) -> AsyncIterator[RequestDb]:
    """FastAPI dependency: the request's transaction, on the application's engine."""
    engine: AsyncEngine = request.app.state.engine
    async with request_transaction(engine) as db:
        yield db


# `scope="function"`: the transaction is committed and the connection released when the route
# function returns, BEFORE the response is sent. A streamed response (SSE) therefore holds no
# connection and never runs before its database work is committed.
RequestDbDep = Annotated[RequestDb, Depends(request_db, scope="function")]
