"""Graph entry points for LangGraph Studio (`uv run langgraph dev`).

The LangGraph server manages persistence itself, so graphs are built
WITHOUT a checkpointer here. Add one line per agent registered in
app/agents/registry.py.
"""

from app.agents.assistant import graph as assistant
from app.core.llm import get_chat_model

assistant_graph = assistant.build(get_chat_model(), checkpointer=None)
