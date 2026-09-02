from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import orchestrator
from app.agent.session.tree import SessionTree
from app.database import async_session, get_db
from app.models import models
from app.operator.audit import redact_audit_args
from app.operator.errors import OperatorError
from app.operator.guards import ActorContext, json_safe
from app.operator.memory import memory_session_id, retrieve_memories, write_memory_candidate
from app.operator.manual_review import (
    ManualReviewConflict,
    ManualReviewNotFound,
    get_manual_review_case,
    list_manual_review_cases,
    resolve_manual_review_case,
)
from app.operator.plan_runtime import pending_plan_bootstrap
from app.operator.public_redaction import redact_public_payload
from app.operator.proposals import confirm_proposal, reject_proposal
from app.operator.session_authority import (
    AUTHORITY_STATE_KEY,
    BROWSER_PRINCIPAL_COOKIE_PATH,
    SessionAuthorityError,
    bind_session_authority,
    issue_principal_token,
    verify_principal_token,
)
from app.routes._agent_sse import agent_sse_response
from app.services.harness_history import (
    delete_conversation,
    get_conversation,
    list_conversations,
)

router = APIRouter()

_BROWSER_PRINCIPAL_COOKIE = "offeru_browser_principal"
_BROWSER_PRINCIPAL_MAX_AGE = 60 * 60 * 24 * 365


def _set_browser_principal_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _BROWSER_PRINCIPAL_COOKIE,
        token,
        max_age=_BROWSER_PRINCIPAL_MAX_AGE,
        httponly=True,
        secure=False,
        samesite="lax",
        path=BROWSER_PRINCIPAL_COOKIE_PATH,
    )


def _authenticated_subject(request: Request) -> str:
    try:
        return verify_principal_token(request.cookies.get(_BROWSER_PRINCIPAL_COOKIE))
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def _actor_for_request_session(
    db: AsyncSession,
    request: Request,
    session_id: str,
    *,
    allow_create: bool,
) -> tuple[ActorContext, str | None]:
    token = request.cookies.get(_BROWSER_PRINCIPAL_COOKIE)
    issued_token: str | None = None
    if token:
        try:
            subject = verify_principal_token(token)
        except SessionAuthorityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    elif allow_create:
        issued_token, subject = issue_principal_token()
    else:
        raise HTTPException(status_code=401, detail="authenticated browser principal is required")
    try:
        actor = await bind_session_authority(
            db, session_id=session_id, auth_subject=subject, allow_create=allow_create
        )
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return actor, issued_token


class HarnessAgentMessage(BaseModel):
    role: str
    content: str


class HarnessAgentChatRequest(BaseModel):
    messages: list[HarnessAgentMessage] = Field(default_factory=list)
    memory: dict[str, Any] | None = None
    conversation_id: str | None = None


class HarnessAgentMemoryImportRequest(BaseModel):
    content: dict[str, Any] | list[Any] | str


class TreeNavigateRequest(BaseModel):
    entry_id: str


def actor_for_conversation(conversation_id: str) -> ActorContext:
    return ActorContext(
        actor_id=models.LOCAL_DEFAULT_ACTOR_ID,
        session_id=conversation_id,
        adapter="web",
    )


def _conversation_id(value: str | None = None) -> str:
    clean = str(value or "").strip()
    return clean or f"conv_{uuid.uuid4().hex[:12]}"


def _last_user_message(messages: list[HarnessAgentMessage]) -> str | None:
    for message in reversed(messages):
        if str(message.role or "").lower() == "user":
            text = str(message.content or "").strip()
            if text:
                return text
    return None


@router.post("/chat")
async def chat(
    body: HarnessAgentChatRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    conversation_id = _conversation_id(body.conversation_id)
    actor, issued_token = await _actor_for_request_session(
        db, request, conversation_id, allow_create=True
    )
    await db.commit()
    if issued_token:
        _set_browser_principal_cookie(response, issued_token)
    result = await orchestrator.run_agent_turn(
        db,
        actor,
        _last_user_message(body.messages),
        conversation_id,
    )
    return _route_response(result)


@router.post("/chat/stream")
async def chat_stream(
    body: HarnessAgentChatRequest,
    request: Request,
):
    conversation_id = _conversation_id(body.conversation_id)
    browser_token = request.cookies.get(_BROWSER_PRINCIPAL_COOKIE)
    if browser_token:
        try:
            auth_subject = verify_principal_token(browser_token)
        except SessionAuthorityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        issued_token = None
    else:
        issued_token, auth_subject = issue_principal_token()
    actor = ActorContext(
        actor_id=models.LOCAL_DEFAULT_ACTOR_ID,
        session_id=conversation_id,
        adapter="web",
        auth_subject=auth_subject,
    )
    if orchestrator.is_session_busy(
        conversation_id,
        actor_id=str(getattr(actor, "actor_id", "") or ""),
    ):
        raise HTTPException(
            status_code=409,
            detail=json_safe(orchestrator.session_busy_response(conversation_id)),
        )
    user_message = _last_user_message(body.messages)

    async def run(event_sink):
        async with _stream_db_session() as stream_db:
            bound_actor = await bind_session_authority(
                stream_db, session_id=conversation_id, auth_subject=auth_subject, allow_create=True
            )
            await stream_db.commit()
            return await orchestrator.run_agent_turn(
                stream_db,
                bound_actor,
                user_message,
                conversation_id,
                event_sink=event_sink,
            )

    stream_response = agent_sse_response(run)
    if issued_token:
        _set_browser_principal_cookie(stream_response, issued_token)
    return stream_response


def _stream_db_session():
    return async_session()


@router.post("/proposals/{proposal_id}/confirm")
async def confirm_proposal_route(
    proposal_id: str,
    request: Request,
    body: dict[str, Any] | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    request_body = dict(body or {})
    actor = await _actor_for_proposal(
        db, proposal_id, request_body, auth_subject=_authenticated_subject(request)
    )
    request_body.pop("operator_session_id", None)
    return redact_public_payload(await confirm_proposal(db, actor, proposal_id, request_body))


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal_route(
    proposal_id: str,
    request: Request,
    body: dict[str, Any] | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    request_body = dict(body or {})
    actor = await _actor_for_proposal(
        db, proposal_id, request_body, auth_subject=_authenticated_subject(request)
    )
    request_body.pop("operator_session_id", None)
    return redact_public_payload(await reject_proposal(db, actor, proposal_id, request_body))


@router.get("/manual-review-cases")
async def list_manual_review_cases_route(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    actor, _ = await _actor_for_request_session(
        db, request, _conversation_id(session_id), allow_create=False
    )
    try:
        return {"cases": await list_manual_review_cases(db, actor)}
    except ManualReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/manual-review-cases/{case_id}")
async def get_manual_review_case_route(
    case_id: str,
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    actor, _ = await _actor_for_request_session(
        db, request, _conversation_id(session_id), allow_create=False
    )
    try:
        return {"case": await get_manual_review_case(db, actor, case_id)}
    except ManualReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ManualReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/manual-review-cases/{case_id}/resolve")
async def resolve_manual_review_case_route(
    case_id: str,
    body: dict[str, Any],
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id is required")
    actor, _ = await _actor_for_request_session(
        db, request, session_id, allow_create=False
    )
    try:
        return await resolve_manual_review_case(db, actor, case_id, body)
    except ManualReviewNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ManualReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/conversations")
async def conversations(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List conversations owned by the authenticated browser principal.

    File-store conversations carry no principal attribution, so ownership is
    derived from the durable AgentSession authority state: only conversations
    whose AgentSession row is bound to this browser principal are listed.
    Unbound/legacy conversations fail closed (not exposed).
    """
    auth_subject = _authenticated_subject(request)
    rows = list((await db.execute(select(models.AgentSession))).scalars().all())
    owned = {
        str(row.session_id)
        for row in rows
        if _session_authority_subject(row) == auth_subject
    }
    conversations = [
        conversation
        for conversation in list_conversations()
        if str(conversation.get("id") or "") in owned
    ]
    return {"conversations": conversations}


@router.get("/sessions/{session_id}/bootstrap")
async def session_bootstrap(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    actor, _ = await _actor_for_request_session(
        db, request, _conversation_id(session_id), allow_create=False
    )
    return await pending_plan_bootstrap(db, actor)


def _session_authority_subject(row: Any) -> str:
    """Read the auth_subject recorded by bind_session_authority on an AgentSession row."""
    state = dict(getattr(row, "state_json", None) or {})
    authority = state.get(AUTHORITY_STATE_KEY)
    if not isinstance(authority, dict):
        return ""
    return str(authority.get("auth_subject") or "")


async def _bind_conversation_owner(db: AsyncSession, conversation_id: str, auth_subject: str) -> ActorContext:
    """Bind the browser principal to the conversation's actor/session ownership.

    Unbound conversations (no AgentSession authority) and conversations owned
    by a different browser principal are rejected with 403; a missing principal
    is already rejected with 401 by the caller.
    """
    try:
        return await bind_session_authority(
            db,
            session_id=_conversation_id(conversation_id),
            auth_subject=auth_subject,
            allow_create=False,
        )
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}")
async def conversation_detail(
    conversation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    auth_subject = _authenticated_subject(request)
    await _bind_conversation_owner(db, conversation_id, auth_subject)
    conversation = get_conversation(_conversation_id(conversation_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.delete("/conversations/{conversation_id}")
async def remove_conversation(
    conversation_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    auth_subject = _authenticated_subject(request)
    await _bind_conversation_owner(db, conversation_id, auth_subject)
    return {"ok": delete_conversation(_conversation_id(conversation_id))}


@router.get("/conversations/{conversation_id}/tree")
async def conversation_tree(
    conversation_id: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    actor, issued_token = await _actor_for_request_session(
        db, request, _conversation_id(conversation_id), allow_create=True
    )
    if issued_token:
        _set_browser_principal_cookie(response, issued_token)
    tree = await SessionTree.load(db, session_id=_conversation_id(conversation_id), actor_id=actor.actor_id)
    rows = tree.entries
    leaf_id = tree.leaf_id
    return {
        "entries": [
            {
                "entry_id": row.entry_id,
                "parent_id": row.parent_id,
                "entry_type": row.entry_type,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "preview": _entry_preview(row),
            }
            for row in rows
        ],
        "leaf_id": leaf_id,
        "messages": _tree_context_messages(tree),
    }


@router.post("/conversations/{conversation_id}/tree/navigate")
async def navigate_conversation_tree(
    conversation_id: str,
    body: TreeNavigateRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    actor, issued_token = await _actor_for_request_session(
        db, request, _conversation_id(conversation_id), allow_create=True
    )
    if issued_token:
        _set_browser_principal_cookie(response, issued_token)
    result = await orchestrator.navigate_tree(db, actor, _conversation_id(conversation_id), body.entry_id)
    if result.get("error") == "session_busy":
        raise HTTPException(status_code=409, detail=result)
    if result.get("ok") is False and isinstance(result.get("error"), Mapping):
        code = str(result["error"].get("code") or "")
        if code == "not_found":
            raise HTTPException(status_code=404, detail=result)
    return result


async def _actor_for_memory_session(
    db: AsyncSession,
    request: Request,
    response: Response,
) -> ActorContext:
    """Bind (or establish) this browser principal's dedicated memory session.

    Memory import/export no longer falls back to a fixed actor: every request
    binds its authenticated browser principal to a deterministic per-principal
    memory session through ``bind_session_authority``, so one principal can
    never read or overwrite another principal's memories. Requests without a
    principal receive a fresh issued principal (mirroring the chat endpoints);
    forged or foreign tokens are rejected with 401/403.
    """
    token = request.cookies.get(_BROWSER_PRINCIPAL_COOKIE)
    issued_token: str | None = None
    if token:
        try:
            subject = verify_principal_token(token)
        except SessionAuthorityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    else:
        issued_token, subject = issue_principal_token()
    try:
        actor = await bind_session_authority(
            db,
            session_id=memory_session_id(subject),
            auth_subject=subject,
            allow_create=True,
        )
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if issued_token:
        _set_browser_principal_cookie(response, issued_token)
    return actor


@router.get("/memory/export")
async def export_memory(
    request: Request,
    response: Response,
    format: str = "json",
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    actor = await _actor_for_memory_session(db, request, response)
    memory = await retrieve_memories(db, actor=actor, session_id=actor.session_id)
    if memory.get("ok") is not True:
        raise HTTPException(status_code=502, detail=json_safe(memory))
    redacted_memory = redact_audit_args(memory)
    if format.lower() in {"md", "markdown"}:
        return {"format": "markdown", "content": _memory_markdown(redacted_memory), "memory": redacted_memory}
    return {"format": "json", "content": redacted_memory, "memory": redacted_memory}


@router.post("/memory/import")
async def import_memory(
    body: HarnessAgentMemoryImportRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    actor = await _actor_for_memory_session(db, request, response)
    stored: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in _memory_import_items(body.content):
        result = await write_memory_candidate(db, actor, **item)
        if result.get("ok") and result.get("status") == "stored":
            stored.append(result["memory"])
        elif result.get("ok"):
            continue
        else:
            errors.append(json_safe(result))
    memory = await retrieve_memories(db, actor=actor, session_id=actor.session_id)
    if memory.get("ok") is not True:
        raise HTTPException(status_code=502, detail=json_safe(memory))
    return {
        "ok": not errors,
        "memory": redact_audit_args(memory),
        "stored": stored,
        "errors": errors,
    }


async def _actor_for_proposal(
    db: AsyncSession, proposal_id: str, body: Mapping[str, Any], *, auth_subject: str
) -> ActorContext:
    session_id = str(body.get("operator_session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="operator_session_id is required for proposal decisions")
    from app.operator.session_authority import SessionAuthorityError, bind_session_authority

    try:
        actor = await bind_session_authority(
            db, session_id=session_id, auth_subject=auth_subject, allow_create=False
        )
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    proposal = await db.scalar(
        select(models.ProposalCache).where(
            models.ProposalCache.proposal_id == str(proposal_id),
            models.ProposalCache.actor_id == str(actor.actor_id),
            models.ProposalCache.session_id == str(actor.session_id),
        )
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal was not found in this actor/session scope")
    return actor


def _route_response(response: Mapping[str, Any]) -> dict[str, Any]:
    public_response = orchestrator.public_agent_response(response)
    if response.get("ok") is False and response.get("error") == "session_busy":
        raise HTTPException(status_code=409, detail=json_safe(public_response))
    return json_safe(redact_public_payload(public_response))


def _tree_context_messages(tree: SessionTree) -> list[dict[str, Any]]:
    context = tree.build_context()
    rows: list[dict[str, Any]] = []
    for index, message in enumerate(context.messages):
        payload = message.model_dump(mode="json") if hasattr(message, "model_dump") else {}
        entry_id = context.message_entry_ids[index] if index < len(context.message_entry_ids) else None
        rows.append(
            {
                "entry_id": entry_id,
                "role": str(payload.get("role") or getattr(message, "role", "")),
                "content": _message_preview(payload),
                "message": payload,
            }
        )
    return rows


def _entry_preview(entry: Any) -> str:
    payload = entry.payload or {}
    entry_type = str(entry.entry_type or "")
    if entry_type in {"message", "custom_message"}:
        return _message_preview(payload)[:120] or entry_type
    if entry_type == "branch_summary":
        return str(payload.get("summary") or entry_type)[:120]
    if entry_type == "compaction":
        return str(payload.get("summary") or entry_type)[:120]
    if entry_type == "session_info":
        return str(payload.get("name") or entry_type)[:120]
    return entry_type


def _message_preview(payload: Mapping[str, Any]) -> str:
    if payload.get("role") == "custom":
        return str(payload.get("content") or "")
    if payload.get("role") == "branchSummary":
        return str(payload.get("summary") or "")
    if payload.get("role") == "compactionSummary":
        return str(payload.get("summary") or "")
    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, Mapping):
                parts.append(str(block.get("text") or block.get("thinking") or ""))
        return "".join(parts)
    return ""


def _memory_import_items(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        text = content.strip()
        return [
            {
                "category": "preferences",
                "topic": "import",
                "content": {"preference": text},
                "confidence": 1.0,
            }
        ] if text else []
    if isinstance(content, list):
        items: list[dict[str, Any]] = []
        for item in content:
            items.extend(_memory_import_items(item))
        return items
    if not isinstance(content, Mapping):
        return []
    if isinstance(content.get("memories"), list):
        return _memory_import_items(content["memories"])

    normalized: list[dict[str, Any]] = []
    for category, raw_items in content.items():
        category_text = str(category)
        values = raw_items if isinstance(raw_items, list) else [raw_items]
        for raw in values:
            if isinstance(raw, Mapping):
                raw_content = raw.get("content")
                payload = raw_content if isinstance(raw_content, Mapping) else {
                    key: value
                    for key, value in raw.items()
                    if key not in {"category", "topic", "skill", "confidence"}
                }
                normalized.append(
                    {
                        "category": str(raw.get("category") or category_text),
                        "topic": str(raw.get("topic") or ""),
                        "content": dict(payload) if isinstance(payload, Mapping) else {"preference": str(payload)},
                        "confidence": float(raw.get("confidence") or 1.0),
                        "skill": str(raw.get("skill") or ""),
                    }
                )
            elif raw not in (None, ""):
                normalized.append(
                    {
                        "category": category_text,
                        "topic": "",
                        "content": {"preference": str(raw)},
                        "confidence": 1.0,
                    }
                )
    return normalized


def _memory_markdown(memory: Mapping[str, Any]) -> str:
    memories = memory.get("memories") if isinstance(memory, Mapping) else []
    lines = ["# Agent Memory", ""]
    for item in memories if isinstance(memories, list) else []:
        if not isinstance(item, Mapping):
            continue
        lines.append(f"- **{item.get('category', '')} / {item.get('topic', '')}**: {json.dumps(item.get('content') or {}, ensure_ascii=False)}")
    return "\n".join(lines)


__all__ = [
    "HarnessAgentChatRequest",
    "actor_for_conversation",
    "chat",
    "chat_stream",
]
