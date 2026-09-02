from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


TRUSTED_PROFILE_READ_EVIDENCE_SOURCES = frozenset({"operator_tool_trace"})
TRUSTED_JOB_READ_EVIDENCE_SOURCES = frozenset({"operator_tool_trace"})
RESUME_GENERATION_SCOPE_VERSION = 1


# Public acquisition plan advertised in capability contracts. The steps are
# routing guidance only: readiness evidence is produced exclusively by backend
# tool traces, never by this text or by a model self-report.
PUBLIC_READINESS_ACQUISITION: dict[str, list[dict[str, Any]]] = {
    "job_context_loaded": [
        {
            "tool": "get_record",
            "model": "job",
            "record_id_from": "input.job_id",
            "detail": "Loads the job/JD and registers trusted job read evidence for this session.",
        },
        {
            "tool": "query_records",
            "model": "job",
            "filters": {"triage_status": "<target>"},
            "detail": "Alternative: locate the job record first, then get_record for detailed content.",
        },
    ],
    "profile_facts_loaded": [
        {
            "tool": "get_record",
            "model": "profile",
            "record_id_from": "input.profile_id",
            "detail": "Loads the official profile and its canonical sections in detail.",
        },
    ],
    "user_exclusions_recorded": [
        {
            "tool": "manage_session",
            "operation": "set_skill_step",
            "updates": {"current_step": "user_exclusions_recorded"},
            "detail": "Records the completed exclusion discussion step; exclusions themselves are not stored here.",
        },
    ],
    "unsupported_claims_removed": [
        {
            "tool": "manage_session",
            "operation": "set_skill_step",
            "updates": {"current_step": "unsupported_claims_removed"},
            "detail": "Records completion of the unsupported-claims review step.",
        },
    ],
    "strategy_confirmed": [
        {
            "tool": "confirmation",
            "operation": "strategy_confirmation",
            "detail": "Requires an explicit durable user decision through the authenticated confirmation UI. A free-form current_step value never satisfies this gate.",
        },
    ],
    "profile_read_evidence": [
        {
            "tool": "get_record",
            "model": "profile",
            "record_id_from": "input.profile_id",
            "detail": "Loads the official profile and its canonical sections in detail.",
        },
    ],
    "job_read_evidence": [
        {
            "tool": "get_record",
            "model": "job",
            "record_id_from": "input.job_id",
            "detail": "Loads the job/JD and registers trusted job read evidence for this session.",
        },
    ],
}


# Operations the model may still perform while a readiness gate is unsatisfied.
# Routing guidance only; the listed reads also produce the trusted evidence.
READINESS_NEXT_ALLOWED_OPERATIONS: list[dict[str, Any]] = [
    {
        "tool": "describe_capability",
        "capabilities": [
            {"kind": "action", "name": "generate_resume", "operation": "invoke"},
            {"kind": "skill", "name": "resume-optimizer", "operation": "activate"},
        ],
    },
    {"tool": "get_record", "models": ["job", "profile", "profile_section", "resume"]},
    {"tool": "query_records", "models": ["job", "profile", "profile_section", "resume"]},
    {"tool": "manage_session", "operations": ["set_context", "set_skill_step", "set_context"]},
]


def resolve_readiness_missing_requirements(
    *,
    profile_evidence: Any,
    job_evidence: Any,
    strategy_confirmed: bool,
) -> list[dict[str, Any]]:
    """Machine-readable missing-requirement entries for readiness rejection.

    Each entry names the unsatisfied gate, its status, and the public
    acquisition steps that can satisfy it (``PUBLIC_READINESS_ACQUISITION``).
    The acquisition plan is routing data only; trust evidence is produced
    exclusively by backend tool traces and durable user decisions.
    """
    names: list[str] = []
    if not profile_read_evidence_ready(profile_evidence):
        names.append("profile_read_evidence")
    if not job_read_evidence_ready(job_evidence):
        names.append("job_read_evidence")
    if not strategy_confirmed:
        names.append("strategy_confirmed")
    return [
        {
            "name": name,
            "status": "missing",
            "satisfy_with": list(PUBLIC_READINESS_ACQUISITION.get(name, [])),
        }
        for name in names
    ]


def readiness_recovery_payload(missing_requirements: list[dict[str, Any]]) -> dict[str, Any]:
    """Structured recovery payload for blocked generate_resume (SPEC §5.4).

    Strategy confirmation is never satisfiable via ``set_skill_step``: its
    acquisition entry requires an explicit durable user decision through the
    authenticated confirmation path.
    """
    return {
        "code": "readiness_requirements_missing",
        "missing_requirements": missing_requirements,
        "next_allowed_operations": READINESS_NEXT_ALLOWED_OPERATIONS,
        "retry_same_action_after": [],
    }


def profile_read_evidence_ready(evidence: Any) -> bool:
    if not isinstance(evidence, Mapping):
        return False
    source = str(evidence.get("source") or "").strip()
    if source not in TRUSTED_PROFILE_READ_EVIDENCE_SOURCES:
        return False
    models = _string_set(evidence.get("models"))
    tools = _string_set(evidence.get("tools"))
    detail_models = _string_set(evidence.get("detail_models"))
    if not {"profile", "profile_section"}.issubset(models):
        return False
    if not {"get_record", "query_records"}.issubset(tools):
        return False
    if not {"profile", "profile_section"}.issubset(detail_models):
        return False
    if not str(evidence.get("profile_id") or "").strip():
        return False
    section_ids = _ordered_string_ids(evidence.get("profile_section_ids"))
    detail_ids = _ordered_string_ids(evidence.get("profile_section_detail_ids"))
    if not section_ids or not detail_ids:
        return False
    if not set(detail_ids).issubset(set(section_ids)):
        return False
    section_count = _int_or_none(evidence.get("profile_section_count"))
    detail_count = _int_or_none(evidence.get("profile_section_detail_count"))
    if section_count is None or section_count <= 0 or detail_count is None or detail_count <= 0:
        return False
    return detail_count == len(detail_ids)


def job_read_evidence_ready(evidence: Any) -> bool:
    if not isinstance(evidence, Mapping):
        return False
    source = str(evidence.get("source") or "").strip()
    if source not in TRUSTED_JOB_READ_EVIDENCE_SOURCES:
        return False
    tools = _string_set(evidence.get("tools"))
    models = _string_set(evidence.get("models"))
    detail_models = _string_set(evidence.get("detail_models"))
    if "get_record" not in tools:
        return False
    if "job" not in models or "job" not in detail_models:
        return False
    if not str(evidence.get("job_id") or "").strip():
        return False
    return bool(evidence.get("raw_description_loaded") or evidence.get("jd_loaded"))


def resume_generation_scope(
    profile_evidence: Any,
    job_evidence: Any,
    *,
    strategy_confirmed: bool,
) -> dict[str, Any]:
    """Build the immutable generation boundary from trusted detailed read facts."""
    if not strategy_confirmed:
        return {}
    if not profile_read_evidence_ready(profile_evidence) or not job_read_evidence_ready(job_evidence):
        return {}
    assert isinstance(profile_evidence, Mapping)
    assert isinstance(job_evidence, Mapping)
    detail_ids = _ordered_string_ids(profile_evidence.get("profile_section_detail_ids"))
    digest_payload = {
        "scope_version": RESUME_GENERATION_SCOPE_VERSION,
        "profile_source": str(profile_evidence.get("source") or ""),
        "profile_id": str(profile_evidence.get("profile_id") or ""),
        "profile_section_ids": detail_ids,
        "profile_section_detail_count": int(profile_evidence.get("profile_section_detail_count") or 0),
        "job_source": str(job_evidence.get("source") or ""),
        "job_id": str(job_evidence.get("job_id") or ""),
        "raw_description_loaded": bool(
            job_evidence.get("raw_description_loaded") or job_evidence.get("jd_loaded")
        ),
        "strategy_confirmed": True,
    }
    encoded = json.dumps(
        digest_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "scope_version": RESUME_GENERATION_SCOPE_VERSION,
        "source": "operator_tool_trace",
        "mode": "detailed_read_evidence_only",
        "strategy_confirmed": True,
        "profile_id": digest_payload["profile_id"],
        "job_id": digest_payload["job_id"],
        "profile_section_ids": detail_ids,
        "evidence_digest": hashlib.sha256(encoded).hexdigest(),
    }


def resume_scope_from_session_state(session_state: Any) -> dict[str, Any]:
    if not isinstance(session_state, Mapping):
        return {}
    evidence = session_state.get("resume_readiness_evidence")
    if not isinstance(evidence, Mapping):
        return {}
    return resume_generation_scope(
        evidence.get("profile_read_evidence"),
        evidence.get("job_read_evidence"),
        strategy_confirmed=session_state.get("strategy_confirmed") is True,
    )


def resume_scope_from_runtime_state(runtime_state: Any) -> dict[str, Any]:
    if not isinstance(runtime_state, Mapping):
        return {}
    metadata = runtime_state.get("metadata")
    gates = runtime_state.get("readiness_gates")
    if not isinstance(metadata, Mapping) or not isinstance(gates, Mapping):
        return {}
    return resume_generation_scope(
        metadata.get("profile_read_evidence"),
        metadata.get("job_read_evidence"),
        strategy_confirmed=gates.get("strategy_confirmed") is True,
    )


def evidence_target_mismatches(args: Mapping[str, Any], session_state: Any) -> list[str]:
    if str(args.get("action") or "") != "generate_resume":
        return []
    input_payload = args.get("input")
    if not isinstance(input_payload, Mapping) or not isinstance(session_state, Mapping):
        return []
    evidence = session_state.get("resume_readiness_evidence")
    if not isinstance(evidence, Mapping):
        return []
    profile_evidence = evidence.get("profile_read_evidence")
    job_evidence = evidence.get("job_read_evidence")
    mismatches: list[str] = []
    if isinstance(profile_evidence, Mapping):
        requested = str(input_payload.get("profile_id") or "").strip()
        observed = str(profile_evidence.get("profile_id") or "").strip()
        if requested and observed and requested != observed:
            mismatches.append("profile_id")
    if isinstance(job_evidence, Mapping):
        requested = str(input_payload.get("job_id") or "").strip()
        observed = str(job_evidence.get("job_id") or "").strip()
        if requested and observed and requested != observed:
            mismatches.append("job_id")
    return mismatches


def _ordered_string_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = str(item or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _string_set(value: Any) -> set[str]:
    return set(_ordered_string_ids(value))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
