# =============================================
# Optimize 路由 — 对话式简历优化会话管理 API
# =============================================
# 旧 /generate 批量生成端点已拆除（前端走 /api/optimize/agent/*）。
# 这里仅保留 /agent/* 会话生命周期端点（start/chat/stream/sessions）。
# =============================================

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.models.models import Job
from app.operator.session_authority import (
    AUTHORITY_STATE_KEY,
    BROWSER_PRINCIPAL_COOKIE_PATH,
    SessionAuthorityError,
    bind_session_authority,
    issue_principal_token,
    verify_principal_token,
)

router = APIRouter()
_logger = logging.getLogger(__name__)

_BROWSER_PRINCIPAL_COOKIE = "offeru_browser_principal"
_BROWSER_PRINCIPAL_MAX_AGE = 60 * 60 * 24 * 365


# =============================================
# 对话式优化 API
# =============================================

from app.agent import orchestrator
from app.services.optimize_sessions import (
    start_session,
    list_sessions_from_db as _list_agent_sessions,
    get_session_detail as _get_session_detail,
    delete_session as _delete_session,
)
from app.models import models
from app.operator.guards import ActorContext
from app.operator.public_redaction import redact_public_payload
from app.routes._agent_sse import agent_sse_response




class OptimizeAgentStartRequest(BaseModel):
    job_ids: list[int] = Field(..., min_length=1, max_length=200)
    mode: Literal["per_job", "combined"] = "per_job"
    profile_id: int | None = None


class OptimizeAgentChatRequest(BaseModel):
    session_id: str
    message: str
    action: Literal["reply", "confirm", "reject", "adjust"] = "reply"
    feedback: str = ""


@router.post("/agent/start")
async def optimize_agent_start(
    body: OptimizeAgentStartRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    result = await start_session(
        job_ids=body.job_ids,
        mode=body.mode,
        profile_id=body.profile_id,
        db=db,
    )
    session_id = result.get("session_id")
    if session_id:
        actor, issued_token = await _optimize_actor_for_request(
            db, request, session_id, allow_create=True
        )
        await db.commit()
        if issued_token:
            _set_browser_principal_cookie(response, issued_token)
    return result


@router.post("/agent/chat")
async def optimize_agent_chat(
    body: OptimizeAgentChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    actor, _ = await _optimize_actor_for_request(
        db, request, body.session_id, allow_create=False
    )
    result = await orchestrator.run_agent_turn(
        db,
        actor,
        _optimize_user_message(body),
        body.session_id,
        preactivated_skill="resume-optimizer",
        skill_gates=_optimize_skill_gates(body),
    )
    return redact_public_payload(orchestrator.public_agent_response(result))


@router.post("/agent/chat/stream")
async def optimize_agent_chat_stream(
    body: OptimizeAgentChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    browser_token = request.cookies.get(_BROWSER_PRINCIPAL_COOKIE)
    if browser_token:
        try:
            auth_subject = verify_principal_token(browser_token)
        except SessionAuthorityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=401, detail="authenticated browser principal is required")

    user_message = _optimize_user_message(body)
    skill_gates = _optimize_skill_gates(body)

    async def run(event_sink):
        async with async_session() as stream_db:
            try:
                bound_actor = await bind_session_authority(
                    stream_db,
                    session_id=body.session_id,
                    auth_subject=auth_subject,
                    allow_create=False,
                )
            except SessionAuthorityError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            await stream_db.commit()
            return await orchestrator.run_agent_turn(
                stream_db,
                bound_actor,
                user_message,
                body.session_id,
                event_sink=event_sink,
                preactivated_skill="resume-optimizer",
                skill_gates=skill_gates,
            )

    return agent_sse_response(run)


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


async def _optimize_actor_for_request(
    db: AsyncSession,
    request: Request,
    session_id: str,
    *,
    allow_create: bool,
) -> tuple[ActorContext, str | None]:
    """Create authenticated actor for optimize routes"""
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


def _optimize_actor(session_id: str) -> ActorContext:
    """Legacy: creates actor without auth_subject - DO NOT USE for new routes"""
    return ActorContext(
        actor_id=models.LOCAL_DEFAULT_ACTOR_ID,
        session_id=str(session_id),
        adapter="web",
    )


def _authenticated_subject(request: Request) -> str:
    """Resolve the browser principal from the session cookie (401 when absent/invalid)."""
    try:
        return verify_principal_token(request.cookies.get(_BROWSER_PRINCIPAL_COOKIE))
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _session_authority_subject(row) -> str:
    """Read the auth_subject recorded by bind_session_authority on an AgentSession row."""
    state = dict(getattr(row, "state_json", None) or {})
    authority = state.get(AUTHORITY_STATE_KEY)
    if not isinstance(authority, dict):
        return ""
    return str(authority.get("auth_subject") or "")


async def _bind_optimize_session_owner(db, session_id: str, auth_subject: str) -> ActorContext:
    """Bind the browser principal to the session for list/detail/delete access.

    Unbound legacy sessions cannot be claimed by session_id (no privileged-fact
    bypass here: allow_create=False), and another principal's sessions are rejected.
    """
    try:
        return await bind_session_authority(
            db, session_id=session_id, auth_subject=auth_subject, allow_create=False
        )
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _optimize_skill_gates(body: OptimizeAgentChatRequest) -> dict[str, object]:
    if body.action == "confirm":
        return {
            "strategy_confirmed": True,
            "current_step": "strategy_confirmed",
        }
    if body.action in {"reject", "adjust"}:
        return {
            "strategy_confirmed": False,
            "current_step": "active",
        }
    # Reply/refinement turns must not silently revoke a previously confirmed
    # strategy stored by the Harness Skill Runtime.
    return {}


def _optimize_user_message(body: OptimizeAgentChatRequest) -> str:
    message = str(body.message or "").strip()
    if body.action == "reply" and not body.feedback:
        return message
    parts = [message]
    if body.action != "reply":
        parts.append(f"Action: {body.action}")
    if body.feedback:
        parts.append(f"Feedback: {body.feedback}")
    return "\n".join(part for part in parts if part)


@router.get("/agent/sessions")
async def optimize_agent_sessions(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    auth_subject = _authenticated_subject(request)
    sessions = await _list_agent_sessions(db)
    # Scope the list to sessions owned by this authenticated browser principal.
    session_ids = [str(s.get("session_id") or "") for s in sessions if s.get("session_id")]
    owned: set[str] = set()
    if session_ids:
        rows = (
            await db.execute(
                select(models.AgentSession).where(models.AgentSession.session_id.in_(session_ids))
            )
        ).scalars().all()
        owned = {
            str(row.session_id)
            for row in rows
            if _session_authority_subject(row) == auth_subject
        }
    sessions = [s for s in sessions if str(s.get("session_id") or "") in owned]
    # Batch query all job IDs at once instead of N+1 per session
    all_job_ids: list[int] = []
    for s in sessions:
        all_job_ids.extend(jid for jid in s.get("job_ids", []) if isinstance(jid, int))

    job_map: dict[int, Job] = {}
    if all_job_ids:
        unique_ids = list(set(all_job_ids))
        jobs_result = await db.execute(select(Job).where(Job.id.in_(unique_ids)))
        job_map = {j.id: j for j in jobs_result.scalars().all()}

    for s in sessions:
        job_ids = s.get("job_ids", [])
        if job_ids and not s.get("title"):
            try:
                jobs = [job_map[jid] for jid in job_ids[:3] if jid in job_map]
                if jobs:
                    titles = [
                        f"{j.company} - {j.title}" if j.company else j.title
                        for j in jobs
                    ]
                    s["title"] = "、".join(titles[:2])
                    if len(titles) > 2:
                        s["title"] += f" 等{len(titles)}个岗位"
                else:
                    s["title"] = f"优化会话 {s.get('session_id', '')[:8]}"
            except Exception:
                s["title"] = f"优化会话 {s.get('session_id', '')[:8]}"
        elif not s.get("title"):
            s["title"] = f"优化会话 {s.get('session_id', '')[:8]}"
        # Add status description
        phase = s.get("phase", "")
        status_map = {
            "confirming": "等待确认",
            "analyzing": "分析中",
            "framework": "框架设计",
            "rewriting": "改写中",
            "completed": "已完成",
        }
        s["status"] = status_map.get(phase, phase)
    return {"sessions": sessions}


@router.get("/agent/sessions/{session_id}")
async def optimize_agent_session_detail(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    auth_subject = _authenticated_subject(request)
    actor = await _bind_optimize_session_owner(db, session_id, auth_subject)
    detail = await _get_session_detail(session_id, db, actor)
    if not detail:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail


@router.delete("/agent/sessions/{session_id}")
async def optimize_agent_session_delete(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    auth_subject = _authenticated_subject(request)
    await _bind_optimize_session_owner(db, session_id, auth_subject)
    deleted = await _delete_session(session_id, db)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"deleted": True, "session_id": session_id}
