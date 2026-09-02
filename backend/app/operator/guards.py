from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence, Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import JSON, Integer, String, and_, cast, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import models
from app.operator.audit import log_agent_audit, redact_audit_args
from app.operator.errors import OperatorError
from app.operator.registry import (
    ACTION_REGISTRY,
    BACKEND_OWNED_ACTION_INPUT_FIELDS,
    MODEL_REGISTRY,
    ActionSpec,
    ModelSpec,
)


TRUSTED_ARG_NAMES = {
    "actor_id",
    "adapter",
    "scopes",
    "auth_subject",
    "identity",
    "identity_scope",
    "operator_session_id",
    "principal",
    "principal_id",
    "subject",
    "subject_id",
    "session",
    "session_id",
    "conversation_id",
    "user_id",
    "tenant",
    "tenant_id",
    "tenant_scope",
    "owner",
    "owner_id",
    "owner_actor",
    "owner_actor_id",
    "owner_user_id",
    "owner_subject",
    "ownership",
    "ownership_scope",
    "ownership_id",
    "scope_owner",
    "created_by",
    "created_by_id",
    "updated_by",
    "updated_by_id",
}
TRUSTED_CONTROL_ARG_NAMES = {
    "after",
    "before",
    "checkpoint_id",
    "confirmation_challenge",
    "locked_payload",
    "tool_name",
    "operation_type",
    "risk_level",
    "expected_version_or_hash",
    "expected_versions",
    "idempotency_key",
    "diff",
    "confirmation_count",
    "confirmation_events",
    "pending_proposal_ids",
    "requires_second_confirmation",
    "first_confirmed_at",
    "second_confirmed_at",
}
TRUSTED_CONFIRM_ARG_NAMES = TRUSTED_ARG_NAMES | TRUSTED_CONTROL_ARG_NAMES
PATCH_MODES = {"replace", "append", "merge", "rewrite"}
DELETE_OPERATIONS = {"archive", "restore", "detach", "remove_from_collection", "delete"}
RISK_CONFIRMATIONS: dict[int, int] = {0: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 2}
SESSION_OPERATIONS = {
    "activate_skill",
    "deactivate_skill",
    "set_context",
    "clear_context",
    "set_skill_step",
    "restore_checkpoint",
}
SESSION_UPDATE_FIELDS = {
    "active_skill",
    "current_step",
    "current_job_id",
    "current_resume_id",
    "current_profile_section_id",
    "current_application_id",
    "pending_proposal_ids",
    "checkpoint_id",
}
SESSION_SNAPSHOT_FIELDS = (
    "session_id",
    "actor_id",
    "adapter",
    "active_skill",
    "current_step",
    "current_job_id",
    "current_resume_id",
    "current_profile_section_id",
    "current_application_id",
    "pending_proposal_ids",
    "checkpoint_id",
)
#: Fields each session operation may actually process (mirrors session.py).
OPERATION_UPDATE_FIELDS = {
    "activate_skill": frozenset({"active_skill", "skill", "name", "current_step"}),
    "deactivate_skill": frozenset(),
    "set_context": frozenset({"current_job_id", "current_resume_id", "current_profile_section_id", "current_application_id"}),
    "clear_context": frozenset(),
    "set_skill_step": frozenset({"current_step"}),
    "restore_checkpoint": frozenset({"checkpoint_id"}),
}
OWNERSHIP_PARENT_FIELDS: dict[str, tuple[str, str]] = {
    "profile_owned": ("profile_id", "profile"),
    "resume_owned": ("resume_id", "resume"),
    "application_owned": ("application_id", "application"),
}
ACTION_REFERENCE_ALIASES = {
    "experience": "interview_experience",
    "experience_id": "interview_experience",
    "question": "interview_question",
    "question_id": "interview_question",
    "notification": "interview_notification",
    "notification_id": "interview_notification",
    "table": "application_table",
    "table_id": "application_table",
    "template": "resume_template",
}


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    session_id: str
    adapter: str = "web"
    auth_subject: str = ""
    scopes: tuple[str, ...] = ()


@dataclass
class ConfirmedIntentScope:
    """Records a recently confirmed intent so subsequent operations on the same
    records by the same actor can have their confirmation friction reduced.

    Phase 5: When a user confirms a proposal (e.g., batch patch 3 jobs to
    "picked"), and then immediately wants to do another operation on the same
    records (e.g., change them to "ignored"), this scope downgrades the risk
    of matching subsequent operations by 1 level.
    """

    actor_id: str
    model: str
    record_ids: frozenset[str]
    operation: str
    confirmed_at: datetime
    ttl_seconds: int = 120

    def is_expired(self) -> bool:
        elapsed = (datetime.utcnow() - self.confirmed_at).total_seconds()
        return elapsed > self.ttl_seconds

    def matches(self, actor_id: str, model: str, record_ids: set[str], operation: str) -> bool:
        if self.is_expired():
            return False
        if self.actor_id != actor_id:
            return False
        if self.model != model:
            return False
        if not record_ids:
            return False
        if not record_ids.issubset(self.record_ids):
            return False
        return self.operation == operation

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "model": self.model,
            "record_ids": sorted(self.record_ids),
            "operation": self.operation,
            "confirmed_at": self.confirmed_at.isoformat(),
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_jsonable(cls, raw: Mapping[str, Any]) -> "ConfirmedIntentScope":
        ids_raw = raw.get("record_ids") or []
        confirmed_at_raw = raw.get("confirmed_at")
        if isinstance(confirmed_at_raw, str):
            confirmed_at = datetime.fromisoformat(confirmed_at_raw)
        elif isinstance(confirmed_at_raw, datetime):
            confirmed_at = confirmed_at_raw
        else:
            confirmed_at = datetime.utcnow()
        return cls(
            actor_id=str(raw.get("actor_id") or ""),
            model=str(raw.get("model") or ""),
            record_ids=frozenset(str(rid) for rid in ids_raw),
            operation=str(raw.get("operation") or ""),
            confirmed_at=confirmed_at,
            ttl_seconds=int(raw.get("ttl_seconds") or 120),
        )


def _read_session_scopes(agent_session: Any) -> list[ConfirmedIntentScope]:
    raw = getattr(agent_session, "active_intent_scopes", None) if agent_session is not None else None
    if not raw:
        return []
    return [ConfirmedIntentScope.from_jsonable(item) for item in raw if isinstance(item, Mapping)]


async def get_active_scopes(db_session: AsyncSession, session_id: str) -> list[ConfirmedIntentScope]:
    """Return live (non-expired) scopes for the given session, persisting any pruning."""
    agent_session = await db_session.get(models.AgentSession, session_id)
    if agent_session is None:
        return []
    scopes = _read_session_scopes(agent_session)
    active = [s for s in scopes if not s.is_expired()]
    if len(active) != len(scopes):
        agent_session.active_intent_scopes = [s.to_jsonable() for s in active] if active else None
    return active


async def record_confirmed_scope(
    db_session: AsyncSession,
    session_id: str,
    scope: ConfirmedIntentScope,
) -> None:
    """Append a newly confirmed scope to the session's active scope list and persist."""
    agent_session = await db_session.get(models.AgentSession, session_id)
    if agent_session is None:
        return
    scopes = [s for s in _read_session_scopes(agent_session) if not s.is_expired()]
    scopes.append(scope)
    agent_session.active_intent_scopes = [s.to_jsonable() for s in scopes]


async def invalidate_scope(
    db_session: AsyncSession,
    session_id: str,
    model: str,
    record_ids: set[str],
) -> None:
    """Remove any active scope on this session that overlaps the given (model, record_ids)."""
    agent_session = await db_session.get(models.AgentSession, session_id)
    if agent_session is None:
        return
    scopes = _read_session_scopes(agent_session)
    remaining = [
        s for s in scopes
        if not (s.model == model and (s.record_ids & record_ids))
    ]
    agent_session.active_intent_scopes = [s.to_jsonable() for s in remaining] if remaining else None


async def downgrade_risk_with_scope(
    db_session: AsyncSession,
    session_id: str,
    actor_id: str,
    model: str,
    record_ids: set[str],
    operation: str,
    current_risk: int,
) -> int:
    """Downgrade risk by 1 level if a matching confirmed intent scope exists.

    Level 5 NEVER downgrades. Levels below 3 are not worth downgrading since
    they already don't require confirmation.
    """
    if current_risk >= 5 or current_risk < 3:
        return current_risk
    if not record_ids or not model:
        return current_risk
    for scope in await get_active_scopes(db_session, session_id):
        if scope.matches(actor_id, model, record_ids, operation):
            return max(1, current_risk - 1)
    return current_risk


MODEL_CLASSES: dict[str, type[Any]] = {
    "job": models.Job,
    "batch": models.Batch,
    "pool": models.Pool,
    "profile": models.Profile,
    "profile_target_role": models.ProfileTargetRole,
    "profile_section": models.ProfileSection,
    "resume_template": models.ResumeTemplate,
    "resume": models.Resume,
    "resume_section": models.ResumeSection,
    "interview_notification": models.InterviewNotification,
    "calendar_event": models.CalendarEvent,
    "application": models.Application,
    "application_workspace_settings": models.ApplicationWorkspaceSettings,
    "application_template": models.ApplicationTemplate,
    "application_table": models.ApplicationTable,
    "application_record": models.ApplicationRecord,
    "interview_experience": models.InterviewExperience,
    "interview_question": models.InterviewQuestion,
}


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(child) for child in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def get_model_spec(model_name: str) -> ModelSpec:
    spec = MODEL_REGISTRY.get(model_name)
    if spec is None or model_name not in MODEL_CLASSES:
        raise OperatorError("validation_error", f"Unknown operator model: {model_name}", {"model": model_name})
    return spec


def get_model_class(model_name: str) -> type[Any]:
    get_model_spec(model_name)
    return MODEL_CLASSES[model_name]


def get_action_spec(action_name: str) -> ActionSpec:
    spec = ACTION_REGISTRY.get(action_name)
    if spec is None:
        raise OperatorError("validation_error", f"Unknown operator action: {action_name}", {"action": action_name})
    return spec


def reject_trusted_args(
    payload: Any,
    *,
    location: str,
    extra_names: Iterable[str] = (),
    allow_top_level_names: Iterable[str] = (),
) -> None:
    trusted_names = TRUSTED_CONFIRM_ARG_NAMES | {str(name) for name in extra_names}
    violations = _trusted_arg_violations(
        payload,
        location=location,
        trusted_names=trusted_names,
        allow_top_level_names={str(name) for name in allow_top_level_names},
    )
    if violations:
        fields = sorted({violation["field"] for violation in violations})
        raise OperatorError(
            "validation_error",
            "Backend-owned actor, session, ownership, and control fields cannot be supplied to operator tools.",
            {
                "location": location,
                "fields": fields,
                "paths": [violation["path"] for violation in violations],
                "trusted_args": violations,
            },
        )


def _trusted_arg_violations(
    value: Any,
    *,
    location: str,
    trusted_names: set[str],
    allow_top_level_names: set[str],
) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    active_container_ids: set[int] = set()

    def walk(child: Any, path: str, depth: int) -> None:
        if isinstance(child, Mapping):
            container_id = id(child)
            if container_id in active_container_ids:
                return
            active_container_ids.add(container_id)
            try:
                for raw_key, nested in child.items():
                    key = str(raw_key)
                    child_path = f"{path}.{key}" if path else key
                    if key in trusted_names and not (depth == 0 and key in allow_top_level_names):
                        violations.append({"field": key, "path": child_path})
                    walk(nested, child_path, depth + 1)
            finally:
                active_container_ids.remove(container_id)
            return
        if isinstance(child, (Sequence, AbstractSet)) and not isinstance(child, (str, bytes, bytearray)):
            container_id = id(child)
            if container_id in active_container_ids:
                return
            active_container_ids.add(container_id)
            try:
                nested_items = list(child)
                if isinstance(child, AbstractSet):
                    nested_items = sorted(nested_items, key=repr)
                for index, nested in enumerate(nested_items):
                    walk(nested, f"{path}[{index}]", depth + 1)
            finally:
                active_container_ids.remove(container_id)

    walk(value, location, 0)
    return violations


def strip_trusted_args(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if str(key) not in TRUSTED_ARG_NAMES}


def strip_trusted_args_deep(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): strip_trusted_args_deep(child)
            for key, child in value.items()
            if str(key) not in TRUSTED_ARG_NAMES
        }
    if isinstance(value, (list, tuple, set)):
        return [strip_trusted_args_deep(child) for child in value]
    return value


def validate_fields(fields: Mapping[str, Any], allowed: Sequence[str], *, purpose: str) -> None:
    disallowed = sorted(set(fields) - set(allowed))
    if disallowed:
        raise OperatorError(
            "validation_error",
            f"Fields are not allowed for {purpose}.",
            {"fields": disallowed, "allowed_fields": list(allowed)},
        )


def validate_model_values(data: Mapping[str, Any], spec: ModelSpec, *, purpose: str) -> None:
    type_errors: list[dict[str, Any]] = []
    for field_name, value in data.items():
        field_spec = spec.fields.get(str(field_name))
        if field_spec is None:
            continue
        if value is None:
            if not field_spec.nullable:
                type_errors.append({"field": field_name, "expected": field_spec.data_type, "reason": "null_not_allowed"})
            continue
        expected = field_spec.data_type
        valid = (
            (expected == "boolean" and isinstance(value, bool))
            or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (expected in {"string", "datetime"} and isinstance(value, str))
            or (expected == "array" and isinstance(value, list))
            or (expected == "object" and isinstance(value, Mapping))
        )
        if not valid:
            type_errors.append({"field": field_name, "expected": expected, "actual": type(value).__name__})
            continue
        if field_spec.enum_values and value not in field_spec.enum_values:
            type_errors.append({"field": field_name, "expected": list(field_spec.enum_values), "actual": value})
    if type_errors:
        raise OperatorError(
            "validation_error",
            f"FieldSpec value validation failed for {purpose}: " + ", ".join(str(item["field"]) for item in type_errors),
            {"model": spec.model, "fields": type_errors},
        )
    validator = spec.validator
    if validator is None:
        return
    try:
        validator(data)
    except OperatorError:
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary for model contracts.
        raise OperatorError(
            "validation_error",
            f"Model value validation failed for {purpose}.",
            {"model": spec.model, "error": str(exc)},
        ) from exc


def validate_filter_fields(filters: Mapping[str, Any] | None, spec: ModelSpec) -> None:
    if not filters:
        return
    invalid: list[str] = []
    allowed = set(spec.filterable_fields)
    for key in filters:
        field = _filter_field_name(str(key))
        if field not in allowed:
            invalid.append(str(key))
    if invalid:
        raise OperatorError(
            "validation_error",
            "Filter fields are not allowed by the model registry.",
            {"fields": invalid, "filterable_fields": list(spec.filterable_fields)},
        )


def validate_sort(sort: str | None, spec: ModelSpec) -> tuple[str, str]:
    if not sort:
        return spec.default_sort
    field, direction = parse_sort_expression(sort)
    allowed = set(spec.filterable_fields) | {spec.primary_key, spec.default_sort[0]}
    if field not in allowed:
        raise OperatorError(
            "validation_error",
            "Sort field is not allowed by the model registry.",
            {"field": field, "sortable_fields": sorted(allowed)},
        )
    return field, direction


def parse_sort_expression(sort: str) -> tuple[str, str]:
    value = str(sort or "").strip()
    if not value:
        return "", "asc"
    direction = "asc"
    prefix_direction = ""
    if value.startswith("-"):
        direction = "desc"
        prefix_direction = "desc"
        value = value[1:].strip()
    elif value.startswith("+"):
        prefix_direction = "asc"
        value = value[1:].strip()

    if "," in value or ":" in value:
        parts = [part.strip() for part in re.split(r"[,:]", value)]
        field = parts[0] if parts else ""
        if len(parts) > 2:
            raise OperatorError(
                "validation_error",
                "Sort accepts only one field and one direction.",
                {"sort": sort},
            )
        raw_direction = parts[1].lower() if len(parts) > 1 else ""
        if raw_direction not in {"asc", "desc"}:
            raise OperatorError(
                "validation_error",
                "Sort direction must be asc or desc.",
                {"field": field, "direction": raw_direction},
            )
        if prefix_direction and raw_direction != prefix_direction:
            raise OperatorError(
                "validation_error",
                "Sort prefix direction conflicts with explicit direction.",
                {"field": field, "prefix_direction": prefix_direction, "direction": raw_direction},
            )
        direction = raw_direction
    elif re.search(r"\s", value):
        raise OperatorError(
            "validation_error",
            "Sort direction must use comma or colon separator.",
            {"sort": sort},
        )
    else:
        field = value
    return field, direction


def serialize_record(
    record: Any,
    spec: ModelSpec,
    fields: Sequence[str],
    *,
    include_long_text: bool,
    truncate_long_text: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    long_fields = set(spec.long_text_fields)
    sensitive_fields = set(spec.sensitive_fields)
    for field_name in fields:
        if field_name in long_fields and not include_long_text:
            continue
        if not hasattr(record, field_name):
            continue
        if field_name in sensitive_fields:
            result[field_name] = "[redacted]"
            continue
        value = json_safe(getattr(record, field_name))
        if truncate_long_text and field_name in long_fields:
            value = truncate_value(value)
        result[field_name] = value
    return result


def truncate_value(value: Any, limit: int = 280) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit].rstrip() + "..."
    if isinstance(value, (dict, list)):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if len(encoded) > limit:
            return encoded[:limit].rstrip() + "..."
    return value


def canonical_version(record: Any, spec: ModelSpec) -> str:
    if spec.version_extractor is not None:
        version = spec.version_extractor(record)
        if version:
            return str(version)
    values = {field: json_safe(getattr(record, field, None)) for field in spec.detail_fields}
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class _VersionValuesProxy:
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = values

    def __getattr__(self, name: str) -> Any:
        return self._values.get(name)


def canonical_version_from_values(values: Mapping[str, Any], spec: ModelSpec) -> str:
    """Apply the same registry-authoritative version algorithm to a durable value snapshot."""
    if spec.version_extractor is not None:
        version = spec.version_extractor(_VersionValuesProxy(values))
        if version:
            return str(version)
    selected = {field: values.get(field) for field in spec.detail_fields}
    encoded = json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def calculate_record_risk(
    spec: ModelSpec,
    *,
    tool_name: str,
    operation: str,
    fields: Sequence[str] = (),
    record_count: int = 1,
) -> int:
    if tool_name in {"query_records", "get_record"}:
        return 0
    if tool_name == "manage_session":
        return 1
    if operation == "delete":
        return 5
    if operation in {"archive", "restore", "detach", "remove_from_collection"}:
        return max(4, int(spec.risk_profile.get("delete_or_archive", 4)))
    base = int(spec.risk_profile.get(operation, spec.risk_profile.get(tool_name, 3)))
    if fields and set(fields) & set(spec.long_text_fields):
        base = max(base, 3)
    if record_count > 1:
        base = max(base, 4)
    return min(max(base, 0), 5)


def calculate_action_risk(spec: ActionSpec, input_payload: Mapping[str, Any]) -> int:
    risk = int(spec.risk_level)
    if spec.is_async or spec.cost_level >= 3:
        risk = max(risk, 4 if spec.confirmation_required else risk)
    if spec.action == "batch_mutate":
        input_data = input_payload if isinstance(input_payload, Mapping) else {}
        operation = str(input_data.get("operation") or "")
        target = input_data.get("target") or {}
        record_ids = target.get("record_ids") if isinstance(target, Mapping) else None
        if isinstance(record_ids, (list, tuple)):
            batch_size = len(record_ids)
        else:
            batch_size = 0
        if operation == "delete" and batch_size > 5:
            risk = max(risk, 5)
        elif batch_size > 1:
            risk = max(risk, 4)
    for key, value in input_payload.items():
        if _is_reference_array_field(str(key)) and isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) > 1:
            risk = max(risk, 4)
        if "delete" in str(key).lower() or "clear" in str(key).lower():
            risk = max(risk, 5)
    return min(max(risk, 0), 5)


def validate_action_schema(spec: ActionSpec, input_payload: Mapping[str, Any]) -> dict[str, Any]:
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
        enum_values = schema_value.get("enum")
        if enum_values is not None and input_payload[field] not in (None, "") and json_safe(input_payload[field]) not in list(enum_values):
            raise OperatorError(
                "validation_error",
                "Action input value is outside the allowed enum.",
                {"action": spec.action, "field": field, "value": json_safe(input_payload[field]), "allowed_values": list(enum_values)},
            )
    return {str(key): json_safe(value) for key, value in input_payload.items()}


async def validate_action_references(session: AsyncSession, actor: ActorContext, spec: ActionSpec, input_payload: Mapping[str, Any]) -> None:
    await normalize_action_references(session, actor, spec, input_payload)


async def normalize_action_references(
    session: AsyncSession,
    actor: ActorContext,
    spec: ActionSpec,
    input_payload: Mapping[str, Any],
) -> dict[str, Any]:
    properties = spec.input_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return dict(input_payload)
    normalized = {str(key): json_safe(value) for key, value in input_payload.items()}
    for field_name in properties:
        field = str(field_name)
        model_name = _action_reference_model(field)
        if model_name is None:
            continue
        value = input_payload.get(field)
        if value in (None, ""):
            continue
        is_array = _is_reference_array_field(field)
        values = value if is_array else [value]
        if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
            raise OperatorError(
                "validation_error",
                "Action reference field must be an array.",
                {"action": spec.action, "field": field},
            )
        related_spec = get_model_spec(model_name)
        related_cls = get_model_class(model_name)
        normalized_values: list[Any] = []
        for record_id in values:
            if record_id in (None, ""):
                continue
            record = await fetch_scoped_record(session, actor, related_spec, related_cls, record_id)
            normalized_values.append(json_safe(getattr(record, related_spec.primary_key)))
        normalized[field] = normalized_values if is_array else (normalized_values[0] if normalized_values else "")
    return normalized


def _normalize_record_id_for_model(model_cls: type[Any], spec: ModelSpec, record_id: Any) -> Any:
    mapper = getattr(model_cls, "__mapper__", None)
    pk_columns = list(getattr(mapper, "primary_key", []) or [])
    if len(pk_columns) != 1:
        return record_id
    pk_column = pk_columns[0]
    column_type = getattr(pk_column, "type", None)
    if isinstance(column_type, Integer):
        try:
            if isinstance(record_id, str):
                cleaned = record_id.strip()
                if not cleaned:
                    raise ValueError("empty primary key")
                if not re.fullmatch(r"[+-]?\d+", cleaned):
                    raise ValueError("non-integer primary key")
                normalized = int(cleaned)
            elif isinstance(record_id, bool):
                raise ValueError("boolean primary key")
            elif type(record_id) is int:
                normalized = record_id
            else:
                raise ValueError("non-integer primary key")
        except (TypeError, ValueError) as exc:
            raise OperatorError(
                "validation_error",
                "Record id must match the model primary key type.",
                {"model": spec.model, "record_id": json_safe(record_id), "primary_key": spec.primary_key},
            ) from exc
        return normalized
    if isinstance(record_id, str):
        return record_id.strip()
    return record_id


async def collect_action_expected_versions(
    session: AsyncSession,
    actor: ActorContext,
    spec: ActionSpec,
    input_payload: Mapping[str, Any],
) -> dict[str, str]:
    properties = spec.input_schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return {}
    expected: dict[str, str] = {}
    for field_name in properties:
        field = str(field_name)
        model_name = _action_reference_model(field)
        if model_name is None:
            continue
        value = input_payload.get(field)
        if value in (None, ""):
            continue
        values = value if _is_reference_array_field(field) else [value]
        if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
            raise OperatorError(
                "validation_error",
                "Action reference field must be an array.",
                {"action": spec.action, "field": field},
            )
        related_spec = get_model_spec(model_name)
        related_cls = get_model_class(model_name)
        for record_id in values:
            if record_id in (None, ""):
                continue
            record = await fetch_scoped_record(session, actor, related_spec, related_cls, record_id)
            await session.refresh(record)
            _add_expected_version(expected, model_name, related_spec, record)
    await _collect_implicit_action_expected_versions(session, actor, spec, input_payload, expected)
    return dict(sorted(expected.items()))


def expected_versions_hash(expected_versions: Mapping[str, Any] | None) -> str:
    if not expected_versions:
        return ""
    normalized = {str(key): str(value) for key, value in sorted(expected_versions.items())}
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _collect_implicit_action_expected_versions(
    session: AsyncSession,
    actor: ActorContext,
    spec: ActionSpec,
    input_payload: Mapping[str, Any],
    expected: dict[str, str],
) -> None:
    if spec.action == "batch_mutate":
        await _collect_batch_mutate_expected_versions(session, actor, input_payload, expected)
        return
    if spec.action == "organize_jobs_into_pool":
        await _add_pool_name_expected_version(session, actor, input_payload.get("pool_name"), expected)
        return
    if spec.action == "generate_resume":
        profile = await _add_profile_expected_version(session, actor, input_payload.get("profile_id"), expected)
        if profile is not None:
            await _add_selected_profile_section_versions(session, profile.id, expected, limit=12)
        return
    if spec.action == "profile_agent_apply_patch":
        profile = await _add_profile_expected_version(session, actor, input_payload.get("profile_id"), expected)
        if profile is not None:
            await _add_profile_child_versions(session, profile.id, expected)
        return
    if spec.action in {"profile_generate_narrative", "profile_instant_draft"}:
        profile = await _add_profile_expected_version(session, actor, input_payload.get("profile_id"), expected)
        if profile is not None:
            await _add_profile_child_versions(session, profile.id, expected)
        return
    if spec.action == "profile_chat_confirm":
        chat_session = await _fetch_profile_chat_session_for_actor(session, actor)
        expected[f"profile_chat_session:{chat_session.id}"] = _internal_record_version(
            chat_session,
            ("id", "profile_id", "topic", "messages_json", "extracted_bullets", "extracted_bullets_count", "status", "updated_at"),
        )
        profile = await _add_profile_expected_version(session, actor, chat_session.profile_id, expected)
        if profile is not None:
            await _add_profile_child_versions(session, profile.id, expected)


async def _collect_batch_mutate_expected_versions(
    session: AsyncSession,
    actor: ActorContext,
    input_payload: Mapping[str, Any],
    expected: dict[str, str],
) -> None:
    model_name = str(input_payload.get("model") or "")
    if not model_name or model_name not in MODEL_REGISTRY or model_name not in MODEL_CLASSES:
        return
    target = input_payload.get("target") or {}
    if not isinstance(target, Mapping):
        return
    record_ids = target.get("record_ids")
    if not isinstance(record_ids, (list, tuple)) or not record_ids:
        return
    related_spec = get_model_spec(model_name)
    related_cls = get_model_class(model_name)
    for record_id in record_ids:
        if record_id in (None, ""):
            continue
        record = await fetch_scoped_record(session, actor, related_spec, related_cls, record_id)
        await session.refresh(record)
        _add_expected_version(expected, model_name, related_spec, record)


async def _add_profile_expected_version(
    session: AsyncSession,
    actor: ActorContext,
    profile_id: Any,
    expected: dict[str, str],
) -> Any | None:
    if profile_id in (None, ""):
        return None
    profile_spec = get_model_spec("profile")
    profile = await fetch_scoped_record(session, actor, profile_spec, models.Profile, profile_id)
    await session.refresh(profile)
    _add_expected_version(expected, "profile", profile_spec, profile)
    return profile


async def _add_pool_name_expected_version(
    session: AsyncSession,
    actor: ActorContext,
    pool_name: Any,
    expected: dict[str, str],
) -> None:
    name = str(pool_name or "").strip()
    if not name:
        return
    pool_spec = get_model_spec("pool")
    pool = (
        await session.execute(
            select(models.Pool)
            .where(models.Pool.owner_actor_id == actor.actor_id, models.Pool.name == name)
            .order_by(models.Pool.id.asc())
        )
    ).scalars().first()
    if pool is None:
        expected[f"pool_name_absence:{name}"] = "absent"
        return
    await session.refresh(pool)
    _add_expected_version(expected, "pool", pool_spec, pool)


async def _add_selected_profile_section_versions(
    session: AsyncSession,
    profile_id: Any,
    expected: dict[str, str],
    *,
    limit: int,
) -> None:
    section_spec = get_model_spec("profile_section")
    sections = (
        await session.execute(
            select(models.ProfileSection)
            .where(models.ProfileSection.profile_id == profile_id)
            .order_by(
                models.ProfileSection.sort_order.asc(),
                models.ProfileSection.updated_at.desc(),
                models.ProfileSection.id.asc(),
            )
            .limit(limit)
        )
    ).scalars().all()
    for section in sections:
        await session.refresh(section)
        _add_expected_version(expected, "profile_section", section_spec, section)


async def _add_profile_child_versions(session: AsyncSession, profile_id: Any, expected: dict[str, str]) -> None:
    section_spec = get_model_spec("profile_section")
    role_spec = get_model_spec("profile_target_role")
    sections = (
        await session.execute(
            select(models.ProfileSection)
            .where(models.ProfileSection.profile_id == profile_id)
            .order_by(models.ProfileSection.id.asc())
        )
    ).scalars().all()
    for section in sections:
        await session.refresh(section)
        _add_expected_version(expected, "profile_section", section_spec, section)
    target_roles = (
        await session.execute(
            select(models.ProfileTargetRole)
            .where(models.ProfileTargetRole.profile_id == profile_id)
            .order_by(models.ProfileTargetRole.id.asc())
        )
    ).scalars().all()
    for target_role in target_roles:
        await session.refresh(target_role)
        _add_expected_version(expected, "profile_target_role", role_spec, target_role)


async def _fetch_profile_chat_session_for_actor(session: AsyncSession, actor: ActorContext) -> Any:
    prefix = "profile_chat_"
    raw = str(actor.session_id or "")
    if not raw.startswith(prefix):
        raise OperatorError("validation_error", "profile_chat_confirm must be bound to a profile chat session.", {"session_id": raw})
    try:
        chat_session_id = int(raw[len(prefix) :])
    except ValueError as exc:
        raise OperatorError("validation_error", "Profile chat session id is invalid.", {"session_id": raw}) from exc
    chat_session = await session.get(models.ProfileChatSession, chat_session_id)
    if chat_session is None:
        raise OperatorError("not_found_error", "Profile chat session was not found.", {"session_id": chat_session_id})
    return chat_session


def _add_expected_version(
    expected: dict[str, str],
    model_name: str,
    spec: ModelSpec,
    record: Any,
) -> None:
    canonical_id = json_safe(getattr(record, spec.primary_key, ""))
    expected[f"{model_name}:{canonical_id}"] = canonical_version(record, spec)


def _internal_record_version(record: Any, fields: Sequence[str]) -> str:
    values = {field: json_safe(getattr(record, field, None)) for field in fields}
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_filters(model_cls: type[Any], spec: ModelSpec, filters: Mapping[str, Any] | None) -> list[Any]:
    validate_filter_fields(filters, spec)
    clauses: list[Any] = []
    for raw_key, value in (filters or {}).items():
        key = str(raw_key)
        _validate_filter_value_shape(key, value)
        field_name = _filter_field_name(key)
        column = getattr(model_cls, field_name)
        if key.endswith("_contains"):
            clauses.append(_contains_clause(column, value, field_name=field_name))
        elif key.endswith("_in"):
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
                raise OperatorError("validation_error", "Filter _in values must be arrays.", {"field": key})
            values = list(value)
            if _is_json_column(column):
                clauses.append(_json_any_in_clause(column, values, field_name=field_name))
            else:
                clauses.append(column.in_(values))
        elif key.endswith("_gte"):
            clauses.append(column >= value)
        elif key.endswith("_lte"):
            clauses.append(column <= value)
        elif _is_json_column(column):
            clauses.append(_contains_clause(column, value, field_name=field_name))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values = [item for item in value if item not in (None, "")]
            if not values:
                raise OperatorError("validation_error", "Filter array values must not be empty.", {"field": key})
            clauses.append(column.in_(values))
        else:
            clauses.append(column == value)
    return clauses


def _validate_filter_value_shape(field: str, value: Any) -> None:
    if isinstance(value, Mapping) or isinstance(value, AbstractSet):
        raise OperatorError(
            "validation_error",
            "Filter values must be scalars or arrays of scalars.",
            {"field": field, "type": type(value).__name__},
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        invalid_types = [
            type(item).__name__
            for item in value
            if isinstance(item, Mapping)
            or isinstance(item, AbstractSet)
            or (isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)))
        ]
        if invalid_types:
            raise OperatorError(
                "validation_error",
                "Filter arrays must contain only scalar values.",
                {"field": field, "invalid_types": invalid_types},
            )


def build_search_clause(model_cls: type[Any], spec: ModelSpec, search: str | None) -> Any | None:
    if not search:
        return None
    if not spec.search_fields:
        raise OperatorError("validation_error", "This model does not allow keyword search.", {"model": spec.model})
    terms = [
        _contains_clause(getattr(model_cls, field), search, field_name=field)
        for field in spec.search_fields
        if hasattr(model_cls, field)
    ]
    return or_(*terms) if terms else None


def _contains_clause(column: Any, value: Any, *, field_name: str) -> Any:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values = [item for item in value if item not in (None, "")]
        if not values:
            raise OperatorError("validation_error", "Filter contains values must not be empty.", {"field": field_name})
        return and_(*[_contains_clause(column, item, field_name=field_name) for item in values])
    if value in (None, ""):
        raise OperatorError("validation_error", "Filter contains value must not be empty.", {"field": field_name})
    if not _is_json_column(column):
        return column.ilike(f"%{value}%")
    text_column = cast(column, String)
    return or_(*[text_column.ilike(f"%{term}%") for term in _json_text_search_terms(value)])


def _json_any_in_clause(column: Any, values: Sequence[Any], *, field_name: str) -> Any:
    valid_values = [item for item in values if item not in (None, "")]
    if not valid_values:
        raise OperatorError("validation_error", "Filter _in values must not be empty.", {"field": field_name})
    return or_(*[_contains_clause(column, item, field_name=field_name) for item in valid_values])


def _json_text_search_terms(value: Any) -> tuple[str, ...]:
    raw = str(value)
    terms = [raw]
    if raw:
        terms.append(json.dumps(raw, ensure_ascii=True)[1:-1])
        terms.append(json.dumps(raw, ensure_ascii=False)[1:-1])
    return tuple(dict.fromkeys(term for term in terms if term))


def _is_json_column(column: Any) -> bool:
    property_columns = getattr(getattr(column, "property", None), "columns", None) or ()
    if not property_columns:
        return False
    column_type = getattr(property_columns[0], "type", None)
    return isinstance(column_type, JSON)


def scope_clause(model_cls: type[Any], spec: ModelSpec, actor: ActorContext) -> Any | None:
    if spec.ownership_scope in {"global_readonly", "system_shared"}:
        return None
    if spec.ownership_scope == "non_operable":
        raise OperatorError("validation_error", "Model is not directly operable.", {"model": spec.model})
    parent_scope = _ownership_parent_scope(model_cls, spec, actor)
    if parent_scope is not None:
        return parent_scope
    if hasattr(model_cls, "owner_actor_id"):
        return getattr(model_cls, "owner_actor_id") == actor.actor_id
    return None


async def fetch_scoped_record(
    session: AsyncSession,
    actor: ActorContext,
    spec: ModelSpec,
    model_cls: type[Any],
    record_id: Any,
) -> Any:
    normalized_record_id = _normalize_record_id_for_model(model_cls, spec, record_id)
    record = await session.get(model_cls, normalized_record_id)
    if record is None:
        raise OperatorError("not_found_error", "Record was not found.", {"model": spec.model, "record_id": normalized_record_id})
    if not await actor_can_access_record(session, actor, spec, record):
        raise OperatorError("permission_error", "Record is outside the current actor scope.", {"model": spec.model})
    return record


async def actor_can_access_record(session: AsyncSession, actor: ActorContext, spec: ModelSpec, record: Any) -> bool:
    if spec.ownership_scope in {"global_readonly", "system_shared"}:
        return True
    parent = _ownership_parent(spec)
    if parent is not None:
        parent_field, parent_model = parent
        parent_id = getattr(record, parent_field, None)
        if parent_id in (None, ""):
            return False
        parent_spec = get_model_spec(parent_model)
        parent_cls = get_model_class(parent_model)
        parent_record = await session.get(parent_cls, parent_id)
        if parent_record is None:
            return False
        return await actor_can_access_record(session, actor, parent_spec, parent_record)
    owner = getattr(record, "owner_actor_id", None)
    if owner is not None:
        return owner == actor.actor_id
    return False


async def validate_create_scope(session: AsyncSession, actor: ActorContext, spec: ModelSpec, data: Mapping[str, Any]) -> None:
    parent = _ownership_parent(spec)
    if parent is not None:
        parent_field = parent[0]
        if parent_field not in data or data.get(parent_field) in (None, ""):
            raise OperatorError(
                "validation_error",
                "Relationship-owned records require their ownership parent.",
                {"model": spec.model, "field": parent_field},
            )
    for field_name, related_model in spec.relations.items():
        if field_name in data and data[field_name] is not None and related_model in MODEL_REGISTRY:
            related_spec = get_model_spec(related_model)
            related_cls = get_model_class(related_model)
            await fetch_scoped_record(session, actor, related_spec, related_cls, data[field_name])


def apply_patch_to_record_image(before: Mapping[str, Any], updates: Mapping[str, Any], patch_mode: str) -> dict[str, Any]:
    if patch_mode not in PATCH_MODES:
        raise OperatorError("validation_error", "Unsupported patch mode.", {"patch_mode": patch_mode})
    after = dict(before)
    for field_name, value in updates.items():
        current = after.get(field_name)
        if patch_mode in {"replace", "rewrite"}:
            after[field_name] = json_safe(value)
        elif patch_mode == "append":
            if current in (None, ""):
                after[field_name] = json_safe(value)
            elif isinstance(current, list):
                after[field_name] = [*current, *value] if isinstance(value, list) else [*current, json_safe(value)]
            elif isinstance(current, str):
                after[field_name] = f"{current}{value}"
            else:
                raise OperatorError("validation_error", "Append mode only supports text and array fields.", {"field": field_name})
        elif patch_mode == "merge":
            if not isinstance(current, Mapping) or not isinstance(value, Mapping):
                raise OperatorError("validation_error", "Merge mode only supports object fields.", {"field": field_name})
            after[field_name] = {**current, **json_safe(value)}
    return after


def diff_dict(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> dict[str, Any]:
    before = before or {}
    after = after or {}
    keys = sorted(set(before) | set(after))
    return {key: {"before": before.get(key), "after": after.get(key)} for key in keys if before.get(key) != after.get(key)}


async def shape_proposal(
    session: AsyncSession,
    actor: ActorContext,
    *,
    tool_name: str,
    operation_type: str,
    model_or_action: str,
    risk_level: int,
    locked_payload: Mapping[str, Any],
    user_message: str = "",
    record_id: Any = None,
    affected_records: Sequence[Mapping[str, Any]] | None = None,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    reason: str = "",
    summary: str = "",
    commit: bool = True,
) -> dict[str, Any]:
    proposal_id = f"prop_{uuid.uuid4().hex}"
    created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    ttl_minutes = 5 if risk_level >= 5 else 10 if risk_level >= 4 else 30
    expires_at = created_at + timedelta(minutes=ttl_minutes)
    requires_second = risk_level >= 5
    confirmations_required = RISK_CONFIRMATIONS.get(risk_level, 0)
    reject_trusted_args(
        locked_payload,
        location=f"proposal:{tool_name}.locked_payload",
        allow_top_level_names={"expected_version_or_hash", "expected_versions", "operation_type", "tool_name"},
    )
    payload = json_safe(locked_payload)
    affected = [json_safe(item) for item in (affected_records or [])]
    before_safe = json_safe(before) if before is not None else None
    after_safe = json_safe(after) if after is not None else None
    proposal = {
        "proposal_id": proposal_id,
        "session_id": actor.session_id,
        "status": "pending",
        "risk_level": risk_level,
        "operation_type": operation_type,
        "tool_name": tool_name,
        "model_or_action": model_or_action,
        "record_id": "" if record_id is None else str(record_id),
        "affected_records": affected,
        "summary": summary or _proposal_summary(operation_type, model_or_action, record_id, risk_level),
        "reason": reason or "User requested an operation that requires confirmation.",
        "user_message_snapshot": user_message or "",
        "before": before_safe,
        "after": after_safe,
        "diff": diff_dict(before_safe, after_safe),
        "locked_payload": payload,
        "actor_id": actor.actor_id,
        "expected_version_or_hash": str(payload.get("expected_version_or_hash", "")),
        "idempotency_key": _idempotency_key(actor, tool_name, operation_type, model_or_action, payload),
        "confirmation_events": [],
        "confirmation_count": 0,
        "confirmations_required": confirmations_required,
        "confirmations_received": 0,
        "confirmation_challenges": [],
        "first_confirmed_at": None,
        "second_confirmed_at": None,
        "requires_second_confirmation": requires_second,
        "confirmation_challenge": f"confirm-{proposal_id[-8:]}" if requires_second else "",
        "expires_at": expires_at.isoformat(),
        "created_at": created_at.isoformat(),
        "confirmation_text": "Requires a second confirmation before execution." if requires_second else "",
    }
    proposal_row = models.ProposalCache(
        proposal_id=proposal_id,
        session_id=actor.session_id,
        actor_id=actor.actor_id,
        status="pending",
        risk_level=risk_level,
        operation_type=operation_type,
        tool_name=tool_name,
        model_or_action=model_or_action,
        record_id=proposal["record_id"],
        affected_records=affected,
        summary=proposal["summary"],
        reason=proposal["reason"],
        user_message_snapshot=user_message or "",
        before=before_safe,
        after=after_safe,
        diff=proposal["diff"],
        locked_payload=payload,
        expected_version_or_hash=proposal["expected_version_or_hash"],
        idempotency_key=proposal["idempotency_key"],
        confirmation_events=[],
        confirmation_count=0,
        confirmations_required=confirmations_required,
        confirmations_received=0,
        confirmation_challenges=[],
        requires_second_confirmation=requires_second,
        confirmation_challenge=proposal["confirmation_challenge"],
        expires_at=expires_at,
        confirmation_text=proposal["confirmation_text"],
    )
    session.add(proposal_row)
    await add_pending_proposal_id(session, actor, proposal_id)
    await log_agent_audit(
        session,
        actor=actor,
        proposal=proposal_row,
        args_snapshot=payload,
        args_redacted=redact_audit_args(payload),
        confirmation_status="proposal_created",
        result_status="pending",
        result_summary=proposal["summary"],
        changed_records=affected,
    )
    if commit:
        await session.commit()
    else:
        await session.flush()
    return proposal


_PENDING_LIST_CAS_ATTEMPTS = 8


async def add_pending_proposal_id(session: AsyncSession, actor: ActorContext, proposal_id: str) -> None:
    proposal_id = str(proposal_id or "").strip()
    if not proposal_id:
        return
    await _cas_update_pending_proposal_ids(
        session,
        actor,
        mutate=lambda pending: pending if proposal_id in pending else [*pending, proposal_id],
    )


async def remove_pending_proposal_id(session: AsyncSession, actor: ActorContext, proposal_id: str) -> None:
    proposal_id = str(proposal_id or "").strip()
    if not proposal_id:
        return
    await remove_pending_proposal_ids(session, actor, [proposal_id])


async def remove_pending_proposal_ids(
    session: AsyncSession,
    actor: ActorContext,
    proposal_ids: list[str] | tuple[str, ...] | set[str],
) -> None:
    """Relative CAS removal: drop ids without rewriting concurrent adds."""
    remove_set = {str(item).strip() for item in proposal_ids if str(item or "").strip()}
    if not remove_set:
        return
    await _cas_update_pending_proposal_ids(
        session,
        actor,
        mutate=lambda pending: [item for item in pending if str(item) not in remove_set],
    )


async def replace_pending_proposal_ids(
    session: AsyncSession,
    actor: ActorContext,
    pending_ids: list[str] | tuple[str, ...] | set[str],
) -> None:
    """Absolute replace — only for intentional clear-all paths (e.g. checkpoint restore).

    Prefer add_pending_proposal_id / remove_pending_proposal_ids for incremental
    mutations. Absolute replace re-applies a fixed desired list on CAS retry and
    can wipe concurrent adds if the caller scanned a stale snapshot.
    """
    desired = [str(item) for item in pending_ids if str(item or "").strip()]
    await _cas_update_pending_proposal_ids(
        session,
        actor,
        mutate=lambda _pending: desired,
    )


async def _cas_update_pending_proposal_ids(
    session: AsyncSession,
    actor: ActorContext,
    *,
    mutate,
) -> None:
    """Atomically update AgentSession.pending_proposal_ids via version CAS.

    The pending list is the sole authority for confirmable proposals. Unlocked
    read-modify-write loses concurrent creates/confirms. CAS retries keep the
    list coherent across concurrent request sessions.

    Important: do not session.refresh() the AgentSession ORM row here. Callers
    often have other dirty session fields (active_skill, context, etc.) that
    have not been flushed yet; a full refresh would discard them.
    """
    await get_or_create_agent_session(session, actor)
    # Persist any already-dirty AgentSession fields before the versioned UPDATE
    # so concurrent CAS does not race against unflushed local mutations.
    await session.flush()
    last_error: Exception | None = None
    for _ in range(_PENDING_LIST_CAS_ATTEMPTS):
        row = (
            await session.execute(
                select(
                    models.AgentSession.pending_proposal_ids,
                    models.AgentSession.pending_list_version,
                ).where(
                    models.AgentSession.session_id == actor.session_id,
                    models.AgentSession.actor_id == actor.actor_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise OperatorError(
                "not_found_error",
                "Agent session was not found for pending-list update.",
                {"session_id": actor.session_id},
            )
        current = [str(item) for item in list(row[0] or []) if str(item or "").strip()]
        version = int(row[1] or 0)
        updated = [str(item) for item in list(mutate(list(current)) or []) if str(item or "").strip()]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in updated:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        if deduped == current:
            agent_session = await session.get(models.AgentSession, actor.session_id)
            if agent_session is not None:
                agent_session.pending_proposal_ids = deduped
                agent_session.pending_list_version = version
            return
        changed = await session.execute(
            update(models.AgentSession)
            .where(
                models.AgentSession.session_id == actor.session_id,
                models.AgentSession.actor_id == actor.actor_id,
                models.AgentSession.pending_list_version == version,
            )
            .values(
                pending_proposal_ids=deduped,
                pending_list_version=version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount == 1:
            agent_session = await session.get(models.AgentSession, actor.session_id)
            if agent_session is not None:
                agent_session.pending_proposal_ids = deduped
                agent_session.pending_list_version = version + 1
                flag_modified(agent_session, "pending_proposal_ids")
            await session.flush()
            return
        last_error = RuntimeError("pending_proposal_ids CAS conflict")
    raise OperatorError(
        "transient_error",
        "Concurrent pending-proposal list update conflicted; retry the request.",
        {
            "session_id": actor.session_id,
            "attempts": _PENDING_LIST_CAS_ATTEMPTS,
            "cause": str(last_error or ""),
        },
    )


async def get_or_create_agent_session(session: AsyncSession, actor: ActorContext) -> Any:
    agent_session = await session.get(models.AgentSession, actor.session_id)
    if agent_session is None:
        agent_session = models.AgentSession(
            session_id=actor.session_id,
            actor_id=actor.actor_id,
            adapter=actor.adapter,
            pending_proposal_ids=[],
        )
        session.add(agent_session)
        await session.flush()
        return agent_session
    if agent_session.actor_id != actor.actor_id:
        raise OperatorError("permission_error", "Session is outside the current actor scope.", {"session_id": actor.session_id})
    return agent_session


def session_snapshot(agent_session: Any) -> dict[str, Any]:
    snapshot = {field: json_safe(getattr(agent_session, field, None)) for field in SESSION_SNAPSHOT_FIELDS}
    snapshot["active_skill"] = snapshot.get("active_skill") or None
    snapshot["current_step"] = snapshot.get("current_step") or None
    snapshot["checkpoint_id"] = snapshot.get("checkpoint_id") or None
    snapshot["pending_proposal_ids"] = snapshot.get("pending_proposal_ids") or []
    return snapshot


def validate_session_updates(operation: str, updates: Mapping[str, Any]) -> dict[str, Any]:
    if operation not in SESSION_OPERATIONS:
        raise OperatorError("validation_error", "Unsupported session operation.", {"operation": operation})
    allow_top_level_names = {"checkpoint_id"} if operation == "restore_checkpoint" else set()
    reject_trusted_args(
        updates,
        location=f"manage_session:{operation}.updates",
        allow_top_level_names=allow_top_level_names,
    )
    normalized_updates = _normalize_activate_skill_aliases(operation, updates)
    rejected = sorted(set(normalized_updates) - SESSION_UPDATE_FIELDS)
    if rejected:
        raise OperatorError("validation_error", "Session update fields are not allowed.", {"fields": rejected})
    allowed_operation_fields = OPERATION_UPDATE_FIELDS.get(operation)
    if allowed_operation_fields is not None:
        outside_operation = sorted(set(normalized_updates) - allowed_operation_fields)
        if outside_operation:
            raise OperatorError(
                "validation_error",
                "Session update fields are not handled by this operation.",
                {"operation": operation, "fields": outside_operation},
            )
    backend_owned = {"pending_proposal_ids"}
    if operation != "restore_checkpoint":
        backend_owned.add("checkpoint_id")
    forbidden = sorted(backend_owned & set(normalized_updates))
    if forbidden:
        raise OperatorError(
            "validation_error",
            "Session proposal and checkpoint fields are backend-owned for this operation.",
            {"operation": operation, "fields": forbidden},
        )
    cleaned = {str(key): json_safe(value) for key, value in normalized_updates.items()}
    if operation == "activate_skill" and "active_skill" not in cleaned:
        raise OperatorError("validation_error", "activate_skill requires active_skill.", {})
    if operation == "activate_skill":
        from app.operator.registry import get_skill_spec

        try:
            get_skill_spec(str(cleaned.get("active_skill") or ""))
        except ValueError as exc:
            raise OperatorError(
                "validation_error",
                "activate_skill requires a registered operator Skill.",
                {"active_skill": cleaned.get("active_skill")},
            ) from exc
    if operation == "set_skill_step" and "current_step" not in cleaned:
        raise OperatorError("validation_error", "set_skill_step requires current_step.", {})
    if operation == "restore_checkpoint" and "checkpoint_id" not in cleaned:
        raise OperatorError("validation_error", "restore_checkpoint requires checkpoint_id.", {})
    return cleaned


def _normalize_activate_skill_aliases(operation: str, updates: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(updates)
    if operation != "activate_skill":
        return normalized
    active_value = normalized.get("active_skill")
    for alias in ("skill", "name"):
        if alias not in normalized:
            continue
        alias_value = normalized.pop(alias)
        if active_value not in (None, "") and alias_value not in (None, "") and str(alias_value) != str(active_value):
            raise OperatorError(
                "validation_error",
                "activate_skill received conflicting skill aliases.",
                {"active_skill": active_value, alias: alias_value},
            )
        if active_value in (None, ""):
            active_value = alias_value
    if active_value not in (None, ""):
        normalized["active_skill"] = active_value
    return normalized


async def apply_session_operation(session: AsyncSession, actor: ActorContext, operation: str, updates: Mapping[str, Any]) -> Any:
    agent_session = await get_or_create_agent_session(session, actor)
    cleaned = validate_session_updates(operation, updates)
    await validate_session_context_scope(session, actor, cleaned)
    if operation == "deactivate_skill":
        agent_session.active_skill = ""
        agent_session.current_step = ""
    elif operation == "clear_context":
        agent_session.current_job_id = None
        agent_session.current_resume_id = None
        agent_session.current_profile_section_id = None
        agent_session.current_application_id = None
    elif operation == "restore_checkpoint":
        checkpoint = await session.get(models.AgentCheckpoint, cleaned["checkpoint_id"])
        if checkpoint is None:
            raise OperatorError("not_found_error", "Checkpoint was not found.", {"checkpoint_id": cleaned["checkpoint_id"]})
        if checkpoint.actor_id != actor.actor_id or checkpoint.session_id != actor.session_id:
            raise OperatorError("permission_error", "Checkpoint is outside the current actor/session scope.", {})
        for field in SESSION_UPDATE_FIELDS:
            if hasattr(checkpoint, field):
                setattr(agent_session, field, getattr(checkpoint, field))
    for field, value in cleaned.items():
        if field in SESSION_UPDATE_FIELDS and field != "pending_proposal_ids":
            setattr(agent_session, field, value or "")
        elif field == "pending_proposal_ids":
            agent_session.pending_proposal_ids = list(value or [])
    agent_session.actor_id = actor.actor_id
    agent_session.adapter = actor.adapter
    await session.commit()
    await session.refresh(agent_session)
    return agent_session


async def validate_session_context_scope(session: AsyncSession, actor: ActorContext, updates: Mapping[str, Any]) -> None:
    context_models = {
        "current_job_id": "job",
        "current_resume_id": "resume",
        "current_profile_section_id": "profile_section",
        "current_application_id": "application",
    }
    for field_name, model_name in context_models.items():
        record_id = updates.get(field_name)
        if record_id in (None, ""):
            continue
        spec = get_model_spec(model_name)
        model_cls = get_model_class(model_name)
        await fetch_scoped_record(session, actor, spec, model_cls, record_id)


def build_query_statement(
    model_cls: type[Any],
    spec: ModelSpec,
    actor: ActorContext,
    *,
    filters: Mapping[str, Any] | None,
    search: str | None,
    sort: str | None,
) -> Any:
    clauses = build_filters(model_cls, spec, filters)
    scope = scope_clause(model_cls, spec, actor)
    if scope is not None:
        clauses.append(scope)
    search_clause = build_search_clause(model_cls, spec, search)
    if search_clause is not None:
        clauses.append(search_clause)
    statement = select(model_cls)
    if clauses:
        statement = statement.where(and_(*clauses))
    sort_field, direction = validate_sort(sort, spec)
    if hasattr(model_cls, sort_field):
        column = getattr(model_cls, sort_field)
        statement = statement.order_by(column.desc() if direction == "desc" else column.asc())
    return statement


def _filter_field_name(key: str) -> str:
    for suffix in ("_contains", "_in", "_gte", "_lte"):
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _ownership_parent(spec: ModelSpec) -> tuple[str, str] | None:
    parent = OWNERSHIP_PARENT_FIELDS.get(spec.ownership_scope)
    if parent is not None:
        return parent
    for field_name, related_model in spec.relations.items():
        if field_name in {"profile_id", "resume_id", "application_id"}:
            return str(field_name), str(related_model)
    return None


def _ownership_parent_scope(model_cls: type[Any], spec: ModelSpec, actor: ActorContext) -> Any | None:
    parent = _ownership_parent(spec)
    if parent is None:
        return None
    parent_field, parent_model = parent
    if not hasattr(model_cls, parent_field):
        return None
    parent_cls = get_model_class(parent_model)
    parent_spec = get_model_spec(parent_model)
    parent_id_column = getattr(model_cls, parent_field)
    parent_pk_column = getattr(parent_cls, parent_spec.primary_key)
    parent_clause = scope_clause(parent_cls, parent_spec, actor)
    if parent_clause is None:
        return parent_id_column == parent_pk_column
    return and_(parent_id_column == parent_pk_column, parent_clause)


def _action_reference_model(field_name: str) -> str | None:
    base = field_name[:-4] if field_name.endswith("_ids") else field_name[:-3] if field_name.endswith("_id") else ""
    if not base:
        return None
    model_name = ACTION_REFERENCE_ALIASES.get(base, base)
    return model_name if model_name in MODEL_REGISTRY and model_name in MODEL_CLASSES else None


def _is_reference_array_field(field_name: str) -> bool:
    return field_name.endswith("_ids")


def _proposal_summary(operation_type: str, model_or_action: str, record_id: Any, risk_level: int) -> str:
    target = f"{model_or_action}:{record_id}" if record_id is not None else model_or_action
    return f"{operation_type} {target} requires confirmation at risk level {risk_level}."


def _idempotency_key(
    actor: ActorContext,
    tool_name: str,
    operation_type: str,
    model_or_action: str,
    locked_payload: Mapping[str, Any],
) -> str:
    encoded = json.dumps(
        {
            "actor_id": actor.actor_id,
            "session_id": actor.session_id,
            "tool_name": tool_name,
            "operation_type": operation_type,
            "model_or_action": model_or_action,
            "locked_payload": json_safe(locked_payload),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()




