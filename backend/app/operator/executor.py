from __future__ import annotations

import inspect
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.agent.types import ImageContent, TextContent
from app.models import models
from app.operator import audit, tools
from app.operator.capability_loading import CAPABILITY_LOADING_STATE_KEY, require_loaded_capability
from app.operator.errors import OperatorError
from app.operator.planning import (
    PLAN_DRAFT_STATE_KEY,
    PLAN_STAGING_STATE_KEY,
    PLAN_TURN_KEY,
    stage_plan_intent,
    staged_intent_output_references,
)
from app.operator.registry import ACTION_REGISTRY, UNIVERSAL_TOOL_NAMES
from app.operator.readiness import resume_scope_from_session_state
from app.operator.security import (
    SecurityDecision,
    check_skill_tool_gate,
    normalize_tool_args,
    validate_trusted_args,
    verify_resume_generate_readiness,
)

_logger = logging.getLogger(__name__)
_READ_TOOL_NAMES = {"query_records", "get_record", "describe_capability"}
_PLAN_WRITE_TOOL_NAMES = {"create_record", "patch_record", "delete_or_archive_record"}


def _should_stage_plan_intent(tool_name: str, args: Mapping[str, Any]) -> bool:
    if tool_name in _PLAN_WRITE_TOOL_NAMES:
        return True
    if tool_name != "invoke_action":
        return False
    spec = ACTION_REGISTRY.get(str(args.get("action") or ""))
    if spec is None:
        return True
    return bool(spec.confirmation_required or spec.side_effects)


@dataclass
class ExecutedToolResult:
    content: list[TextContent | ImageContent]
    details: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    terminate: bool = False


TOOL_EXECUTORS: dict[str, Callable[..., Any]] = {
    name: getattr(tools, name)
    for name in (
        "query_records",
        "get_record",
        "create_record",
        "patch_record",
        "delete_or_archive_record",
        "invoke_action",
        "manage_session",
        "describe_capability",
    )
    if hasattr(tools, name)
}


def canonical_tool_signature(tool_name: str, args: Mapping[str, Any] | None) -> str:
    encoded = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "%s:%s" % (tool_name, encoded)


async def execute_tool(
    tool_name: str,
    args: Mapping[str, Any] | None,
    *,
    db: Any,
    actor: Any,
    session_state: dict[str, Any],
    on_update: Any = None,
    cancel: Any = None,
    effect_context: Mapping[str, Any] | None = None,
) -> ExecutedToolResult:
    normalized_args = normalize_tool_args(tool_name, args or {})
    canonical_name = (
        "invoke_action"
        if tool_name not in UNIVERSAL_TOOL_NAMES and "action" in normalized_args and "input" in normalized_args
        else tool_name
    )
    try:
        effect = _normalize_effect_context(effect_context) if effect_context is not None else None
    except ValueError as exc:
        return _error_result(
            str(exc),
            details={"code": "validation_error", "stage": "effect_context"},
        )
    if effect is not None:
        conflict = _effect_scope_conflict(effect, actor)
        if conflict is not None:
            return conflict
        replay = await _effect_preflight(db, effect, canonical_name, normalized_args, session_state)
        if replay is not None:
            return replay

    for decision in (
        validate_trusted_args(canonical_name, normalized_args, actor),
        check_skill_tool_gate(canonical_name, normalized_args, session_state.get("active_skill")),
        verify_resume_generate_readiness(normalized_args, session_state),
    ):
        if decision.blocked:
            details: dict[str, Any] = {"security": {"blocked": True, "reason": decision.reason}}
            if decision.recovery is not None:
                # Machine-readable recovery accompanies every security block:
                # readiness rejections carry the missing-requirements payload so
                # the model can satisfy the gates instead of retrying blindly.
                details["readiness_recovery"] = decision.recovery
            result = _error_result(decision.reason, details=details)
            await _audit_quietly(db, actor, canonical_name, normalized_args, result, confirmation_status="security_block")
            return result

    backend_confirmed_scope: Mapping[str, Any] | None = None
    if canonical_name == "invoke_action" and str(normalized_args.get("action") or "") == "generate_resume":
        scope = resume_scope_from_session_state(session_state)
        if isinstance(scope, Mapping) and scope:
            # Freeze the evidence-derived scope into the staged intent args so
            # the sealed execution contract binds the same generation boundary;
            # the tool re-verifies it against the backend-supplied keyword.
            input_payload = normalized_args.get("input")
            if isinstance(input_payload, Mapping):
                normalized_args = {
                    **normalized_args,
                    "input": {**dict(input_payload), "confirmed_scope": scope},
                }
            backend_confirmed_scope = scope
    executor = TOOL_EXECUTORS.get(canonical_name) or getattr(tools, canonical_name, None)
    if executor is None:
        return _error_result("Tool %s not found" % canonical_name, details={"tool_name": canonical_name})

    try:
        if bool(session_state.get(CAPABILITY_LOADING_STATE_KEY)):
            await require_loaded_capability(
                db,
                actor,
                tool_name=canonical_name,
                args=normalized_args,
            )
        await _persist_resume_readiness_if_available(db, actor, canonical_name, normalized_args, session_state)
        staged_intent = None
        if bool(session_state.get(PLAN_STAGING_STATE_KEY)) and _should_stage_plan_intent(canonical_name, normalized_args):
            try:
                # Call-time validation of the write payload before it is
                # staged. Without this gate every field-level check that the
                # real tool would run (patch_mode whitelist, merge-mode object
                # semantics, field whitelists, value types, action schemas) is
                # skipped, so invalid payloads only fail at Plan
                # materialization and abort the whole turn as
                # agent_turn_failed. The gate returns the identical
                # validation_error shape the real path produces, stages
                # nothing, and leaves no durable intent/draft/audit rows so
                # the model can recover in-turn.
                await tools.validate_staged_write_payload(
                    db,
                    actor,
                    canonical_name,
                    normalized_args,
                    backend_confirmed_scope=backend_confirmed_scope,
                )
            except OperatorError as exc:
                if effect is not None:
                    await _rollback(db)
                return _result_from_raw(exc.to_response(), canonical_name, normalized_args)
            turn_key = str(session_state.get(PLAN_TURN_KEY) or "")
            sequence = int(session_state.get("_plan_effect_sequence", 0) or 0) + 1
            session_state["_plan_effect_sequence"] = sequence
            if effect is not None:
                effect_key = f"{effect['invocation_key']}:{effect['tool_call_id']}"
            else:
                signature_hash = hashlib.sha256(
                    canonical_tool_signature(canonical_name, normalized_args).encode("utf-8")
                ).hexdigest()
                effect_key = f"{turn_key}:{sequence}:{signature_hash}"
            staged_intent = await stage_plan_intent(
                db, actor, turn_key=turn_key, canonical_effect_key=effect_key,
                tool_name=canonical_name, args=normalized_args,
                base_version=str(normalized_args.get("expected_version_or_hash") or ""),
            )
            session_state[PLAN_DRAFT_STATE_KEY] = staged_intent.draft_id
        call_kwargs = {
            "session": db,
            "db": db,
            "actor": actor,
            "user_message": session_state.get("user_message", ""),
            **normalized_args,
        }
        if canonical_name == "invoke_action":
            call_kwargs["confirmed_scope"] = backend_confirmed_scope
        if effect is not None and canonical_name in {
            "create_record",
            "patch_record",
            "delete_or_archive_record",
            "invoke_action",
            "manage_session",
        }:
            call_kwargs["_defer_commit"] = True
        if staged_intent is not None:
            output_references = staged_intent_output_references(staged_intent)
            raw_result = {
                "ok": True,
                "status": "intent_staged",
                "draft_id": staged_intent.draft_id,
                "intent_id": staged_intent.intent_id,
                "output_references": output_references,
                "reference_guidance": (
                    "For a later staged operation in this same PlanDraft, copy the exact "
                    "output_references.<name> object into an input with the matching semantic_type. "
                    "Do not invent placeholders such as 'pending' and do not guess future record IDs."
                    if output_references
                    else "This staged intent declares no referenceable typed output."
                ),
                "write_occurred": False,
                "completion_reason": "awaiting_plan_compilation",
            }
        else:
            raw_result = await _call_executor(executor, call_kwargs)
        if canonical_name == "manage_session":
            _merge_session_snapshot(raw_result, session_state)
        result = _result_from_raw(raw_result, canonical_name, normalized_args)
        await _audit_quietly(db, actor, canonical_name, normalized_args, result, raw_result=raw_result)
        if effect is not None:
            completed = await _commit_effect(
                db,
                effect=effect,
                tool_name=canonical_name,
                args=normalized_args,
                raw_result=raw_result,
            )
            if completed is not None:
                return completed
        return result
    except OperatorError as exc:
        if effect is not None:
            await _rollback(db)
        raw_error = exc.to_response()
        result = _result_from_raw(raw_error, canonical_name, normalized_args)
        if effect is None:
            await _audit_quietly(
                db,
                actor,
                canonical_name,
                normalized_args,
                result,
                raw_result=raw_error,
                error=exc.message,
            )
        return result
    except Exception as exc:
        if effect is not None:
            await _rollback(db)
        result = _error_result(str(exc), details={"exception": exc.__class__.__name__})
        if effect is None:
            await _audit_quietly(db, actor, canonical_name, normalized_args, result, error=str(exc))
        return result


_EFFECT_REQUIRED_FIELDS = (
    "invocation_key",
    "tool_call_id",
    "lease_token",
    "generation",
    "session_generation",
    "actor_id",
    "session_id",
)


def _normalize_effect_context(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("effect_context must be an object")
    missing = [field for field in _EFFECT_REQUIRED_FIELDS if value.get(field) in (None, "")]
    if missing:
        raise ValueError("effect_context is missing required fields: %s" % ", ".join(missing))
    try:
        generation = int(value["generation"])
        session_generation = int(value["session_generation"])
    except (TypeError, ValueError) as exc:
        raise ValueError("effect_context generations must be integers") from exc
    if generation < 1 or session_generation < 1:
        raise ValueError("effect_context generations must be positive")
    return {
        "invocation_key": str(value["invocation_key"]),
        "tool_call_id": str(value["tool_call_id"]),
        "lease_token": str(value["lease_token"]),
        "generation": generation,
        "session_generation": session_generation,
        "actor_id": str(value["actor_id"]),
        "session_id": str(value["session_id"]),
    }


def _effect_scope_conflict(effect: Mapping[str, Any], actor: Any) -> ExecutedToolResult | None:
    actor_id = str(getattr(actor, "actor_id", "") or getattr(actor, "id", "") or "")
    session_id = str(getattr(actor, "session_id", "") or "")
    if actor_id == effect["actor_id"] and session_id == effect["session_id"]:
        return None
    return _error_result(
        "Tool effect receipt conflict: effect scope does not match the executing actor/session.",
        details={"code": "conflict_error", "stage": "effect_scope"},
    )


def _effect_binding(tool_name: str, args: Mapping[str, Any]) -> dict[str, str]:
    encoded = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    action = str(args.get("action") or "") if isinstance(args, Mapping) else ""
    return {
        "tool_name": tool_name,
        "action": action,
        "args_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


_CONTINUATION_SHIFT_LATCH_KEY = "_continuation_effect_shift_detected"


def _effect_ordinal(tool_call_id: str) -> int | None:
    """Parse the position-stable ordinal of a canonical ``effect:{N}`` id.

    Returns ``None`` for non-positional ids (legacy/foreign ids) so the
    position-region guard never mis-fires on ids it cannot order.
    """
    raw = str(tool_call_id or "")
    if not raw.startswith("effect:"):
        return None
    suffix = raw[len("effect:") :]
    try:
        return int(suffix)
    except (TypeError, ValueError):
        return None


async def _invocation_has_later_committed_effect(db: Any, effect: Mapping[str, Any]) -> bool:
    """True when the invocation already committed an effect at a HIGHER ordinal.

    A call arriving at an unreceipted position while the invocation already has
    committed receipts with later ordinals sits INSIDE the committed region
    (e.g. the interior hole left by a previously failed call, or a displaced
    committed call). Position-based identity is then ambiguous, so the call
    must fail closed before any tool execution.
    """
    ordinal = _effect_ordinal(str(effect.get("tool_call_id") or ""))
    if ordinal is None:
        return False
    rows = (
        await db.scalars(
            select(models.AgentToolInvocationReceipt.tool_call_id).where(
                models.AgentToolInvocationReceipt.invocation_key == effect["invocation_key"]
            )
        )
    ).all()
    for stored_id in rows:
        stored_ordinal = _effect_ordinal(stored_id)
        if stored_ordinal is not None and stored_ordinal > ordinal:
            return True
    return False


def _mark_shift_latched(session_state: Mapping[str, Any]) -> None:
    """Record that a position-shift was detected for this continuation turn.

    Once the position mapping of a retry stream diverges from the committed
    receipts (a binding mismatch at a receipt position, or a call inside the
    committed region), NO later call of the same turn can be trusted to be new
    work: the provider may re-emit any already-committed call at any later
    position. All subsequent effect calls therefore fail closed pre-execution.
    """
    session_state[_CONTINUATION_SHIFT_LATCH_KEY] = True


async def _effect_preflight(
    db: Any,
    effect: Mapping[str, Any],
    tool_name: str,
    args: Mapping[str, Any],
    session_state: Mapping[str, Any] | None = None,
) -> ExecutedToolResult | None:
    binding = _effect_binding(tool_name, args)
    # Once a position shift was detected earlier in this turn, no further call
    # may execute: the committed region is no longer position-identifiable.
    if session_state is not None and bool(session_state.get(_CONTINUATION_SHIFT_LATCH_KEY)):
        await _rollback(db)
        return _error_result(
            "Tool effect position shift already detected for this invocation; "
            "refusing to execute further calls; manual review required.",
            details={"code": "conflict_error", "stage": "effect_position_shift"},
        )
    receipt = await _load_effect_receipt(db, effect)
    if receipt is not None:
        if not _receipt_matches(receipt, effect, binding):
            await _rollback(db)
            if session_state is not None:
                _mark_shift_latched(session_state)
            return _error_result(
                "Tool effect receipt conflict: scope, tool, action, or arguments changed on replay.",
                details={"code": "conflict_error", "stage": "effect_receipt"},
            )
        if receipt.status == "succeeded" and isinstance(receipt.result, Mapping):
            raw_result = dict(receipt.result)
            await _rollback(db)
            return _result_from_raw(raw_result, tool_name, args)
        await _rollback(db)
        return _error_result(
            "Tool effect receipt conflict: invocation is not replayable.",
            details={"code": "conflict_error", "stage": "effect_receipt"},
        )

    # Position-region guard: an unreceipted position inside the already
    # committed ordinal region is a hole or a displaced committed call; either
    # way executing it could double-apply an already-committed write effect.
    if await _invocation_has_later_committed_effect(db, effect):
        await _rollback(db)
        if session_state is not None:
            _mark_shift_latched(session_state)
        return _error_result(
            "Tool effect position shift: a committed effect exists at a later "
            "position for this invocation, so this call may be a displaced "
            "already-committed effect; refusing to execute; manual review required.",
            details={"code": "conflict_error", "stage": "effect_position_shift"},
        )

    claim = await db.get(
        models.AgentContinuationInvocation,
        effect["invocation_key"],
        populate_existing=True,
    )
    if not _claim_matches(claim, effect) or not await _session_claim_matches(db, effect):
        await _rollback(db)
        return _fence_error()
    return None


async def _commit_effect(
    db: Any,
    *,
    effect: Mapping[str, Any],
    tool_name: str,
    args: Mapping[str, Any],
    raw_result: Any,
) -> ExecutedToolResult | None:
    binding = _effect_binding(tool_name, args)
    receipt = models.AgentToolInvocationReceipt(
        invocation_key=effect["invocation_key"],
        tool_call_id=effect["tool_call_id"],
        actor_id=effect["actor_id"],
        session_id=effect["session_id"],
        tool_name=binding["tool_name"],
        action=binding["action"],
        args_hash=binding["args_hash"],
        generation=effect["generation"],
        status="succeeded",
        result=dict(raw_result) if isinstance(raw_result, Mapping) else {"value": raw_result},
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(receipt)
    try:
        fenced = await db.execute(
            update(models.AgentContinuationInvocation)
            .where(
                models.AgentContinuationInvocation.invocation_key == effect["invocation_key"],
                models.AgentContinuationInvocation.actor_id == effect["actor_id"],
                models.AgentContinuationInvocation.session_id == effect["session_id"],
                models.AgentContinuationInvocation.status == "running",
                models.AgentContinuationInvocation.lease_token == effect["lease_token"],
                models.AgentContinuationInvocation.attempt_count == effect["generation"],
            )
            .values(lease_token=models.AgentContinuationInvocation.lease_token)
            .execution_options(synchronize_session=False)
        )
        session_fenced = await db.execute(
            update(models.AgentSessionExecutionLease)
            .where(
                models.AgentSessionExecutionLease.actor_id == effect["actor_id"],
                models.AgentSessionExecutionLease.session_id == effect["session_id"],
                models.AgentSessionExecutionLease.owner_invocation_key == effect["invocation_key"],
                models.AgentSessionExecutionLease.lease_token == effect["lease_token"],
                models.AgentSessionExecutionLease.generation == effect["session_generation"],
                models.AgentSessionExecutionLease.lease_expires_at >= datetime.now(timezone.utc).replace(tzinfo=None),
            )
            .values(lease_token=models.AgentSessionExecutionLease.lease_token)
            .execution_options(synchronize_session=False)
        )
        if fenced.rowcount != 1 or session_fenced.rowcount != 1:
            await _rollback(db)
            return _fence_error()
        await db.commit()
    except IntegrityError:
        await _rollback(db)
        existing = await _load_effect_receipt(db, effect)
        if existing is not None and _receipt_matches(existing, effect, binding):
            if existing.status == "succeeded" and isinstance(existing.result, Mapping):
                replay = _result_from_raw(dict(existing.result), tool_name, args)
                await _rollback(db)
                return replay
        await _rollback(db)
        return _error_result(
            "Tool effect receipt conflict: another invocation committed a different effect.",
            details={"code": "conflict_error", "stage": "effect_commit"},
        )
    return None


async def _load_effect_receipt(db: Any, effect: Mapping[str, Any]) -> Any:
    return (
        await db.scalars(
            select(models.AgentToolInvocationReceipt).where(
                models.AgentToolInvocationReceipt.invocation_key == effect["invocation_key"],
                models.AgentToolInvocationReceipt.tool_call_id == effect["tool_call_id"],
            )
        )
    ).one_or_none()


def _receipt_matches(receipt: Any, effect: Mapping[str, Any], binding: Mapping[str, str]) -> bool:
    return (
        receipt.actor_id == effect["actor_id"]
        and receipt.session_id == effect["session_id"]
        and receipt.tool_name == binding["tool_name"]
        and receipt.action == binding["action"]
        and receipt.args_hash == binding["args_hash"]
    )


async def _session_claim_matches(db: Any, effect: Mapping[str, Any]) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    lease = await db.get(
        models.AgentSessionExecutionLease,
        (effect["actor_id"], effect["session_id"]),
        populate_existing=True,
    )
    return bool(
        lease is not None
        and lease.owner_invocation_key == effect["invocation_key"]
        and lease.lease_token == effect["lease_token"]
        and int(lease.generation or 0) == int(effect["session_generation"])
        and lease.lease_expires_at is not None
        and lease.lease_expires_at >= now
    )

def _claim_matches(claim: Any, effect: Mapping[str, Any]) -> bool:
    return bool(
        claim is not None
        and claim.actor_id == effect["actor_id"]
        and claim.session_id == effect["session_id"]
        and claim.status == "running"
        and claim.lease_token == effect["lease_token"]
        and int(claim.attempt_count or 0) == effect["generation"]
    )


def _fence_error() -> ExecutedToolResult:
    return _error_result(
        "Tool effect fence rejected a stale continuation generation.",
        details={"code": "conflict_error", "stage": "effect_fence"},
    )


async def _rollback(db: Any) -> None:
    rollback = getattr(db, "rollback", None)
    if callable(rollback):
        value = rollback()
        if inspect.isawaitable(value):
            await value


async def _call_executor(executor: Callable[..., Any], kwargs: dict[str, Any]) -> Any:
    try:
        signature = inspect.signature(executor)
    except ValueError:
        value = executor(**kwargs)
    else:
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            value = executor(**kwargs)
        else:
            filtered = {key: value for key, value in kwargs.items() if key in signature.parameters}
            value = executor(**filtered)
    if inspect.isawaitable(value):
        return await value
    return value


def _merge_session_snapshot(raw_result: Any, session_state: dict[str, Any]) -> None:
    if not isinstance(raw_result, Mapping) or raw_result.get("ok") is not True:
        return
    snapshot = raw_result.get("session_snapshot")
    if not isinstance(snapshot, Mapping):
        return
    for key in (
        "active_skill",
        "current_step",
        "current_job_id",
        "current_resume_id",
        "current_profile_section_id",
        "current_application_id",
        "pending_proposal_ids",
        "checkpoint_id",
    ):
        if key in snapshot:
            session_state[key] = snapshot.get(key)


def _result_from_raw(raw_result: Any, tool_name: str, args: Mapping[str, Any]) -> ExecutedToolResult:
    if not isinstance(raw_result, Mapping):
        return ExecutedToolResult(content=[TextContent(text=str(raw_result))], details={"raw_result": raw_result})

    details = dict(raw_result)
    details["raw_result"] = dict(raw_result)
    cards = _extract_cards(raw_result)
    proposals = _extract_proposals(raw_result)
    if cards:
        details["cards"] = cards
    if proposals:
        details["proposals"] = proposals

    is_error = raw_result.get("ok") is False or isinstance(raw_result.get("error"), Mapping)
    guidance = _zero_result_guidance(tool_name, args, raw_result)
    if guidance:
        details["guidance"] = guidance

    text = _tool_result_text(raw_result, tool_name, is_error=is_error)
    if guidance:
        text = "%s\n\n%s" % (text, guidance["message"]) if text else guidance["message"]
    return ExecutedToolResult(
        content=[TextContent(text=text or json.dumps(raw_result, ensure_ascii=False, default=str))],
        details=details,
        is_error=is_error,
        terminate=bool(raw_result.get("terminate")),
    )


def _summary_text(result: Mapping[str, Any], *, is_error: bool) -> str:
    if is_error:
        error = result.get("error")
        if isinstance(error, Mapping):
            return str(error.get("message") or error.get("code") or "Tool failed")
        return str(error or "Tool failed")
    for key in ("summary", "message", "status"):
        value = result.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _tool_result_text(result: Mapping[str, Any], tool_name: str, *, is_error: bool) -> str:
    if not is_error and (
        tool_name in _READ_TOOL_NAMES or str(result.get("status") or "") == "intent_staged"
    ):
        return json.dumps(result, ensure_ascii=False, default=str)
    return _summary_text(result, is_error=is_error)


def _zero_result_guidance(tool_name: str, args: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any] | None:
    if tool_name not in {"query_records", "get_record"}:
        return None
    total = result.get("total")
    records = result.get("records")
    empty_records = isinstance(records, list) and len(records) == 0
    if total not in (0, "0") and not empty_records:
        return None
    return {
        "reason": "zero_results",
        "message": (
            "The latest %s call returned zero matching records. Broaden the search, remove overly specific filters, "
            "or ask the user for a visible identifier before trying a write."
        )
        % tool_name,
    }


def _extract_cards(result: Mapping[str, Any]) -> list[Any]:
    cards = result.get("cards")
    if isinstance(cards, list):
        return cards
    card = result.get("card")
    if isinstance(card, Mapping):
        return [card]
    model = str(result.get("model") or "").strip()
    if not model:
        return []
    records: list[Mapping[str, Any]] = []
    raw_records = result.get("records")
    if isinstance(raw_records, list):
        records.extend(item for item in raw_records if isinstance(item, Mapping))
    raw_record = result.get("record")
    if isinstance(raw_record, Mapping):
        records.append(raw_record)
    return [_card_from_record(model, record) for record in records]


def _card_from_record(model: str, record: Mapping[str, Any]) -> dict[str, Any]:
    card_type = _card_type(model)
    return {
        "card_type": card_type,
        "record_id": record.get("id"),
        "title": _first_text(record, _title_fields(card_type)) or card_type.replace("_", " ").title(),
        "subtitle": _subtitle(card_type, record),
    }


def _card_type(model: str) -> str:
    aliases = {
        "jobs": "job",
        "profiles": "profile",
        "application_record": "application",
        "application_records": "application",
        "resumes": "resume",
    }
    normalized = model.strip().lower()
    return aliases.get(normalized, normalized.rstrip("s") or "record")


def _title_fields(card_type: str) -> tuple[str, ...]:
    return {
        "job": ("title", "role", "job_title", "position", "company"),
        "profile": ("name", "title", "headline", "school", "company"),
        "application": ("job_title", "title", "role", "company_name", "company", "status"),
        "resume": ("title", "user_name", "name"),
    }.get(card_type, ("title", "name", "role", "company", "id"))


def _subtitle(card_type: str, record: Mapping[str, Any]) -> str:
    fields = {
        "job": ("company", "location"),
        "profile": ("school", "company", "location"),
        "application": ("company_name", "company", "status"),
        "resume": ("user_name",),
    }.get(card_type, ("company", "location", "status"))
    parts = [_clean_text(record.get(field)) for field in fields]
    return " / ".join(part for part in parts if part)


def _first_text(record: Mapping[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        text = _clean_text(record.get(field))
        if text:
            return text
    return ""


def _clean_text(value: Any, *, limit: int = 120) -> str:
    if value is None or isinstance(value, Mapping) or isinstance(value, list):
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip()


def _extract_proposals(result: Mapping[str, Any]) -> list[Any]:
    proposal = result.get("proposal")
    proposals = result.get("proposals")
    items = []
    if isinstance(proposal, Mapping):
        items.append(dict(proposal))
    if isinstance(proposals, list):
        items.extend(proposals)
    return items


def _error_result(message: str, *, details: dict[str, Any] | None = None) -> ExecutedToolResult:
    return ExecutedToolResult(
        content=[TextContent(text="ERROR: %s" % message if not message.startswith("ERROR:") else message)],
        details=details or {},
        is_error=True,
    )


async def _audit_quietly(
    db: Any,
    actor: Any,
    tool_name: str,
    args: Mapping[str, Any],
    result: ExecutedToolResult,
    *,
    confirmation_status: str = "",
    raw_result: Any = None,
    error: str = "",
) -> None:
    try:
        await audit.log_agent_audit(
            db,
            actor=actor,
            tool_name=tool_name,
            args_snapshot=dict(args),
            args_redacted=audit.redact_audit_args(dict(args)),
            confirmation_status=confirmation_status or _confirmation_status(raw_result),
            result_status="error" if result.is_error else _result_status(raw_result),
            result_summary=result.content[0].text if result.content else "",
            error=error,
        )
    except Exception:
        _logger.exception("Agent tool audit failed")


def _confirmation_status(raw_result: Any) -> str:
    if isinstance(raw_result, Mapping) and raw_result.get("status") == "proposal_required":
        return "proposal_required"
    return "not_required"


def _result_status(raw_result: Any) -> str:
    if isinstance(raw_result, Mapping):
        return str(raw_result.get("status") or "success")
    return "success"


async def _persist_resume_readiness_if_available(
    db: Any,
    actor: Any,
    tool_name: str,
    args: Mapping[str, Any],
    session_state: Mapping[str, Any],
) -> None:
    if tool_name != "invoke_action" or str(args.get("action") or "") != "generate_resume":
        return
    if str(session_state.get("active_skill") or "") != "resume-optimizer":
        return
    evidence = session_state.get("resume_readiness_evidence")
    if not isinstance(evidence, Mapping):
        return
    profile_evidence = evidence.get("profile_read_evidence")
    job_evidence = evidence.get("job_read_evidence")
    if not isinstance(profile_evidence, Mapping) or not isinstance(job_evidence, Mapping):
        return
    try:
        from app.harness import skill_runtime

        await skill_runtime.set_active_skill_state(
            db,
            actor,
            skill_name="resume-optimizer",
            skill_step=str(session_state.get("current_step") or "strategy_confirmed"),
            status="active",
            readiness_gates={"strategy_confirmed": bool(session_state.get("strategy_confirmed"))},
            metadata={
                "profile_read_evidence": dict(profile_evidence),
                "job_read_evidence": dict(job_evidence),
            },
            source="harness_skill_runtime",
        )
    except Exception:
        _logger.exception("Failed to persist resume readiness evidence")

