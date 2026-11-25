from uuid import UUID

from pydantic import BaseModel, Field


class AgentInfo(BaseModel):
    name: str
    description: str


class RunRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: UUID | None = Field(
        default=None,
        description=(
            "Id of one of the user's conversations with this agent, in the active organization, "
            "as returned by the server; omit to start a new conversation."
        ),
    )


class RunResponse(BaseModel):
    thread_id: UUID  # serialized as a string: unchanged on the JSON side
    output: str
