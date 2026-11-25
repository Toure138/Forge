"""Conversations (FR-32): each one belongs to one user, in one organization, with one agent.

A conversation is only ever found by its owner, with its organization active, for its agent. Every
query filters explicitly on the active organization AND the current user, with RLS underneath
(`organization_id = app_current_org() AND user_id = app_current_user()`). Anything else — unknown
id, another user's, another organization's, another agent's — gets the same 404 (FR-37).
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from app.core.errors import ApiError
from app.db import tables
from app.db.context import RequestDb


def conversation_not_found() -> ApiError:
    return ApiError(404, "conversation_not_found", "Conversation not found.")


def _owner(db: RequestDb) -> tuple[UUID, UUID]:
    if db.organization_id is None or db.user_id is None:
        raise RuntimeError("conversations need the active organization and the current user")
    return db.organization_id, db.user_id


async def get_or_create(
    db: RequestDb, conversation_id: UUID | None, agent_name: str, *, now: datetime
) -> UUID:
    """The id of the conversation to run: `conversation_id` if it is one of the current user's
    conversations with `agent_name` in the active organization (404 `conversation_not_found`
    otherwise), or a new conversation, created by the server, when `conversation_id` is None."""
    organization_id, user_id = _owner(db)
    c = tables.conversations
    if conversation_id is None:
        conversation_id = uuid4()
        await db.connection.execute(
            insert(c).values(
                id=conversation_id,
                organization_id=organization_id,
                user_id=user_id,
                agent_name=agent_name,
                created_at=now,
            )
        )
        return conversation_id
    statement = (
        select(c.c.id)
        .where(c.c.id == conversation_id)
        .where(c.c.organization_id == organization_id)
        .where(c.c.user_id == user_id)
        .where(c.c.agent_name == agent_name)
    )
    if (await db.connection.execute(statement)).scalar_one_or_none() is None:
        raise conversation_not_found()
    return conversation_id


def checkpoint_key(organization_id: UUID, user_id: UUID, conversation_id: UUID) -> str:
    """The LangGraph `thread_id` of a conversation: defence in depth, a faulty ownership check
    still could not land on the checkpoint of another user or organization."""
    return f"{organization_id}/{user_id}/{conversation_id}"
