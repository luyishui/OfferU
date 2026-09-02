from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.types import (
    AgentMessage,
    AssistantMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
)
from app.models.models import AgentTreeEntry

from . import storage


_AGENT_MESSAGE_ADAPTER = TypeAdapter(AgentMessage)


@dataclass(frozen=True)
class AgentTreeEntrySnapshot:
    ord: Optional[int]
    entry_id: str
    session_id: str
    actor_id: str
    parent_id: Optional[str]
    entry_type: str
    payload: dict[str, Any]
    invocation_key: Optional[str] = None
    invocation_sequence: Optional[int] = None
    created_at: Any = None


@dataclass
class SessionContext:
    messages: list[AgentMessage]
    thinking_level: Optional[str] = None
    model: Optional[str] = None
    active_tool_names: Optional[list[str]] = None
    message_entry_ids: list[Optional[str]] = None


class SessionTree:
    """Append-only tree session with PI-compatible leaf replay semantics.

    ``model_change`` and ``thinking_level_change`` are recorded state changes:
    ``build_context`` extracts the last effective values, while the caller
    decides whether and how to apply them to a model invocation.
    """

    def __init__(
        self,
        *,
        session_id: str,
        actor_id: str,
        entries: list[AgentTreeEntry | AgentTreeEntrySnapshot],
        invocation_key: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.actor_id = actor_id
        self.invocation_key = invocation_key
        self._next_invocation_sequence = 0
        self._required_replay_sequence_count = 0
        self._replay_complete = True
        self.entries: list[AgentTreeEntrySnapshot] = []
        self.by_id: dict[str, AgentTreeEntrySnapshot] = {}
        self.leaf_id: Optional[str] = None
        for entry in entries:
            self._remember_entry(entry)
        if invocation_key:
            replay_entries = [
                entry
                for entry in self.entries
                if entry.invocation_key == invocation_key
                and entry.invocation_sequence is not None
            ]
            if replay_entries:
                replay_sequences = sorted(
                    int(entry.invocation_sequence or 0) for entry in replay_entries
                )
                expected_sequences = list(range(replay_sequences[-1] + 1))
                if replay_sequences != expected_sequences:
                    raise storage.ContinuationReplayIntegrityError(
                        "Continuation invocation durable tail has a sequence gap; manual review required"
                    )
                self._required_replay_sequence_count = len(replay_sequences)
                self._replay_complete = False
                first_replay_entry = min(
                    replay_entries,
                    key=lambda entry: int(entry.invocation_sequence or 0),
                )
                self.leaf_id = first_replay_entry.parent_id

    def assert_invocation_replay_complete(self) -> None:
        if not self._replay_complete:
            raise storage.ContinuationReplayIntegrityError(
                "Continuation invocation durable tail was not fully replayed; manual review required"
            )

    @classmethod
    async def load(
        cls,
        db: AsyncSession,
        *,
        session_id: str,
        actor_id: str,
        invocation_key: Optional[str] = None,
    ) -> "SessionTree":
        entries = await storage.load_entries(db, session_id=session_id, actor_id=actor_id)
        return cls(
            session_id=session_id,
            actor_id=actor_id,
            entries=entries,
            invocation_key=invocation_key,
        )

    async def append_message(self, db: AsyncSession, message: AgentMessage) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_MESSAGE,
            payload=message.model_dump(mode="json"),
        )

    async def append_model_change(self, db: AsyncSession, model: str) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_MODEL_CHANGE,
            payload={"model": model},
        )

    async def append_thinking_level_change(self, db: AsyncSession, level: str) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_THINKING_LEVEL_CHANGE,
            payload={"level": level},
        )

    async def append_active_tools_change(self, db: AsyncSession, tools: list[str]) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_ACTIVE_TOOLS_CHANGE,
            payload={"tools": list(tools)},
        )

    async def append_compaction(
        self,
        db: AsyncSession,
        *,
        summary: str,
        first_kept_entry_id: str,
        tokens_before: int,
        details: Optional[dict[str, Any]] = None,
    ) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_COMPACTION,
            payload={
                "summary": summary,
                "first_kept_entry_id": first_kept_entry_id,
                "tokens_before": tokens_before,
                "details": details,
            },
        )

    async def append_branch_summary(
        self,
        db: AsyncSession,
        *,
        from_id: str,
        summary: str,
    ) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_BRANCH_SUMMARY,
            payload={"from_id": from_id, "summary": summary},
        )

    async def append_custom(
        self,
        db: AsyncSession,
        custom_type: str,
        data: Optional[dict[str, Any]],
    ) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_CUSTOM,
            payload={"custom_type": custom_type, "data": data},
        )

    async def append_custom_message(self, db: AsyncSession, message: CustomMessage) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_CUSTOM_MESSAGE,
            payload=message.model_dump(mode="json"),
        )

    async def append_label(self, db: AsyncSession, *, target_id: str, label: str) -> AgentTreeEntrySnapshot:
        if target_id not in self.by_id:
            raise ValueError(f"Session tree entry not found: {target_id}")
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_LABEL,
            payload={"target_id": target_id, "label": label},
        )

    async def append_session_info(self, db: AsyncSession, *, name: str) -> AgentTreeEntrySnapshot:
        return await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_SESSION_INFO,
            payload={"name": name},
        )

    async def move_to(self, db: AsyncSession, entry_id: str) -> None:
        if entry_id not in self.by_id:
            raise ValueError(f"Session tree entry not found: {entry_id}")
        await self._append(
            db,
            entry_type=storage.ENTRY_TYPE_LEAF,
            payload={"target_id": entry_id},
        )

    def path_to_root(self, entry_id: Optional[str] = None) -> list[AgentTreeEntrySnapshot]:
        current_id = self.leaf_id if entry_id is None else entry_id
        if current_id is None:
            return []

        path: list[AgentTreeEntrySnapshot] = []
        visited: set[str] = set()
        while current_id is not None:
            if current_id in visited:
                raise ValueError(f"Cycle detected in session tree at entry: {current_id}")
            visited.add(current_id)

            entry = self.by_id.get(current_id)
            if entry is None:
                raise ValueError(f"Dangling session tree parent_id: {current_id}")
            if entry.entry_type == storage.ENTRY_TYPE_LEAF:
                current_id = str((entry.payload or {}).get("target_id") or "")
                if not current_id:
                    raise ValueError(f"Leaf entry missing target_id: {entry.entry_id}")
                continue

            path.append(entry)
            current_id = entry.parent_id
        path.reverse()
        return path

    def build_context(self) -> SessionContext:
        path = self.path_to_root()
        thinking_level: Optional[str] = None
        model: Optional[str] = None
        active_tool_names: Optional[list[str]] = None
        messages_with_indexes: list[tuple[int, str, AgentMessage]] = []
        path_index_by_entry_id: dict[str, int] = {}
        last_compaction: Optional[tuple[int, AgentTreeEntry]] = None

        for index, entry in enumerate(path):
            path_index_by_entry_id[entry.entry_id] = index
            payload = entry.payload or {}

            if entry.entry_type == storage.ENTRY_TYPE_THINKING_LEVEL_CHANGE:
                thinking_level = _string_or_none(payload.get("level") or payload.get("thinking_level"))
            elif entry.entry_type == storage.ENTRY_TYPE_MODEL_CHANGE:
                model = _string_or_none(payload.get("model"))
            elif entry.entry_type == storage.ENTRY_TYPE_ACTIVE_TOOLS_CHANGE:
                tools = payload.get("tools")
                if tools is None:
                    tools = payload.get("active_tool_names")
                if tools is None:
                    tools = payload.get("activeToolNames")
                active_tool_names = [str(tool) for tool in tools] if tools is not None else None
            elif entry.entry_type == storage.ENTRY_TYPE_COMPACTION:
                last_compaction = (index, entry)

            message = self._message_from_entry(entry)
            if message is not None:
                messages_with_indexes.append((index, entry.entry_id, message))
                if isinstance(message, AssistantMessage):
                    if message.model:
                        model = message.model

        message_entry_ids = [entry_id for _, entry_id, _ in messages_with_indexes]
        messages = [message for _, _, message in messages_with_indexes]
        if last_compaction is not None:
            compaction_index, compaction_entry = last_compaction
            compaction_payload = compaction_entry.payload or {}
            first_kept_entry_id = _string_or_none(compaction_payload.get("first_kept_entry_id"))
            first_kept_index = (
                path_index_by_entry_id.get(first_kept_entry_id)
                if first_kept_entry_id is not None
                else None
            )
            if first_kept_index is None or first_kept_index >= compaction_index:
                kept_before_pairs: list[tuple[str, AgentMessage]] = []
            else:
                kept_before_pairs = [
                    (entry_id, message)
                    for index, entry_id, message in messages_with_indexes
                    if first_kept_index <= index < compaction_index
                ]
            after_pairs = [
                (entry_id, message)
                for index, entry_id, message in messages_with_indexes
                if index > compaction_index
            ]
            messages = [
                CompactionSummaryMessage(
                    summary=str(compaction_payload.get("summary") or ""),
                    tokens_before=int(compaction_payload.get("tokens_before") or 0),
                )
            ] + [message for _, message in kept_before_pairs] + [message for _, message in after_pairs]
            message_entry_ids = [None] + [entry_id for entry_id, _ in kept_before_pairs] + [entry_id for entry_id, _ in after_pairs]

        return SessionContext(
            messages=messages,
            thinking_level=thinking_level,
            model=model,
            active_tool_names=active_tool_names,
            message_entry_ids=message_entry_ids,
        )

    async def _append(
        self,
        db: AsyncSession,
        *,
        entry_type: str,
        payload: dict[str, Any],
    ) -> AgentTreeEntrySnapshot:
        invocation_sequence = None
        if self.invocation_key:
            invocation_sequence = self._next_invocation_sequence
            if (
                invocation_sequence >= self._required_replay_sequence_count
                and not self._replay_complete
            ):
                raise storage.ContinuationReplayIntegrityError(
                    "Continuation invocation durable tail was not fully replayed before a new append; manual review required"
                )

        previous_entries = list(self.entries)
        previous_by_id = dict(self.by_id)
        previous_leaf_id = self.leaf_id
        previous_sequence = self._next_invocation_sequence
        previous_replay_complete = self._replay_complete
        try:
            row = await storage.append_entry(
                db,
                session_id=self.session_id,
                actor_id=self.actor_id,
                entry_type=entry_type,
                payload=payload,
                parent_id=self.leaf_id,
                invocation_key=self.invocation_key,
                invocation_sequence=invocation_sequence,
            )
            entry = _snapshot_entry(row)
            if entry.entry_id in self.by_id:
                self.leaf_id = self._leaf_id_after_entry(entry)
            else:
                self._remember_entry(entry)
            if self.invocation_key:
                self._next_invocation_sequence = previous_sequence + 1
                if (
                    not self._replay_complete
                    and self._next_invocation_sequence
                    == self._required_replay_sequence_count
                ):
                    self._replay_complete = True
            return entry
        except Exception:
            self.entries = previous_entries
            self.by_id = previous_by_id
            self.leaf_id = previous_leaf_id
            self._next_invocation_sequence = previous_sequence
            self._replay_complete = previous_replay_complete
            raise

    def _remember_entry(self, entry: AgentTreeEntry | AgentTreeEntrySnapshot) -> None:
        snapshot = _snapshot_entry(entry)
        self.entries.append(snapshot)
        self.by_id[snapshot.entry_id] = snapshot
        self.leaf_id = self._leaf_id_after_entry(snapshot)

    def _leaf_id_after_entry(self, entry: AgentTreeEntrySnapshot) -> Optional[str]:
        """Replay PI jsonl-storage leaf semantics.

        PI's ``leafIdAfterEntry`` in
        ``references/pi/packages/agent/src/harness/session/jsonl-storage.ts:109``
        returns ``targetId`` only for ``leaf`` pointer entries; every regular
        entry, including ``label`` and ``session_info``, advances the active
        leaf to its own id.
        """

        if entry.entry_type == storage.ENTRY_TYPE_LEAF:
            target_id = _string_or_none((entry.payload or {}).get("target_id"))
            return target_id
        return entry.entry_id

    def _message_from_entry(self, entry: AgentTreeEntrySnapshot) -> Optional[AgentMessage]:
        payload = entry.payload or {}
        if entry.entry_type == storage.ENTRY_TYPE_MESSAGE:
            return _AGENT_MESSAGE_ADAPTER.validate_python(payload)
        if entry.entry_type == storage.ENTRY_TYPE_CUSTOM_MESSAGE:
            return CustomMessage.model_validate(payload)
        if entry.entry_type == storage.ENTRY_TYPE_BRANCH_SUMMARY:
            return BranchSummaryMessage(
                summary=str(payload.get("summary") or ""),
                from_id=str(payload.get("from_id") or ""),
            )
        return None


def _snapshot_entry(entry: AgentTreeEntry | AgentTreeEntrySnapshot) -> AgentTreeEntrySnapshot:
    if isinstance(entry, AgentTreeEntrySnapshot):
        return entry
    values = entry.__dict__
    return AgentTreeEntrySnapshot(
        ord=values.get("ord"),
        entry_id=str(_loaded_value(values, "entry_id")),
        session_id=str(_loaded_value(values, "session_id")),
        actor_id=str(_loaded_value(values, "actor_id")),
        parent_id=_string_or_none(values.get("parent_id")),
        entry_type=str(_loaded_value(values, "entry_type")),
        payload=copy.deepcopy(_loaded_value(values, "payload") or {}),
        invocation_key=_string_or_none(values.get("invocation_key")),
        invocation_sequence=values.get("invocation_sequence"),
        created_at=values.get("created_at"),
    )


def _loaded_value(values: dict[str, Any], key: str) -> Any:
    if key not in values:
        raise ValueError(f"Cannot materialize unloaded AgentTreeEntry.{key}")
    return values[key]


def _string_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)
