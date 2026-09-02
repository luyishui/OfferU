from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from pathlib import Path
from typing import Any


_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key[=:]\s*\S+)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")
_PHONE_RE = re.compile(
    r"(?<![\w-])(?:\+?\d{1,3}[\s.-]*)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]+\d{3,4}[\s.-]+\d{3,4}(?![\w-])"
)
_SENSITIVE_KEY_RE = re.compile(r"(api[_-]?key|token|secret|password|passwd|authorization|credential|phone|mobile|tel)", re.I)


def redact_for_log(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY_RE.search(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_for_log(item)
        return redacted
    if isinstance(value, list):
        return [redact_for_log(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_log(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    text = _SECRET_RE.sub("[REDACTED]", value)
    for secret in _runtime_secret_values():
        text = text.replace(secret, "[REDACTED]")
    text = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text


def _runtime_secret_values() -> list[str]:
    candidates = [
        os.environ.get("LIVE_EVAL_LLM_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
        os.environ.get("DEEPSEEK_API_KEY"),
        os.environ.get("QWEN_API_KEY"),
        os.environ.get("ZHIPU_API_KEY"),
        os.environ.get("SILICONFLOW_API_KEY"),
        os.environ.get("GEMINI_API_KEY"),
    ]
    values: list[str] = []
    for value in candidates:
        text = str(value or "").strip()
        if len(text) >= 8 and text not in values:
            values.append(text)
    return values


DURABLE_FACT_TABLES = (
    "AgentPlanDraft",
    "AgentPlanIntent",
    "ProposalCache",
    "ProposalPlan",
    "ConfirmationGroup",
    "ConfirmationDecision",
    "OperationNode",
    "NodeDependency",
    "PlanNodeExecutionSnapshot",
    "AtomicGroupExecutionClaim",
    "PlanRebaseReceipt",
    "NodeExecutionRevision",
    "SagaGroup",
    "SagaCompensationReceipt",
    "PlanGroupExecutionJob",
    "NodeExecutionReceipt",
    "NodeExecutionOutcome",
    "PlanGroupResultReceipt",
    "AgentAuditLog",
    "ProposalContinuation",
    "ManualReviewCase",
    "ManualReviewResolution",
)

# Redacted durable projections bound into the snapshot (§5.10). These are NOT
# additional authority tables in `tables`; they are safe projections of
# capability load receipts, AgentSession pending authority and Skill state,
# case-relevant AgentMemory facts, and the durable execution-state envelope.
PROJECTION_SCHEMA_VERSION = 1
PROJECTION_MODEL_TABLES = (
    "AgentCapabilityLoadReceipt",
    "AgentSession",
    "AgentMemory",
)
PROJECTION_SECTION_KEYS = (
    "capability_load_receipts",
    "agent_session_states",
    "agent_memory_facts",
    "execution_state_envelopes",
    "provider_context",
)
_SELF_REPORTED_DIGEST_MARKERS = {"sse-only", "self-reported", "unverified", "unset"}

# Provider availability failures arrive inside the SSE event stream
# (stop_reason="error" + error_message, final ok:true) rather than as an HTTP
# status. These constants and the predicate are the shared taxonomy leaf used
# by both the eval loop (recording provider_failure events) and the grader
# (never labeling provider availability as model behavior).
#
# Markers are matched only inside provider error payloads — never against
# model output text — so the tuples stay exact substring sets:
# - "Error code: 424" / "Service temporarily unavailable": the HTTP 424 family
#   (keeps the http_424 category/status_code 424 end to end);
# - "Connection error" / "Request timed out": transport disconnect text placed
#   into `message_end`/`turn_end` error_message by the provider layer;
# - "Agent stream failed" / code "agent_sse_failed": the SSE `error` event the
#   app emits when the provider stream dies mid-generation.
# 424 classification is a strict subset of the text markers; anything that
# hits a marker but is not in the 424 family is provider_unavailable.
PROVIDER_FAILURE_TEXT_MARKERS = (
    "Error code: 424",
    "Service temporarily unavailable",
    "Connection error",
    "Request timed out",
    "Agent stream failed",
)
PROVIDER_FAILURE_HTTP_424_TEXT_MARKERS = ("Error code: 424", "Service temporarily unavailable")
PROVIDER_FAILURE_SERVICE_CODES = ("agent_sse_failed",)
PROVIDER_ERROR_SSE_EVENT_NAMES = ("message_start", "message_end", "turn_end", "agent_end", "final", "error")


def sse_event_payload_is_provider_failure(data: Any) -> bool:
    """True when an SSE event payload carries a provider availability error.

    Recognizes the provider error shapes observed in real eval artifacts:
    - `message_end`/`turn_end`/`agent_end` carry `message.stop_reason ==
      "error"` with `error_message` containing one of the
      PROVIDER_FAILURE_TEXT_MARKERS (424 family, "Connection error." /
      "Request timed out." transport disconnects);
    - the SSE `error` event carries `{"error": {"code": "agent_sse_failed",
      "message": "Agent stream failed."}}` when the provider stream dies
      mid-generation.
    Only those explicit availability markers/codes qualify, so generic
    stop_reason=error payloads and production error codes keep their normal
    taxonomy, and model output text is never scanned.
    """
    if not isinstance(data, Mapping):
        return False
    provider_error = data.get("error")
    if isinstance(provider_error, Mapping):
        if str(provider_error.get("code") or "") in PROVIDER_FAILURE_SERVICE_CODES:
            return True
        code_message = str(provider_error.get("message") or "")
        if any(marker in code_message for marker in PROVIDER_FAILURE_TEXT_MARKERS):
            return True
    candidates: list[Any] = []
    message = data.get("message")
    if isinstance(message, Mapping):
        candidates.append(message)
    messages = data.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
        candidates.append(messages[0])
    candidates.append(data)
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if str(candidate.get("stop_reason") or "") != "error":
            continue
        detail = str(candidate.get("error_message") or "")
        if any(marker in detail for marker in PROVIDER_FAILURE_TEXT_MARKERS):
            return True
    return False


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def project_capability_load_receipts(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Redacted AgentCapabilityLoadReceipt projection (kind/name/operation/
    schema_digest/loaded_at). Receipts contain no challenge/lease/idempotency
    secrets or raw payloads; the projection keeps every digest-bound field the
    grader needs to verify the capability fact chain."""
    projections: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        projection = {
            "actor_id": str(row.get("actor_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "capability_kind": str(row.get("capability_kind") or row.get("kind") or ""),
            "capability_name": str(row.get("capability_name") or row.get("name") or ""),
            "operation": str(row.get("operation") or ""),
            "schema_digest": str(row.get("schema_digest") or ""),
            "loaded_at": row.get("loaded_at"),
        }
        projections.append(_canonical_json_value(projection))
    return sorted(
        projections,
        key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str),
    )


def project_agent_session_states(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Redacted AgentSession projection: pending authority (pending_proposal_ids
    + pending_list_version) and the Skill-runtime subset of state_json."""
    projections: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        state_json = row.get("state_json") if isinstance(row.get("state_json"), Mapping) else {}
        skill = _project_skill_runtime(state_json.get("skill_runtime"))
        projection = {
            "session_id": str(row.get("session_id") or ""),
            "actor_id": str(row.get("actor_id") or ""),
            "adapter": str(row.get("adapter") or ""),
            "active_skill": str(row.get("active_skill") or ""),
            "current_step": str(row.get("current_step") or ""),
            "pending_proposal_ids": [
                str(item) for item in (row.get("pending_proposal_ids") or []) if str(item)
            ],
            "pending_list_version": int(row.get("pending_list_version") or 0),
            "skill_runtime": skill,
            "checkpoint_id": str(row.get("checkpoint_id") or ""),
        }
        projections.append(_canonical_json_value(projection))
    return sorted(
        projections,
        key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str),
    )


def _project_skill_runtime(runtime: Any) -> dict[str, Any]:
    """Redacted skill_runtime subset: skill_name/status/current_step/readiness
    gates booleans and a digest-bound evidence metadata projection. Raw
    evidence payloads, record contents, and authorization internals are never
    exported."""
    if not isinstance(runtime, Mapping):
        return {}
    active = runtime.get("active_skill")
    if not isinstance(active, Mapping):
        return {}
    evidence = active.get("metadata") if isinstance(active.get("metadata"), Mapping) else {}
    return {
        "skill_name": str(active.get("skill_name") or ""),
        "status": str(active.get("status") or ""),
        "current_step": str(active.get("current_step") or ""),
        "source": str(active.get("source") or ""),
        "readiness_gates": {
            str(key): bool(value)
            for key, value in (active.get("readiness_gates") or {}).items()
            if isinstance(active.get("readiness_gates"), Mapping)
        },
        "evidence_metadata_digest": canonical_digest(redact_for_log(evidence)),
    }


def project_agent_memory_facts(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Redacted AgentMemory projection: category/topic/scope/session plus a
    content digest. Raw user-provided content is never exported."""
    projections: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        projection = {
            "memory_id": str(row.get("memory_id") or ""),
            "actor_id": str(row.get("actor_id") or ""),
            "session_id": str(row.get("session_id") or ""),
            "category": str(row.get("category") or ""),
            "topic": str(row.get("topic") or ""),
            "skill": str(row.get("skill") or ""),
            "confidence": float(row.get("confidence") or 0.0),
            "content_digest": canonical_digest(redact_for_log(row.get("content_json"))),
            "created_at": row.get("created_at"),
        }
        projections.append(_canonical_json_value(projection))
    return sorted(
        projections,
        key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str),
    )


def project_execution_state_envelope(tables: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Durable execution-state envelope derived strictly from authoritative
    tables (AgentSession pending authority, ProposalPlan, ConfirmationGroup,
    ProposalCache, ManualReviewCase). Model-safe: no challenge tokens, leases,
    idempotency keys, or raw locked payloads.

    TODO(wp3): when build_public_execution_state_envelope lands, this projection
    should delegate to the shared builder so system context, SSE, bootstrap,
    continuation, and grader consume one authority. Until then the envelope is
    this independent durable summary with a binding digest.
    """
    plans = sorted(
        (
            {"plan_id": str(row.get("plan_id") or ""), "status": str(row.get("status") or "")}
            for row in (tables.get("ProposalPlan") or [])
            if isinstance(row, Mapping) and row.get("plan_id")
        ),
        key=lambda item: str(item["plan_id"]),
    )
    groups = sorted(
        (
            _project_envelope_group(row)
            for row in (tables.get("ConfirmationGroup") or [])
            if isinstance(row, Mapping) and row.get("group_id")
        ),
        key=lambda item: str(item["group_id"]),
    )
    group_policy = {
        str(row.get("group_id") or ""): row.get("policy_json")
        for row in (tables.get("ConfirmationGroup") or [])
        if isinstance(row, Mapping) and isinstance(row.get("policy_json"), Mapping)
    }
    proposals = sorted(
        (
            _project_envelope_proposal(row, group_policy.get(str(row.get("confirmation_group_id") or "")))
            for row in (tables.get("ProposalCache") or [])
            if isinstance(row, Mapping) and row.get("proposal_id")
        ),
        key=lambda item: str(item["proposal_id"]),
    )
    manual_cases = sorted(
        (
            {
                "case_id": str(row.get("case_id") or ""),
                "status": str(row.get("status") or ""),
                "reason_code": str(row.get("reason_code") or ""),
            }
            for row in (tables.get("ManualReviewCase") or [])
            if isinstance(row, Mapping) and row.get("case_id")
        ),
        key=lambda item: str(item["case_id"]),
    )
    payload = {
        "plans": plans,
        "groups": groups,
        "proposals": proposals,
        "completed_results": [],
        "manual_review_cases": manual_cases,
    }
    return {
        **payload,
        "envelope_digest": canonical_digest(payload),
    }


def _project_envelope_group(row: Mapping[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "")
    terminal_statuses = {"completed", "manual_review", "manual_review_resolved", "compensated", "cancelled"}
    confirmable = status in {"", "awaiting_confirmation", "pending", "confirmable", "blocked_by_dependency"}
    return {
        "group_id": str(row.get("group_id") or ""),
        "plan_id": str(row.get("plan_id") or ""),
        "status": status,
        "dependency_group_ids": [str(item) for item in (row.get("dependency_group_ids") or []) if str(item)],
        "confirmable_now": confirmable and status not in terminal_statuses,
    }


def _project_envelope_proposal(row: Mapping[str, Any], policy: Any) -> dict[str, Any]:
    events = row.get("confirmation_events")
    received = sum(
        1
        for item in (events or [])
        if isinstance(item, Mapping) and str(item.get("status") or item.get("decision") or "") in {"confirmed", "confirm"}
    )
    required = max(1, int((policy or {}).get("confirmations_required") or 1) if isinstance(policy, Mapping) else 1)
    challenge_required = received < required and required > 1
    if challenge_required:
        next_action = "await_user_second_confirmation"
    elif received == 0 and required == 1:
        next_action = "await_user_confirmation"
    elif received >= required:
        next_action = "await_durable_execution" if str(row.get("status") or "") in {"confirmed", "authorized"} else "durable_settled"
    else:
        next_action = "await_user_confirmation"
    return {
        "proposal_id": str(row.get("proposal_id") or ""),
        "plan_id": str(row.get("plan_id") or ""),
        "group_id": str(row.get("confirmation_group_id") or ""),
        "status": str(row.get("status") or ""),
        "confirmations_required": required,
        "confirmations_received": received,
        "challenge_required": challenge_required,
        "next_action": next_action,
    }


def _validate_durable_projections(projections: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if int(projections.get("schema_version") or 0) != PROJECTION_SCHEMA_VERSION:
        errors.append(f"Durable fact projection schema version is invalid: {projections.get('schema_version')!r}")
    for key in PROJECTION_SECTION_KEYS:
        value = projections.get(key)
        if value is None:
            continue
        if key == "provider_context":
            if not isinstance(value, Mapping):
                errors.append(f"Durable fact projection {key} must be an object")
            continue
        if not isinstance(value, list):
            errors.append(f"Durable fact projection {key} must be a list")
    return errors


def _redacted_rows_for_model(rows: Iterable[Any], columns: Iterable[str]) -> list[dict[str, Any]]:
    return [
        _canonical_json_value({column: getattr(row, column) for column in columns})
        for row in rows
    ]


def build_durable_fact_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable, self-describing projection of authoritative durable rows.

    The input is a DB snapshot, not an SSE event collection. Rows and object keys
    are canonicalized so equivalent query orders produce byte-identical facts.
    """
    source = snapshot.get("tables") if isinstance(snapshot.get("tables"), Mapping) else snapshot
    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in DURABLE_FACT_TABLES:
        raw_rows = source.get(table_name) if isinstance(source, Mapping) else []
        normalized: list[dict[str, Any]] = []
        if isinstance(raw_rows, list):
            for row in raw_rows:
                if isinstance(row, Mapping):
                    normalized.append(_canonical_json_value(dict(row)))
        tables[table_name] = sorted(
            normalized,
            key=lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str),
        )
    source_metadata = snapshot.get("snapshot_metadata") if isinstance(snapshot.get("snapshot_metadata"), Mapping) else {}
    snapshot_metadata = _canonical_json_value(
        dict(source_metadata)
        if source_metadata
        else {
            "snapshot_version": 1,
            "dialect": "provided",
            "consistency": "provided_atomic_snapshot",
            "transaction_isolation": "external",
            "transaction_id": "",
        }
    )
    payload = {"schema_version": 1, "snapshot_metadata": snapshot_metadata, "tables": tables}
    projections = snapshot.get("durable_projections")
    if isinstance(projections, Mapping) and projections:
        payload["durable_projections"] = _canonical_json_value(dict(projections))
    return {**payload, "snapshot_digest": canonical_digest(payload)}


def redact_durable_fact_snapshot(snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Redacted copy of a durable fact snapshot re-signed over its redacted payload.

    The in-memory snapshot keeps its original `snapshot_digest` (scoring binds
    the unredacted values). The persisted artifact must be self-verifying, so
    this returns the redacted projection with a digest computed over the
    redacted content — `verify_durable_fact_snapshot` then passes on the saved
    copy even though sensitive keys/values were rewritten.
    """
    if not isinstance(snapshot, Mapping):
        return dict(snapshot) if snapshot is not None else None
    redacted = redact_for_log(snapshot)
    if not isinstance(redacted, Mapping):
        return dict(snapshot)
    rebuilt = build_durable_fact_snapshot(redacted)
    result = dict(redacted)
    result["snapshot_digest"] = str(rebuilt.get("snapshot_digest") or "")
    return result


def verify_durable_fact_snapshot(snapshot: Mapping[str, Any] | None) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    if not isinstance(snapshot, Mapping):
        return {}, ["Part 6 Plan grading requires a deterministic durable fact snapshot"]
    metadata = snapshot.get("snapshot_metadata")
    if (
        int(snapshot.get("schema_version") or 0) != 1
        or not isinstance(snapshot.get("tables"), Mapping)
        or not isinstance(metadata, Mapping)
        or int(metadata.get("snapshot_version") or 0) != 1
        or not str(metadata.get("dialect") or "")
        or not str(metadata.get("consistency") or "")
        or not str(metadata.get("transaction_isolation") or "")
    ):
        return {}, ["Durable fact snapshot schema or consistency metadata is invalid"]
    source_tables = snapshot["tables"]
    missing = [name for name in DURABLE_FACT_TABLES if name not in source_tables]
    if missing:
        return {}, [f"Durable fact snapshot is missing required authority table(s): {', '.join(missing)}"]
    projections = snapshot.get("durable_projections")
    if projections is not None:
        if not isinstance(projections, Mapping):
            return {}, ["Durable fact projections must be an object"]
        projection_errors = _validate_durable_projections(projections)
        if projection_errors:
            return {}, projection_errors
    rebuilt = build_durable_fact_snapshot(snapshot)
    signed_payload = {
        "schema_version": 1,
        "snapshot_metadata": rebuilt["snapshot_metadata"],
        "tables": rebuilt["tables"],
    }
    if rebuilt.get("durable_projections"):
        signed_payload["durable_projections"] = rebuilt["durable_projections"]
    if str(snapshot.get("snapshot_digest") or "") != canonical_digest(signed_payload):
        return {}, ["Durable fact snapshot digest is invalid"]
    return rebuilt["tables"], []


async def snapshot_durable_facts(
    db: Any,
    *,
    system_protocol: Any = None,
    provider_tools: Any = None,
) -> dict[str, Any]:
    """Read grader authority tables from one transactionally consistent DB snapshot.

    Redacted durable projections (capability load receipts, AgentSession
    pending authority and Skill state, AgentMemory facts, and the execution
    state envelope) are bound into the snapshot digest. When provided,
    `system_protocol` (the exact system prompt the provider saw) and
    `provider_tools` (the exact provider-visible tool definitions) are redacted
    and bound into the snapshot as `durable_projections.provider_context` so
    eval artifacts retain exact provider context without leaking secrets.
    """
    from sqlalchemy import inspect as sa_inspect, select, text
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.models import models

    async def read_tables(reader: Any) -> dict[str, list[dict[str, Any]]]:
        raw: dict[str, list[dict[str, Any]]] = {}
        for table_name in DURABLE_FACT_TABLES:
            model = getattr(models, table_name)
            mapper = sa_inspect(model)
            statement = select(model)
            if mapper.primary_key:
                statement = statement.order_by(*mapper.primary_key)
            rows = (await reader.execute(statement)).scalars().all()
            raw[table_name] = [
                {column.key: _canonical_json_value(getattr(row, column.key)) for column in mapper.columns}
                for row in rows
            ]
        return raw

    async def read_projection_rows(reader: Any) -> dict[str, list[dict[str, Any]]]:
        raw: dict[str, list[dict[str, Any]]] = {}
        for table_name in PROJECTION_MODEL_TABLES:
            model = getattr(models, table_name)
            mapper = sa_inspect(model)
            statement = select(model)
            if mapper.primary_key:
                statement = statement.order_by(*mapper.primary_key)
            rows = (await reader.execute(statement)).scalars().all()
            columns = [column.key for column in mapper.columns]
            raw[table_name] = [
                {column: _canonical_json_value(getattr(row, column)) for column in columns}
                for row in rows
            ]
        return raw

    async def read_all(reader: Any) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
        return await read_tables(reader), await read_projection_rows(reader)

    bind = getattr(db, "bind", None)
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "unknown")
    if dialect == "postgresql":
        if bind is None or not hasattr(bind, "connect"):
            raise RuntimeError("PostgreSQL durable snapshot requires an independent AsyncEngine connection")
        async with bind.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ")
            async with connection.begin():
                transaction_isolation = str(await connection.scalar(text("SHOW transaction_isolation")) or "").lower()
                if transaction_isolation.replace("_", " ") != "repeatable read":
                    raise RuntimeError(
                        f"PostgreSQL durable snapshot isolation is {transaction_isolation!r}, expected REPEATABLE READ"
                    )
                transaction_id = str(await connection.scalar(text("SELECT txid_current()")) or "")
                async with AsyncSession(bind=connection, expire_on_commit=False) as snapshot_db:
                    raw, projection_rows = await read_all(snapshot_db)
        snapshot_metadata = {
            "snapshot_version": 1,
            "dialect": "postgresql",
            "consistency": "repeatable_read_transaction",
            "transaction_isolation": transaction_isolation,
            "transaction_id": transaction_id,
        }
    else:
        raw, projection_rows = await read_all(db)
        snapshot_metadata = {
            "snapshot_version": 1,
            "dialect": dialect,
            "consistency": "single_session_transaction",
            "transaction_isolation": "serializable" if dialect == "sqlite" else "session_default",
            "transaction_id": "",
        }
    snapshot = {"snapshot_metadata": snapshot_metadata, "tables": raw}
    projections = _build_durable_projections(
        snapshot,
        projection_rows,
        system_protocol=system_protocol,
        provider_tools=provider_tools,
    )
    if projections:
        snapshot["durable_projections"] = projections
    return build_durable_fact_snapshot(snapshot)


def _build_durable_projections(
    snapshot: Mapping[str, Any],
    projection_rows: Mapping[str, list[dict[str, Any]]],
    *,
    system_protocol: Any = None,
    provider_tools: Any = None,
) -> dict[str, Any]:
    """Assemble the redacted durable projections bound into the snapshot digest."""
    projections: dict[str, Any] = {"schema_version": PROJECTION_SCHEMA_VERSION}
    projections["capability_load_receipts"] = project_capability_load_receipts(
        projection_rows.get("AgentCapabilityLoadReceipt") or []
    )
    projections["agent_session_states"] = project_agent_session_states(
        projection_rows.get("AgentSession") or []
    )
    projections["agent_memory_facts"] = project_agent_memory_facts(
        projection_rows.get("AgentMemory") or []
    )
    tables = snapshot.get("tables") if isinstance(snapshot.get("tables"), Mapping) else {}
    projections["execution_state_envelopes"] = [project_execution_state_envelope(tables)]
    if system_protocol is not None or provider_tools is not None:
        provider_context: dict[str, Any] = {}
        if system_protocol is not None:
            provider_context["system_protocol"] = redact_for_log(system_protocol)
        if provider_tools is not None:
            provider_context["provider_tools"] = redact_for_log(provider_tools)
        projections["provider_context"] = provider_context
    return projections


async def snapshot_execution_state_envelope(db: Any) -> dict[str, Any]:
    """Per-turn durable execution-state summary (independent of SSE).

    TODO(wp3): delegate to build_public_execution_state_envelope when it lands;
    until then this is the WP7 independent projection consumed by eval artifacts.
    """
    from sqlalchemy import inspect as sa_inspect, select
    from app.models import models

    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name in ("ProposalPlan", "ConfirmationGroup", "ProposalCache", "ManualReviewCase"):
        model = getattr(models, table_name)
        mapper = sa_inspect(model)
        statement = select(model)
        if mapper.primary_key:
            statement = statement.order_by(*mapper.primary_key)
        rows = (await db.execute(statement)).scalars().all()
        tables[table_name] = [
            {column.key: _canonical_json_value(getattr(row, column.key)) for column in mapper.columns}
            for row in rows
        ]
    return project_execution_state_envelope(tables)


def _canonical_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonical_json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical_json_value(item) for item in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def write_json(path: Path, value: Any, *, redact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_for_log(value) if redact else value
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_text(path: Path, value: str, *, redact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _redact_text(value) if redact else value
    path.write_text(text, encoding="utf-8")


def append_ndjson(path: Path, value: Any, *, redact: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_for_log(value) if redact else value
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        handle.write("\n")


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    event_name = "message"
    data_lines: list[str] = []
    raw_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if line == "":
            event = _finish_event(event_name, data_lines, raw_lines)
            if event is not None:
                yield event
            event_name = "message"
            data_lines = []
            raw_lines = []
            continue

        raw_lines.append(line)
        if line.startswith(":"):
            continue
        field, value = _split_sse_field(line)
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)

    event = _finish_event(event_name, data_lines, raw_lines)
    if event is not None:
        yield event


def _split_sse_field(line: str) -> tuple[str, str]:
    if ":" not in line:
        return line, ""
    field, value = line.split(":", 1)
    if value.startswith(" "):
        value = value[1:]
    return field, value


def _finish_event(event_name: str, data_lines: list[str], raw_lines: list[str]) -> dict[str, Any] | None:
    if not raw_lines and not data_lines:
        return None
    raw_data = "\n".join(data_lines)
    event = {
        "event": event_name or "message",
        "raw_data": raw_data,
        "raw_lines": list(raw_lines),
    }
    if raw_data == "":
        event["data"] = {}
        return event
    try:
        event["data"] = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        event["data"] = {}
        event["parse_error"] = str(exc)
    return event


def extract_final_text(events: Iterable[Mapping[str, Any]]) -> str:
    final_text = ""
    for event in events:
        event_name = str(event.get("event") or "")
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        if event_name == "final":
            text = str(data.get("assistant_message") or "")
            if text:
                final_text = text
        elif event_name == "proposal_confirm":
            text = _confirmation_continuation_text(data)
            if text:
                final_text = text
        elif event_name == "message_end":
            message = data.get("message") if isinstance(data.get("message"), Mapping) else {}
            if str(message.get("role") or "") == "assistant":
                text = message_text(message)
                if text:
                    final_text = text
    return final_text


def _confirmation_continuation_text(data: Mapping[str, Any]) -> str:
    responses = data.get("responses")
    if not isinstance(responses, list):
        return ""
    for item in reversed(responses):
        if not isinstance(item, Mapping):
            continue
        response = item.get("response")
        if not isinstance(response, Mapping):
            continue
        continuation = response.get("continuation")
        if isinstance(continuation, Mapping):
            text = str(continuation.get("assistant_message") or "")
            if text:
                return text
        text = str(response.get("assistant_message") or "")
        if text:
            return text
    return ""


def message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping):
                text = block.get("text") or block.get("thinking") or ""
                if text:
                    parts.append(str(text))
    return "".join(parts)


def extract_tool_calls(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    pending: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_name = str(event.get("event") or "")
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        if event_name == "tool_execution_start":
            call_id = str(data.get("tool_call_id") or data.get("toolCallId") or f"tool_{index}")
            call = {
                "index": len(ordered),
                "event_index_start": index,
                "tool_call_id": call_id,
                "tool_name": str(data.get("tool_name") or data.get("toolName") or ""),
                "args": data.get("args") if isinstance(data.get("args"), Mapping) else {},
            }
            pending[call_id] = call
            ordered.append(call)
        elif event_name == "tool_execution_end":
            call_id = str(data.get("tool_call_id") or data.get("toolCallId") or "")
            call = pending.get(call_id)
            if call is None:
                call = {
                    "index": len(ordered),
                    "event_index_start": None,
                    "tool_call_id": call_id,
                    "tool_name": str(data.get("tool_name") or data.get("toolName") or ""),
                    "args": {},
                }
                ordered.append(call)
            call["event_index_end"] = index
            call["is_error"] = bool(data.get("is_error") or data.get("isError"))
            result = data.get("result") if isinstance(data.get("result"), Mapping) else {}
            call["result"] = result
            details = result.get("details") if isinstance(result.get("details"), Mapping) else {}
            raw_result = details.get("raw_result") if isinstance(details.get("raw_result"), Mapping) else details
            if raw_result:
                call["result_status"] = raw_result.get("status") or raw_result.get("result_status")
                call["model"] = raw_result.get("model") or call["args"].get("model")
                call["action"] = raw_result.get("action") or call["args"].get("action")
                proposal = raw_result.get("proposal") if isinstance(raw_result.get("proposal"), Mapping) else {}
                if proposal:
                    call["proposal_id"] = proposal.get("proposal_id") or proposal.get("id")
    return ordered


def trace_text(events: Iterable[Mapping[str, Any]]) -> str:
    return json.dumps(list(events), ensure_ascii=False, default=str)
