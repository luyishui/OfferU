from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import models


REDACTED_VALUE = "[redacted]"
SENSITIVE_KEY_TOKENS = {
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "contact_json",
    "content_json",
    "credential",
    "email",
    "email_body",
    "jwt",
    "password",
    "phone",
    "raw_text",
    "refresh_token",
    "secret",
    "source_profile_snapshot",
    "token",
    "wechat",
}
LONG_TEXT_KEY_TOKENS = {
    "body",
    "content",
    "description",
    "message",
    "notes",
    "raw",
    "summary",
    "text",
}
MAX_PUBLIC_SCALAR_LENGTH = 240


async def log_agent_audit(
    session: AsyncSession,
    *,
    actor: Any | None = None,
    proposal: Any | None = None,
    proposal_id: str = "",
    tool_name: str = "",
    args_snapshot: Mapping[str, Any] | None = None,
    args_redacted: Mapping[str, Any] | None = None,
    risk_level: int | None = None,
    confirmation_status: str = "",
    result_status: str = "",
    result_summary: str = "",
    changed_records: Sequence[Mapping[str, Any]] | None = None,
    error: str = "",
    request_id: str = "",
    tool_call_id: str = "",
    user_message: str = "",
    confirmation_event_id: str = "",
    idempotency_key: str = "",
    before_version_or_hash: str = "",
    after_version_or_hash: str = "",
    result_receipt_id: str = "",
    result_digest: str = "",
) -> models.AgentAuditLog:
    """Append an operator audit row. Caller owns transaction/commit timing."""

    actor_id = _first_nonempty(_getattr(actor, "actor_id"), _getattr(proposal, "actor_id"))
    session_id = _first_nonempty(_getattr(actor, "session_id"), _getattr(proposal, "session_id"))
    adapter = _first_nonempty(_getattr(actor, "adapter"))
    audit = models.AgentAuditLog(
        audit_id=f"audit_{uuid.uuid4().hex}",
        actor_id=actor_id,
        session_id=session_id,
        adapter=adapter,
        request_id=request_id,
        tool_call_id=tool_call_id,
        user_message=user_message or str(_getattr(proposal, "user_message_snapshot") or ""),
        tool_name=tool_name or str(_getattr(proposal, "tool_name") or ""),
        args_snapshot=_json_safe(args_snapshot or {}),
        args_redacted=_json_safe(args_redacted if args_redacted is not None else redact_audit_args(args_snapshot or {})),
        risk_level=int(risk_level if risk_level is not None else (_getattr(proposal, "risk_level") or 0)),
        proposal_id=proposal_id or str(_getattr(proposal, "proposal_id") or ""),
        confirmation_event_id=confirmation_event_id,
        idempotency_key=idempotency_key or str(_getattr(proposal, "idempotency_key") or ""),
        confirmation_status=confirmation_status,
        result_status=result_status,
        result_summary=result_summary,
        changed_records=[_json_safe(item) for item in (changed_records if changed_records is not None else (_getattr(proposal, "affected_records") or []))],
        result_receipt_id=str(result_receipt_id or ""),
        result_digest=str(result_digest or ""),
        before_version_or_hash=before_version_or_hash or str(_getattr(proposal, "expected_version_or_hash") or ""),
        after_version_or_hash=after_version_or_hash,
        error=error,
    )
    session.add(audit)
    return audit


def redact_audit_args(value: Any) -> Any:
    """Return a public-safe copy of operator args for audit display/search."""

    return _redact_value(value, parent_key="")


def _getattr(value: Any, field: str) -> Any:
    return getattr(value, field, None) if value is not None else None


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _redact_value(value: Any, *, parent_key: str) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            key_str = str(key)
            if _is_sensitive_key(key_str):
                redacted[key_str] = REDACTED_VALUE
            else:
                redacted[key_str] = _redact_value(child, parent_key=key_str)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [_redact_value(child, parent_key=parent_key) for child in value]
    safe_value = _json_safe(value)
    if _is_sensitive_scalar(safe_value) or _is_long_public_text(parent_key, safe_value):
        return REDACTED_VALUE
    return safe_value


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(token in normalized for token in SENSITIVE_KEY_TOKENS)


def _is_sensitive_scalar(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return (
        ("@" in value and "." in value.rsplit("@", 1)[-1])
        or any(marker in lowered for marker in ("bearer ", "api_key=", "apikey=", "token=", "secret=", "password="))
        or _looks_like_phone(value)
    )


def _is_long_public_text(parent_key: str, value: Any) -> bool:
    if not isinstance(value, str) or len(value) <= MAX_PUBLIC_SCALAR_LENGTH:
        return False
    normalized = _normalize_key(parent_key)
    return any(token in normalized for token in LONG_TEXT_KEY_TOKENS)


def _looks_like_phone(value: str) -> bool:
    digits = [char for char in value if char.isdigit()]
    if len(digits) < 9:
        return False
    allowed = set("0123456789+-.() xX")
    return all(char in allowed for char in value)


def _normalize_key(key: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in key)
