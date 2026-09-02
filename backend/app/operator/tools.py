from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import models
from app.harness import skill_runtime
from app.operator.application_lifecycle import (
    ApplicationLifecycleError,
    normalize_apply_status_update,
)
from app.operator.audit import log_agent_audit, redact_audit_args
from app.operator.capability_loading import persist_capability_load_receipt
from app.operator.capability_map import describe_capability_contract
from app.operator.create_conflicts import reject_duplicate_job_create_conflict
from app.operator.errors import (
    OperatorError,
    conflict_error,
    not_found_error,
    permission_error,
    transient_error,
    validation_error,
)
from app.operator.guards import (
    ActorContext,
    DELETE_OPERATIONS,
    PATCH_MODES,
    RISK_CONFIRMATIONS,
    apply_patch_to_record_image,
    build_query_statement,
    calculate_action_risk,
    calculate_record_risk,
    canonical_version,
    collect_action_expected_versions,
    expected_versions_hash,
    fetch_scoped_record,
    get_action_spec,
    get_model_class,
    get_model_spec,
    json_safe,
    normalize_action_references,
    reject_trusted_args,
    serialize_record,
    shape_proposal,
    scope_clause,
    validate_action_schema,
    validate_create_scope,
    validate_fields,
    validate_model_values,
)
from app.operator.registry import BACKEND_OWNED_ACTION_INPUT_FIELDS, RegistryContractError
from app.operator.session import update_session_state
from app.operator.visibility import attach_visibility
from app.services.job_schema import normalize_job_record_payload
from app.services.profile_archive_sync import (
    remove_profile_section_from_personal_archive,
    sync_profile_section_to_personal_archive,
)
from app.services.profile_schema import normalize_profile_section_record_payload


async def describe_capability(
    session: AsyncSession,
    actor: ActorContext,
    kind: str,
    name: str,
    operation: str,
) -> dict[str, Any]:
    try:
        schema = describe_capability_contract(kind, name, operation)
        await persist_capability_load_receipt(session, actor, schema)
        return {
            "ok": True,
            "status": "capability_loaded",
            "capability": schema,
        }
    except RegistryContractError as exc:
        await _rollback_quietly(session)
        return validation_error(
            str(exc),
            {"capability_kind": kind, "capability_name": name, "operation": operation},
        )
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive database boundary.
        await _rollback_quietly(session)
        return transient_error("Capability schema could not be loaded.", {"error": str(exc)})

async def query_records(
    session: AsyncSession,
    actor: ActorContext,
    model: str,
    filters: Mapping[str, Any] | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 10,
    sort: str | None = None,
) -> dict[str, Any]:
    try:
        spec = get_model_spec(model)
        model_cls = get_model_class(model)
        page, page_size = _validate_pagination(page, page_size)
        statement = build_query_statement(model_cls, spec, actor, filters=filters, search=search, sort=sort)
        total_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        total = int((await session.execute(total_statement)).scalar_one())
        rows = (
            await session.execute(statement.offset((page - 1) * page_size).limit(page_size))
        ).scalars().all()
        records = [
            _augment_query_record_summary(
                model,
                row,
                serialize_record(
                    row,
                    spec,
                    spec.summary_fields,
                    include_long_text=False,
                    truncate_long_text=True,
                ),
            )
            for row in rows
        ]
        response = {
            "ok": True,
            "status": "success",
            "model": model,
            "total": total,
            "page": page,
            "page_size": page_size,
            "records": records,
        }
        disambiguation = _query_disambiguation_hint(model, total, records)
        if disambiguation is None:
            disambiguation = await _query_zero_result_retarget_hint(
                session,
                actor,
                model=model,
                model_cls=model_cls,
                spec=spec,
                total=total,
                filters=filters,
                search=search,
                limit=page_size,
            )
        if disambiguation is not None:
            response["disambiguation"] = disambiguation
        return response
    except OperatorError as exc:
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive boundary for adapters.
        return transient_error("Operator query failed transiently.", {"error": str(exc)})


def _query_disambiguation_hint(
    model: str,
    total: int,
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if int(total or 0) <= 1:
        return None
    return {
        "needed": True,
        "reason": "multiple_candidates",
        "message": (
            f"Multiple {model} records matched. Do not keep broad repeated reads; "
            "ask the user to choose by visible fields or apply a confirmed narrowing constraint before writing."
        ),
        "total": int(total or 0),
        "returned_count": len(records),
        "candidates": [_query_candidate_summary(record) for record in records],
    }


async def _query_zero_result_retarget_hint(
    session: AsyncSession,
    actor: ActorContext,
    *,
    model: str,
    model_cls: type[Any],
    spec: Any,
    total: int,
    filters: Mapping[str, Any] | None,
    search: str | None,
    limit: int,
) -> dict[str, Any] | None:
    if int(total or 0) != 0:
        return None
    relaxed_clauses = _relaxed_candidate_clauses(model_cls, spec, filters=filters, search=search)
    if not relaxed_clauses:
        return None
    scope = scope_clause(model_cls, spec, actor)
    if scope is not None:
        relaxed_clauses.append(scope)
    statement = select(model_cls).where(and_(*relaxed_clauses))
    sort_field, direction = getattr(spec, "default_sort", ("created_at", "desc"))
    if hasattr(model_cls, sort_field):
        column = getattr(model_cls, sort_field)
        statement = statement.order_by(column.desc() if direction == "desc" else column.asc())
    candidate_limit = min(max(int(limit or 10), 3), 10)
    rows = (await session.execute(statement.limit(candidate_limit))).scalars().all()
    if not rows:
        return None
    records = [
        _augment_query_record_summary(
            model,
            row,
            serialize_record(
                row,
                spec,
                spec.summary_fields,
                include_long_text=False,
                truncate_long_text=True,
            ),
        )
        for row in rows
    ]
    return {
        "needed": True,
        "reason": "no_exact_match",
        "message": (
            "没有精确匹配到目标记录。请根据候选重新定位目标记录，"
            "或补充公司、职位、状态等可见字段后再生成写入 proposal。"
        ),
        "total": 0,
        "returned_count": len(records),
        "candidates": [_query_candidate_summary(record) for record in records],
    }


def _relaxed_candidate_clauses(
    model_cls: type[Any],
    spec: Any,
    *,
    filters: Mapping[str, Any] | None,
    search: str | None,
) -> list[Any]:
    clauses: list[Any] = []
    filterable = set(getattr(spec, "filterable_fields", ()) or ())
    searchable = set(getattr(spec, "search_fields", ()) or ())
    for raw_key, value in (filters or {}).items():
        field_name = _retarget_filter_field_name(str(raw_key))
        if field_name not in filterable or not hasattr(model_cls, field_name):
            continue
        values = _retarget_values(value)
        if not values:
            continue
        column = getattr(model_cls, field_name)
        field_clauses = [_relaxed_text_clause(column, candidate) for candidate in values]
        field_clauses = [clause for clause in field_clauses if clause is not None]
        if field_clauses:
            clauses.append(or_(*field_clauses))
    search_values = _retarget_values(search)
    if search_values and searchable:
        search_clauses: list[Any] = []
        for field_name in searchable:
            if not hasattr(model_cls, field_name):
                continue
            column = getattr(model_cls, field_name)
            for candidate in search_values:
                clause = _relaxed_text_clause(column, candidate)
                if clause is not None:
                    search_clauses.append(clause)
        if search_clauses:
            clauses.append(or_(*search_clauses))
    return clauses


def _retarget_filter_field_name(key: str) -> str:
    for suffix in ("_contains", "_in", "_gte", "_lte"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _retarget_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, Mapping):
        return []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_retarget_values(item))
        return values
    text = str(value).strip()
    if not text:
        return []
    parts = [part.strip() for part in text.split() if part.strip()]
    return parts or [text]


def _relaxed_text_clause(column: Any, value: str) -> Any | None:
    text = str(value or "").strip()
    if not text:
        return None
    text_column = cast(column, String)
    clauses: list[Any] = [text_column.ilike(f"%{text}%")]
    cjk_chars = [char for char in text if _is_cjk_char(char)]
    if 2 <= len(cjk_chars) <= 8:
        clauses.append(and_(*[text_column.ilike(f"%{char}%") for char in cjk_chars]))
    return or_(*clauses)


def _is_cjk_char(char: str) -> bool:
    if not char:
        return False
    codepoint = ord(char)
    return 0x4E00 <= codepoint <= 0x9FFF


def _query_candidate_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    candidate: dict[str, Any] = {}
    if record.get("id") not in (None, ""):
        candidate["id"] = record.get("id")
    label_parts: list[str] = []
    for key in ("company_name", "company", "job_title", "title", "name"):
        value = str(record.get(key) or "").strip()
        if value and value not in label_parts:
            label_parts.append(value)
    if label_parts:
        candidate["label"] = " / ".join(label_parts[:3])
    for key in ("location", "source", "apply_status", "triage_status", "status", "scope"):
        value = record.get(key)
        if value not in (None, ""):
            candidate[key] = value
    return candidate


async def get_record(
    session: AsyncSession,
    actor: ActorContext,
    model: str,
    record_id: Any,
    include_long_text: bool = True,
) -> dict[str, Any]:
    try:
        spec = get_model_spec(model)
        model_cls = get_model_class(model)
        record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
        return {
            "ok": True,
            "status": "success",
            "model": model,
            "record": serialize_record(
                record,
                spec,
                spec.detail_fields,
                include_long_text=include_long_text,
                truncate_long_text=False,
            ),
        }
    except OperatorError as exc:
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive boundary for adapters.
        return transient_error("Operator get failed transiently.", {"error": str(exc)})


async def create_record(
    session: AsyncSession,
    actor: ActorContext,
    model: str,
    data: Mapping[str, Any],
    user_message: str = "",
    _defer_commit: bool = False,
    _force_proposal: bool = False,
) -> dict[str, Any]:
    try:
        if not isinstance(data, Mapping):
            raise OperatorError("validation_error", "Create data must be an object.", {"model": model})
        spec = get_model_spec(model)
        model_cls = get_model_class(model)
        reject_trusted_args(data, location=f"create_record:{model}.data")
        cleaned = {str(key): value for key, value in data.items()}
        cleaned = _normalize_model_record_payload(model, cleaned)
        if model == "application_record":
            cleaned = _normalize_application_record_status_update(cleaned)
        validate_fields(cleaned, spec.creatable_fields, purpose=f"create {model}")
        validate_model_values(cleaned, spec, purpose=f"create {model}")
        await validate_create_scope(session, actor, spec, cleaned)
        cleaned = _derive_backend_create_fields(model, cleaned, actor)
        if model == "job":
            await reject_duplicate_job_create_conflict(session, actor, cleaned)
        after = _record_image_from_fields(cleaned, spec.detail_fields)
        risk = calculate_record_risk(spec, tool_name="create_record", operation="create", fields=tuple(cleaned))
        locked_payload = {
            "tool_name": "create_record",
            "operation_type": "create",
            "model": model,
            "data": json_safe(cleaned),
            "expected_version_or_hash": "",
        }
        if risk >= 3 or _force_proposal:
            proposal = await shape_proposal(
                session,
                actor,
                tool_name="create_record",
                operation_type="create",
                model_or_action=model,
                risk_level=risk,
                locked_payload=locked_payload,
                user_message=user_message,
                affected_records=[{"model": model, "id": None}],
                before=None,
                after=after,
                reason="Creating official OfferU data requires confirmation.",
                summary=_create_record_proposal_summary(model, cleaned),
                commit=not _defer_commit,
            )
            return {"ok": True, "status": "proposal_required", "proposal": proposal}
        record = model_cls(**cleaned)
        if hasattr(record, "owner_actor_id"):
            record.owner_actor_id = actor.actor_id
        session.add(record)
        await session.flush()
        await _sync_profile_section_archive(session, model, record)
        if not _defer_commit:
            await session.commit()
        await session.refresh(record)
        return attach_visibility({
            "ok": True,
            "status": "completed",
            "model": model,
            "record": serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False),
            "risk_level": risk,
        })
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive boundary for adapters.
        await _rollback_quietly(session)
        return transient_error("Operator create failed transiently.", {"error": str(exc)})


async def patch_record(
    session: AsyncSession,
    actor: ActorContext,
    model: str,
    record_id: Any,
    updates: Mapping[str, Any],
    patch_mode: str,
    user_message: str = "",
    _defer_commit: bool = False,
    _force_proposal: bool = False,
) -> dict[str, Any]:
    try:
        if patch_mode not in PATCH_MODES:
            raise OperatorError("validation_error", "Unsupported patch mode.", {"patch_mode": patch_mode})
        if not isinstance(updates, Mapping):
            raise OperatorError("validation_error", "Patch updates must be an object.", {"model": model})
        cleaned_updates = {str(key): json_safe(value) for key, value in updates.items()}
        cleaned_updates = _normalize_model_record_payload(model, cleaned_updates)
        if model == "application_record":
            cleaned_updates = _normalize_application_record_status_update(cleaned_updates)
        reject_trusted_args(cleaned_updates, location=f"patch_record:{model}.updates")
        spec = get_model_spec(model)
        model_cls = get_model_class(model)
        validate_fields(cleaned_updates, spec.writable_fields, purpose=f"patch {model}")
        validate_model_values(cleaned_updates, spec, purpose=f"patch {model}")
        record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
        before = serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False)
        after = apply_patch_to_record_image(before, cleaned_updates, patch_mode)
        risk = calculate_record_risk(spec, tool_name="patch_record", operation="patch", fields=tuple(cleaned_updates))
        expected_version = canonical_version(record, spec)
        locked_payload = {
            "tool_name": "patch_record",
            "operation_type": "patch",
            "model": model,
            "record_id": record_id,
            "updates": json_safe(dict(cleaned_updates)),
            "patch_mode": patch_mode,
            "expected_version_or_hash": expected_version,
        }
        if risk >= 3 or _force_proposal:
            proposal = await shape_proposal(
                session,
                actor,
                tool_name="patch_record",
                operation_type="patch",
                model_or_action=model,
                risk_level=risk,
                locked_payload=locked_payload,
                user_message=user_message,
                record_id=record_id,
                affected_records=[{"model": model, "id": record_id}],
                before=before,
                after=after,
                reason="Patching official OfferU content requires confirmation.",
                summary=_patch_record_proposal_summary(model, before, record_id),
                commit=not _defer_commit,
            )
            return {"ok": True, "status": "proposal_required", "proposal": proposal}
        _apply_direct_patch(record, cleaned_updates, patch_mode)
        await session.flush()
        await _sync_profile_section_archive(session, model, record)
        if not _defer_commit:
            await session.commit()
        await session.refresh(record)
        return attach_visibility({
            "ok": True,
            "status": "completed",
            "model": model,
            "record": serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False),
            "risk_level": risk,
        })
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive boundary for adapters.
        await _rollback_quietly(session)
        return transient_error("Operator patch failed transiently.", {"error": str(exc)})


async def delete_or_archive_record(
    session: AsyncSession,
    actor: ActorContext,
    model: str,
    record_id: Any,
    operation: str,
    user_message: str = "",
    _defer_commit: bool = False,
    _force_proposal: bool = False,
) -> dict[str, Any]:
    try:
        if operation not in DELETE_OPERATIONS:
            raise OperatorError("validation_error", "Unsupported delete/archive operation.", {"operation": operation})
        if str(model) == "agent_conversation":
            raise OperatorError(
                "not_implemented",
                "Agent conversation lifecycle changes require a durable post-confirmation filesystem job.",
                {"model": "agent_conversation", "operation": operation},
            )
        spec = get_model_spec(model)
        model_cls = get_model_class(model)
        record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
        before = serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False)
        after = _delete_after_image(before, operation)
        risk = calculate_record_risk(spec, tool_name="delete_or_archive_record", operation=operation)
        expected_version = canonical_version(record, spec)
        if risk < 3 and not _force_proposal:
            result_record = None
            if operation == "delete":
                await _remove_profile_section_archive(session, model, record)
                await session.delete(record)
                result_status = "deleted"
            else:
                result_status = _apply_visibility_operation(record, operation)
                result_record = serialize_record(
                    record,
                    spec,
                    spec.detail_fields,
                    include_long_text=True,
                    truncate_long_text=False,
                )
                await _sync_profile_section_archive(session, model, record)
            await session.flush()
            if not _defer_commit:
                await session.commit()
            return attach_visibility({
                "ok": True,
                "status": "completed",
                "tool_name": "delete_or_archive_record",
                "model": model,
                "record_id": json_safe(record_id),
                "operation": operation,
                "result_status": result_status,
                "record": result_record,
                "risk_level": risk,
            })
        proposal = await shape_proposal(
            session,
            actor,
            tool_name="delete_or_archive_record",
            operation_type=operation,
            model_or_action=model,
            risk_level=risk,
            locked_payload={
                "tool_name": "delete_or_archive_record",
                "operation_type": operation,
                "model": model,
                "record_id": record_id,
                "expected_version_or_hash": expected_version,
            },
            user_message=user_message,
            record_id=record_id,
            affected_records=[{"model": model, "id": record_id}],
            before=before,
            after=after,
            reason="Destructive or visibility-changing operations require confirmation.",
            summary=_delete_or_archive_proposal_summary(model, before, operation, record_id),
            commit=not _defer_commit,
        )
        return {"ok": True, "status": "proposal_required", "proposal": proposal}
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive boundary for adapters.
        await _rollback_quietly(session)
        return transient_error("Operator delete/archive failed transiently.", {"error": str(exc)})


async def _resolve_record_ids_from_filter(
    session: AsyncSession,
    actor: ActorContext,
    model_name: str,
    filter_payload: dict[str, Any],
) -> list[int]:
    spec = get_model_spec(model_name)
    model_cls = get_model_class(model_name)
    statement = build_query_statement(
        model_cls, spec, actor, filters=filter_payload, search=None, sort=None,
    )
    subq = statement.order_by(None).subquery()
    pk_col = subq.c[spec.primary_key]
    id_statement = select(pk_col)
    rows = (await session.execute(id_statement)).scalars().all()
    return sorted(int(row) for row in rows)


async def invoke_action(
    session: AsyncSession,
    actor: ActorContext,
    action: str,
    input: Mapping[str, Any],
    user_message: str = "",
    _defer_commit: bool = False,
    _force_proposal: bool = False,
    confirmed_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        if not isinstance(input, Mapping):
            raise OperatorError("validation_error", "Action input must be an object.", {"action": action})
        spec = get_action_spec(action)
        if action == "generate_resume":
            # confirmed_scope is backend-owned: the provider schema never
            # declares it, so validate_action_schema would reject any value. The
            # backend freezes the evidence-derived scope into the staged args;
            # accept it here only when it matches the backend-supplied keyword,
            # otherwise reject the forged field.
            supplied_scope = confirmed_scope if isinstance(confirmed_scope, Mapping) else None
            provider_scope = input.get("confirmed_scope")
            if provider_scope is not None:
                if supplied_scope is None or json_safe(provider_scope) != json_safe(supplied_scope):
                    raise OperatorError(
                        "validation_error",
                        "Action input contains the backend-owned field confirmed_scope.",
                        {"action": action},
                    )
                input = {str(key): value for key, value in input.items() if key != "confirmed_scope"}
            if supplied_scope is None or not supplied_scope:
                raise OperatorError(
                    "validation_error",
                    "generate_resume requires backend-confirmed generation scope from trusted evidence.",
                    {"action": action},
                )
        cleaned = validate_action_schema(spec, input)
        cleaned = await normalize_action_references(session, actor, spec, cleaned)
        cleaned = _normalize_action_input_for_proposal(action, cleaned)
        if action == "generate_resume":
            cleaned = {**dict(cleaned), "confirmed_scope": json_safe(supplied_scope)}
        # Materialize batch_mutate by_filter target into by_ids before risk
        # calculation so risk reflects the actual blast radius (PLAN line 506,
        # line 519). by_filter resolves to ids at proposal creation time;
        # confirm-time execution always sees by_ids.
        original_filter: dict[str, Any] | None = None
        if action == "batch_mutate":
            target = (cleaned or {}).get("target") or {}
            mode = str((target or {}).get("mode") or "").lower()
            model_for_filter = str((cleaned or {}).get("model") or "")
            if mode == "by_filter":
                filter_payload = (target or {}).get("filter") or {}
                if not isinstance(filter_payload, Mapping) or not filter_payload:
                    raise OperatorError(
                        "validation_error",
                        "batch_mutate by_filter requires a non-empty filter object.",
                        {},
                    )
                if (target or {}).get("record_ids"):
                    raise OperatorError(
                        "validation_error",
                        "batch_mutate by_filter must not set record_ids; use by_ids instead.",
                        {},
                    )
                resolved_ids = await _resolve_record_ids_from_filter(
                    session, actor, model_for_filter, dict(filter_payload),
                )
                if not resolved_ids:
                    raise OperatorError(
                        "validation_error",
                        "batch_mutate by_filter resolved to zero records; refine the filter.",
                        {"filter": dict(filter_payload)},
                    )
                if len(resolved_ids) > 500:
                    raise OperatorError(
                        "validation_error",
                        "batch_mutate by_filter resolved to too many records (max 500).",
                        {"resolved_count": len(resolved_ids)},
                    )
                original_filter = dict(filter_payload)
                cleaned = dict(cleaned)
                cleaned["target"] = {
                    "mode": "by_ids",
                    "record_ids": [str(rid) for rid in sorted(resolved_ids)],
                }
            elif mode == "by_ids":
                if not (target or {}).get("record_ids"):
                    raise OperatorError(
                        "validation_error",
                        "batch_mutate by_ids requires non-empty record_ids.",
                        {},
                    )
        expected_versions = await collect_action_expected_versions(session, actor, spec, cleaned)
        expected_version = expected_versions_hash(expected_versions)
        risk = calculate_action_risk(spec, cleaned)
        # Phase 5: scope-based risk downgrade
        if action == "batch_mutate":
            target = (cleaned or {}).get("target") or {}
            record_ids_raw = target.get("record_ids") or []
            record_ids_set = set(str(rid) for rid in record_ids_raw)
            model_name = str((cleaned or {}).get("model") or "")
            op = str((cleaned or {}).get("operation") or "")
            from app.operator.guards import downgrade_risk_with_scope
            risk = await downgrade_risk_with_scope(
                session, actor.session_id, actor.actor_id, model_name,
                record_ids_set, op, risk,
            )
        if not _action_is_implemented(spec):
            await _audit_unimplemented_action(
                session,
                actor,
                action=action,
                input_payload=cleaned,
                risk_level=risk,
                user_message=user_message,
                reason=str(spec.non_operable_reason or "Action is not implemented."),
            )
            if not _defer_commit:
                await session.commit()
            return _not_implemented_response(spec)
        locked_payload = {
            "tool_name": "invoke_action",
            "operation_type": "action",
            "action": action,
            "input": cleaned,
            "expected_versions": expected_versions,
            "expected_version_or_hash": expected_version,
        }
        if action == "batch_mutate":
            target = (cleaned or {}).get("target") or {}
            locked_payload["expected_count"] = len(target.get("record_ids") or [])
            if original_filter:
                locked_payload["original_filter"] = original_filter
        if spec.confirmation_required or risk >= 3 or _force_proposal:
            if action == "generate_resume":
                readiness = await skill_runtime.verify_resume_generate_readiness(session, actor)
                if not readiness.get("ok"):
                    raise OperatorError(
                        "validation_error",
                        str(readiness.get("message") or "generate_resume requires Harness Skill Runtime readiness."),
                        {"action": action, "readiness": json_safe(readiness)},
                    )
            proposal = await shape_proposal(
                session,
                actor,
                tool_name="invoke_action",
                operation_type="action",
                model_or_action=action,
                risk_level=risk,
                locked_payload=locked_payload,
                user_message=user_message,
                affected_records=[{"action": action, "result_model": spec.result_model}],
                before=None,
                after={"action": action, "input": cleaned, "expected_status": "pending_confirmation"},
                reason="This action may create, change, queue, or spend resources and requires confirmation.",
                summary=_action_proposal_summary(action, cleaned),
                commit=not _defer_commit,
            )
            return {"ok": True, "status": "proposal_required", "proposal": proposal}
        direct_invoke_actions = {
            "auto_write_application_content",
            "resolve_resume_logo",
            "export_resume_pdf",
            "export_resume_image",
            "analyze_resume",
            "job_stats",
            "smartfill_map",
            "smartfill_option_match",
            "smartfill_field_map",
            "smartfill_module_count",
            # WP5: durable memory write is low-risk and executes directly after
            # every write_memory_candidate guard; it never bypasses the
            # category whitelist, business-fact rejection, sensitive-content
            # confirmation, scope, or redaction.
            "remember_preference",
        }
        if action in direct_invoke_actions:
            from app.operator.proposals import _prepare_invoke_action

            direct_proposal = SimpleNamespace(
                proposal_id=f"direct:{action}",
                model_or_action=action,
                expected_version_or_hash=expected_version,
                risk_level=risk,
                confirmations_required=RISK_CONFIRMATIONS.get(int(risk), 0),
                requires_second_confirmation=int(risk) >= 5,
                locked_payload=locked_payload,
            )
            execution = await _prepare_invoke_action(session, actor, direct_proposal, locked_payload)
            result = await execution()
            result = attach_visibility(result, action=action)
            await _audit_completed_action_boundary(
                session,
                actor,
                action=action,
                input_payload=cleaned,
                risk_level=risk,
                user_message=user_message,
                result_status=str(result.get("status") or "completed"),
                result_summary=str(result.get("summary") or "Action completed."),
            )
            if not _defer_commit:
                await session.commit()
            return attach_visibility({
                "ok": True,
                "status": str(result.get("status") or "completed"),
                "tool_name": "invoke_action",
                "action": action,
                "risk_level": risk,
                "confirmation_required": False,
                "result": result,
            }, action=action)
        if action == "optimize_agent_chat":
            compatibility_status = "legacy_compatibility_continues"
            result_summary = (
                "Optimize agent chat crossed the operator compatibility boundary; "
                "legacy chat may continue, but no official operator work was completed."
            )
            await _audit_completed_action_boundary(
                session,
                actor,
                action=action,
                input_payload=cleaned,
                risk_level=risk,
                user_message=user_message,
                confirmation_status="legacy_compatibility",
                result_status=compatibility_status,
                result_summary=result_summary,
            )
            if not _defer_commit:
                await session.commit()
            return {
                "ok": True,
                "status": compatibility_status,
                "tool_name": "invoke_action",
                "action": action,
                "risk_level": risk,
                "confirmation_required": False,
                "official_work_completed": False,
                "legacy_compatibility_allowed": True,
                "result": {
                    "status": compatibility_status,
                    "summary": result_summary,
                    "official_work_completed": False,
                    "legacy_compatibility_allowed": True,
                },
            }
        return _not_implemented_response(spec)
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive boundary for adapters.
        await _rollback_quietly(session)
        return transient_error("Operator action failed transiently.", {"error": str(exc)})


def _action_proposal_summary(action: str, input_payload: Mapping[str, Any]) -> str:
    if action == "generate_resume":
        title = str(input_payload.get("title") or "").strip()
        if title:
            return f"生成定制简历：{title}"
        job_id = str(input_payload.get("job_id") or "").strip()
        return f"生成岗位定制简历{f'（job_id={job_id}）' if job_id else ''}"
    if action == "organize_jobs_into_pool":
        pool_name = str(input_payload.get("pool_name") or "").strip()
        job_ids = input_payload.get("job_ids")
        count = len(job_ids) if isinstance(job_ids, list) else 0
        target = f"到「{pool_name}」" if pool_name else "到目标岗位池"
        return f"整理岗位入池：{count} 个岗位{target}"
    if action == "import_jobs_to_application_table":
        job_ids = input_payload.get("job_ids")
        count = len(job_ids) if isinstance(job_ids, list) else 0
        table_id = str(input_payload.get("table_id") or "").strip()
        target = f"投递表（table_id={table_id}）" if table_id else "投递表"
        return f"导入投递表：{count} 个岗位到{target}"
    if action == "interview_extract_questions":
        experience_id = str(input_payload.get("experience_id") or "").strip()
        return f"提炼面试题：面经 {experience_id}" if experience_id else "提炼面试题"
    if action == "batch_mutate":
        return _batch_mutate_proposal_summary(input_payload)
    return ""


def _create_record_proposal_summary(model: str, data: Mapping[str, Any]) -> str:
    if model == "job":
        company = str(data.get("company") or "").strip()
        title = str(data.get("title") or "").strip()
        label = " - ".join(part for part in (company, title) if part)
        return f"创建岗位：{label}" if label else "创建岗位"
    if model == "pool":
        name = str(data.get("name") or "").strip()
        return f"创建岗位池：{name}" if name else "创建岗位池"
    if model == "interview_experience":
        company = str(data.get("company") or "").strip()
        role = str(data.get("role") or data.get("job_title") or "").strip()
        label = " - ".join(part for part in (company, role) if part)
        return f"创建面经：{label}" if label else "创建面经"
    if model == "profile_section":
        title = str(data.get("title") or "").strip()
        return f"创建档案内容：{title}" if title else "创建档案内容"
    return f"创建{model}"


def _patch_record_proposal_summary(model: str, before: Mapping[str, Any], record_id: Any) -> str:
    if model == "profile_section":
        title = str(before.get("title") or "").strip()
        return f"修改档案条目：{title}" if title else f"修改档案条目（id={record_id}）"
    if model == "application_record":
        company = str(before.get("company_name") or "").strip()
        title = str(before.get("job_title") or "").strip()
        label = " - ".join(part for part in (company, title) if part)
        return f"修改投递记录：{label}" if label else f"修改投递记录（id={record_id}）"
    return f"修改记录：{model}#{record_id}"


def _delete_or_archive_proposal_summary(model: str, before: Mapping[str, Any], operation: str, record_id: Any) -> str:
    operation_label = {
        "delete": "删除",
        "archive": "归档",
        "restore": "恢复",
        "detach": "移除关联",
        "remove_from_collection": "移出集合",
    }.get(operation, operation or "处理")
    if model == "job":
        company = str(before.get("company") or "").strip()
        title = str(before.get("title") or "").strip()
        label = " - ".join(part for part in (company, title) if part)
        return f"{operation_label}岗位：{label}" if label else f"{operation_label}岗位（id={record_id}）"
    return f"{operation_label}记录：{model}#{record_id}"


def _batch_mutate_proposal_summary(input_payload: Mapping[str, Any]) -> str:
    operation = str(input_payload.get("operation") or "").strip().lower()
    model = str(input_payload.get("model") or "").strip()
    target = input_payload.get("target")
    record_ids: list[Any] = []
    if isinstance(target, Mapping):
        raw_ids = target.get("record_ids")
        if isinstance(raw_ids, list):
            record_ids = raw_ids
    count = len(record_ids)
    model_label = _batch_mutate_model_label(model)
    operation_label = {
        "patch": "更新",
        "delete": "删除",
        "archive": "归档",
        "restore": "恢复",
    }.get(operation, "批量处理")
    detail = _batch_mutate_patch_detail(input_payload) if operation == "patch" else ""
    count_part = f"{count} 条" if count else "选中的"
    suffix = f"：{detail}" if detail else ""
    return f"批量{operation_label}{model_label}：{count_part}{suffix}"


def _batch_mutate_model_label(model: str) -> str:
    return {
        "application_record": "投递记录",
        "job": "岗位",
        "pool": "岗位池",
        "profile_section": "档案内容",
        "resume": "简历",
    }.get(model, model or "记录")


def _batch_mutate_patch_detail(input_payload: Mapping[str, Any]) -> str:
    statuses: list[str] = []
    for update in _batch_mutate_update_objects(input_payload):
        status = _extract_application_status_from_update(update)
        if status and status not in statuses:
            statuses.append(status)
    if statuses:
        joined = "、".join(statuses[:4])
        return f"投递状态改为{joined}"
    updates = input_payload.get("updates")
    if isinstance(updates, Mapping) and updates:
        keys = "、".join(str(key) for key in list(updates)[:4])
        return f"更新字段：{keys}"
    return ""


def _batch_mutate_update_objects(input_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    updates: list[Mapping[str, Any]] = []
    base_updates = input_payload.get("updates")
    if isinstance(base_updates, Mapping):
        updates.append(base_updates)
    per_record = input_payload.get("per_record_updates")
    if isinstance(per_record, Mapping):
        for value in per_record.values():
            if isinstance(value, Mapping):
                updates.append(value)
    elif isinstance(per_record, list):
        for item in per_record:
            if isinstance(item, Mapping):
                nested = item.get("updates")
                updates.append(nested if isinstance(nested, Mapping) else item)
    return updates


def _extract_application_status_from_update(update: Mapping[str, Any]) -> str:
    direct = str(update.get("apply_status") or "").strip()
    if direct:
        return direct
    custom_values = update.get("custom_values")
    if isinstance(custom_values, Mapping):
        return str(custom_values.get("apply_status") or "").strip()
    return ""


async def manage_session(
    session: AsyncSession,
    actor: ActorContext,
    operation: str,
    updates: Mapping[str, Any],
    _defer_commit: bool = False,
) -> dict[str, Any]:
    try:
        if not isinstance(updates, Mapping):
            raise OperatorError("validation_error", "Session updates must be an object.", {"operation": operation})
        response = await update_session_state(
            session,
            actor,
            operation,
            updates,
            _defer_commit=_defer_commit,
        )
        if response.get("ok") is True:
            response["risk_level"] = 1
        return response
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive boundary for adapters.
        await _rollback_quietly(session)
        return transient_error("Operator session update failed transiently.", {"error": str(exc)})


def _operator_error_response(exc: OperatorError) -> dict[str, Any]:
    if exc.code == "not_implemented":
        return {
            "ok": False,
            "error": {
                "code": "not_implemented",
                "message": exc.message,
                "details": json_safe(exc.details or {}),
            },
        }
    if exc.code == "validation_error":
        return validation_error(exc.message, exc.details)
    if exc.code == "permission_error":
        return permission_error(exc.message, exc.details)
    if exc.code == "not_found_error":
        return not_found_error(exc.message, exc.details)
    if exc.code == "conflict_error":
        return conflict_error(exc.message, exc.details)
    if exc.code == "transient_error":
        return transient_error(exc.message, exc.details)
    return validation_error(exc.message, exc.details)


def _augment_query_record_summary(model: str, record: Any, summary: dict[str, Any]) -> dict[str, Any]:
    if model == "profile_section":
        preview = _compact_mapping_preview(getattr(record, "content_json", None))
        if preview:
            return {**summary, "content_preview": preview}
        return summary
    if model == "application_record":
        custom_values = getattr(record, "custom_values", None)
        if not isinstance(custom_values, Mapping):
            return summary
        augmented = dict(summary)
        apply_status = _string_preview(custom_values.get("apply_status"))
        if apply_status:
            augmented["apply_status"] = apply_status
        preview = _string_preview(custom_values)
        if len(preview) > 240:
            preview = preview[:240].rstrip() + "..."
        if preview:
            augmented["custom_values_preview"] = preview
        return augmented
    return summary


def _compact_mapping_preview(content: Any, *, limit: int = 240) -> str:
    if isinstance(content, Mapping):
        candidates: list[Any] = [
            content.get("bullet"),
            content.get("description"),
            content.get("summary"),
        ]
        normalized = content.get("normalized")
        if isinstance(normalized, Mapping):
            candidates.extend([normalized.get("bullet"), normalized.get("description"), normalized.get("summary")])
        field_values = content.get("field_values")
        if isinstance(field_values, Mapping):
            candidates.extend(field_values.values())
        bullets = content.get("bullets")
        if isinstance(bullets, list):
            candidates.extend(bullets[:3])
        text = " | ".join(part for part in (_string_preview(value) for value in candidates) if part)
    else:
        text = _string_preview(content)
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _string_preview(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, (list, tuple)):
        return " ".join(part for part in (_string_preview(item) for item in value) if part)
    if isinstance(value, Mapping):
        return " ".join(part for part in (_string_preview(item) for item in value.values()) if part)
    return str(value)


def _action_is_implemented(spec: Any) -> bool:
    return str(getattr(spec, "implementation_status", "")) == "implemented"


def _not_implemented_response(spec: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "not_implemented",
            "message": f"Operator action is not implemented: {spec.action}",
            "details": {
                "action": spec.action,
                "implementation_status": str(getattr(spec, "implementation_status", "not_implemented")),
                "reason": str(getattr(spec, "non_operable_reason", "") or "Action is not implemented."),
            },
        },
    }


async def _audit_unimplemented_action(
    session: AsyncSession,
    actor: ActorContext,
    *,
    action: str,
    input_payload: Mapping[str, Any],
    risk_level: int,
    user_message: str,
    reason: str,
) -> None:
    args_snapshot = {"action": action, "input": json_safe(input_payload)}
    await log_agent_audit(
        session,
        actor=actor,
        tool_name="invoke_action",
        args_snapshot=args_snapshot,
        args_redacted=redact_audit_args(args_snapshot),
        risk_level=risk_level,
        confirmation_status="not_implemented",
        result_status="not_implemented",
        result_summary=reason,
        user_message=user_message,
        error=reason,
    )


async def _audit_completed_action_boundary(
    session: AsyncSession,
    actor: ActorContext,
    *,
    action: str,
    input_payload: Mapping[str, Any],
    risk_level: int,
    user_message: str,
    result_summary: str,
    confirmation_status: str = "not_required",
    result_status: str = "completed",
) -> None:
    args_snapshot = {"action": action, "input": json_safe(input_payload)}
    await log_agent_audit(
        session,
        actor=actor,
        tool_name="invoke_action",
        args_snapshot=args_snapshot,
        args_redacted=redact_audit_args(args_snapshot),
        risk_level=risk_level,
        confirmation_status=confirmation_status,
        result_status=result_status,
        result_summary=result_summary,
        user_message=user_message,
    )


def _normalize_action_input_for_proposal(action: str, input_payload: Mapping[str, Any]) -> dict[str, Any]:
    if action == "organize_jobs_into_pool":
        return _normalize_organize_jobs_into_pool_input(input_payload)
    if action == "batch_mutate":
        return _normalize_batch_mutate_input_for_proposal(input_payload)
    return {str(key): json_safe(value) for key, value in input_payload.items()}


def _normalize_batch_mutate_input_for_proposal(input_payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {str(key): json_safe(value) for key, value in input_payload.items()}
    if str(normalized.get("model") or "") != "application_record" or str(normalized.get("operation") or "") != "patch":
        return normalized
    updates = normalized.get("updates")
    if isinstance(updates, Mapping):
        normalized["updates"] = _normalize_application_record_status_update(updates)
    per_record = normalized.get("per_record_updates")
    if isinstance(per_record, Mapping):
        normalized["per_record_updates"] = {
            str(record_id): _normalize_application_record_status_update(record_updates)
            if isinstance(record_updates, Mapping)
            else json_safe(record_updates)
            for record_id, record_updates in per_record.items()
        }
    elif isinstance(per_record, list):
        normalized["per_record_updates"] = [
            {
                **dict(item),
                "updates": _normalize_application_record_status_update(
                    item.get("updates"),
                )
                if isinstance(item, Mapping) and isinstance(item.get("updates"), Mapping)
                else item.get("updates") if isinstance(item, Mapping) else None,
            }
            if isinstance(item, Mapping)
            else json_safe(item)
            for item in per_record
        ]
    return normalized


def _normalize_application_record_status_update(value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize ``apply_status`` through the single ApplicationLifecycleSpec
    authority: unknown values fail closed (validation_error), granular
    interview markers resolve to the interview stage (round text preserved
    under ``interview_round``), and the canonical value stays top-level so the
    FieldSpec enum and the durable ``apply_status`` column write the same
    value."""
    try:
        return normalize_apply_status_update(value)
    except ApplicationLifecycleError as exc:
        raise OperatorError(
            "validation_error",
            str(exc),
            {"field": "apply_status"},
        ) from exc


def _derive_backend_create_fields(model: str, data: Mapping[str, Any], actor: ActorContext) -> dict[str, Any]:
    cleaned = {str(key): json_safe(value) for key, value in data.items()}
    cleaned = _normalize_model_record_payload(model, cleaned)
    if model == "profile_section":
        cleaned = normalize_profile_section_record_payload(cleaned)
    if model == "job" and not str(cleaned.get("hash_key") or "").strip():
        seed = {
            "actor_id": actor.actor_id,
            "title": cleaned.get("title"),
            "company": cleaned.get("company"),
            "location": cleaned.get("location"),
            "url": cleaned.get("url"),
            "apply_url": cleaned.get("apply_url"),
            "source": cleaned.get("source"),
        }
        encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        cleaned["hash_key"] = "operator-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:40]
    return cleaned


def _normalize_model_record_payload(model: str, data: Mapping[str, Any]) -> dict[str, Any]:
    if model == "job":
        return normalize_job_record_payload(data)
    return {str(key): value for key, value in data.items()}


def _normalize_organize_jobs_into_pool_input(input_payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_job_ids = input_payload.get("job_ids")
    if not isinstance(raw_job_ids, list) or not raw_job_ids:
        raise OperatorError("validation_error", "organize_jobs_into_pool requires at least one job id.", {})
    job_ids = _canonical_positive_int_ids(raw_job_ids, field_name="job_ids")
    pool_name = _normalize_required_text(input_payload.get("pool_name"), field_name="pool_name", max_length=100)
    pool_scope = _normalize_pool_scope(input_payload.get("pool_scope") or "picked")
    triage_status = _normalize_triage_status(input_payload.get("triage_status") or pool_scope)
    if not triage_status:
        triage_status = pool_scope
    if triage_status != pool_scope:
        raise OperatorError(
            "validation_error",
            "Pool scope and job triage status must match for a pooled batch move.",
            {"pool_scope": pool_scope, "triage_status": triage_status},
        )
    pool_description = _normalize_optional_text(input_payload.get("pool_description"), max_length=1000)
    _normalize_bool(input_payload.get("reuse_existing"), default=True)
    # This action is the canonical create-or-reuse compound job organization
    # transaction. A model-supplied false value would turn a valid semantic move
    # into a pool-name conflict and leave jobs unmoved.
    reuse_existing = True

    validate_model_values({"triage_status": triage_status}, get_model_spec("job"), purpose="organize jobs into pool")
    validate_model_values({"scope": pool_scope}, get_model_spec("pool"), purpose="organize jobs into pool")
    return {
        **{str(key): json_safe(value) for key, value in input_payload.items()},
        "job_ids": job_ids,
        "pool_name": pool_name,
        "pool_scope": pool_scope,
        "pool_description": pool_description,
        "triage_status": triage_status,
        "reuse_existing": reuse_existing,
    }


def _canonical_positive_int_ids(values: list[Any], *, field_name: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            record_id = int(value)
        except (TypeError, ValueError) as exc:
            raise OperatorError("validation_error", "Record ids must be integers.", {"field": field_name}) from exc
        if record_id <= 0:
            raise OperatorError("validation_error", "Record ids must be positive integers.", {"field": field_name})
        if record_id not in seen:
            ids.append(record_id)
            seen.add(record_id)
    if not ids:
        raise OperatorError("validation_error", "At least one record id is required.", {"field": field_name})
    return ids


def _normalize_triage_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    aliases = {
        "unscreened": "inbox",
        "inbox": "inbox",
        "screened": "picked",
        "selected": "picked",
        "picked": "picked",
        "ignored": "ignored",
    }
    if not status:
        return ""
    normalized = aliases.get(status, status)
    if normalized not in {"inbox", "picked", "ignored"}:
        raise OperatorError("validation_error", "Invalid triage status.", {"triage_status": value})
    return normalized


def _normalize_pool_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    aliases = {
        "screened": "picked",
        "selected": "picked",
        "saved": "picked",
        "unscreened": "inbox",
        "trash": "ignored",
    }
    normalized = aliases.get(scope, scope)
    if normalized not in {"inbox", "picked", "ignored"}:
        raise OperatorError("validation_error", "Invalid pool scope.", {"pool_scope": value})
    return normalized


def _normalize_required_text(value: Any, *, field_name: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise OperatorError("validation_error", "Required text value is missing.", {"field": field_name})
    if len(text) > max_length:
        raise OperatorError(
            "validation_error",
            "Text value is too long.",
            {"field": field_name, "max_length": max_length, "length": len(text)},
        )
    return text


def _normalize_optional_text(value: Any, *, max_length: int) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if len(text) > max_length:
        raise OperatorError(
            "validation_error",
            "Text value is too long.",
            {"max_length": max_length, "length": len(text)},
        )
    return text


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise OperatorError("validation_error", "Boolean value is invalid.", {"value": json_safe(value)})


def _validate_pagination(page: int, page_size: int) -> tuple[int, int]:
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError) as exc:
        raise OperatorError("validation_error", "Pagination values must be integers.", {}) from exc
    if page < 1:
        raise OperatorError("validation_error", "Page must be at least 1.", {"page": page})
    if page_size < 1 or page_size > 100:
        raise OperatorError("validation_error", "Page size must be between 1 and 100.", {"page_size": page_size})
    return page, page_size


def _record_image_from_fields(data: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: json_safe(data[field]) for field in fields if field in data}


def _apply_direct_patch(record: Any, updates: Mapping[str, Any], patch_mode: str) -> None:
    for field, value in updates.items():
        current = getattr(record, field, None)
        if patch_mode in {"replace", "rewrite"}:
            setattr(record, field, value)
        elif patch_mode == "append":
            if current in (None, ""):
                setattr(record, field, value)
            elif isinstance(current, list):
                setattr(record, field, [*current, *value] if isinstance(value, list) else [*current, value])
            elif isinstance(current, str):
                setattr(record, field, f"{current}{value}")
            else:
                raise OperatorError("validation_error", "Append mode only supports text and array fields.", {"field": field})
        elif patch_mode == "merge":
            if not isinstance(current, Mapping) or not isinstance(value, Mapping):
                raise OperatorError("validation_error", "Merge mode only supports object fields.", {"field": field})
            setattr(record, field, {**current, **value})


def _is_unresolved_output_reference(value: Any) -> bool:
    """True when ``value`` is a ``$output`` typed-output placeholder.

    Such placeholders are only resolved at Plan materialization time, so any
    check that depends on the resolved value (FieldSpec types, merge-mode
    object semantics against the current record image, reference lookups)
    must be deferred for them; rejecting them here would break legitimate
    same-Plan dependency chains.
    """
    return isinstance(value, Mapping) and isinstance(value.get("$output"), Mapping)


def _contains_unresolved_output_reference(value: Any) -> bool:
    if _is_unresolved_output_reference(value):
        return True
    if isinstance(value, Mapping):
        return any(_contains_unresolved_output_reference(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_unresolved_output_reference(child) for child in value)
    return False


def _validate_staged_action_schema(spec: Any, input_payload: Mapping[str, Any]) -> None:
    """Mirror of ``guards.validate_action_schema`` for the staging gate.

    Runs before a write intent is staged. The only intentional difference is
    that enum checks are skipped for fields that currently hold an unresolved
    ``$output`` placeholder (their value is only knowable at materialization).
    Reference normalization and per-action proposal normalization are NOT part
    of this gate; they need database state or ``$output`` resolution.
    """
    reject_trusted_args(input_payload, location=f"action:{spec.action}.input")
    schema = spec.input_schema
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise OperatorError("validation_error", "Action input schema is invalid.", {"action": spec.action})
    disallowed = sorted(set(input_payload) - set(properties))
    if disallowed:
        raise OperatorError(
            "validation_error",
            "Action input contains fields outside the exact operation contract: %s."
            % ", ".join(disallowed),
            {
                "action": spec.action,
                "fields": disallowed,
                "allowed_fields": sorted(str(key) for key in properties),
                "backend_owned_hint": sorted(field for field in disallowed if field in BACKEND_OWNED_ACTION_INPUT_FIELDS),
            },
        )
    required = schema.get("required", ())
    missing = [field for field in required if field not in input_payload]
    if missing:
        raise OperatorError("validation_error", "Action input is missing required fields.", {"fields": missing})
    for field_name, schema_value in properties.items():
        field = str(field_name)
        if field not in input_payload or not isinstance(schema_value, Mapping):
            continue
        if _is_unresolved_output_reference(input_payload[field]):
            continue
        enum_values = schema_value.get("enum")
        if enum_values is not None and input_payload[field] not in (None, "") and json_safe(input_payload[field]) not in list(enum_values):
            raise OperatorError(
                "validation_error",
                "Action input value is outside the allowed enum.",
                {"action": spec.action, "field": field, "value": json_safe(input_payload[field]), "allowed_values": list(enum_values)},
            )


async def _validate_staged_patch_payload(
    session: AsyncSession,
    actor: ActorContext,
    args: Mapping[str, Any],
) -> None:
    """Fail-fast staging gate for ``patch_record``.

    Runs the same checks as the real ``patch_record`` execution path
    (patch_mode whitelist, updates shape, trusted-arg rejection, field
    whitelist, FieldSpec value types, and - when the target record exists and
    its identity is not a ``$output`` placeholder - the mode-specific record
    image semantics such as merge-mode object fields) so that invalid payloads
    fail at call time instead of aborting the whole turn at Plan
    materialization. It never mutates anything.
    """
    model = str(args.get("model") or "")
    patch_mode = str(args.get("patch_mode") or "replace")
    if patch_mode not in PATCH_MODES:
        raise OperatorError("validation_error", "Unsupported patch mode.", {"patch_mode": patch_mode})
    updates = args.get("updates")
    if not isinstance(updates, Mapping):
        raise OperatorError("validation_error", "Patch updates must be an object.", {"model": model})
    cleaned_updates = {str(key): json_safe(value) for key, value in updates.items()}
    cleaned_updates = _normalize_model_record_payload(model, cleaned_updates)
    if model == "application_record":
        cleaned_updates = _normalize_application_record_status_update(cleaned_updates)
    reject_trusted_args(cleaned_updates, location=f"patch_record:{model}.updates")
    spec = get_model_spec(model)
    model_cls = get_model_class(model)
    validate_fields(cleaned_updates, spec.writable_fields, purpose=f"patch {model}")
    validate_model_values(
        {str(key): value for key, value in cleaned_updates.items() if not _is_unresolved_output_reference(value)},
        spec,
        purpose=f"patch {model}",
    )
    record_id = args.get("record_id")
    if _is_unresolved_output_reference(record_id):
        return
    record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
    before = serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False)
    apply_patch_to_record_image(before, cleaned_updates, patch_mode)


def _validate_staged_create_payload(args: Mapping[str, Any]) -> None:
    """Fail-fast staging gate for ``create_record``.

    Runs the database-independent part of the real ``create_record`` execution
    (data shape, model registry, trusted-arg rejection, payload normalization,
    creatable-field whitelist, FieldSpec value types). The ownership-scope
    relation lookups and the duplicate-job existence check are deferred to the
    real execution path: they are existence checks against database state that
    can legitimately reference ``$output`` placeholders in a chained Plan.
    """
    model = str(args.get("model") or "")
    data = args.get("data")
    if not isinstance(data, Mapping):
        raise OperatorError("validation_error", "Create data must be an object.", {"model": model})
    spec = get_model_spec(model)
    reject_trusted_args(data, location=f"create_record:{model}.data")
    cleaned = {str(key): json_safe(value) for key, value in data.items()}
    cleaned = _normalize_model_record_payload(model, cleaned)
    if model == "application_record":
        cleaned = _normalize_application_record_status_update(cleaned)
    validate_fields(cleaned, spec.creatable_fields, purpose=f"create {model}")
    validate_model_values(
        {str(key): value for key, value in cleaned.items() if not _is_unresolved_output_reference(value)},
        spec,
        purpose=f"create {model}",
    )


async def _validate_staged_delete_payload(
    session: AsyncSession,
    actor: ActorContext,
    args: Mapping[str, Any],
) -> None:
    """Fail-fast staging gate for ``delete_or_archive_record``.

    Runs the same checks as the real execution path up to the scoped record
    fetch. The fetch is deferred when ``record_id`` is an unresolved
    ``$output`` placeholder.
    """
    model = str(args.get("model") or "")
    operation = str(args.get("operation") or "")
    if operation not in DELETE_OPERATIONS:
        raise OperatorError("validation_error", "Unsupported delete/archive operation.", {"operation": operation})
    if model == "agent_conversation":
        raise OperatorError(
            "not_implemented",
            "Agent conversation lifecycle changes require a durable post-confirmation filesystem job.",
            {"model": "agent_conversation", "operation": operation},
        )
    spec = get_model_spec(model)
    model_cls = get_model_class(model)
    record_id = args.get("record_id")
    if _is_unresolved_output_reference(record_id):
        return
    await fetch_scoped_record(session, actor, spec, model_cls, record_id)


def _validate_staged_invoke_action_payload(
    args: Mapping[str, Any],
    *,
    backend_confirmed_scope: Mapping[str, Any] | None = None,
) -> None:
    """Fail-fast staging gate for ``invoke_action``.

    Runs the database-independent part of the real ``invoke_action``
    execution: input shape, action registry, backend-owned ``confirmed_scope``
    handling for ``generate_resume``, and the exact operation schema
    (trusted-arg rejection, field whitelist, required fields, enum values
    with ``$output`` deferral). Reference normalization (database reads) and
    per-action proposal normalization that depends on unresolved ``$output``
    values stay on the real execution path.
    """
    input_payload = args.get("input")
    if not isinstance(input_payload, Mapping):
        raise OperatorError("validation_error", "Action input must be an object.", {"action": str(args.get("action") or "")})
    action = str(args.get("action") or "")
    spec = get_action_spec(action)
    if action == "generate_resume":
        supplied_scope = backend_confirmed_scope if isinstance(backend_confirmed_scope, Mapping) else None
        provider_scope = input_payload.get("confirmed_scope")
        if provider_scope is not None:
            if supplied_scope is None or json_safe(provider_scope) != json_safe(supplied_scope):
                raise OperatorError(
                    "validation_error",
                    "Action input contains the backend-owned field confirmed_scope.",
                    {"action": action},
                )
            input_payload = {str(key): value for key, value in input_payload.items() if key != "confirmed_scope"}
        if supplied_scope is None or not supplied_scope:
            raise OperatorError(
                "validation_error",
                "generate_resume requires backend-confirmed generation scope from trusted evidence.",
                {"action": action},
            )
    _validate_staged_action_schema(spec, input_payload)
    if not _contains_unresolved_output_reference(input_payload):
        _normalize_action_input_for_proposal(action, input_payload)


async def validate_staged_write_payload(
    session: AsyncSession,
    actor: ActorContext,
    tool_name: str,
    args: Mapping[str, Any],
    *,
    backend_confirmed_scope: Mapping[str, Any] | None = None,
) -> None:
    """Call-time validation gate for plan-staged write tools.

    Under PLAN_STAGING the executor short-circuits write tools into synthetic
    ``intent_staged`` results; without this gate, every field-level check that
    exists on the real execution path (whitelists, value types, merge-mode
    object semantics, action schemas) is skipped and invalid payloads only
    fail at Plan materialization, aborting the whole turn as
    ``agent_turn_failed``. This gate runs the same validation primitives the
    real tools use, raises ``OperatorError`` on the first violation, and never
    writes, stages, or audits anything.
    """
    if tool_name == "patch_record":
        await _validate_staged_patch_payload(session, actor, args)
    elif tool_name == "create_record":
        _validate_staged_create_payload(args)
        # Ownership-scope and duplicate existence checks are cheap database
        # reads that stay close to the real execution semantics; they are only
        # skipped when the payload carries unresolved $output placeholders.
        if not _contains_unresolved_output_reference(args.get("data") or {}):
            model_value = str(args.get("model") or "")
            spec = get_model_spec(model_value)
            model_cls = get_model_class(model_value)
            cleaned = {str(key): json_safe(value) for key, value in dict(args.get("data") or {}).items()}
            cleaned = _normalize_model_record_payload(model_value, cleaned)
            if model_value == "application_record":
                cleaned = _normalize_application_record_status_update(cleaned)
            if spec.relations:
                await validate_create_scope(session, actor, spec, cleaned)
            if model_value == "job":
                await reject_duplicate_job_create_conflict(session, actor, cleaned)
    elif tool_name == "delete_or_archive_record":
        await _validate_staged_delete_payload(session, actor, args)
    elif tool_name == "invoke_action":
        _validate_staged_invoke_action_payload(args, backend_confirmed_scope=backend_confirmed_scope)
    else:
        raise OperatorError("validation_error", "Tool cannot stage a write payload.", {"tool_name": tool_name})


def _delete_after_image(before: Mapping[str, Any], operation: str) -> dict[str, Any]:
    after = dict(before)
    if operation == "delete":
        after["_deleted"] = True
    elif operation == "archive":
        after["archived"] = True
    elif operation == "restore":
        after["archived"] = False
    else:
        after["_operation"] = operation
    return after


async def _sync_profile_section_archive(session: AsyncSession, model: str, record: Any) -> None:
    if str(model) != "profile_section":
        return
    profile_id = getattr(record, "profile_id", None)
    if profile_id in (None, ""):
        return
    profile = await session.get(models.Profile, profile_id)
    if profile is None:
        return
    sync_profile_section_to_personal_archive(profile, record)


async def _remove_profile_section_archive(session: AsyncSession, model: str, record: Any) -> None:
    if str(model) != "profile_section":
        return
    profile_id = getattr(record, "profile_id", None)
    if profile_id in (None, ""):
        return
    profile = await session.get(models.Profile, profile_id)
    if profile is None:
        return
    remove_profile_section_from_personal_archive(profile, record)


def _apply_visibility_operation(record: Any, operation: str) -> str:
    if operation == "archive":
        if hasattr(record, "archived"):
            record.archived = True
            return "archived"
        if hasattr(record, "is_archived"):
            record.is_archived = True
            return "archived"
        if hasattr(record, "status"):
            record.status = "archived"
            return "archived"
    if operation == "restore":
        if hasattr(record, "archived"):
            record.archived = False
            return "restored"
        if hasattr(record, "is_archived"):
            record.is_archived = False
            return "restored"
        if hasattr(record, "status"):
            record.status = "active"
            return "restored"
    return "completed_noop"


async def _rollback_quietly(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        pass



