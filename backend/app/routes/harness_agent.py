from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.harness_agent import run_harness_agent_turn

router = APIRouter()


class HarnessAgentMessage(BaseModel):
    role: str
    content: str


class HarnessAgentChatRequest(BaseModel):
    messages: list[HarnessAgentMessage] = Field(default_factory=list)
    confirmed_action_ids: list[str] = Field(default_factory=list)


@router.post("/chat")
async def chat(body: HarnessAgentChatRequest) -> dict[str, Any]:
    return await run_harness_agent_turn(
        messages=[message.model_dump() for message in body.messages],
        confirmed_action_ids=body.confirmed_action_ids,
    )
