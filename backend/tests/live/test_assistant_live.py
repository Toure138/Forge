"""Live tests: call the REAL model (costs tokens). Skipped unless RUN_LIVE=1.

Run with:  RUN_LIVE=1 uv run pytest -m live
Use them to validate agent behaviour that a fake model cannot prove
(tool choice, prompt following). Keep them few and cheap.
"""

import pytest

from app.agents.assistant import graph as assistant
from app.core.llm import get_chat_model

pytestmark = pytest.mark.live


async def test_assistant_uses_time_tool():
    agent = assistant.build(get_chat_model())
    result = await agent.ainvoke(
        {
            "messages": [
                {"role": "user", "content": "Quelle heure est-il en UTC ? Utilise ton outil."}
            ]
        }
    )
    tool_names = [
        call["name"] for m in result["messages"] for call in getattr(m, "tool_calls", []) or []
    ]
    assert "get_current_time" in tool_names
