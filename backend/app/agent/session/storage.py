from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import AgentTreeEntry


ENTRY_TYPE_MESSAGE = "message"
ENTRY_TYPE_THINKING_LEVEL_CHANGE = "thinking_level_change"
ENTRY_TYPE_MODEL_CHANGE = "model_change"
ENTRY_TYPE_ACTIVE_TOOLS_CHANGE = "active_tools_change"
ENTRY_TYPE_COMPACTION = "compaction"
ENTRY_TYPE_BRANCH_SUMMARY = "branch_summary"
ENTRY_TYPE_CUSTOM = "custom"
ENTRY_TYPE_CUSTOM_MESSAGE = "custom_message"
ENTRY_TYPE_LABEL = "label"
ENTRY_TYPE_SESSION_INFO = "session_info"
ENTRY_TYPE_LEAF = "leaf"


class ContinuationReplayIntegrityError(RuntimeError):
    """Durable continuation replay no longer matches its canonical tree tail."""


ENTRY_TYPES = (
    ENTRY_TYPE_MESSAGE,
    ENTRY_TYPE_THINKING_LEVEL_CHANGE,
    ENTRY_TYPE_MODEL_CHANGE,
    ENTRY_TYPE_ACTIVE_TOOLS_CHANGE,
    ENTRY_TYPE_COMPACTION,
    ENTRY_TYPE_BRANCH_SUMMARY,
    ENTRY_TYPE_CUSTOM,
    ENTRY_TYPE_CUSTOM_MESSAGE,
    ENTRY_TYPE_LABEL,
    ENTRY_TYPE_SESSION_INFO,
    ENTRY_TYPE_LEAF,
)


def _uuid7_hex() -> str:
    """Return an RFC 9562 UUIDv7 hex string without requiring Python 3.12."""

    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return f"{value:032x}"


def generate_entry_id(existing: Optional[set[str]] = None) -> str:
    existing_ids = existing or set()
    for _ in range(100):
        candidate = _uuid7_hex()[:8]
        if candidate not in existing_ids:
            return candidate
    return _uuid7_hex()


async def append_entry(
    db: AsyncSession,
    *,
    session_id: str,
    actor_id: str,
    entry_type: str,
    payload: dict[str, Any],
    parent_id: Optional[str],
    invocation_key: Optional[str] = None,
    invocation_sequence: Optional[int] = None,
) -> AgentTreeEntry:
    if entry_type not in ENTRY_TYPES:
        raise ValueError(f"Unknown session tree entry_type: {entry_type}")

    if invocation_key:
        if invocation_sequence is None:
            raise ContinuationReplayIntegrityError(
                "Continuation invocation sequence is required; manual review required"
            )
        existing = await db.scalar(
            select(AgentTreeEntry).where(
                AgentTreeEntry.invocation_key == invocation_key,
                AgentTreeEntry.invocation_sequence == invocation_sequence,
            )
        )
        if existing is not None:
            _assert_invocation_replay_matches(
                existing,
                session_id=session_id,
                actor_id=actor_id,
                entry_type=entry_type,
                payload=payload,
                parent_id=parent_id,
            )
            return existing

    if invocation_key:
        entry_id = hashlib.sha256(
            f"{invocation_key}:{invocation_sequence}".encode("utf-8")
        ).hexdigest()[:32]
        values = {
            "entry_id": entry_id,
            "session_id": session_id,
            "actor_id": actor_id,
            "parent_id": parent_id,
            "entry_type": entry_type,
            "payload": payload,
            "invocation_key": invocation_key,
            "invocation_sequence": invocation_sequence,
        }
        dialect_name = str(db.get_bind().dialect.name)
        if dialect_name == "sqlite":
            statement = sqlite_insert(AgentTreeEntry).values(**values).on_conflict_do_nothing()
        elif dialect_name == "postgresql":
            statement = postgresql_insert(AgentTreeEntry).values(**values).on_conflict_do_nothing()
        else:
            statement = None
        if statement is not None:
            await db.execute(statement)
            existing = await db.scalar(
                select(AgentTreeEntry).where(
                    AgentTreeEntry.invocation_key == invocation_key,
                    AgentTreeEntry.invocation_sequence == invocation_sequence,
                )
            )
            if existing is None:
                raise ContinuationReplayIntegrityError(
                    "Continuation invocation sequence insert conflicted without a reusable entry; manual review required"
                )
            _assert_invocation_replay_matches(
                existing,
                session_id=session_id,
                actor_id=actor_id,
                entry_type=entry_type,
                payload=payload,
                parent_id=parent_id,
            )
            return existing

    entry_id = ""
    for _ in range(3):
        candidate = generate_entry_id()
        exists = await db.scalar(select(AgentTreeEntry.entry_id).where(AgentTreeEntry.entry_id == candidate))
        if not exists:
            entry_id = candidate
            break
    if not entry_id:
        entry_id = _uuid7_hex()
    entry = AgentTreeEntry(
        entry_id=entry_id,
        session_id=session_id,
        actor_id=actor_id,
        parent_id=parent_id,
        entry_type=entry_type,
        payload=payload,
        invocation_key=invocation_key,
        invocation_sequence=invocation_sequence,
    )
    db.add(entry)
    await db.flush()
    return entry


def _assert_invocation_replay_matches(
    existing: AgentTreeEntry,
    *,
    session_id: str,
    actor_id: str,
    entry_type: str,
    payload: dict[str, Any],
    parent_id: Optional[str],
) -> None:
    ignore_message_timestamp = entry_type in {
        ENTRY_TYPE_MESSAGE,
        ENTRY_TYPE_CUSTOM_MESSAGE,
    }
    checks = (
        ("session_id", str(existing.session_id), str(session_id)),
        ("actor_id", str(existing.actor_id), str(actor_id)),
        ("entry_type", str(existing.entry_type), str(entry_type)),
        ("parent_id", existing.parent_id, parent_id),
        (
            "payload",
            _canonical_payload(
                existing.payload or {},
                ignore_top_level_timestamp=ignore_message_timestamp,
            ),
            _canonical_payload(
                payload,
                ignore_top_level_timestamp=ignore_message_timestamp,
            ),
        ),
    )
    for field, stored, replayed in checks:
        if stored != replayed:
            raise ContinuationReplayIntegrityError(
                f"Continuation invocation sequence {field} mismatch; manual review required"
            )


def _canonical_payload(
    payload: dict[str, Any],
    *,
    ignore_top_level_timestamp: bool = False,
) -> str:
    normalized = dict(payload)
    if ignore_top_level_timestamp:
        normalized.pop("timestamp", None)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


async def load_entries(
    db: AsyncSession,
    *,
    session_id: str,
    actor_id: str,
) -> list[AgentTreeEntry]:
    result = await db.execute(
        select(AgentTreeEntry)
        .where(
            AgentTreeEntry.session_id == session_id,
            AgentTreeEntry.actor_id == actor_id,
        )
        .order_by(AgentTreeEntry.ord.asc())
    )
    return list(result.scalars().all())
