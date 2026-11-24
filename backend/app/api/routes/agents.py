"""Agent endpoints. Every route needs `agents:use` in the active organization (`X-Organization-Id`,
FR-31): refusals (401, 403, 404, 422) are JSON errors raised before any stream is opened.

`thread_id` is the id of a conversation (FR-32): created by the server, it can only be continued
by its owner, in its organization, with its agent (404 `conversation_not_found` otherwise). The
conversation is checked or created, and the request's transaction committed, BEFORE the response
is returned (`RequestDbDep`, scope "function"): no database connection is held while the model
streams. The agents' checkpointer sees `{organization_id}/{user_id}/{conversation_id}`.

GET  /agents                -> the agents (name, description)
POST /agents/{name}/invoke  -> full answer (JSON)
POST /agents/{name}/stream  -> Server-Sent Events:
    event: metadata  data: {"thread_id": ...}
    event: token     data: {"text": ...}
    event: tool      data: {"name": ..., "status": "start"|"end"}
    event: error     data: {"message": ...}
    event: done      data: {}
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Request
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from sse_starlette.sse import EventSourceResponse

from app.agents.registry import AgentRegistry
from app.api.dependencies import ClockDep
from app.core.authz import OrgContext, require
from app.core.clock import Clock
from app.core.errors import ApiError
from app.db.context import RequestDb, RequestDbDep
from app.schemas.agents import AgentInfo, RunRequest, RunResponse
from app.services import conversations

router = APIRouter(prefix="/agents", tags=["agents"])


def _registry(request: Request) -> AgentRegistry:
    registry: AgentRegistry = request.app.state.registry
    return registry


def _get_agent(request: Request, name: str) -> Any:
    try:
        return _registry(request).get(name)
    except KeyError:
        raise ApiError(404, "agent_not_found", "Agent not found.") from None


def _text(message: BaseMessage) -> str:
    return str(message.text)


AgentsUse = Annotated[OrgContext, require("agents:use")]


@router.get("", name="list_agents", response_model=list[AgentInfo])
async def list_agents(_: AgentsUse, request: Request) -> list[AgentInfo]:
    return [AgentInfo(name=a.name, description=a.description) for a in _registry(request).list()]


async def _conversation(
    ctx: OrgContext, db: RequestDb, clock: Clock, name: str, body: RunRequest
) -> tuple[UUID, dict[str, Any]]:
    """(conversation id returned to the client, LangGraph config) of the conversation to run."""
    conversation_id = await conversations.get_or_create(db, body.thread_id, name, now=clock.now())
    key = conversations.checkpoint_key(ctx.organization_id, ctx.principal.user_id, conversation_id)
    return conversation_id, {"configurable": {"thread_id": key}}


@router.post("/{name}/invoke", name="invoke_agent", response_model=RunResponse)
async def invoke(
    name: str,
    body: RunRequest,
    ctx: AgentsUse,
    db: RequestDbDep,
    clock: ClockDep,
    request: Request,
) -> RunResponse:
    agent = _get_agent(request, name)
    thread_id, config = await _conversation(ctx, db, clock, name, body)
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": body.message}]}, config=config
    )
    return RunResponse(thread_id=thread_id, output=_text(result["messages"][-1]))


@router.post("/{name}/stream", name="stream_agent")
async def stream(
    name: str,
    body: RunRequest,
    ctx: AgentsUse,
    db: RequestDbDep,
    clock: ClockDep,
    request: Request,
) -> EventSourceResponse:
    agent = _get_agent(request, name)
    # Database work done here, before the response: the generator below never touches `db`.
    thread_id, config = await _conversation(ctx, db, clock, name, body)

    async def events() -> AsyncIterator[dict[str, str]]:
        yield _sse("metadata", {"thread_id": str(thread_id)})
        try:
            async for msg, meta in agent.astream(
                {"messages": [{"role": "user", "content": body.message}]},
                config=config,
                stream_mode="messages",
            ):
                # Only the top-level agent's tokens are shown; sub-agent tokens
                # (nested namespaces) stay internal.
                if meta.get("checkpoint_ns", "").count("|") > 0:
                    continue
                if isinstance(msg, AIMessageChunk):
                    for chunk in msg.tool_call_chunks or []:
                        if chunk.get("name"):
                            yield _sse("tool", {"name": chunk["name"], "status": "start"})
                    if text := _text(msg):
                        yield _sse("token", {"text": text})
                elif isinstance(msg, ToolMessage):
                    yield _sse("tool", {"name": msg.name, "status": "end"})
        except Exception as exc:  # surfaced to the client, logged by uvicorn
            yield _sse("error", {"message": str(exc)})
        yield _sse("done", {})

    return EventSourceResponse(events())


def _sse(event: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": event, "data": json.dumps(data)}
