"""Assistant agent: a deep agent (planning + virtual FS + sub-agents)."""

from deepagents import create_deep_agent
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agents.assistant.prompts import SYSTEM_PROMPT
from app.agents.assistant.tools import TOOLS

NAME = "assistant"
DESCRIPTION = "General-purpose deep agent with planning, files and sub-agents."


def build(model: BaseChatModel, checkpointer: BaseCheckpointSaver | None = None):
    return create_deep_agent(
        model=model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        subagents=[
            {
                "name": "researcher",
                "description": "Investigates one focused question and reports findings.",
                "system_prompt": "You research one question thoroughly and "
                "return a short, sourced summary.",
            }
        ],
        checkpointer=checkpointer,
        name=NAME,
    )
