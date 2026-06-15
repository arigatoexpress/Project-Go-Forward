from pydantic import BaseModel, Field


class CopilotTurn(BaseModel):
    """One prior message in the Ops Copilot conversation."""

    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="The message text")


class CopilotRequest(BaseModel):
    """Request schema for the admin/employee Ops Copilot endpoint."""

    message: str = Field(..., description="The staffer's question or message", max_length=4000)
    history: list[CopilotTurn] = Field(
        default_factory=list,
        description="Prior conversation turns (most recent last); the last 10 are used.",
    )
