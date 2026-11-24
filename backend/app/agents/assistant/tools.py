"""Custom tools for the assistant agent.

Rules for tools (see backend/CLAUDE.md):
- typed arguments + a docstring: the docstring IS the description the model sees;
- pure and deterministic where possible, so they can be unit-tested without an LLM;
- return plain strings or JSON-serialisable data.
"""

from datetime import UTC, datetime

from langchain_core.tools import tool


@tool
def get_current_time() -> str:
    """Return the current UTC date and time in ISO 8601 format."""
    return datetime.now(UTC).isoformat()


TOOLS = [get_current_time]
