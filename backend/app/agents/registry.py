"""Agent registry: the only place the API learns which agents exist.

To add an agent: create `app/agents/<name>/graph.py` exposing NAME,
DESCRIPTION and `build(model, checkpointer)`, then register it in AGENTS.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.assistant import graph as assistant

Builder = Callable[[BaseChatModel, BaseCheckpointSaver | None], Any]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    build: Builder


AGENTS: dict[str, AgentSpec] = {
    assistant.NAME: AgentSpec(assistant.NAME, assistant.DESCRIPTION, assistant.build),
}


class AgentRegistry:
    """Builds each agent once and caches the compiled graph."""

    def __init__(self, model: BaseChatModel, checkpointer: BaseCheckpointSaver | None = None):
        self._model = model
        # In-memory: conversations are lost on restart. Swap for a Postgres/SQLite
        # checkpointer when persistence becomes a requirement (write an ADR).
        self._checkpointer = checkpointer or InMemorySaver()
        self._compiled: dict[str, Any] = {}

    def list(self) -> list[AgentSpec]:
        return list(AGENTS.values())

    def get(self, name: str) -> Any:
        if name not in AGENTS:
            raise KeyError(name)
        if name not in self._compiled:
            self._compiled[name] = AGENTS[name].build(self._model, self._checkpointer)
        return self._compiled[name]
