from __future__ import annotations

import re
from typing import Any


_SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|passwd|authorization|credential)", re.I)
_HIDDEN_PUBLIC_KEYS = {"lockedpayload", "actorid", "sessionid"}
_HIDDEN_REASONING_KEYS = {"reasoningcontent", "chainofthought"}
_MACHINE_VALUE_KEYS = {
    "id",
    "proposalid",
    "recordid",
    "checkpointid",
    "preconfirmationcheckpointid",
    "idempotencykey",
    "expectedversionorhash",
    "confirmationchallenge",
    "nextchallenge",
    "operatorsessionid",
    "conversationid",
    "createdat",
    "updatedat",
    "expiresat",
    "archivedat",
    "deletedat",
    "restoredat",
}
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_INLINE_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key[=:]\s*\S+)", re.I)
_CONFIRMATION_CHALLENGE_RE = re.compile(r"\bconfirm-[0-9a-fA-F]{12}\b")


def redact_public_payload(value: Any) -> Any:
    return _redact_public_payload(value, parent_key="")


def _redact_public_payload(value: Any, *, parent_key: str) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        proposal_session_id = value.get("session_id") if value.get("proposal_id") else None
        for key, item in value.items():
            key_text = str(key)
            if is_hidden_public_key(key_text):
                continue
            if _SENSITIVE_KEY_RE.search(key_text):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_public_payload(item, parent_key=key_text)
        if proposal_session_id is not None and not redacted.get("operator_session_id"):
            redacted["operator_session_id"] = str(proposal_session_id)
        return redacted
    if isinstance(value, list):
        return [_redact_public_payload(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        if _normalize_public_key(parent_key) in _MACHINE_VALUE_KEYS:
            return value
        return _redact_public_text(value)
    return value


def is_hidden_public_key(key: str) -> bool:
    normalized = _normalize_public_key(key)
    return normalized in _HIDDEN_PUBLIC_KEYS or normalized in _HIDDEN_REASONING_KEYS


def _normalize_public_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _redact_public_text(value: str) -> str:
    challenge_tokens: list[str] = []

    def hold_challenge(match: re.Match[str]) -> str:
        challenge_tokens.append(match.group(0))
        return f"__OFFERU_CONFIRMATION_CHALLENGE_{len(challenge_tokens) - 1}__"

    text = _CONFIRMATION_CHALLENGE_RE.sub(hold_challenge, value)
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _INLINE_SECRET_RE.sub("[REDACTED]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    for index, token in enumerate(challenge_tokens):
        text = text.replace(f"__OFFERU_CONFIRMATION_CHALLENGE_{index}__", token)
    return text
