from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import orchestrator
from app.agent.messages import create_custom_message
from app.agent.types import AgentMessage
from app.database import async_session, get_db
from app.models import models
from app.operator.guards import ActorContext
from app.routes._agent_sse import agent_sse_response

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class AgentChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    conversation_id: str | None = None
    page_context: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None


@router.post("/chat")
async def agent_chat(
    body: AgentChatRequest,
    db: AsyncSession = Depends(get_db),
):
    conversation_id = str(body.conversation_id or "").strip() or f"conv_{uuid.uuid4().hex[:12]}"
    actor = ActorContext(
        actor_id=models.LOCAL_DEFAULT_ACTOR_ID,
        session_id=conversation_id,
        adapter="web",
    )
    user_message = _last_user_message(body.messages)
    injected_messages = _legacy_injected_messages(body)

    async def run(event_sink):
        async with async_session() as stream_db:
            return await orchestrator.run_agent_turn(
                stream_db,
                actor,
                user_message,
                conversation_id,
                event_sink=event_sink,
                injected_messages=injected_messages,
            )

    return agent_sse_response(run)


def _last_user_message(messages: list[ChatMessage]) -> str | None:
    for message in reversed(messages):
        if str(message.role or "").lower() == "user":
            text = str(message.content or "").strip()
            if text:
                return text
    return None


def _legacy_injected_messages(body: AgentChatRequest) -> list[AgentMessage]:
    messages: list[AgentMessage] = []
    if body.page_context:
        messages.append(
            create_custom_message(
                "legacy_page_context",
                "Legacy page context:\n" + _json_context(body.page_context),
                display=False,
                details={"source": "api_agent_page_context"},
            )
        )
    if body.memory:
        messages.append(
            create_custom_message(
                "legacy_memory",
                "Legacy memory context:\n" + _json_context(body.memory),
                display=False,
                details={"source": "api_agent_memory"},
            )
        )
    return messages


def _json_context(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


__all__ = ["router", "AgentChatRequest", "ChatMessage"]
