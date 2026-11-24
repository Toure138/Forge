from pydantic import BaseModel, Field


class AgentInfo(BaseModel):
    name: str
    description: str


class RunRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = Field(
        default=None, description="Conversation id; omit to start a new thread."
    )


class RunResponse(BaseModel):
    thread_id: str
    output: str
