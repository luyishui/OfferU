from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.models import models
from app.operator.guards import ActorContext


AUTHORITY_STATE_KEY = "session_authority_v1"
PRINCIPAL_TOKEN_VERSION = "v1"
BROWSER_PRINCIPAL_COOKIE_PATH = "/api"


class SessionAuthorityError(PermissionError):
    pass


def _secret() -> bytes:
    return str(get_settings().secret_key or "change-me-in-production").encode("utf-8")


def issue_principal_token() -> tuple[str, str]:
    subject = secrets.token_urlsafe(32)
    payload = f"{PRINCIPAL_TOKEN_VERSION}.{subject}"
    signature = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}", subject


def verify_principal_token(token: str | None) -> str:
    raw = str(token or "").strip()
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != PRINCIPAL_TOKEN_VERSION or not parts[1] or not parts[2]:
        raise SessionAuthorityError("authenticated browser principal is missing or malformed")
    payload = f"{parts[0]}.{parts[1]}"
    expected = hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        raise SessionAuthorityError("authenticated browser principal signature is invalid")
    return parts[1]


def _authority_state(row: Any) -> dict[str, Any]:
    state = dict(getattr(row, "state_json", None) or {})
    authority = state.get(AUTHORITY_STATE_KEY)
    return dict(authority) if isinstance(authority, dict) else {}


async def bind_session_authority(
    db: Any,
    *,
    session_id: str,
    auth_subject: str,
    allow_create: bool,
) -> ActorContext:
    session_value = str(session_id or "").strip()
    subject_value = str(auth_subject or "").strip()
    if not session_value:
        raise SessionAuthorityError("session_id is required")
    if not subject_value:
        raise SessionAuthorityError("authenticated browser principal is required")

    row = await db.get(models.AgentSession, session_value, populate_existing=True)
    if row is None:
        if not allow_create:
            raise SessionAuthorityError("session is not established for this authenticated browser principal")
        row = models.AgentSession(
            session_id=session_value,
            actor_id=models.LOCAL_DEFAULT_ACTOR_ID,
            adapter="web",
            pending_proposal_ids=[],
            state_json={AUTHORITY_STATE_KEY: {"auth_subject": subject_value}},
        )
        db.add(row)
        await db.flush()
    else:
        authority = _authority_state(row)
        stored_subject = str(authority.get("auth_subject") or "")
        if stored_subject:
            if not hmac.compare_digest(stored_subject, subject_value):
                raise SessionAuthorityError("session belongs to a different authenticated browser principal")
        else:
            if not allow_create:
                raise SessionAuthorityError("session authority has not been established")
            if await _has_privileged_facts_async(db, session_value):
                raise SessionAuthorityError("legacy session has privileged facts and cannot be claimed by session_id")
            state = dict(row.state_json or {})
            state[AUTHORITY_STATE_KEY] = {"auth_subject": subject_value}
            row.state_json = state
            flag_modified(row, "state_json")
            await db.flush()

    return ActorContext(
        actor_id=str(row.actor_id or models.LOCAL_DEFAULT_ACTOR_ID),
        session_id=session_value,
        adapter=str(row.adapter or "web"),
        auth_subject=subject_value,
    )


async def _has_privileged_facts_async(db: Any, session_id: str) -> bool:
    checks = (
        select(models.ProposalCache.proposal_id).where(models.ProposalCache.session_id == session_id).limit(1),
        select(models.ManualReviewCase.case_id).where(models.ManualReviewCase.session_id == session_id).limit(1),
        select(models.ProposalPlan.plan_id).where(models.ProposalPlan.session_id == session_id).limit(1),
    )
    for query in checks:
        if await db.scalar(query) is not None:
            return True
    return False


def principal_subject_from_token(token: str | None) -> str:
    return verify_principal_token(token)
