from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.operator.guards import json_safe


MODEL_RESOURCE_MAP: dict[str, set[str]] = {
    "profile": {"profile"},
    "profile_section": {"profile"},
    "profile_target_role": {"profile"},
    "job": {"jobs"},
    "pool": {"jobs", "pools"},
    "resume": {"resume"},
    "resume_section": {"resume"},
    "resume_template": {"resume"},
    "application": {"applications"},
    "application_table": {"applications"},
    "application_record": {"applications"},
    "application_table_record": {"applications"},
    "application_workspace_settings": {"applications"},
    "application_template": {"applications"},
    "calendar_event": {"calendar"},
    "interview_notification": {"calendar", "interview"},
    "interview_experience": {"interview"},
    "interview_question": {"interview"},
    "agent_conversation": {"settings"},
    "agent_memory": {"settings"},
    "agent_session": {"settings"},
}


ACTION_RESOURCE_MAP: dict[str, set[str]] = {
    "profile_chat_confirm": {"profile"},
    "profile_agent_apply_patch": {"profile"},
    "profile_generate_narrative": {"profile"},
    "profile_instant_draft": {"profile"},
    "optimize_agent_chat": {"resume"},
    "generate_resume": {"resume"},
    "optimize_resume": {"resume"},
    "batch_optimize_resume": {"resume"},
    "apply_resume_ai_patch": {"resume"},
    "apply_resume_ai_batch": {"resume"},
    "apply_resume_template": {"resume"},
    "parse_resume": {"profile", "resume"},
    "upload_resume_photo": {"resume"},
    "upload_resume_logo": {"resume"},
    "resolve_resume_logo": {"resume"},
    "export_resume_pdf": {"resume"},
    "export_resume_image": {"resume"},
    "analyze_resume": {"resume"},
    "batch_triage_jobs": {"jobs", "pools"},
    "batch_delete_jobs": {"jobs", "pools"},
    "batch_mutate": {"jobs", "pools", "profile", "resume", "applications", "calendar", "interview"},
    "organize_jobs_into_pool": {"jobs", "pools"},
    "run_scraper": {"jobs"},
    "job_stats": {"jobs"},
    "import_jobs_to_application_table": {"applications"},
    "import_latest_extension_batch": {"applications"},
    "generate_cover_letter": {"applications"},
    "auto_write_application_content": {"applications"},
    "calendar_auto_fill": {"calendar"},
    "sync_email": {"calendar", "interview"},
    "interview_generate_answer": {"interview"},
    "interview_extract_questions": {"interview"},
    "smartfill_map": {"profile"},
    "smartfill_option_match": {"profile"},
    "smartfill_field_map": {"profile"},
    "smartfill_module_count": {"profile"},
}


def attach_visibility(
    result: Mapping[str, Any],
    *,
    proposal: Any | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    safe_result = json_safe(dict(result))
    if not isinstance(safe_result, dict):
        safe_result = {"result": safe_result}
    changed_records = changed_records_from_result(safe_result, proposal=proposal)
    affected_resources = affected_resources_from_result(
        safe_result,
        proposal=proposal,
        changed_records=changed_records,
        action=action,
    )
    safe_result["changed_records"] = changed_records
    safe_result["affected_resources"] = affected_resources

    nested = safe_result.get("result")
    if isinstance(nested, Mapping):
        nested_visible = attach_visibility(nested, proposal=proposal, action=action)
        safe_result["result"] = nested_visible
        merged_records = _dedupe_records([*changed_records, *nested_visible.get("changed_records", [])])
        merged_resources = sorted(set(affected_resources) | set(nested_visible.get("affected_resources", [])))
        safe_result["changed_records"] = merged_records
        safe_result["affected_resources"] = merged_resources
    return safe_result


def changed_records_from_result(
    result: Mapping[str, Any],
    *,
    proposal: Any | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    has_explicit_changed_records = "changed_records" in result
    existing = result.get("changed_records")
    if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray)):
        for item in existing:
            if isinstance(item, Mapping):
                records.append(_clean_record_ref(item))
    if has_explicit_changed_records:
        return _dedupe_records(records)

    affected = getattr(proposal, "affected_records", None) if proposal is not None else None
    if isinstance(affected, Sequence) and not isinstance(affected, (str, bytes, bytearray)):
        for item in affected:
            if isinstance(item, Mapping):
                records.append(_clean_record_ref(item))

    model = _clean_text(result.get("model"))
    is_write = _is_write_result(result)
    if model:
        record = result.get("record")
        if is_write and isinstance(record, Mapping):
            records.append({"model": model, "id": record.get("id")})
        record_id = result.get("record_id")
        if is_write and record_id not in (None, ""):
            records.append({"model": model, "id": record_id})
        raw_records = result.get("records")
        if is_write and isinstance(raw_records, Sequence) and not isinstance(raw_records, (str, bytes, bytearray)):
            records_model = _records_model_for_result(result, model)
            for item in raw_records:
                if isinstance(item, Mapping):
                    records.append({"model": records_model, "id": item.get("id")})

    if is_write:
        _append_named_record(records, "resume", result.get("resume"))
        _append_named_record(records, "profile", result.get("profile"))
        _append_named_record(records, "pool", result.get("pool"))
        _append_named_record(records, "application", result.get("application"))
        _append_named_record(records, "application_table", result.get("table"))
        action_name = _clean_text(result.get("action"))
        sections_model = "profile_section" if action_name.startswith("profile_") else "resume_section"
        _append_named_records(records, sections_model, result.get("sections"))
        _append_named_records(records, "profile_section", result.get("profile_sections"))
        _append_named_records(records, "job", result.get("jobs"))
        _append_named_records(records, "calendar_event", result.get("calendar_events"))
        _append_named_records(records, "interview_notification", result.get("notifications"))
        _append_named_records(records, "interview_question", result.get("questions"))

    nested = result.get("result")
    if isinstance(nested, Mapping):
        records.extend(changed_records_from_result(nested, proposal=proposal))

    return _dedupe_records(records)


def affected_resources_from_result(
    result: Mapping[str, Any],
    *,
    proposal: Any | None = None,
    changed_records: Sequence[Mapping[str, Any]] | None = None,
    action: str | None = None,
) -> list[str]:
    resources: set[str] = set()
    existing = result.get("affected_resources")
    if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray)):
        resources.update(_clean_text(item) for item in existing if _clean_text(item))

    action_name = _clean_text(action) or _clean_text(result.get("action"))
    if not action_name and proposal is not None:
        if _clean_text(getattr(proposal, "tool_name", "")) == "invoke_action":
            action_name = _clean_text(getattr(proposal, "model_or_action", ""))
    resources.update(ACTION_RESOURCE_MAP.get(action_name, set()))

    tool_name = _clean_text(result.get("tool_name"))
    model_or_action = _clean_text(getattr(proposal, "model_or_action", "")) if proposal is not None else ""
    if tool_name in {"create_record", "patch_record", "delete_or_archive_record"} and model_or_action:
        resources.update(MODEL_RESOURCE_MAP.get(model_or_action, set()))

    for record in changed_records or ():
        model = _clean_text(record.get("model") if isinstance(record, Mapping) else "")
        resources.update(MODEL_RESOURCE_MAP.get(model, set()))
        result_model = _clean_text(record.get("result_model") if isinstance(record, Mapping) else "")
        resources.update(MODEL_RESOURCE_MAP.get(result_model, set()))
        record_action = _clean_text(record.get("action") if isinstance(record, Mapping) else "")
        resources.update(ACTION_RESOURCE_MAP.get(record_action, set()))

    nested = result.get("result")
    if isinstance(nested, Mapping):
        resources.update(affected_resources_from_result(nested, proposal=proposal, action=action_name))

    return sorted(resource for resource in resources if resource)


def _append_named_record(records: list[dict[str, Any]], model: str, value: Any) -> None:
    if isinstance(value, Mapping):
        record_id = value.get("id") or value.get(f"{model}_id")
        if record_id not in (None, ""):
            records.append({"model": model, "id": record_id})


def _is_write_result(result: Mapping[str, Any]) -> bool:
    tool_name = _clean_text(result.get("tool_name"))
    if tool_name in {"create_record", "patch_record", "delete_or_archive_record", "invoke_action"}:
        return True
    if _clean_text(result.get("action")):
        return True
    status = _clean_text(result.get("status"))
    return status in {"completed", "deleted", "archived", "restored", "confirmed"}


def _records_model_for_result(result: Mapping[str, Any], fallback: str) -> str:
    action = _clean_text(result.get("action"))
    if action in {"batch_triage_jobs", "batch_delete_jobs", "organize_jobs_into_pool"}:
        return "job"
    if action in {
        "import_jobs_to_application_table",
        "import_latest_extension_batch",
        "auto_write_application_content",
    }:
        return "application_record"
    return fallback


def _append_named_records(records: list[dict[str, Any]], model: str, value: Any) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return
    for item in value:
        _append_named_record(records, model, item)


def _clean_record_ref(item: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = {str(key): json_safe(value) for key, value in item.items()}
    if "model" in cleaned:
        cleaned["model"] = _clean_text(cleaned["model"])
    if "result_model" in cleaned:
        cleaned["result_model"] = _clean_text(cleaned["result_model"])
    if "action" in cleaned:
        cleaned["action"] = _clean_text(cleaned["action"])
    return cleaned


def _dedupe_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in records:
        if not isinstance(item, Mapping):
            continue
        cleaned = _clean_record_ref(item)
        model = _clean_text(cleaned.get("model") or cleaned.get("result_model"))
        action = _clean_text(cleaned.get("action"))
        record_id = _clean_text(cleaned.get("id"))
        if not model and not action:
            continue
        if model and not record_id and not action:
            continue
        key = (model, action, record_id)
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _clean_text(value: Any) -> str:
    return str(value or "").strip()
