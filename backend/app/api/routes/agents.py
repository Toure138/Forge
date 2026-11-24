"""Agent endpoints.

POST /agents/{name}/invoke  -> full answer (JSON)
POST /agents/{name}/stream  -> Server-Sent Events:
    event: metadata  data: {"thread_id": ...}
    event: token     data: {"text": ...}
    event: tool      data: {"name": ..., "status": "start"|"end"}
    event: error     data: {"message": ...}
    event: done      data: {}
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import AIMessageChunk, BaseMessage, ToolMessage
from sse_starlette.sse import EventSourceResponse

from app.agents.registry import AgentRegistry
from app.schemas.agents import AgentInfo, RunRequest, RunResponse

router = APIRouter(prefix="/agents", tags=["agents"])


def _registry(request: Request) -> AgentRegistry:
    registry: AgentRegistry = request.app.state.registry
    return registry


def _get_agent(request: Request, name: str) -> Any:
    try:
        return _registry(request).get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown agent '{name}'") from None


def _text(message: BaseMessage) -> str:
    return str(message.text)


@router.get("", response_model=list[AgentInfo])
async def list_agents(request: Request) -> list[AgentInfo]:
    return [AgentInfo(name=a.name, description=a.description) for a in _registry(request).list()]


@router.post("/{name}/invoke", response_model=RunResponse)
async def invoke(name: str, body: RunRequest, request: Request) -> RunResponse:
    agent = _get_agent(request, name)
    thread_id = body.thread_id or str(uuid.uuid4())
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": body.message}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return RunResponse(thread_id=thread_id, output=_text(result["messages"][-1]))


@router.post("/{name}/stream")
async def stream(name: str, body: RunRequest, request: Request) -> EventSourceResponse:
    agent = _get_agent(request, name)
    thread_id = body.thread_id or str(uuid.uuid4())

    async def events() -> AsyncIterator[dict[str, str]]:
        yield {"event": "metadata", "data": json.dumps({"thread_id": thread_id})}
        try:
            async for msg, meta in agent.astream(
                {"messages": [{"role": "user", "content": body.message}]},
                config={"configurable": {"thread_id": thread_id}},
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
