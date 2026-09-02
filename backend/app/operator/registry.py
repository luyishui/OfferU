from __future__ import annotations

import hashlib
import importlib
import json
import pathlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from app.operator.application_lifecycle import ApplicationLifecycleSpec
from app.operator.errors import OperatorError


UNIVERSAL_TOOL_NAMES = (
    "query_records",
    "get_record",
    "create_record",
    "patch_record",
    "delete_or_archive_record",
    "invoke_action",
    "manage_session",
    "describe_capability",
)

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_BOOLEAN = {"type": "boolean"}
_OBJECT = {"type": "object", "additionalProperties": True}
_STRING_ARRAY = {"type": "array", "items": _STRING}
_STRING_ARRAY_MIN1 = {"type": "array", "items": _STRING, "minItems": 1}
POOL_SCOPE_VALUES = ("inbox", "picked", "ignored")
TRIAGE_STATUS_VALUES = ("inbox", "picked", "ignored")
_POOL_SCOPE_STRING = {
    "type": "string",
    "enum": list(POOL_SCOPE_VALUES),
    "description": "Job pool visibility scope. Use picked for 已筛选 pools.",
}
_TRIAGE_STATUS_STRING = {
    "type": "string",
    "enum": list(TRIAGE_STATUS_VALUES),
    "description": "Canonical job triage status.",
}
_APPLICATION_STATUS_STRING = {
    "type": "string",
    "enum": list(ApplicationLifecycleSpec.states),
    "description": "Canonical Application lifecycle stage derived from the ApplicationLifecycleSpec authority.",
}
_SORT_STRING = {
    "type": "string",
    "description": (
        "Single-field sort using a registered sort-compatible field. Prefer field or -field; "
        "accepted equivalents include field,asc, field,desc, field:asc, and field:desc. "
        "Do not use SQL snippets, whitespace direction syntax, or multi-field sort."
    ),
}

TOOL_ARGUMENT_ALIASES: Mapping[str, Mapping[str, str]] = {
    "query_records": {"limit": "page_size"},
    "get_record": {"id": "record_id"},
    "patch_record": {"id": "record_id"},
    "delete_or_archive_record": {"id": "record_id"},
}

TOOL_ARGUMENT_SCHEMAS: Mapping[str, Mapping[str, Any]] = {
    "query_records": {
        "type": "object",
        "properties": {
            "model": _STRING,
            "filters": _OBJECT,
            "search": _STRING,
            "page": _INTEGER,
            "page_size": _INTEGER,
            "sort": _SORT_STRING,
        },
        "required": ["model"],
        "additionalProperties": False,
    },
    "get_record": {
        "type": "object",
        "properties": {
            "model": _STRING,
            "record_id": {"oneOf": [_STRING, _INTEGER]},
            "include_long_text": _BOOLEAN,
        },
        "required": ["model", "record_id"],
        "additionalProperties": False,
    },
    "create_record": {
        "type": "object",
        "properties": {"model": _STRING, "data": _OBJECT},
        "required": ["model", "data"],
        "additionalProperties": False,
    },
    "patch_record": {
        "type": "object",
        "properties": {
            "model": _STRING,
            "record_id": {"oneOf": [_STRING, _INTEGER]},
            "updates": _OBJECT,
            "patch_mode": {"type": "string", "enum": ["replace", "append", "merge", "rewrite"]},
        },
        "required": ["model", "record_id", "updates", "patch_mode"],
        "additionalProperties": False,
    },
    "delete_or_archive_record": {
        "type": "object",
        "properties": {
            "model": _STRING,
            "record_id": {"oneOf": [_STRING, _INTEGER]},
            "operation": {"type": "string", "enum": ["archive", "restore", "detach", "remove_from_collection", "delete"]},
        },
        "required": ["model", "record_id", "operation"],
        "additionalProperties": False,
    },
    "invoke_action": {
        "type": "object",
        "properties": {"action": _STRING, "input": _OBJECT},
        "required": ["action", "input"],
        "additionalProperties": False,
    },
    "manage_session": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["activate_skill", "deactivate_skill", "set_context", "clear_context", "set_skill_step", "restore_checkpoint"],
            },
            "updates": {
                "type": "object",
                "description": "Session update payload. For activate_skill, prefer active_skill; skill and name are accepted aliases.",
                "properties": {
                    "active_skill": _STRING,
                    "skill": _STRING,
                    "name": _STRING,
                    "current_step": _STRING,
                    "current_job_id": {"oneOf": [_STRING, _INTEGER]},
                    "current_resume_id": {"oneOf": [_STRING, _INTEGER]},
                    "current_profile_section_id": {"oneOf": [_STRING, _INTEGER]},
                    "current_application_id": {"oneOf": [_STRING, _INTEGER]},
                    "checkpoint_id": _STRING,
                },
                "additionalProperties": True,
            },
        },
        "required": ["operation", "updates"],
        "additionalProperties": False,
    },
    "describe_capability": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["model", "action", "session-command", "skill"]},
            "name": _STRING,
            "operation": _STRING,
        },
        "required": ["kind", "name", "operation"],
        "additionalProperties": False,
    },
}

@dataclass(frozen=True)
class OperationResultContract:
    status_type: str = "operation_status"
    primary_record_type: str = "record_envelope"
    affected_records_type: str = "affected_record_list"
    typed_outputs: Mapping[str, str] = field(default_factory=dict)
    before_version_type: str = "version_token"
    after_version_type: str = "version_token"
    write_occurred_type: str = "boolean"
    completion_reason_type: str = "completion_reason"


@dataclass(frozen=True)
class UniversalToolSpec:
    name: str
    description: str
    argument_schema: Mapping[str, Any]
    schema_loading: str
    side_effecting: bool
    result_contract: OperationResultContract = field(default_factory=OperationResultContract)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    data_type: str
    description: str
    semantic_role: str
    data_origin: str
    write_owner: str
    readable: bool = True
    generic_creatable: bool = False
    generic_writable: bool = False
    filterable: bool = False
    searchable: bool = False
    summary_visible: bool = False
    detail_visible: bool = False
    long_text: bool = False
    required_on_create: bool = False
    nullable: bool = True
    enum_values: tuple[str, ...] = ()
    relation_target: str | None = None
    aliases: tuple[str, ...] = ()
    examples: tuple[Any, ...] = ()
    write_guidance: str = ""
    forbidden_uses: tuple[str, ...] = ()
    internal: bool = False


class RegistryContractError(RuntimeError):
    """Agent-visible registry is incomplete or inconsistent and must fail closed."""


_UNIVERSAL_TOOL_DESCRIPTIONS: Mapping[str, str] = {
    "query_records": (
        "Read a bounded collection of records using the current model query schema. Load the query schema when filters or sorting are needed; "
        "this tool has no business side effect, performs no confirmation, and returns a typed read result with record envelopes and completion status."
    ),
    "get_record": (
        "Read one authoritative record by its typed model and record ID. Use it before planning writes or interpreting long text; it has no side effect "
        "or confirmation, may require the current read schema, and returns a typed record envelope plus version information for later planning."
    ),
    "create_record": (
        "Stage creation of one registered model record after loading the current create schema. It does not bypass confirmation: side effects are compiled, "
        "risk-scored, version-aware where applicable, and presented for authorization; its result identifies the staged intent or confirmed typed record output."
    ),
    "patch_record": (
        "Stage a field-aware update after loading the current patch schema and reading the target version. Compatible same-record fields must be merged before "
        "confirmation; stale versions remain fenced, and the typed result reports the staged intent, affected record, versions, and completion reason."
    ),
    "delete_or_archive_record": (
        "Stage a registered lifecycle operation after loading the current delete/archive schema and target version. The operation requires policy-driven confirmation, "
        "never weakens version fencing, and returns a typed result describing authorization, affected records, write status, and completion reason."
    ),
    "invoke_action": (
        "Stage a registered domain action only after loading its exact action schema. Action risk, confirmation, typed outputs, retry authority, and compensation policy "
        "come from the registry rather than the model; the result reports the staged intent or durable execution outcome with versions and completion reason."
    ),
    "manage_session": (
        "Apply a registered session command using the current session-command schema. Session state changes are not business-record writes, but they remain actor/session "
        "scoped and return a typed status and completion reason; this tool cannot authorize proposals, lower confirmation, or alter version-fencing policy."
    ),
    "describe_capability": (
        "Load the exact authoritative schema for one model operation, action, or session command and persist its digest-bound receipt for this actor/session. "
        "This read-only capability operation performs no business write or confirmation and returns field semantics, examples, policies, typed outputs, and schema digest."
    ),
}


def _tool_schema_loading(name: str) -> str:
    return {
        "query_records": "query",
        "get_record": "read",
        "create_record": "write",
        "patch_record": "write",
        "delete_or_archive_record": "write",
        "invoke_action": "action",
        "manage_session": "session",
        "describe_capability": "none",
    }[name]


UNIVERSAL_TOOL_SPECS: Mapping[str, UniversalToolSpec] = {
    name: UniversalToolSpec(
        name=name,
        description=_UNIVERSAL_TOOL_DESCRIPTIONS[name],
        argument_schema=TOOL_ARGUMENT_SCHEMAS[name],
        schema_loading=_tool_schema_loading(name),
        side_effecting=name not in {"query_records", "get_record", "describe_capability"},
        result_contract=OperationResultContract(
            typed_outputs={
                "primary_record_id": "record_id<any>",
                "affected_record_ids": "record_id_list<any>",
            }
        ),
    )
    for name in UNIVERSAL_TOOL_NAMES
}


_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")

from app.operator.field_specs_catalog import FIELD_SPEC_CATALOG

BACKEND_OWNED_ACTION_INPUT_FIELDS = frozenset(
    {
        "actor_id",
        "adapter",
        "after",
        "auth_subject",
        "before",
        "checkpoint_id",
        "confirmation_challenge",
        "confirmation_count",
        "confirmation_events",
        "confirmed_scope",
        "diff",
        "expected_version_or_hash",
        "first_confirmed_at",
        "idempotency_key",
        "locked_payload",
        "operation_type",
        "owner_actor_id",
        "owner_id",
        "ownership_scope",
        "pending_proposal_ids",
        "requires_second_confirmation",
        "risk_level",
        "scopes",
        "second_confirmed_at",
        "session_id",
        "tenant_id",
        "tool_name",
        "user_id",
    }
)

# Actions restricted to an explicit, registry-verified Skill execution channel.
# Each value is the Skill whose contract references the action; the model may
# discover and load such actions only through that Skill's capability surface.
SKILL_EXECUTION_CHANNEL_ACTIONS: dict[str, str] = {
    "profile_agent_apply_patch": "resume-experience-mining",
}


@dataclass(frozen=True)
class ModelSpec:
    model: str
    label: str
    description: str
    primary_key: str
    readable_fields: tuple[str, ...]
    summary_fields: tuple[str, ...]
    detail_fields: tuple[str, ...]
    creatable_fields: tuple[str, ...] = ()
    writable_fields: tuple[str, ...] = ()
    filterable_fields: tuple[str, ...] = ()
    search_fields: tuple[str, ...] = ()
    long_text_fields: tuple[str, ...] = ()
    sensitive_fields: tuple[str, ...] = ()
    relations: Mapping[str, str] = field(default_factory=dict)
    risk_profile: Mapping[str, int] = field(default_factory=dict)
    default_sort: tuple[str, str] = ("created_at", "desc")
    serializer: Callable[[Any], Mapping[str, Any]] | None = None
    validator: Callable[[Mapping[str, Any]], None] | None = None
    ownership_scope: str = "actor_owned"
    version_field_or_hash: str = "operator_version_hash"
    version_extractor: Callable[[Any], str] | None = None
    fields: Mapping[str, FieldSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fields:
            return
        ordered = tuple(self.fields.values())
        derived = {
            "readable_fields": tuple(item.name for item in ordered if item.readable),
            "summary_fields": tuple(item.name for item in ordered if item.summary_visible),
            "detail_fields": tuple(item.name for item in ordered if item.detail_visible),
            "creatable_fields": tuple(item.name for item in ordered if item.generic_creatable),
            "writable_fields": tuple(item.name for item in ordered if item.generic_writable),
            "filterable_fields": tuple(item.name for item in ordered if item.filterable),
            "search_fields": tuple(item.name for item in ordered if item.searchable),
            "long_text_fields": tuple(item.name for item in ordered if item.long_text),
            "relations": {item.name: item.relation_target for item in ordered if item.relation_target},
        }
        for attr, value in derived.items():
            object.__setattr__(self, attr, value)


@dataclass(frozen=True)
class ActionParameterSpec:
    name: str
    json_type: str
    semantic_type: str
    required: bool = False
    referenceable: bool = False
    durable: bool = True
    description: str = ""


@dataclass(frozen=True)
class EffectSpec:
    operation: str
    model: str
    kind: str = "database_record"
    visibility: str = "public"
    required: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        if self.operation not in {"create", "patch", "delete", "*"}:
            raise ValueError(f"Unsupported effect operation: {self.operation}")
        if not self.model:
            raise ValueError("EffectSpec.model is required")
        if self.kind not in {"database_record", "external_resource", "artifact"}:
            raise ValueError(f"Unsupported effect kind: {self.kind}")
        if self.visibility not in {"public", "supporting", "internal"}:
            raise ValueError(f"Unsupported effect visibility: {self.visibility}")

    def as_contract(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "model": self.model,
            "kind": self.kind,
            "visibility": self.visibility,
            "required": self.required,
            "description": self.description,
        }

@dataclass(frozen=True)
class ActionSpec:
    action: str
    label: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    input_parameters: Mapping[str, ActionParameterSpec] = field(default_factory=dict)
    output_parameters: Mapping[str, ActionParameterSpec] = field(default_factory=dict)
    side_effects: tuple[str, ...] = ()
    effect_specs: tuple[EffectSpec, ...] = ()
    is_async: bool = False
    cost_level: int = 1
    risk_level: int = 0
    confirmation_required: bool = False
    result_model: str | None = None
    planner_visible: bool = True
    allowed_on_surfaces: tuple[str, ...] = ()
    related_domains: tuple[str, ...] = ()
    related_capabilities: tuple[str, ...] = ()
    readiness_gates: tuple[str, ...] = ()
    refine_contracts: tuple[str, ...] = ()
    confirmation_points: tuple[str, ...] = ()
    result_artifacts: tuple[str, ...] = ()
    conditional_rules: tuple[Mapping[str, Any], ...] = ()
    write_mode: str = "plan_staged"
    implementation_status: str = "not_implemented"
    non_operable_reason: str = "No real operator action handler is wired yet."
    handler: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if self.write_mode not in {"plan_staged", "read_only"}:
            raise ValueError(
                f"Action {self.action} has unsupported write_mode {self.write_mode!r}"
            )
        modifiers = self.side_effects or self.effect_specs or self.confirmation_required
        if modifiers and self.write_mode == "read_only":
            raise ValueError(
                f"Action {self.action} declares side effects or confirmation but is read_only"
            )
        if not modifiers and self.write_mode == "plan_staged":
            # Pure read/analysis actions must stay read_only so the contract is
            # never advertised as a staged write path.
            object.__setattr__(self, "write_mode", "read_only")
        for rule in self.conditional_rules:
            _validate_conditional_rule(self.action, rule, self.input_schema)


@dataclass(frozen=True)
class CompensationSpec:
    operation: str
    automatic: bool
    max_attempts: int
    requires_version_fence: bool
    description: str


_COMPENSATION_TEMPLATES: Mapping[str, CompensationSpec] = {
    "create_record": CompensationSpec("delete_created_record", True, 2, True, "Delete only the exact created record when its receipt and current version still match."),
    "patch_record": CompensationSpec("restore_previous_fields", True, 2, True, "Restore only touched fields when current values and version prove no later legitimate write would be overwritten."),
}
COMPENSATION_REGISTRY: dict[str, CompensationSpec] = {}


@dataclass(frozen=True)
class SkillSpec:
    skill: str
    label: str
    description: str
    activation_examples: tuple[str, ...]
    required_context: tuple[str, ...]
    optional_context: tuple[str, ...]
    skill_path: str
    allowed_tools: tuple[str, ...]
    step_schema: Mapping[str, Any]
    interrupt_policy: Mapping[str, Any]
    exit_policy: Mapping[str, Any]
    checkpoint_policy: Mapping[str, Any]
    page_activation: tuple[str, ...] = ()
    planner_visible: bool = True
    allowed_on_surfaces: tuple[str, ...] = ()
    related_domains: tuple[str, ...] = ()
    related_capabilities: tuple[str, ...] = ()
    readiness_gates: tuple[str, ...] = ()
    refine_contracts: tuple[str, ...] = ()
    allowed_write_actions: tuple[str, ...] = ()
    confirmation_points: tuple[str, ...] = ()


def _version_hash(record: Any) -> str:
    return str(getattr(record, "operator_version_hash", "") or "")


def _canonical_field_version(record: Any, fields: tuple[str, ...]) -> str:
    values = {field_name: getattr(record, field_name, None) for field_name in fields}
    encoded = json.dumps(values, default=str, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _version_extractor(fields: tuple[str, ...]) -> Callable[[Any], str]:
    def extract(record: Any) -> str:
        stored_hash = _version_hash(record)
        if stored_hash:
            return stored_hash
        return _canonical_field_version(record, fields)

    return extract


def _not_implemented_handler(*_: Any, **__: Any) -> None:
    raise NotImplementedError("Operator action handlers are wired in later tasks.")


def _proposal_dispatch_handler(*_: Any, **__: Any) -> None:
    raise RuntimeError("Implemented operator actions execute through invoke_action proposal dispatch.")


_SPECIAL_FIELD_SEMANTICS: Mapping[tuple[str, str], tuple[str, str, str, str, tuple[str, ...]]] = {
    ("job", "raw_description"): (
        "source_job_description",
        "Source or user-provided job description used as the authoritative role content.",
        "source_or_user",
        "source_or_user",
        ("Do not use this field for screening notes or AI analysis summaries.",),
    ),
    ("job", "summary"): (
        "ai_job_analysis_summary",
        "AI-derived analysis summary of the job posting; it is not a user screening note.",
        "ai_analysis",
        "analysis_action",
        ("Do not store user annotations or source job descriptions here.",),
    ),
    ("job", "keywords"): (
        "ai_job_analysis_keywords",
        "AI-derived analysis keywords for job matching and search; they are not user annotations.",
        "ai_analysis",
        "analysis_action",
        ("Do not store free-form user screening notes here.",),
    ),
    ("job", "user_notes"): (
        "job_screening_annotation",
        "User-authored annotation recorded while discovering, comparing, or screening a job.",
        "user",
        "user_or_agent",
        ("Do not treat this as source job content or an AI-derived analysis field.",),
    ),
    ("job", "triage_status"): (
        "job_workflow_status",
        "Canonical workflow state for the job inbox: inbox, picked, or ignored.",
        "user_or_workflow",
        "user_or_agent",
        ("Do not encode notes, application status, or arbitrary labels in this field.",),
    ),
    ("application", "notes"): (
        "application_process_annotation",
        "User-authored annotation about the formal application process after a job enters application tracking.",
        "user",
        "user_or_agent",
        ("Do not use this field for pre-application job-screening annotations.",),
    ),
}

_COMMON_FIELD_DESCRIPTIONS: Mapping[str, str] = {
    "id": "Stable database record identifier exposed read-only in the public record envelope.",
    "title": "Human-readable title used to identify this business record.",
    "name": "Human-readable name used to identify this business record.",
    "company": "Organization associated with this business record.",
    "location": "Human-readable geographic or remote-work location.",
    "created_at": "Backend-generated creation timestamp exposed read-only in the record envelope.",
    "updated_at": "Backend-generated last-update timestamp exposed read-only in the record envelope.",
    "operator_version_hash": "Backend-owned optimistic-concurrency token exposed read-only for version-fenced operations.",
    "status": "Registered lifecycle or workflow status for this business record.",
    "description": "Business description for this record, interpreted according to the containing model contract.",
    "notes": "User-authored notes whose exact semantic role is defined by the containing model contract.",
}


def _orm_model_class(model: str) -> Any | None:
    try:
        from app.models import models as orm_models
    except Exception:
        return None
    class_name = "".join(part.capitalize() for part in model.split("_"))
    return getattr(orm_models, class_name, None)


def _column_contract(model: str, field_name: str) -> tuple[str, bool, bool]:
    model_cls = _orm_model_class(model)
    table = getattr(model_cls, "__table__", None)
    column = table.columns.get(field_name) if table is not None else None
    if column is None:
        return "string", True, False
    from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text
    if isinstance(column.type, Boolean):
        data_type = "boolean"
    elif isinstance(column.type, Integer):
        data_type = "integer"
    elif isinstance(column.type, Float):
        data_type = "number"
    elif isinstance(column.type, DateTime):
        data_type = "datetime"
    elif isinstance(column.type, JSON):
        data_type = "object" if field_name.endswith("_json") or field_name in {"contact_json", "style_config", "content_json", "state_json", "schema_json", "custom_values", "mapping_json", "payload_json"} else "array"
    elif isinstance(column.type, (String, Text)):
        data_type = "string"
    else:
        data_type = "string"
    required = bool(not column.nullable and not column.primary_key and column.default is None and column.server_default is None)
    return data_type, bool(column.nullable), required


def _field_semantics(model: str, label: str, field_name: str) -> tuple[str, str, str, str, tuple[str, ...]]:
    special = _SPECIAL_FIELD_SEMANTICS.get((model, field_name))
    if special is not None:
        return special
    semantic_role = f"{model}_{field_name}"
    description = _COMMON_FIELD_DESCRIPTIONS.get(field_name)
    if description is None:
        raise RegistryContractError(
            f"model {model}.{field_name}: semantic description must come from the explicit FieldSpec catalog"
        )
    if field_name in {"id", "created_at", "updated_at", "operator_version_hash"}:
        return semantic_role, description, "backend", "backend", ()
    if field_name.startswith("raw_"):
        return semantic_role, description, "source", "source_or_user", ()
    return semantic_role, description, "user_or_system", "user_or_agent", ()


def _build_field_specs(
    model: str,
    label: str,
    *,
    readable: tuple[str, ...],
    summary: tuple[str, ...],
    detail: tuple[str, ...],
    creatable: tuple[str, ...],
    writable: tuple[str, ...],
    filterable: tuple[str, ...],
    search: tuple[str, ...],
    long_text: tuple[str, ...],
    relations: Mapping[str, str],
) -> Mapping[str, FieldSpec]:
    explicit = FIELD_SPEC_CATALOG.get(model)
    if explicit is None:
        raise RegistryContractError(f"model {model}: explicit FieldSpec catalog is missing")
    tuple_fields = {
        "enum_values", "aliases", "examples", "forbidden_uses",
    }
    fields: dict[str, FieldSpec] = {}
    for field_name, raw in explicit.items():
        values = dict(raw)
        for key in tuple_fields:
            values[key] = tuple(values.get(key) or ())
        fields[field_name] = FieldSpec(**values)
    compatibility = {
        "readable": set(readable), "summary": set(summary), "detail": set(detail),
        "creatable": set(creatable), "writable": set(writable), "filterable": set(filterable),
        "searchable": set(search), "long_text": set(long_text), "relations": set(relations),
    }
    authoritative = {
        "readable": {name for name, item in fields.items() if item.readable},
        "summary": {name for name, item in fields.items() if item.summary_visible},
        "detail": {name for name, item in fields.items() if item.detail_visible},
        "creatable": {name for name, item in fields.items() if item.generic_creatable},
        "writable": {name for name, item in fields.items() if item.generic_writable},
        "filterable": {name for name, item in fields.items() if item.filterable},
        "searchable": {name for name, item in fields.items() if item.searchable},
        "long_text": {name for name, item in fields.items() if item.long_text},
        "relations": {name for name, item in fields.items() if item.relation_target},
    }
    mismatches = [key for key in compatibility if compatibility[key] != authoritative[key]]
    if mismatches:
        raise RegistryContractError(f"model {model}: legacy compatibility declarations diverge from explicit FieldSpec catalog: {mismatches}")
    return fields



def _model(
    model: str,
    label: str,
    description: str,
    readable: tuple[str, ...],
    summary: tuple[str, ...],
    detail: tuple[str, ...],
    filterable: tuple[str, ...],
    *,
    creatable: tuple[str, ...] = (),
    writable: tuple[str, ...] = (),
    search: tuple[str, ...] = (),
    long_text: tuple[str, ...] = (),
    sensitive: tuple[str, ...] = (),
    relations: Mapping[str, str] | None = None,
    risk: Mapping[str, int] | None = None,
    sort: tuple[str, str] = ("created_at", "desc"),
    ownership: str = "actor_owned",
    version_policy: str = "operator_version_hash",
    version_extractor: Callable[[Any], str] | None = _version_hash,
    validator: Callable[[Mapping[str, Any]], None] | None = None,
) -> ModelSpec:
    relation_map = relations or {}
    field_specs = _build_field_specs(
        model,
        label,
        readable=readable,
        summary=summary,
        detail=detail,
        creatable=creatable,
        writable=writable,
        filterable=filterable,
        search=search,
        long_text=long_text,
        relations=relation_map,
    )
    if version_extractor is _version_hash:
        derived_detail = tuple(item.name for item in field_specs.values() if item.detail_visible)
        derived_writable = tuple(item.name for item in field_specs.values() if item.generic_writable)
        version_extractor = _version_extractor(tuple(dict.fromkeys((*derived_detail, *derived_writable))))
    return ModelSpec(
        model=model,
        label=label,
        description=description,
        primary_key="id",
        readable_fields=readable,
        summary_fields=summary,
        detail_fields=detail,
        creatable_fields=creatable,
        writable_fields=writable,
        filterable_fields=filterable,
        search_fields=search,
        long_text_fields=long_text,
        sensitive_fields=sensitive,
        relations=relation_map,
        risk_profile=risk or {"read": 0, "create": 3, "patch": 3, "delete_or_archive": 4},
        default_sort=sort,
        ownership_scope=ownership,
        version_field_or_hash=version_policy,
        version_extractor=version_extractor,
        validator=validator,
        fields=field_specs,
    )


def _validate_pool_values(data: Mapping[str, Any]) -> None:
    if "scope" not in data:
        return
    scope = str(data.get("scope") or "").strip()
    if scope not in POOL_SCOPE_VALUES:
        raise OperatorError(
            "validation_error",
            "Pool scope must be a visible job bucket.",
            {"field": "scope", "value": data.get("scope"), "allowed_values": list(POOL_SCOPE_VALUES)},
        )


def _validate_job_values(data: Mapping[str, Any]) -> None:
    if "triage_status" in data:
        status = str(data.get("triage_status") or "").strip()
        if status not in TRIAGE_STATUS_VALUES:
            raise OperatorError(
                "validation_error",
                "Job triage status must be canonical.",
                {"field": "triage_status", "value": data.get("triage_status"), "allowed_values": list(TRIAGE_STATUS_VALUES)},
            )


MODEL_REGISTRY: dict[str, ModelSpec] = {
    "job": _model(
        "job",
        "Job",
        "A collected job posting that can be searched, triaged, grouped, and used for applications.",
        (
            "id",
            "title",
            "company",
            "location",
            "url",
            "apply_url",
            "source",
            "raw_description",
            "posted_at",
            "salary_min",
            "salary_max",
            "salary_text",
            "education",
            "experience",
            "job_type",
            "company_size",
            "company_industry",
            "company_logo",
            "is_campus",
            "summary",
            "keywords",
            "user_notes",
            "triage_status",
            "pool_id",
            "batch_id",
            "created_at",
            "operator_version_hash",
        ),
        ("id", "title", "company", "location", "source", "triage_status", "pool_id"),
        (
            "id",
            "title",
            "company",
            "location",
            "url",
            "apply_url",
            "source",
            "raw_description",
            "posted_at",
            "salary_text",
            "education",
            "experience",
            "job_type",
            "company_size",
            "company_industry",
            "summary",
            "keywords",
            "user_notes",
            "triage_status",
            "pool_id",
            "batch_id",
            "created_at",
            "operator_version_hash",
        ),
        ("id", "title", "company", "location", "source", "triage_status", "pool_id", "batch_id", "is_campus", "keywords"),
        creatable=(
            "title",
            "company",
            "location",
            "url",
            "apply_url",
            "source",
            "raw_description",
            "posted_at",
            "salary_min",
            "salary_max",
            "salary_text",
            "education",
            "experience",
            "job_type",
            "company_size",
            "company_industry",
            "company_logo",
            "is_campus",
            "user_notes",
            "triage_status",
            "pool_id",
            "batch_id",
        ),
        writable=("triage_status", "pool_id", "user_notes"),
        search=("title", "company", "location", "summary", "raw_description", "keywords", "user_notes"),
        long_text=("raw_description", "summary", "user_notes"),
        relations={"pool_id": "pool", "batch_id": "batch"},
        sort=("created_at", "desc"),
        validator=_validate_job_values,
    ),
    "batch": _model(
        "batch",
        "Batch",
        "Read-only job collection batch metadata used to resolve the registered Job.batch_id relation.",
        ("id", "source", "keywords", "location", "max_results", "job_count", "status", "total_fetched", "created_at"),
        ("id", "source", "location", "status", "job_count"),
        ("id", "source", "keywords", "location", "max_results", "job_count", "status", "total_fetched", "created_at"),
        ("id", "source", "location", "status", "created_at"),
        search=("source", "location", "keywords"),
        sort=("created_at", "desc"),
        ownership="global_readonly",
        version_policy="canonical_hash",
        version_extractor=None,
    ),    "pool": _model(
        "pool",
        "Pool",
        "A user-managed job collection for grouping selected postings.",
        (
            "id",
            "name",
            "description",
            "color",
            "sort_order",
            "scope",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "name", "color", "scope", "sort_order"),
        (
            "id",
            "name",
            "description",
            "color",
            "sort_order",
            "scope",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "name", "scope"),
        creatable=("name", "description", "color", "sort_order", "scope"),
        writable=("name", "description", "color", "sort_order", "scope"),
        search=("name", "description"),
        long_text=("description",),
        sort=("sort_order", "asc"),
        validator=_validate_pool_values,
    ),
    "profile": _model(
        "profile",
        "Profile",
        "A user's master profile containing identity, education, positioning, and narrative material.",
        (
            "id",
            "name",
            "school",
            "major",
            "degree",
            "gpa",
            "email",
            "phone",
            "wechat",
            "headline",
            "exit_story",
            "cross_cutting_advantage",
            "base_info_json",
            "is_default",
            "onboarding_step",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "name", "school", "major", "degree", "headline", "is_default"),
        (
            "id",
            "name",
            "school",
            "major",
            "degree",
            "gpa",
            "email",
            "phone",
            "wechat",
            "headline",
            "exit_story",
            "cross_cutting_advantage",
            "base_info_json",
            "is_default",
            "onboarding_step",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "is_default", "school", "major", "degree"),
        creatable=(
            "name",
            "school",
            "major",
            "degree",
            "gpa",
            "email",
            "phone",
            "wechat",
            "headline",
            "exit_story",
            "cross_cutting_advantage",
            "base_info_json",
            "is_default",
            "onboarding_step",
        ),
        writable=(
            "name",
            "school",
            "major",
            "degree",
            "gpa",
            "email",
            "phone",
            "wechat",
            "headline",
            "exit_story",
            "cross_cutting_advantage",
            "base_info_json",
            "is_default",
            "onboarding_step",
        ),
        search=("name", "school", "major", "headline", "exit_story", "cross_cutting_advantage"),
        long_text=("exit_story", "cross_cutting_advantage"),
        sensitive=("email", "phone", "wechat"),
        sort=("updated_at", "desc"),
    ),
    "profile_target_role": _model(
        "profile_target_role",
        "Profile target role",
        "A target role attached to a profile for fit and positioning.",
        ("id", "profile_id", "role_name", "role_level", "fit", "created_at", "operator_version_hash"),
        ("id", "profile_id", "role_name", "fit"),
        ("id", "profile_id", "role_name", "role_level", "fit", "created_at", "operator_version_hash"),
        ("id", "profile_id", "fit", "role_name"),
        creatable=("profile_id", "role_name", "role_level", "fit"),
        writable=("role_name", "role_level", "fit"),
        search=("role_name", "role_level"),
        relations={"profile_id": "profile"},
        ownership="profile_owned",
    ),
    "profile_section": _model(
        "profile_section",
        "Profile section",
        "A structured profile fact or bullet used by profile and resume workflows.",
        (
            "id",
            "profile_id",
            "section_type",
            "parent_id",
            "title",
            "sort_order",
            "content_json",
            "source",
            "confidence",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "profile_id", "section_type", "title", "source", "confidence"),
        (
            "id",
            "profile_id",
            "section_type",
            "parent_id",
            "title",
            "sort_order",
            "content_json",
            "source",
            "confidence",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "profile_id", "section_type", "source", "confidence"),
        creatable=("profile_id", "section_type", "parent_id", "title", "sort_order", "content_json", "source", "confidence"),
        writable=("section_type", "parent_id", "title", "sort_order", "content_json", "source", "confidence"),
        search=("title",),
        long_text=("content_json",),
        relations={"profile_id": "profile", "parent_id": "profile_section"},
        ownership="profile_owned",
        sort=("sort_order", "asc"),
    ),
    "resume_template": _model(
        "resume_template",
        "Resume template",
        "A built-in or user-visible resume layout template.",
        ("id", "name", "thumbnail_url", "css_variables", "html_layout", "is_builtin", "created_at"),
        ("id", "name", "thumbnail_url", "is_builtin"),
        ("id", "name", "thumbnail_url", "css_variables", "html_layout", "is_builtin", "created_at"),
        ("id", "name", "is_builtin"),
        search=("name",),
        long_text=("html_layout",),
        risk={"read": 0, "create": 3, "patch": 3, "delete_or_archive": 4},
        ownership="global_readonly",
        version_policy="created_at",
        version_extractor=None,
    ),
    "resume": _model(
        "resume",
        "Resume",
        "A generated or manually maintained resume with global settings and sections.",
        (
            "id",
            "user_name",
            "title",
            "photo_url",
            "summary",
            "contact_json",
            "template_id",
            "style_config",
            "is_primary",
            "language",
            "source_mode",
            "source_job_ids",
            "source_profile_snapshot",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "user_name", "title", "template_id", "is_primary", "language", "source_mode"),
        (
            "id",
            "user_name",
            "title",
            "photo_url",
            "summary",
            "contact_json",
            "template_id",
            "style_config",
            "is_primary",
            "language",
            "source_mode",
            "source_job_ids",
            "source_profile_snapshot",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "template_id", "is_primary", "language", "source_mode"),
        creatable=("user_name", "title", "photo_url", "summary", "contact_json", "template_id", "style_config", "is_primary", "language", "source_mode", "source_job_ids", "source_profile_snapshot"),
        writable=("user_name", "title", "photo_url", "summary", "contact_json", "template_id", "style_config", "is_primary", "language", "source_mode", "source_job_ids", "source_profile_snapshot"),
        search=("user_name", "title", "summary"),
        long_text=("summary",),
        sensitive=("contact_json",),
        relations={"template_id": "resume_template"},
        sort=("updated_at", "desc"),
    ),
    "resume_section": _model(
        "resume_section",
        "Resume section",
        "A structured section within a resume.",
        (
            "id",
            "resume_id",
            "section_type",
            "sort_order",
            "title",
            "visible",
            "content_json",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "resume_id", "section_type", "title", "visible", "sort_order"),
        (
            "id",
            "resume_id",
            "section_type",
            "sort_order",
            "title",
            "visible",
            "content_json",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "resume_id", "section_type", "visible"),
        creatable=("resume_id", "section_type", "sort_order", "title", "visible", "content_json"),
        writable=("section_type", "sort_order", "title", "visible", "content_json"),
        search=("title",),
        long_text=("content_json",),
        relations={"resume_id": "resume"},
        ownership="actor_owned",
        sort=("sort_order", "asc"),
    ),
    "interview_notification": _model(
        "interview_notification",
        "Interview notification",
        "A parsed interview invitation or hiring-process email.",
        (
            "id",
            "email_subject",
            "email_from",
            "email_body",
            "company",
            "position",
            "category",
            "interview_time",
            "location",
            "action_required",
            "parsed_at",
            "created_at",
            "operator_version_hash",
        ),
        ("id", "company", "position", "category", "interview_time", "location"),
        (
            "id",
            "email_subject",
            "email_from",
            "email_body",
            "company",
            "position",
            "category",
            "interview_time",
            "location",
            "action_required",
            "parsed_at",
            "created_at",
            "operator_version_hash",
        ),
        ("id", "company", "position", "category", "interview_time"),
        creatable=("email_subject", "email_from", "email_body", "company", "position", "category", "interview_time", "location", "action_required"),
        writable=("company", "position", "category", "interview_time", "location", "action_required"),
        search=("email_subject", "email_from", "email_body", "company", "position", "action_required"),
        long_text=("email_body", "action_required"),
        sensitive=("email_from", "email_body"),
        sort=("interview_time", "asc"),
    ),
    "calendar_event": _model(
        "calendar_event",
        "Calendar event",
        "A scheduled interview, deadline, or related career event.",
        (
            "id",
            "title",
            "description",
            "event_type",
            "start_time",
            "end_time",
            "location",
            "related_job_id",
            "related_notification_id",
            "created_at",
            "operator_version_hash",
        ),
        ("id", "title", "event_type", "start_time", "end_time", "location"),
        (
            "id",
            "title",
            "description",
            "event_type",
            "start_time",
            "end_time",
            "location",
            "related_job_id",
            "related_notification_id",
            "created_at",
            "operator_version_hash",
        ),
        ("id", "event_type", "start_time", "related_job_id", "related_notification_id"),
        creatable=("title", "description", "event_type", "start_time", "end_time", "location", "related_job_id", "related_notification_id"),
        writable=("title", "description", "event_type", "start_time", "end_time", "location", "related_job_id", "related_notification_id"),
        search=("title", "description", "location"),
        long_text=("description",),
        relations={"related_job_id": "job", "related_notification_id": "interview_notification"},
        sort=("start_time", "asc"),
    ),
    "application": _model(
        "application",
        "Application",
        "A formal application tracked against a job.",
        ("id", "job_id", "status", "cover_letter", "apply_url", "notes", "submitted_at", "created_at", "updated_at", "operator_version_hash"),
        ("id", "job_id", "status", "submitted_at", "updated_at"),
        ("id", "job_id", "status", "cover_letter", "apply_url", "notes", "submitted_at", "created_at", "updated_at", "operator_version_hash"),
        ("id", "job_id", "status", "submitted_at"),
        creatable=("job_id", "status", "cover_letter", "apply_url", "notes", "submitted_at"),
        writable=("status", "cover_letter", "apply_url", "notes", "submitted_at"),
        search=("status", "cover_letter", "notes", "apply_url"),
        long_text=("cover_letter", "notes"),
        relations={"job_id": "job"},
        sort=("updated_at", "desc"),
    ),
    "application_workspace_settings": _model(
        "application_workspace_settings",
        "Application workspace settings",
        "Display and synchronization preferences for the application workspace.",
        ("id", "auto_row_height", "auto_column_width", "delete_subtable_sync_total_default", "created_at", "updated_at"),
        ("id", "auto_row_height", "auto_column_width", "delete_subtable_sync_total_default"),
        ("id", "auto_row_height", "auto_column_width", "delete_subtable_sync_total_default", "created_at", "updated_at"),
        ("id", "auto_row_height", "auto_column_width", "delete_subtable_sync_total_default"),
        creatable=("auto_row_height", "auto_column_width", "delete_subtable_sync_total_default"),
        writable=("auto_row_height", "auto_column_width", "delete_subtable_sync_total_default"),
        ownership="system_shared",
        version_policy="updated_at",
        version_extractor=None,
        sort=("updated_at", "desc"),
    ),
    "application_template": _model(
        "application_template",
        "Application template",
        "A default application-table schema template.",
        ("id", "schema_json", "created_at", "updated_at"),
        ("id", "updated_at"),
        ("id", "schema_json", "created_at", "updated_at"),
        ("id", "updated_at"),
        search=(),
        long_text=("schema_json",),
        ownership="global_readonly",
        version_policy="updated_at",
        version_extractor=None,
        sort=("updated_at", "desc"),
    ),
    "application_table": _model(
        "application_table",
        "Application table",
        "A table or subtable container in the application workspace.",
        ("id", "name", "is_total", "schema_json", "created_at", "updated_at", "operator_version_hash"),
        ("id", "name", "is_total", "updated_at"),
        ("id", "name", "is_total", "schema_json", "created_at", "updated_at", "operator_version_hash"),
        ("id", "name", "is_total"),
        creatable=("name", "is_total", "schema_json"),
        writable=("name", "is_total", "schema_json"),
        search=("name",),
        long_text=("schema_json",),
        sort=("updated_at", "desc"),
    ),
    "application_record": _model(
        "application_record",
        "Application record",
        "A canonical application-row entity shared by total and sub tables.",
        (
            "id",
            "job_ref_id",
            "application_id",
            "apply_status",
            "company_name",
            "job_title",
            "location",
            "job_link",
            "source",
            "salary_text",
            "updated_at_value",
            "custom_values",
            "is_duplicate",
            "duplicate_group",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "company_name", "job_title", "location", "source", "updated_at_value", "is_duplicate"),
        (
            "id",
            "job_ref_id",
            "application_id",
            "apply_status",
            "company_name",
            "job_title",
            "location",
            "job_link",
            "source",
            "salary_text",
            "updated_at_value",
            "custom_values",
            "is_duplicate",
            "duplicate_group",
            "created_at",
            "updated_at",
            "operator_version_hash",
        ),
        ("id", "job_ref_id", "application_id", "company_name", "job_title", "location", "source", "is_duplicate", "duplicate_group", "created_at"),
        creatable=("job_ref_id", "apply_status", "company_name", "job_title", "location", "job_link", "source", "salary_text", "updated_at_value", "custom_values", "is_duplicate", "duplicate_group"),
        writable=("apply_status", "company_name", "job_title", "location", "job_link", "source", "salary_text", "updated_at_value", "custom_values", "is_duplicate", "duplicate_group"),
        search=("company_name", "job_title", "location", "source", "salary_text"),
        long_text=("custom_values",),
        relations={"job_ref_id": "job", "application_id": "application"},
        sort=("updated_at", "desc"),
    ),
    "interview_experience": _model(
        "interview_experience",
        "Interview experience",
        "A collected interview-experience source article or note.",
        ("id", "company", "role", "source_url", "source_platform", "raw_text", "interview_rounds", "job_id", "collected_at", "operator_version_hash"),
        ("id", "company", "role", "source_platform", "job_id", "collected_at"),
        ("id", "company", "role", "source_url", "source_platform", "raw_text", "interview_rounds", "job_id", "collected_at", "operator_version_hash"),
        ("id", "company", "role", "source_platform", "job_id"),
        creatable=("company", "role", "source_url", "source_platform", "raw_text", "interview_rounds", "job_id"),
        writable=("company", "role", "source_url", "source_platform", "raw_text", "interview_rounds", "job_id"),
        search=("company", "role", "raw_text", "interview_rounds"),
        long_text=("raw_text", "interview_rounds"),
        relations={"job_id": "job"},
        sort=("collected_at", "desc"),
    ),
    "interview_question": _model(
        "interview_question",
        "Interview question",
        "A structured interview question mined from an experience.",
        ("id", "experience_id", "question_text", "round_type", "category", "difficulty", "frequency", "suggested_answer", "job_id", "created_at", "operator_version_hash"),
        ("id", "question_text", "round_type", "category", "difficulty", "frequency"),
        ("id", "experience_id", "question_text", "round_type", "category", "difficulty", "frequency", "suggested_answer", "job_id", "created_at", "operator_version_hash"),
        ("id", "experience_id", "round_type", "category", "difficulty", "job_id"),
        creatable=("experience_id", "question_text", "round_type", "category", "difficulty", "frequency", "suggested_answer", "job_id"),
        writable=("question_text", "round_type", "category", "difficulty", "frequency", "suggested_answer", "job_id"),
        search=("question_text", "suggested_answer"),
        long_text=("suggested_answer",),
        relations={"experience_id": "interview_experience", "job_id": "job"},
        sort=("created_at", "desc"),
    ),
}


for _model_name in MODEL_REGISTRY:
    for _tool_name, _template in _COMPENSATION_TEMPLATES.items():
        COMPENSATION_REGISTRY[f"{_tool_name}:{_model_name}"] = _template


def compensation_spec_key(tool_name: str, target_name: str) -> str:
    key = f"{str(tool_name or '')}:{str(target_name or '')}"
    return key if key in COMPENSATION_REGISTRY else "manual_review"


def field_for_semantic_role(model_name: str, semantic_role: str) -> FieldSpec:
    spec = MODEL_REGISTRY.get(str(model_name or ""))
    if spec is None:
        raise RegistryContractError(f"Unknown model capability: {model_name}")
    matches = [item for item in spec.fields.values() if item.semantic_role == semantic_role]
    if len(matches) != 1:
        raise RegistryContractError(
            f"Model {model_name!r} must expose exactly one field for semantic role {semantic_role!r}; found {len(matches)}"
        )
    return matches[0]


def _physical_type_matches(field_spec: FieldSpec, column: Any) -> bool:
    from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text
    expected = field_spec.data_type
    if expected == "boolean":
        return isinstance(column.type, Boolean)
    if expected == "integer":
        return isinstance(column.type, Integer) and not isinstance(column.type, Boolean)
    if expected == "number":
        return isinstance(column.type, (Integer, Float)) and not isinstance(column.type, Boolean)
    if expected == "datetime":
        return isinstance(column.type, DateTime)
    if expected in {"array", "object"}:
        return isinstance(column.type, JSON)
    if expected == "string":
        return isinstance(column.type, (String, Text))
    return False


def _description_is_placeholder(description: str, identifier: str) -> bool:
    value = str(description or "").strip()
    lowered = value.casefold()
    if not value:
        return True
    if "typed action parameter" in lowered or "fieldspec" in lowered or "明确定义" in value:
        return True
    normalized_identifier = re.sub(r"[^a-z0-9]+", " ", str(identifier or "").casefold()).strip()
    normalized_value = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    restatements = {
        normalized_identifier,
        f"{normalized_identifier} field",
        f"{normalized_identifier} parameter",
        f"the {normalized_identifier} field",
        f"the {normalized_identifier} parameter",
    }
    return bool(normalized_identifier and normalized_value in restatements)


def validate_registry_contracts(
    *,
    model_registry: Mapping[str, ModelSpec] | None = None,
    tool_specs: Mapping[str, UniversalToolSpec] | None = None,
    action_registry: Mapping[str, ActionSpec] | None = None,
) -> None:
    registry = dict(model_registry or MODEL_REGISTRY)
    tools = dict(tool_specs or UNIVERSAL_TOOL_SPECS)
    actions = dict(action_registry or ACTION_REGISTRY)
    errors: list[str] = []
    try:
        from app.operator.guards import MODEL_CLASSES
    except Exception:
        MODEL_CLASSES = {}  # type: ignore[assignment]

    for model_name, model_spec in registry.items():
        if not model_spec.fields:
            errors.append(f"model {model_name}: no authoritative FieldSpec entries")
            continue
        seen_roles: dict[str, str] = {}
        model_cls = MODEL_CLASSES.get(model_name) or _orm_model_class(model_name)
        columns = getattr(getattr(model_cls, "__table__", None), "columns", {})
        for field_name, field_spec in model_spec.fields.items():
            if field_name != field_spec.name:
                errors.append(f"model {model_name}: FieldSpec key/name mismatch for {field_name}")
            if _description_is_placeholder(field_spec.description, field_name):
                errors.append(f"model {model_name}.{field_name}: placeholder description is forbidden")
            if not field_spec.semantic_role.strip():
                errors.append(f"model {model_name}.{field_name}: semantic role is required")
            previous = seen_roles.get(field_spec.semantic_role)
            if previous is not None:
                errors.append(
                    f"model {model_name}: duplicate semantic role {field_spec.semantic_role!r} on {previous!r} and {field_name!r}"
                )
            else:
                seen_roles[field_spec.semantic_role] = field_name
            if field_spec.relation_target and field_spec.relation_target not in registry:
                errors.append(
                    f"model {model_name}.{field_name}: relation target {field_spec.relation_target!r} is not registered"
                )
            column = columns.get(field_name) if hasattr(columns, "get") else None
            if column is None:
                errors.append(f"model {model_name}.{field_name}: physical column is missing")
                continue
            if not _physical_type_matches(field_spec, column):
                errors.append(
                    f"model {model_name}.{field_name}: type mismatch registry={field_spec.data_type!r} database={column.type!s}"
                )
            if bool(field_spec.nullable) != bool(column.nullable):
                errors.append(
                    f"model {model_name}.{field_name}: nullability mismatch registry={field_spec.nullable!r} database={column.nullable!r}"
                )
            if field_spec.write_owner in {"backend", "analysis_action"} and (
                field_spec.generic_creatable or field_spec.generic_writable
            ):
                errors.append(
                    f"model {model_name}.{field_name}: write owner {field_spec.write_owner!r} forbids generic create/patch"
                )

    for action_name, action_spec in actions.items():
        if set(action_spec.input_parameters) != set((action_spec.input_schema.get("properties") or {})):
            errors.append(f"action {action_name}: typed input parameter contract is incomplete")
        if set(action_spec.output_parameters) != set((action_spec.output_schema.get("properties") or {})):
            errors.append(f"action {action_name}: typed output parameter contract is incomplete")
        input_properties = action_spec.input_schema.get("properties") if isinstance(action_spec.input_schema, Mapping) else {}
        input_properties = input_properties if isinstance(input_properties, Mapping) else {}
        exposed_backend_owned = sorted(BACKEND_OWNED_ACTION_INPUT_FIELDS & set(input_properties))
        if exposed_backend_owned:
            errors.append(
                f"action {action_name}: backend-owned inputs are exposed to the provider schema: {exposed_backend_owned}"
            )
        declared_required = set((action_spec.input_schema.get("required") or ()))
        if declared_required - set(input_properties):
            errors.append(
                f"action {action_name}: required inputs are missing from input schema: "
                f"{sorted(declared_required - set(input_properties))}"
            )
        for name, parameter in action_spec.input_parameters.items():
            if parameter.required and name not in declared_required:
                errors.append(
                    f"action {action_name}.{name}: typed contract marks required but input schema required list omits it"
                )
        for rule in action_spec.conditional_rules:
            try:
                _validate_conditional_rule(action_name, rule, action_spec.input_schema)
            except ValueError as exc:
                errors.append(f"action {action_name}: invalid conditional rule: {exc}")
        if action_spec.side_effects and action_spec.write_mode == "read_only":
            errors.append(
                f"action {action_name}: declares side effects but is read_only"
            )
        if action_spec.write_mode == "read_only" and (
            action_spec.side_effects or action_spec.effect_specs or action_spec.confirmation_required
        ):
            errors.append(
                f"action {action_name}: write_mode read_only conflicts with side effects/confirmation"
            )
        for direction, parameters, schema in (
            ("input", action_spec.input_parameters, action_spec.input_schema),
            ("output", action_spec.output_parameters, action_spec.output_schema),
        ):
            properties = schema.get("properties") if isinstance(schema, Mapping) else {}
            properties = properties if isinstance(properties, Mapping) else {}
            for parameter in parameters.values():
                if not parameter.semantic_type:
                    errors.append(f"action {action_name}.{parameter.name}: semantic type is required")
                if _description_is_placeholder(parameter.description, parameter.name):
                    errors.append(f"action {action_name}.{parameter.name}: placeholder description is forbidden")
                property_schema = properties.get(parameter.name)
                property_description = (
                    str(property_schema.get("description") or "")
                    if isinstance(property_schema, Mapping)
                    else ""
                )
                if property_description != parameter.description:
                    errors.append(
                        f"action {action_name}.{parameter.name}: {direction} schema description must match the typed parameter contract"
                    )

    # Skill allowed write actions must resolve to registered actions that the
    # model can discover and load through the normal capability surface.
    try:
        visible_action_names = set(agent_visible_actions())
    except Exception:
        visible_action_names = set(actions)
    for skill_name, skill_spec in SKILL_REGISTRY.items():
        if not getattr(skill_spec, "planner_visible", True):
            continue
        for action_name in skill_spec.allowed_write_actions:
            if action_name not in actions:
                errors.append(
                    f"skill {skill_name}: allowed_write_actions references unknown action {action_name}"
                )
                continue
            if action_name not in visible_action_names and not SKILL_EXECUTION_CHANNEL_ACTIONS.get(action_name):
                errors.append(
                    f"skill {skill_name}: allowed_write_actions references hidden action {action_name} "
                    "that the model cannot discover or load"
                )

    if tuple(tools) != UNIVERSAL_TOOL_NAMES:
        errors.append("universal tool registry order/names do not match UNIVERSAL_TOOL_NAMES")
    for name, tool_spec in tools.items():
        if _description_is_placeholder(tool_spec.description, name):
            errors.append(f"tool {name}: production description is incomplete")
        if tool_spec.argument_schema.get("type") != "object":
            errors.append(f"tool {name}: argument schema must be an object")
        if not tool_spec.result_contract.status_type or not tool_spec.result_contract.completion_reason_type:
            errors.append(f"tool {name}: typed result contract is incomplete")

    if errors:
        raise RegistryContractError("Registry contract validation failed:\n- " + "\n- ".join(errors))


def _object_schema(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "additionalProperties": False}


def _array_of(item_schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": dict(item_schema)}


_RESULT_SCHEMA = _object_schema({"status": _STRING, "summary": _STRING})


_ACTION_PARAMETER_DESCRIPTIONS: Mapping[str, str] = {
    "accepted_item_ids": "Actor-scoped proposed profile-item identifiers explicitly accepted for durable application.",
    "account_id": "Actor-scoped integration account used to authorize the requested external synchronization.",
    "action": "Canonical action identifier echoed in the result for durable routing and audit correlation.",
    "application": "Structured application record returned after the requested application workflow completes.",
    "application_id": "Actor-scoped Application record that supplies context or receives the requested change.",
    "application_record_id": "Actor-scoped ApplicationRecord projection bound to the canonical Application.",
    "after_status": "Canonical Application lifecycle state after the transition.",
    "applied": "Whether the requested profile or resume patch produced a durable accepted change.",
    "applied_sections_count": "Number of canonical profile sections changed by the approved patch.",
    "archive": "Chooses reversible archival instead of permanent deletion for the selected records.",
    "before_status": "Canonical Application lifecycle state before the transition.",
    "target_status": "Target canonical Application lifecycle state for the advance action; must be a legal transition from the current state per the ApplicationLifecycleSpec authority.",
    "material_type": "Kind of application material to prepare; currently supported: cover_letter.",
    "constraints": "Optional user constraints to honor while preparing the application material.",
    "created": "Whether the action produced a new durable record (otherwise an existing record was reused).",
    "material": "Prepared application material content (for example the cover letter) produced by the action.",
    "notes": "User-authored annotation whose exact semantic role is defined by the containing action or model contract.",
    "artifact": "Durable export artifact metadata, including identity, media type, and retrieval details.",
    "batch_id": "Durable acquisition or processing batch identifier used to correlate the action result.",
    "calendar_event_count": "Number of calendar events created or updated from synchronized recruiting messages.",
    "calendar_events": "Structured calendar-event records produced by the synchronization workflow.",
    "candidate": "Structured candidate profile facts supplied for confirmation or content generation.",
    "candidates": "Allowed destination choices evaluated by the smart-fill matching workflow.",
    "catalog": "Registered destination-field catalog used to constrain smart-fill mappings.",
    "changed_count": "Number of durable records whose business values actually changed.",
    "changed_sections_count": "Number of resume sections changed by the completed optimization workflow.",
    "changes": "Explicit user-approved resume changes to apply as one guarded batch.",
    "confidence": "Normalized confidence score for the returned semantic match.",
    "confirmed_scope": "Backend-confirmed generation boundary describing the approved profile and job inputs.",
    "content_type": "Media type of the generated export so clients can handle the artifact safely.",
    "cover_letter": "User-reviewable cover-letter text generated or stored for the selected application.",
    "created_count": "Number of durable records created by the completed action.",
    "created_sections_count": "Number of canonical profile sections created from the approved source material.",
    "destination_field": "Registered destination field that may receive the mapped source content.",
    "download_url": "Authorized retrieval location for the generated export artifact.",
    "edits": "User-approved field edits to apply to the candidate profile record.",
    "event": "Structured calendar event created from the confirmed interview notification.",
    "experience_id": "Actor-scoped InterviewExperience record used as the source for question extraction.",
    "feedback": "User feedback that constrains the next optimization response without directly mutating records.",
    "field_name": "Registered form field whose option set is being matched.",
    "fields": "Destination form-field definitions available for smart-fill mapping.",
    "file_id": "Durable uploaded-file identifier used as the source for parsing or analysis.",
    "file_name": "Safe user-facing filename assigned to the generated artifact.",
    "form_schema": "Structured destination form definition used to constrain generated application content.",
    "format": "Requested supported output format for generation or export.",
    "fragments": "Source content fragments available for semantic field mapping.",
    "instructions": "User-provided constraints that guide generation without expanding the authorized write scope.",
    "job_id": "Actor-scoped Job record that supplies requirements or receives the requested workflow change.",
    "job_ids": "Actor-scoped Job identifiers defining the exact authorized batch or generation scope.",
    "jobs": "Structured Job records returned by acquisition, import, or batch processing.",
    "level1_title": "Top-level form section title used as semantic context for option matching.",
    "level2_title": "Nested form section title used as semantic context for option matching.",
    "limit": "Maximum number of eligible actor-scoped records the action may process.",
    "location": "Geographic search constraint supplied to the job acquisition workflow.",
    "mappings": "Validated source-to-destination mappings produced by the smart-fill workflow.",
    "matchType": "Classification explaining whether the selected option was exact, semantic, or unmatched.",
    "max_results": "Upper bound on source listings requested from the acquisition provider.",
    "message": "User message that provides the current optimization request and conversational context.",
    "model": "Registered operator model whose actor-scoped records are targeted by the batch operation.",
    "modules": "Resume module counts and structure inferred for downstream smart-fill decisions.",
    "name": "Employer or organization name used to resolve a trusted logo asset.",
    "notification_count": "Number of interview notifications created or updated during synchronization.",
    "notification_id": "Actor-scoped InterviewNotification record supplying confirmed scheduling evidence.",
    "notifications": "Structured interview notifications produced by the synchronization workflow.",
    "operation": "Registered mutation mode that defines whether the batch patches, archives, restores, or deletes.",
    "options": "Allowed form options from which the smart-fill matcher may select.",
    "patch": "Structured field patch proposed for guarded application to the target record.",
    "pool": "Structured job-pool record created or reused by the organization workflow.",
    "pool_created": "Whether the workflow created a new pool instead of reusing an existing one.",
    "pool_description": "User-facing purpose statement for the job pool being created or reused.",
    "pool_id": "Actor-scoped JobPool record that receives or organizes the selected jobs.",
    "pool_name": "User-facing name used to find or create the destination job pool.",
    "pool_scope_repaired": "Whether the workflow corrected an existing pool’s ownership or visibility scope.",
    "profile": "Structured actor-owned Profile record returned by the completed workflow.",
    "profileValues": "Canonical profile values available as sources for destination form fields.",
    "profile_id": "Actor-scoped Profile record supplying facts or receiving approved canonical sections.",
    "profile_sections": "Canonical profile-section records parsed, created, or returned by the action.",
    "query": "Natural-language or structured search expression used to select relevant source information.",
    "question": "Structured InterviewQuestion record returned after answer generation.",
    "question_id": "Actor-scoped InterviewQuestion record for which an answer is requested.",
    "questions": "Structured interview questions extracted or returned for preparation.",
    "record": "Structured durable record returned after the requested action completes.",
    "record_id": "Primary actor-scoped record identifier affected or created by the action.",
    "records": "Structured durable records that were selected, changed, or created by the action.",
    "refine": "Structured refinement metadata describing how generated resume content may be iterated.",
    "report": "Read-only analytical summary produced from durable actor-scoped data.",
    "resume": "Structured actor-owned Resume record returned by the completed workflow.",
    "resume_id": "Actor-scoped Resume record used as the source or destination of the action.",
    "resume_value": "Candidate resume value being compared with registered destination options.",
    "resumes": "Structured Resume records produced by the batch optimization workflow.",
    "reuse_existing": "Allows reuse of a matching actor-owned pool instead of creating a duplicate.",
    "rounds": "Structured interview-round summaries extracted from the source experience.",
    "runId": "Stable smart-fill run identifier used to correlate mappings and diagnostics.",
    "sections": "Structured resume sections returned after parsing, generation, or optimization.",
    "sections_count": "Number of resume sections included in the completed result.",
    "since": "Lower time boundary limiting which recruiting messages are synchronized.",
    "skipped_existing_job_ids": "Actor-scoped jobs skipped because durable equivalents already existed.",
    "source": "Registered provider or source channel from which the action obtains data.",
    "source_field": "Canonical source field whose value is being mapped.",
    "source_ids": "Registered acquisition-source identifiers defining which providers may be queried.",
    "source_model": "Registered model that supplies values for smart-fill mapping.",
    "source_text": "User-provided source material from which canonical profile sections may be drafted.",
    "status": "Canonical completion state used by orchestration to distinguish completed, pending, and failed outcomes.",
    "style": "Requested interview-answer structure or communication style applied during generation.",
    "suggested_answer": "Generated answer draft returned for user review and rehearsal.",
    "summary": "Human-readable result summary grounded in the action’s durable outcome.",
    "table": "Structured actor-owned application table returned by the workflow.",
    "table_id": "Actor-scoped ApplicationTable defining the schema and ownership boundary for the action.",
    "target": "Exact by-ID or materialized actor-scoped record selection for a batch mutation.",
    "target_role": "Role context used to tailor profile narrative generation.",
    "task": "Structured asynchronous task metadata returned by the action.",
    "task_id": "Durable background-task identifier used to inspect subsequent progress.",
    "task_payload": "Durable task metadata describing the accepted batch optimization work.",
    "template": "Structured resume-template record applied or returned by the workflow.",
    "template_id": "Registered ResumeTemplate selected for rendering the actor-owned resume.",
    "text": "User-approved or source text supplied for analysis, parsing, or generation.",
    "title": "User-facing title assigned to the generated resume version.",
    "tone": "Requested communication tone applied to generated application content.",
    "tool_name": "Universal tool boundary that executed the action, echoed for durable routing.",
    "total_jobs": "Number of jobs present in the imported extension batch.",
    "value": "Normalized destination option value selected or evaluated by smart-fill matching.",
    "visibility": "Field-level visibility decisions returned with smart-fill mappings.",
    "window": "Read-only reporting interval used to aggregate job statistics.",
}


def _action_parameter_description(name: str, schema: Mapping[str, Any]) -> str:
    explicit = str(schema.get("description") or "").strip() if isinstance(schema, Mapping) else ""
    if explicit:
        return explicit
    description = _ACTION_PARAMETER_DESCRIPTIONS.get(str(name))
    if description is None:
        raise RegistryContractError(
            f"action parameter {name!r} requires an explicit production business description"
        )
    return description


def _action_parameter_specs(properties: Mapping[str, Any], *, required: tuple[str, ...], result_model: str | None, output: bool) -> dict[str, ActionParameterSpec]:
    specs: dict[str, ActionParameterSpec] = {}
    relation_aliases = {"table": "application_table", "section": "profile_section", "template": "resume_template"}
    for name, schema in properties.items():
        json_type = str(schema.get("type") or "object") if isinstance(schema, Mapping) else "object"
        semantic_type = str(schema.get("x-semantic-type") or "") if isinstance(schema, Mapping) else ""
        if not semantic_type:
            if output and result_model and name in {"record_id", "primary_record_id"}:
                semantic_type = f"record_id<{result_model}>"
            else:
                singular = name[:-4] if name.endswith("_ids") else name[:-3] if name.endswith("_id") else ""
                if singular:
                    singular = relation_aliases.get(singular, singular)
                    semantic_type = f"record_id<{singular}>" + ("[]" if name.endswith("_ids") else "")
                else:
                    semantic_type = json_type
        referenceable = bool(output and isinstance(schema, Mapping) and schema.get("x-referenceable", semantic_type.startswith("record_id<") and not semantic_type.endswith("[]")))
        specs[name] = ActionParameterSpec(
            name=name, json_type=json_type, semantic_type=semantic_type, required=name in required,
            referenceable=referenceable, durable=bool(not isinstance(schema, Mapping) or schema.get("x-durable", True)),
            description=_action_parameter_description(str(name), schema if isinstance(schema, Mapping) else {}),
        )
    return specs


def _json_safe_rule(rule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): list(value) if isinstance(value, (tuple, list)) else value
        for key, value in rule.items()
    }


def _validate_conditional_rule(action: str, rule: Mapping[str, Any], input_schema: Mapping[str, Any]) -> None:
    if not isinstance(rule, Mapping):
        raise ValueError(f"Action {action} conditional rule must be an object")
    declared = set((input_schema.get("properties") or {}))
    clauses: list[tuple[str, Any]] = []
    for key in ("at_least_one_of", "exactly_one_of", "all_of"):
        value = rule.get(key)
        if value is None:
            continue
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError(f"Action {action} conditional rule {key} must be a non-empty list")
        clauses.append((key, tuple(value)))
    if not clauses:
        raise ValueError(f"Action {action} conditional rule must declare at least one clause")
    for _, names in clauses:
        unknown = [name for name in names if name not in declared]
        if unknown:
            raise ValueError(
                f"Action {action} conditional rule references undeclared inputs: {sorted(unknown)}"
            )


def _action(
    action: str,
    label: str,
    description: str,
    *,
    side_effects: tuple[str, ...],
    effect_specs: tuple[EffectSpec, ...] | None = None,
    risk: int,
    result_model: str | None = None,
    is_async: bool = False,
    cost: int = 1,
    input_properties: Mapping[str, Any] | None = None,
    output_properties: Mapping[str, Any] | None = None,
    implemented: bool = False,
    non_operable_reason: str = "No real operator action handler is wired yet.",
    required_input: tuple[str, ...] | None = None,
    handler: Callable[..., Any] | None = None,
    planner_visible: bool = True,
    allowed_on_surfaces: tuple[str, ...] = (),
    related_domains: tuple[str, ...] = (),
    related_capabilities: tuple[str, ...] = (),
    readiness_gates: tuple[str, ...] = (),
    refine_contracts: tuple[str, ...] = (),
    confirmation_points: tuple[str, ...] = (),
    result_artifacts: tuple[str, ...] = (),
    conditional_rules: tuple[Mapping[str, Any], ...] = (),
    write_mode: str | None = None,
) -> ActionSpec:
    properties = input_properties or {"context": _OBJECT}
    backend_owned_fields = sorted(BACKEND_OWNED_ACTION_INPUT_FIELDS & set(properties))
    if backend_owned_fields:
        raise ValueError(
            f"{action} action input_schema cannot expose backend-owned fields: {backend_owned_fields}"
        )
    required_names = tuple(required_input or ())
    outputs = output_properties or {"status": _STRING, "summary": _STRING}
    input_parameters = _action_parameter_specs(properties, required=required_names, result_model=result_model, output=False)
    output_parameters = _action_parameter_specs(outputs, required=(), result_model=result_model, output=True)
    described_inputs = {
        name: {**dict(schema), "description": input_parameters[name].description}
        for name, schema in properties.items()
    }
    described_outputs = {
        name: {**dict(schema), "description": output_parameters[name].description}
        for name, schema in outputs.items()
    }
    input_schema = _object_schema(described_inputs)
    if required_names:
        input_schema["required"] = list(required_names)
    for rule in conditional_rules:
        _validate_conditional_rule(action, rule, input_schema)
    if conditional_rules:
        input_schema["x-conditional-rules"] = [_json_safe_rule(rule) for rule in conditional_rules]
    effective_write_mode = write_mode or (
        "plan_staged"
        if (side_effects or effect_specs or (risk >= 3))
        else "read_only"
    )
    return ActionSpec(
        action=action,
        label=label,
        description=description,
        input_schema=input_schema,
        output_schema=_object_schema(described_outputs),
        input_parameters=input_parameters,
        output_parameters=output_parameters,
        side_effects=side_effects,
        effect_specs=tuple(effect_specs or tuple(
            EffectSpec(*str(declaration).split(":", 1)) for declaration in side_effects
        )),
        is_async=is_async,
        cost_level=cost,
        risk_level=risk,
        confirmation_required=risk >= 3,
        result_model=result_model,
        planner_visible=planner_visible,
        allowed_on_surfaces=allowed_on_surfaces,
        related_domains=related_domains,
        related_capabilities=related_capabilities,
        readiness_gates=readiness_gates,
        refine_contracts=refine_contracts,
        confirmation_points=confirmation_points,
        result_artifacts=result_artifacts,
        implementation_status="implemented" if implemented else "not_implemented",
        non_operable_reason="" if implemented else non_operable_reason,
        handler=handler or (_proposal_dispatch_handler if implemented else _not_implemented_handler),
    )


ACTION_REGISTRY: dict[str, ActionSpec] = {
    "run_scraper": _action(
        "run_scraper",
        "Run scraper",
        "Collect jobs from configured sources.",
        side_effects=("create:job", "create:batch"),
        risk=4,
        result_model="job",
        is_async=True,
        cost=3,
        input_properties={"source_ids": _STRING_ARRAY, "query": _STRING, "location": _STRING, "max_results": _INTEGER},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "task_id": _STRING,
            "batch_id": _STRING,
            "source_ids": _STRING_ARRAY,
            "query": _STRING,
            "created_count": _INTEGER,
            "jobs": _array_of(_OBJECT),
            "task": _OBJECT,
            "summary": _STRING,
        },
        required_input=("source_ids", "query"),
        implemented=False,
        non_operable_reason=(
            "Agent-triggered scraper runs require a durable outbox and external idempotency key before they can be confirmed safely."
        ),
    ),
    "sync_email": _action(
        "sync_email",
        "Sync email",
        "Parse interview notifications from connected email.",
        side_effects=("create:interview_notification", "create:calendar_event"),
        risk=4,
        result_model="interview_notification",
        is_async=True,
        cost=3,
        input_properties={"account_id": _STRING, "since": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "account_id": _STRING,
            "source": _STRING,
            "notification_count": _INTEGER,
            "calendar_event_count": _INTEGER,
            "notifications": _array_of(_OBJECT),
            "calendar_events": _array_of(_OBJECT),
            "summary": _STRING,
        },
        required_input=("account_id",),
        # Do not expose or execute until a real mailbox connector exists. The prior
        # prepare path wrote fixed demo interview/calendar rows (OfferU Labs /
        # @mail.example.test), which is not a truthful capability.
        implemented=False,
        planner_visible=False,
        non_operable_reason=(
            "Email sync requires a real mailbox connector, account verification, "
            "and durable external idempotency before it can be confirmed safely."
        ),
    ),
    "job_stats": _action(
        "job_stats",
        "Job stats",
        "Return actor-scoped job analytics, trends, and weekly report data.",
        side_effects=(),
        risk=0,
        result_model=None,
        input_properties={"window": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "report": _OBJECT,
            "summary": _STRING,
        },
        implemented=True,
    ),
    "import_jobs_to_application_table": _action(
        "import_jobs_to_application_table",
        "Import jobs to application table",
        "Create application records from selected jobs.",
        side_effects=("create:application_record",),
        effect_specs=(
            EffectSpec("create", "application_record", visibility="public"),
            EffectSpec("create", "application_workspace_settings", visibility="supporting"),
            EffectSpec("create", "application_template", visibility="supporting"),
            EffectSpec("create", "application_table", visibility="supporting"),
            EffectSpec("patch", "application_table", visibility="supporting"),
            EffectSpec("create", "application_table_record", visibility="supporting"),
        ),
        risk=4,
        result_model="application_record",
        input_properties={"job_ids": _STRING_ARRAY, "table_id": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "table_id": _STRING,
            "created_count": _INTEGER,
            "skipped_existing_job_ids": _array_of(_INTEGER),
            "records": _array_of(_OBJECT),
            "table": _OBJECT,
            "summary": _STRING,
        },
        required_input=("job_ids", "table_id"),
        implemented=True,
    ),
    "import_latest_extension_batch": _action(
        "import_latest_extension_batch",
        "Import latest extension batch",
        "Import the latest browser-extension job collection batch into an application table.",
        side_effects=("create:application_record",),
        effect_specs=(
            EffectSpec("create", "application_record", visibility="public"),
            EffectSpec("create", "application_workspace_settings", visibility="supporting"),
            EffectSpec("create", "application_template", visibility="supporting"),
            EffectSpec("create", "application_table", visibility="supporting"),
            EffectSpec("patch", "application_table", visibility="supporting"),
            EffectSpec("create", "application_table_record", visibility="supporting"),
        ),
        risk=4,
        result_model="application_record",
        input_properties={"table_id": _STRING, "batch_id": _STRING, "source": _STRING, "limit": _INTEGER},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "table_id": _STRING,
            "batch_id": _STRING,
            "source": _STRING,
            "total_jobs": _INTEGER,
            "created_count": _INTEGER,
            "skipped_existing_job_ids": _array_of(_INTEGER),
            "records": _array_of(_OBJECT),
            "table": _OBJECT,
            "summary": _STRING,
        },
        required_input=("table_id",),
        implemented=True,
    ),
    "batch_triage_jobs": _action(
        "batch_triage_jobs", "Batch triage jobs", "Apply triage status or pool changes to multiple jobs.",
        side_effects=("patch:job",), risk=4, result_model="job",
        input_properties={"job_ids": _STRING_ARRAY_MIN1, "triage_status": _TRIAGE_STATUS_STRING, "pool_id": _STRING},
        output_properties={
            "status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING,
            "changed_count": _INTEGER, "records": _array_of(_OBJECT), "summary": _STRING,
        },
        required_input=("job_ids",),
        conditional_rules=({"at_least_one_of": ("triage_status", "pool_id")},),
        implemented=True,
    ),
    "batch_mutate": _action(
        "batch_mutate",
        "Batch mutate records",
        "Apply batch patch, delete, archive, or restore operations to multiple records of any registered model. "
        "For multiple records that need different patch values, use per_record_updates in one proposal instead of repeated patch_record calls.",
        side_effects=("patch:*", "delete:*"),
        risk=4,
        result_model="",
        input_properties={
            "operation": {"type": "string", "enum": ["patch", "delete", "archive", "restore"]},
            "model": {"type": "string", "description": "Any MODEL_REGISTRY registered model name."},
            "target": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["by_ids", "by_filter"]},
                    "record_ids": {"type": "array", "items": {"type": "string"}},
                    "filter": {"type": "object"},
                },
                "required": ["mode"],
            },
            "updates": {"type": "object", "description": "Field updates for patch operation."},
            "per_record_updates": {
                "type": "object",
                "description": "Optional map of record id to field updates for patch operations with different values per record.",
            },
            "patch_mode": {"type": "string", "enum": ["replace", "merge"], "description": "Use merge to preserve nested object fields such as application_record.custom_values."},
        },
        required_input=("operation", "model", "target"),
        output_properties={
            "status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING,
            "operation": _STRING, "changed_count": _INTEGER, "records": _array_of(_OBJECT), "summary": _STRING,
        },
        implemented=True,
        related_capabilities=("job", "pool", "application_record", "profile_section", "resume_section"),
        refine_contracts=("batch_record_count_verified", "batch_updates_verified"),
        confirmation_points=("proposal_before_write",),
    ),
    "organize_jobs_into_pool": _action(
        "organize_jobs_into_pool",
        "Organize jobs into pool",
        "Create or reuse a visible job pool and move selected jobs into it in one guarded transaction.",
        side_effects=("create:pool", "patch:job"),
        risk=4,
        result_model="pool",
        input_properties={
            "job_ids": _STRING_ARRAY_MIN1,
            "pool_name": {**_STRING, "minLength": 1},
            "pool_scope": _POOL_SCOPE_STRING,
            "pool_description": _STRING,
            "triage_status": _TRIAGE_STATUS_STRING,
            "reuse_existing": _BOOLEAN,
        },
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "pool": _OBJECT,
            "pool_id": _STRING,
            "pool_created": _BOOLEAN,
            "pool_scope_repaired": _BOOLEAN,
            "changed_count": _INTEGER,
            "records": _array_of(_OBJECT),
            "job_ids": _array_of(_INTEGER),
            "summary": _STRING,
        },
        implemented=True,
        allowed_on_surfaces=("global-agent", "jobs", "pools"),
        related_domains=("job", "pool"),
        related_capabilities=("job", "pool"),
        refine_contracts=("pool_membership_verified", "pool_count_verified"),
        confirmation_points=("proposal_before_write",),
        required_input=("job_ids", "pool_name"),
    ),
    "batch_delete_jobs": _action(
        "batch_delete_jobs",
        "Batch delete jobs",
        "Delete or archive multiple job postings.",
        side_effects=("patch:job", "delete:job"),
        effect_specs=(EffectSpec("patch", "job"), EffectSpec("delete", "job")),
        risk=5,
        result_model="job",
        input_properties={"job_ids": _STRING_ARRAY, "archive": _BOOLEAN},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "operation": _STRING,
            "changed_count": _INTEGER,
            "records": _array_of(_OBJECT),
            "summary": _STRING,
        },
        required_input=("job_ids",),
        implemented=True,
    ),
    "generate_cover_letter": _action(
        "generate_cover_letter",
        "Generate cover letter",
        "Generate a cover letter for a job/application context and save it to the application record.",
        side_effects=("patch:application",),
        risk=3,
        result_model="application",
        cost=3,
        input_properties={"job_id": _STRING, "application_id": _STRING, "tone": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "application": _OBJECT,
            "cover_letter": _STRING,
            "summary": _STRING,
        },
        required_input=("job_id", "application_id"),
        implemented=True,
    ),
    "auto_write_application_content": _action(
        "auto_write_application_content",
        "Auto-write application content",
        "Create or reuse the application workspace row for a job in the total application table.",
        side_effects=("create:application_record",),
        risk=2,
        result_model="application_record",
        input_properties={"job_id": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "table_id": _STRING,
            "created_count": _INTEGER,
            "records": _array_of(_OBJECT),
            "summary": _STRING,
        },
        required_input=("job_id",),
        implemented=True,
    ),
    "generate_resume": _action(
        "generate_resume",
        "Generate resume",
        "Generate a resume from profile and job context.",
        side_effects=("create:resume", "create:resume_section"),
        risk=4,
        result_model="resume",
        cost=4,
        input_properties={"profile_id": _STRING, "job_id": _STRING, "template_id": _STRING, "title": _STRING, "instructions": _STRING},
        output_properties={
            "status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING,
            "record_id": _STRING, "resume_id": _STRING, "resume": _OBJECT,
            "sections": _array_of(_OBJECT), "sections_count": _INTEGER, "refine": _OBJECT, "summary": _STRING,
        },
        required_input=("profile_id", "job_id"),
        implemented=True,
        allowed_on_surfaces=("global-agent", "resume", "optimize"),
        related_domains=("resume", "job", "profile"),
        related_capabilities=("job", "profile", "resume", "resume_section", "resume-optimizer", "resume-experience-mining"),
        readiness_gates=(
            "job_context_loaded",
            "profile_facts_loaded",
            "user_exclusions_recorded",
            "unsupported_claims_removed",
            "strategy_confirmed",
        ),
        refine_contracts=(
            "resume_not_empty",
            "canonical_section_types",
            "legacy_section_types_absent",
            "excluded_content_absent",
            "unsupported_claims_absent",
            "jd_alignment_present",
        ),
        confirmation_points=("proposal_before_write",),
        result_artifacts=("resume_id",),
    ),
    "optimize_resume": _action(
        "optimize_resume",
        "Optimize resume",
        "Optimize a resume against selected job context.",
        side_effects=("patch:resume", "create:resume_section", "patch:resume_section"),
        effect_specs=(EffectSpec("patch", "resume"), EffectSpec("create", "resume_section"), EffectSpec("patch", "resume_section")),
        risk=4,
        result_model="resume",
        cost=4,
        input_properties={"resume_id": _STRING, "job_id": _STRING, "instructions": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "resume_id": _STRING,
            "resume": _OBJECT,
            "sections": _array_of(_OBJECT),
            "changed_sections_count": _INTEGER,
            "summary": _STRING,
        },
        required_input=("resume_id", "job_id"),
        implemented=True,
    ),
    "batch_optimize_resume": _action(
        "batch_optimize_resume",
        "Batch optimize resume",
        "Queue resume optimization across multiple jobs.",
        side_effects=("create:resume", "create:resume_section"),
        risk=4,
        result_model="resume",
        is_async=True,
        cost=5,
        input_properties={"resume_id": _STRING, "job_ids": _STRING_ARRAY},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "resume_id": _STRING,
            "created_count": _INTEGER,
            "resumes": _array_of(_OBJECT),
            "sections_count": _INTEGER,
            "task_id": _STRING,
            "task_payload": _OBJECT,
            "summary": _STRING,
        },
        required_input=("resume_id", "job_ids"),
        implemented=True,
    ),
    "apply_resume_ai_patch": _action(
        "apply_resume_ai_patch",
        "Apply resume AI patch",
        "Apply a reviewed AI patch to resume content.",
        side_effects=("patch:resume", "create:resume_section", "patch:resume_section"),
        effect_specs=(EffectSpec("patch", "resume"), EffectSpec("create", "resume_section"), EffectSpec("patch", "resume_section")),
        risk=3,
        result_model="resume",
        input_properties={"resume_id": _STRING, "patch": _OBJECT},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "resume_id": _STRING,
            "resume": _OBJECT,
            "sections": _array_of(_OBJECT),
            "changed_sections_count": _INTEGER,
            "summary": _STRING,
        },
        required_input=("resume_id", "patch"),
        implemented=True,
    ),
    "parse_resume": _action(
        "parse_resume",
        "Parse resume",
        "Parse an uploaded or pasted resume into profile/resume records.",
        side_effects=("create:resume", "create:resume_section", "create:profile", "create:profile_section"),
        effect_specs=(
            EffectSpec("create", "resume"), EffectSpec("create", "resume_section"),
            EffectSpec("create", "profile"), EffectSpec("create", "profile_section"),
        ),
        risk=3,
        result_model="resume",
        cost=3,
        input_properties={"file_id": _STRING, "text": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "resume_id": _STRING,
            "resume": _OBJECT,
            "sections": _array_of(_OBJECT),
            "profile_sections": _array_of(_OBJECT),
            "summary": _STRING,
        },
        required_input=("text",),
        implemented=True,
    ),
    "profile_chat_confirm": _action(
        "profile_chat_confirm", "Confirm profile chat extraction",
        "Confirm extracted profile bullets from the current backend-bound profile chat session.",
        side_effects=("create:profile_section",), risk=3, result_model="profile_section",
        input_properties={"accepted_item_ids": _STRING_ARRAY, "candidate": _OBJECT, "edits": _OBJECT},
        output_properties={
            "status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING,
            "record_id": _STRING, "record": _OBJECT, "summary": _STRING,
        },
        implemented=True, planner_visible=False,
    ),
    "profile_agent_apply_patch": _action(
        "profile_agent_apply_patch", "Apply profile patch", "Apply a reviewed agent patch to profile data.",
        side_effects=("patch:profile", "create:profile_section", "patch:profile_section", "create:profile_target_role"),
        effect_specs=(
            EffectSpec("patch", "profile"), EffectSpec("create", "profile_section"),
            EffectSpec("patch", "profile_section"), EffectSpec("create", "profile_target_role"),
        ), risk=3, result_model="profile",
        input_properties={"profile_id": _STRING, "patch": _OBJECT},
        output_properties={
            "status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING,
            "record_id": _STRING, "applied": _BOOLEAN, "applied_sections_count": _INTEGER,
            "profile": _OBJECT, "summary": _STRING,
        },
        implemented=True, planner_visible=False,
    ),
    "optimize_agent_chat": _action("optimize_agent_chat", "Optimize agent chat", "Advance the current backend-bound conversational resume optimization session.", side_effects=("create:optimize_session", "patch:optimize_session"), risk=2, result_model=None, cost=3, input_properties={"message": _STRING, "action": {"type": "string", "enum": ["reply", "confirm", "reject", "adjust"]}, "feedback": _STRING}, implemented=True, planner_visible=False),
    "smartfill_map": _action(
        "smartfill_map",
        "SmartFill map",
        "Generate or reuse form-field mapping suggestions.",
        side_effects=("create:smartfill_run", "create:smartfill_run_log", "create:smartfill_map_cache"),
        risk=2,
        result_model=None,
        cost=2,
        input_properties={
            "fields": _array_of(_OBJECT),
            "profile": _OBJECT,
            "profileValues": _array_of(_OBJECT),
            "catalog": _array_of(_OBJECT),
            "form_schema": _OBJECT,
            "source_model": _STRING,
        },
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "runId": _STRING,
            "mappings": _array_of(_OBJECT),
            "visibility": _OBJECT,
            "summary": _STRING,
        },
        implemented=True,
    ),
    "smartfill_option_match": _action(
        "smartfill_option_match",
        "SmartFill option match",
        "Match a field value against available UI options.",
        side_effects=("create:smartfill_run_log",),
        risk=1,
        result_model=None,
        input_properties={
            "candidates": _STRING_ARRAY,
            "resume_value": _STRING,
            "level1_title": _STRING,
            "level2_title": _STRING,
            "field_name": _STRING,
            "value": _STRING,
            "options": _STRING_ARRAY,
        },
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "value": _STRING,
            "matchType": _STRING,
            "confidence": {"type": "number"},
            "summary": _STRING,
        },
        implemented=True,
    ),
    "smartfill_field_map": _action(
        "smartfill_field_map",
        "SmartFill field map",
        "Map source archive values to destination form controls.",
        side_effects=("create:smartfill_run_log",),
        risk=1,
        result_model=None,
        input_properties={
            "fragments": _array_of(_OBJECT),
            "profile": _OBJECT,
            "source_field": _STRING,
            "destination_field": _STRING,
        },
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "mappings": _array_of(_OBJECT),
            "summary": _STRING,
        },
        implemented=True,
    ),
    "smartfill_module_count": _action(
        "smartfill_module_count",
        "SmartFill module count",
        "Count detected form modules for SmartFill planning.",
        side_effects=("create:smartfill_run_log",),
        risk=1,
        result_model=None,
        input_properties={"profile": _OBJECT, "form_schema": _OBJECT},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "modules": _array_of(_OBJECT),
            "summary": _STRING,
        },
        implemented=True,
    ),
    "apply_resume_template": _action(
        "apply_resume_template",
        "Apply resume template",
        "Apply a template to an existing resume.",
        side_effects=("patch:resume",),
        risk=3,
        result_model="resume",
        input_properties={"resume_id": _STRING, "template_id": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "resume_id": _STRING,
            "template_id": _STRING,
            "resume": _OBJECT,
            "template": _OBJECT,
            "summary": _STRING,
        },
        required_input=("resume_id", "template_id"),
        implemented=True,
    ),
    "upload_resume_photo": _action(
        "upload_resume_photo",
        "Upload resume photo",
        "Upload or replace the photo attached to a resume.",
        side_effects=("patch:resume",),
        risk=3,
        result_model="resume",
        input_properties={"resume_id": _STRING, "file_id": _STRING},
        output_properties={"status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING, "record_id": _STRING, "resume_id": _STRING, "resume": _OBJECT, "summary": _STRING},
        required_input=("resume_id", "file_id"),
        implemented=True,
    ),
    "upload_resume_logo": _action(
        "upload_resume_logo",
        "Upload resume logo",
        "Upload a logo asset for a resume.",
        side_effects=("patch:resume",),
        risk=3,
        result_model="resume",
        input_properties={"resume_id": _STRING, "file_id": _STRING},
        output_properties={"status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING, "record_id": _STRING, "resume_id": _STRING, "resume": _OBJECT, "summary": _STRING},
        required_input=("resume_id", "file_id"),
        implemented=True,
    ),
    "resolve_resume_logo": _action(
        "resolve_resume_logo",
        "Resolve resume logo",
        "Resolve a company or school logo for resume display.",
        side_effects=("patch:resume",),
        risk=2,
        result_model="resume",
        input_properties={"resume_id": _STRING, "name": _STRING},
        output_properties={"status": _STRING, "tool_name": _STRING, "action": _STRING, "model": _STRING, "record_id": _STRING, "resume_id": _STRING, "resume": _OBJECT, "summary": _STRING},
        required_input=("resume_id", "name"),
        implemented=True,
    ),
    "export_resume_pdf": _action(
        "export_resume_pdf",
        "Export resume PDF",
        "Export a resume as a PDF file.",
        side_effects=(),
        effect_specs=(),
        risk=1,
        result_model=None,
        input_properties={"resume_id": _STRING},
        output_properties={"status": _STRING, "tool_name": _STRING, "action": _STRING, "resume_id": _STRING, "format": _STRING, "content_type": _STRING, "file_name": _STRING, "download_url": _STRING, "artifact": _OBJECT, "summary": _STRING},
        required_input=("resume_id",),
        implemented=True,
    ),
    "export_resume_image": _action(
        "export_resume_image",
        "Export resume image",
        "Export a resume as an image file.",
        side_effects=(),
        effect_specs=(),
        risk=1,
        result_model=None,
        input_properties={"resume_id": _STRING, "format": _STRING},
        output_properties={"status": _STRING, "tool_name": _STRING, "action": _STRING, "resume_id": _STRING, "format": _STRING, "content_type": _STRING, "file_name": _STRING, "download_url": _STRING, "artifact": _OBJECT, "summary": _STRING},
        required_input=("resume_id",),
        implemented=True,
    ),
    "analyze_resume": _action(
        "analyze_resume",
        "Analyze resume",
        "Analyze a resume and return structured suggestions.",
        side_effects=(),
        effect_specs=(),
        risk=1,
        result_model=None,
        cost=3,
        input_properties={"resume_id": _STRING, "job_id": _STRING},
        output_properties={"status": _STRING, "tool_name": _STRING, "action": _STRING, "resume_id": _STRING, "job_id": _STRING, "report": _OBJECT, "summary": _STRING},
        required_input=("resume_id",),
        implemented=True,
    ),
    "apply_resume_ai_batch": _action(
        "apply_resume_ai_batch",
        "Apply resume AI batch",
        "Apply a reviewed batch of AI resume edits.",
        side_effects=("patch:resume", "create:resume_section", "patch:resume_section"),
        effect_specs=(EffectSpec("patch", "resume"), EffectSpec("create", "resume_section"), EffectSpec("patch", "resume_section")),
        risk=4,
        result_model="resume",
        input_properties={"resume_id": _STRING, "changes": _array_of(_OBJECT)},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "resume_id": _STRING,
            "resume": _OBJECT,
            "sections": _array_of(_OBJECT),
            "changed_sections_count": _INTEGER,
            "summary": _STRING,
        },
        required_input=("resume_id", "changes"),
        implemented=True,
    ),
    "calendar_auto_fill": _action(
        "calendar_auto_fill",
        "Calendar auto-fill",
        "Create calendar event details from interview context.",
        side_effects=("create:calendar_event",),
        risk=3,
        result_model="calendar_event",
        input_properties={"notification_id": _STRING, "text": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "created_count": _INTEGER,
            "event": _OBJECT,
            "summary": _STRING,
        },
        required_input=("notification_id",),
        implemented=True,
    ),
    "interview_extract_questions": _action(
        "interview_extract_questions",
        "Extract interview questions",
        "Extract structured questions from a collected interview experience and save them to the question bank.",
        side_effects=("create:interview_question", "patch:interview_experience"),
        risk=3,
        result_model="interview_question",
        cost=3,
        input_properties={"experience_id": _STRING, "job_id": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "experience_id": _STRING,
            "created_count": _INTEGER,
            "questions": _array_of(_OBJECT),
            "rounds": _array_of(_STRING),
            "summary": _STRING,
        },
        required_input=("experience_id",),
        implemented=True,
    ),
    "profile_generate_narrative": _action(
        "profile_generate_narrative",
        "Generate profile narrative",
        "Generate narrative profile positioning from structured facts.",
        side_effects=("patch:profile",),
        risk=3,
        result_model="profile",
        cost=3,
        input_properties={"profile_id": _STRING, "target_role": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "profile": _OBJECT,
            "summary": _STRING,
        },
        required_input=("profile_id", "target_role"),
        implemented=True,
    ),
    "profile_instant_draft": _action(
        "profile_instant_draft",
        "Profile instant draft",
        "Create a quick profile draft from provided source material.",
        side_effects=("patch:profile", "create:profile_section"),
        risk=3,
        result_model="profile",
        cost=3,
        input_properties={"profile_id": _STRING, "source_text": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "profile": _OBJECT,
            "sections": _array_of(_OBJECT),
            "created_sections_count": _INTEGER,
            "summary": _STRING,
        },
        required_input=("profile_id", "source_text"),
        implemented=True,
    ),
    "interview_generate_answer": _action(
        "interview_generate_answer",
        "Generate interview answer",
        "Generate a draft answer for an interview question.",
        side_effects=("patch:interview_question",),
        risk=3,
        result_model="interview_question",
        cost=3,
        input_properties={"question_id": _STRING, "job_id": _STRING, "style": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "question": _OBJECT,
            "suggested_answer": _STRING,
            "summary": _STRING,
        },
        required_input=("question_id",),
        implemented=True,
    ),
    "ensure_application_for_job": _action(
        "ensure_application_for_job",
        "Ensure application for job",
        "Ensure one canonical Application for a job and bind its workspace projection.",
        side_effects=("create:application", "create:application_record"),
        risk=2,
        result_model="application",
        cost=1,
        input_properties={"job_id": _STRING, "table_id": _STRING},
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "application_id": _STRING,
            "application_record_id": _STRING,
            "table_id": _STRING,
            "application": _OBJECT,
            "created": _BOOLEAN,
            "created_count": _INTEGER,
            "records": _array_of(_OBJECT),
            "summary": _STRING,
        },
        required_input=("job_id",),
        implemented=True,
        allowed_on_surfaces=("global-agent", "job", "applications", "optimize"),
        related_domains=("job", "application"),
        related_capabilities=("job", "application", "application_record"),
        confirmation_points=("proposal_before_write",),
        result_artifacts=("application_id", "application_record_id"),
    ),
    "advance_application": _action(
        "advance_application",
        "Advance application",
        "Validate and perform one Application lifecycle transition.",
        side_effects=("patch:application", "patch:application_record"),
        risk=4,
        result_model="application",
        cost=1,
        input_properties={
            "application_id": _STRING,
            "target_status": _APPLICATION_STATUS_STRING,
            "notes": _STRING,
        },
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "application_id": _STRING,
            "application_record_id": _STRING,
            "application": _OBJECT,
            "before_status": _STRING,
            "after_status": _STRING,
            "summary": _STRING,
        },
        required_input=("application_id", "target_status"),
        implemented=True,
        allowed_on_surfaces=("global-agent", "job", "applications", "optimize"),
        related_domains=("job", "application"),
        related_capabilities=("job", "application", "application_record"),
        confirmation_points=("proposal_before_write",),
        result_artifacts=("application_id",),
    ),
    "prepare_application_material": _action(
        "prepare_application_material",
        "Prepare application material",
        "Prepare reviewable cover-letter material for a canonical Application.",
        side_effects=("patch:application",),
        risk=3,
        result_model="application",
        cost=1,
        input_properties={
            "application_id": _STRING,
            "material_type": {"type": "string", "enum": ["cover_letter"], "description": "Kind of application material to prepare; currently supported: cover_letter."},
            "tone": _STRING,
            "constraints": _STRING,
        },
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "record_id": _STRING,
            "application_id": _STRING,
            "application": _OBJECT,
            "material_type": _STRING,
            "material": _OBJECT,
            "summary": _STRING,
        },
        required_input=("application_id", "material_type"),
        implemented=True,
        allowed_on_surfaces=("global-agent", "job", "applications", "optimize"),
        related_domains=("job", "application"),
        related_capabilities=("job", "application", "resume"),
        confirmation_points=("proposal_before_write",),
        result_artifacts=("application_id",),
    ),
    "remember_preference": _action(
        "remember_preference",
        "Remember preference",
        "Persist one durable actor/session-scoped memory preference. Content must be a policy-style preference object; sensitive or official business data is never stored without explicit confirmation.",
        side_effects=("create:agent_memory",),
        risk=1,
        result_model="agent_memory",
        cost=1,
        input_properties={
            "category": {
                "type": "string",
                "enum": sorted(
                    {
                        "facts",
                        "preferences",
                        "goals",
                        "constraints",
                        "style_preferences",
                        "interaction_preferences",
                        "workflow_preferences",
                    }
                ),
                "description": "Memory category; the write is rejected when the category is not whitelisted.",
            },
            "topic": {
                "type": "string",
                "description": "Optional memory topic such as global, job, application, resume, or interview.",
            },
            "content": {
                "type": "object",
                "additionalProperties": True,
                "description": "Policy-style preference object (for example {\"language\": \"简体中文\"} or {\"avoid\": [...]}) that must not contain official business facts or sensitive personal data.",
            },
            "scope": {
                "type": "string",
                "enum": ["session", "actor"],
                "description": "session keeps the memory inside this conversation scope; actor makes it a durable actor-level memory.",
            },
        },
        output_properties={
            "status": _STRING,
            "tool_name": _STRING,
            "action": _STRING,
            "model": _STRING,
            "memory_id": {
                "type": "string",
                "description": "Durable AgentMemory identifier assigned when the preference is stored.",
            },
            "category": {
                "type": "string",
                "description": "Whitelisted memory category that accepted the stored preference.",
            },
            "topic": {
                "type": "string",
                "description": "Memory topic the stored preference is filed under.",
            },
            "scope": {
                "type": "string",
                "description": "Resolved memory scope: session keeps it conversation-scoped, actor makes it a durable actor-level memory.",
            },
            "needs_confirmation": {
                "type": "boolean",
                "description": "True when sensitive content was detected and explicit user confirmation is required before storage; nothing is stored in that case.",
            },
            "memory": {
                "type": "object",
                "description": "Serialized AgentMemory record produced by the guarded write.",
            },
            "confirmation": {
                "type": "object",
                "description": "Structured confirmation request returned when sensitive content prevents automatic storage.",
            },
            "summary": _STRING,
        },
        required_input=("category", "content", "scope"),
        implemented=True,
        allowed_on_surfaces=("global-agent", "job", "applications", "optimize"),
        related_domains=("memory",),
        related_capabilities=("agent_memory",),
        confirmation_points=(),
        result_artifacts=("memory_id",),
    ),
}


_IMPLEMENTED_ACTION_IMPORTS: Mapping[str, tuple[tuple[str, str], ...]] = {
    "ensure_application_for_job": (
        ("app.services.application_workspace", "ensure_canonical_application_for_job"),
        ("app.services.application_workspace", "create_records_from_jobs_no_commit"),
    ),
    "advance_application": (
        ("app.operator.application_lifecycle", "ApplicationLifecycleSpec"),
    ),
    "prepare_application_material": (
        ("app.operator.application_lifecycle", "ApplicationLifecycleSpec"),
    ),
    "import_jobs_to_application_table": (
        ("app.services.application_workspace", "create_records_from_jobs_no_commit"),
    ),
    "import_latest_extension_batch": (
        ("app.services.application_workspace", "create_records_from_jobs_no_commit"),
    ),
    "auto_write_application_content": (
        ("app.services.application_workspace", "create_records_from_jobs_no_commit"),
    ),
    "generate_resume": (
        ("app.services.profile_archive_sections", "build_personal_archive_sections"),
    ),
    "smartfill_option_match": (
        ("app.services.option_matcher", "option_match"),
    ),
    "smartfill_field_map": (
        ("app.routes.profile", "_get_or_create_default_profile"),
        ("app.routes.profile", "_load_profile_bundle"),
        ("app.routes.profile", "_serialize_profile"),
        ("app.routes.profile", "_smartfill_profile_view"),
        ("app.services.field_mapper", "field_map"),
        ("app.services.profile_schema", "normalize_base_info_payload"),
    ),
    "smartfill_module_count": (
        ("app.routes.profile", "_get_or_create_default_profile"),
        ("app.routes.profile", "_load_profile_bundle"),
        ("app.routes.profile", "_serialize_profile"),
        ("app.routes.profile", "_smartfill_profile_view"),
        ("app.services.profile_schema", "normalize_base_info_payload"),
    ),
    "smartfill_map": (
        ("app.routes.profile", "SmartFillFieldItem"),
        ("app.routes.profile", "_build_smartfill_catalog_from_profile"),
        ("app.routes.profile", "_get_or_create_default_profile"),
        ("app.routes.profile", "_load_profile_bundle"),
        ("app.routes.profile", "_new_smartfill_run_id"),
        ("app.routes.profile", "_sanitize_ai_mappings"),
        ("app.routes.profile", "_sanitize_smartfill_catalog"),
        ("app.routes.profile", "_serialize_profile"),
        ("app.services.profile_schema", "normalize_base_info_payload"),
    ),
    "remember_preference": (
        ("app.operator.memory", "memory_session_id"),
        ("app.operator.memory", "retrieve_memories"),
        ("app.operator.memory", "write_memory_candidate"),
    ),
}


def confirmable_action_names() -> frozenset[str]:
    """Actions with a real confirmation prepare path.

    Sole routing truth lives in ``proposals._invoke_action_preparers`` /
    ``proposals.confirmable_action_names``. Lazy import avoids a circular
    dependency while registry is still initializing.
    """
    from app.operator.proposals import confirmable_action_names as _from_prepare

    return _from_prepare()


def __getattr__(name: str) -> Any:
    # Backward-compatible export used by tests; always derived from prepare dispatch.
    if name == "CONFIRMABLE_ACTION_NAMES":
        return confirmable_action_names()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def action_is_agent_visible(spec: ActionSpec) -> bool:
    """Whether an action may be advertised in the agent capability map/prompt.

    Agent-visible requires all of:
    - registered as implemented
    - planner_visible (UI-only / internal actions stay hidden)
    - a confirmation prepare path exists so propose→confirm cannot dead-end
    """
    return (
        str(getattr(spec, "implementation_status", "") or "") == "implemented"
        and bool(getattr(spec, "planner_visible", True))
        and str(getattr(spec, "action", "") or "") in confirmable_action_names()
    )


def agent_visible_actions() -> dict[str, ActionSpec]:
    return {
        name: spec
        for name, spec in ACTION_REGISTRY.items()
        if action_is_agent_visible(spec)
    }


def verify_implemented_action_dependencies(*, check_prepare_paths: bool | None = None) -> None:
    """Fail closed on unusable helpers / unconfirmable agent-visible actions.

    Declared action helpers are always imported and required to be callable.
    Prepare handlers are checked when ``app.operator.proposals`` is already
    importable so registry module load does not create a circular import with
    the prepare dispatch table (the sole source of confirmable actions).
    """
    failures: list[str] = []
    missing = object()
    for action_name, imports in _IMPLEMENTED_ACTION_IMPORTS.items():
        spec = ACTION_REGISTRY.get(action_name)
        if spec is None:
            failures.append(f"{action_name}: action is not registered")
            continue
        if spec.implementation_status != "implemented":
            continue
        for module_name, attr_name in imports:
            try:
                module = importlib.import_module(module_name)
            except ImportError as exc:
                failures.append(f"{action_name}: cannot import {module_name}: {exc}")
                continue
            try:
                dependency = getattr(module, attr_name, missing)
            except Exception as exc:  # pragma: no cover - defensive module wiring
                failures.append(f"{action_name}: cannot read {module_name}.{attr_name}: {exc}")
                continue
            if dependency is missing:
                failures.append(f"{action_name}: {module_name}.{attr_name} is missing")
            elif not callable(dependency):
                failures.append(f"{action_name}: {module_name}.{attr_name} is not callable")

    import sys

    proposals_loaded = "app.operator.proposals" in sys.modules
    if check_prepare_paths is None:
        check_prepare_paths = proposals_loaded
    if check_prepare_paths:
        try:
            from app.operator.proposals import _invoke_action_preparers

            preparers = _invoke_action_preparers()
            if not isinstance(preparers, Mapping):
                raise TypeError("prepare dispatch inventory is not a mapping")
        except Exception as exc:  # pragma: no cover - wiring failures
            failures.append(f"confirmable prepare path inventory unavailable: {exc}")
            preparers = {}
        for action_name, spec in ACTION_REGISTRY.items():
            if spec.implementation_status != "implemented" or not bool(spec.planner_visible):
                continue
            preparer = preparers.get(action_name, missing)
            if preparer is missing:
                failures.append(
                    f"{action_name}: agent-visible implemented action has no confirmation prepare path"
                )
            elif not callable(preparer):
                failures.append(
                    f"{action_name}: agent-visible implemented action confirmation prepare path is not callable"
                )
    if failures:
        raise RuntimeError("Implemented action dependency check failed: " + "; ".join(failures))


# Import-time: helpers only (proposals may not be loadable yet without a cycle).
verify_implemented_action_dependencies(check_prepare_paths=False)


def _skill(
    skill: str,
    label: str,
    description: str,
    examples: tuple[str, ...],
    required: tuple[str, ...],
    pages: tuple[str, ...],
    *,
    allowed_on_surfaces: tuple[str, ...] = (),
    related_domains: tuple[str, ...] = (),
    related_capabilities: tuple[str, ...] = (),
    readiness_gates: tuple[str, ...] = (),
    refine_contracts: tuple[str, ...] = (),
    allowed_write_actions: tuple[str, ...] = (),
    confirmation_points: tuple[str, ...] = (),
) -> SkillSpec:
    return SkillSpec(
        skill=skill,
        label=label,
        description=description,
        activation_examples=examples,
        required_context=required,
        optional_context=("current_job_id", "current_resume_id", "current_profile_section_id", "current_application_id"),
        skill_path=f"backend/app/operator/skills/{skill}/SKILL.md",
        allowed_tools=UNIVERSAL_TOOL_NAMES,
        step_schema={"type": "object", "properties": {"current_step": {"type": "string"}}, "additionalProperties": True},
        interrupt_policy={"allow_deactivate": True, "allow_context_switch": True},
        exit_policy={"on_complete": "deactivate_skill", "on_cancel": "restore_or_clear_context"},
        checkpoint_policy={"before_activation": True, "before_step_change": True, "before_high_risk_confirmation": True},
        page_activation=pages,
        allowed_on_surfaces=allowed_on_surfaces,
        related_domains=related_domains,
        related_capabilities=related_capabilities,
        readiness_gates=readiness_gates,
        refine_contracts=refine_contracts,
        allowed_write_actions=allowed_write_actions,
        confirmation_points=confirmation_points,
    )


SKILL_REGISTRY: dict[str, SkillSpec] = {
    "resume-optimizer": _skill(
        "resume-optimizer",
        "Resume optimizer",
        "Generate, tailor, critique, or optimize a resume from official profile facts and job/JD context.",
        (
            "Generate a resume for this job from my profile",
            "Create a new resume from my profile and this JD",
            "Optimize this resume for the selected job",
        ),
        (),
        ("resume_optimization",),
        allowed_on_surfaces=("global-agent", "resume", "optimize"),
        related_domains=("resume", "job", "profile"),
        related_capabilities=(
            "generate_resume",
            "optimize_resume",
            "resume-experience-mining",
            "job",
            "resume",
            "resume_section",
            "profile",
            "profile_section",
        ),
        readiness_gates=(
            "job_context_loaded",
            "profile_facts_loaded",
            "user_exclusions_recorded",
            "unsupported_claims_removed",
            "strategy_confirmed",
        ),
        refine_contracts=(
            "resume_not_empty",
            "canonical_section_types",
            "legacy_section_types_absent",
            "excluded_content_absent",
            "unsupported_claims_absent",
            "jd_alignment_present",
        ),
        allowed_write_actions=("generate_resume", "optimize_resume"),
        confirmation_points=("strategy_confirmation", "proposal_before_write"),
    ),
    "resume-experience-mining": _skill(
        "resume-experience-mining",
        "Resume experience mining",
        "Interview the user to mine stronger resume bullets.",
        ("Mine my experience", "Turn this experience into bullets"),
        ("current_profile_section_id",),
        ("resume_builder", "resume_experience_mining", "experience_mining"),
        allowed_on_surfaces=("global-agent", "resume", "optimize"),
        related_domains=("resume", "profile"),
        related_capabilities=("resume-optimizer", "profile", "profile_section", "resume"),
        readiness_gates=(
            "experience_scope_confirmed",
            "role_boundary_confirmed",
            "user_exclusions_recorded",
            "strategy_confirmed",
        ),
        refine_contracts=(
            "unsupported_claims_absent",
            "excluded_content_absent",
            "resume_not_empty",
        ),
        allowed_write_actions=("profile_agent_apply_patch",),
        confirmation_points=("strategy_confirmation",),
    ),
    "profile-cleanup": _skill("profile-cleanup", "Profile cleanup", "Clean and consolidate profile facts.", ("Clean up my profile", "Organize my profile facts"), ("profile_id",), ("profile",)),
    "interview-practice": _skill("interview-practice", "Interview practice", "Run interview practice using job and interview-question context.", ("Practice an interview", "Practice interview questions"), (), ("interview",)),
    "job-triage-copilot": _skill("job-triage-copilot", "Job triage copilot", "Help review, compare, and triage collected jobs.", ("Triage my jobs", "Review my job inbox"), (), ("jobs",)),
}


PAGE_SKILL_ALIASES = {
    "resume_optimization": "resume-optimizer",
    "resume_optimizer": "resume-optimizer",
    "resume_builder": "resume-experience-mining",
    "resume_experience_mining": "resume-experience-mining",
    "experience_mining": "resume-experience-mining",
    "profile": "profile-cleanup",
    "profile_cleanup": "profile-cleanup",
    "profile-cleanup": "profile-cleanup",
    "interview": "interview-practice",
    "interview_practice": "interview-practice",
    "jobs": "job-triage-copilot",
    "job_triage": "job-triage-copilot",
}

ROUTE_SKILL_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("/resume", "optimize"), "resume-optimizer"),
    (("/resume", "builder"), "resume-experience-mining"),
    (("/profile", "cleanup"), "profile-cleanup"),
    (("/interview",), "interview-practice"),
    (("/jobs",), "job-triage-copilot"),
)


def operator_skill_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "skills"


def is_registered_skill(skill: str) -> bool:
    return _safe_skill_name(skill) in SKILL_REGISTRY


def get_skill_spec(skill: str) -> SkillSpec:
    skill_name = _safe_skill_name(skill)
    spec = SKILL_REGISTRY.get(skill_name)
    if spec is None:
        raise ValueError(f"Unknown operator skill: {skill}")
    return spec


def skill_sop_path(skill: str, *, root: pathlib.Path | None = None) -> pathlib.Path:
    spec = get_skill_spec(skill)
    skill_root = (root or operator_skill_root()).resolve()
    candidate = (skill_root / spec.skill / "SKILL.md").resolve()
    if skill_root not in candidate.parents:
        raise ValueError(f"Skill path escapes operator skill root: {skill}")
    return candidate


def resolve_skill_for_page_context(context: Any) -> str | None:
    if isinstance(context, str):
        values = [context]
    elif isinstance(context, Mapping):
        values = [
            str(context.get("page") or ""),
            str(context.get("surface") or ""),
            str(context.get("route") or ""),
            str(context.get("path") or ""),
        ]
    else:
        values = []

    normalized_values = [_normalize_context_value(value) for value in values if value]
    for value in normalized_values:
        direct = PAGE_SKILL_ALIASES.get(value)
        if direct in SKILL_REGISTRY:
            return direct
        for spec in SKILL_REGISTRY.values():
            if value in {_normalize_context_value(page) for page in spec.page_activation}:
                return spec.skill

    route_text = " ".join(value.lower() for value in values if value)
    for required_parts, skill_name in ROUTE_SKILL_RULES:
        if all(part in route_text for part in required_parts) and skill_name in SKILL_REGISTRY:
            return skill_name
    return None


def _safe_skill_name(skill: str) -> str:
    skill_name = str(skill or "").strip()
    if not skill_name or "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        return ""
    return skill_name


def _normalize_context_value(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in str(value or "")).strip("_")


OPERABLE_ORM_MODELS = {
    "Job": "job",
    "Pool": "pool",
    "Profile": "profile",
    "ProfileTargetRole": "profile_target_role",
    "ProfileSection": "profile_section",
    "ResumeTemplate": "resume_template",
    "Resume": "resume",
    "ResumeSection": "resume_section",
    "InterviewNotification": "interview_notification",
    "CalendarEvent": "calendar_event",
    "Application": "application",
    "ApplicationWorkspaceSettings": "application_workspace_settings",
    "ApplicationTemplate": "application_template",
    "ApplicationTable": "application_table",
    "ApplicationRecord": "application_record",
    "InterviewExperience": "interview_experience",
    "InterviewQuestion": "interview_question",
}


NON_OPERABLE_ORM_MODELS = {
    "Batch": ("batch", "Collection metadata; jobs expose user-operable batch membership."),
    "ProfileChatSession": ("profile_chat_session", "Internal transcript for profile extraction workflow."),
    "ApplicationTableRecord": ("application_table_record", "Join table; operate via application_table/application_record."),
    "OptimizeSession": ("optimize_session", "Internal state for resume optimization workflow."),
    "SmartFillMapCache": ("smartfill_map_cache", "Internal cache managed by SmartFill actions."),
    "SmartFillRun": ("smartfill_run", "Internal action run record."),
    "SmartFillRunLog": ("smartfill_run_log", "Internal diagnostics log."),
    "AgentSession": ("agent_session", "Operator runtime session state managed by manage_session."),
    "AgentConversation": ("agent_conversation", "Internal conversation transcript storage."),
    "AgentMemory": ("agent_memory", "Scoped memory store, not direct business CRUD."),
    "ProposalCache": ("proposal_cache", "Confirmation-gate storage managed by proposal flow."),
    "AgentAuditLog": ("agent_audit_log", "Append-only audit log."),
    "AgentCheckpoint": ("agent_checkpoint", "Runtime rollback checkpoint storage."),
}


ROUTE_ACTION_SURFACES = {
    "apply_resume_template": ("backend/app/routes/resume.py", "/{resume_id}/apply-template/{template_id}"),
    "upload_resume_photo": ("backend/app/routes/resume.py", "/{resume_id}/photo"),
    "upload_resume_logo": ("backend/app/routes/resume.py", "/{resume_id}/logo"),
    "resolve_resume_logo": ("backend/app/routes/resume.py", "/{resume_id}/logo/resolve"),
    "export_resume_pdf": ("backend/app/routes/resume.py", "/{resume_id}/export/pdf"),
    "export_resume_image": ("backend/app/routes/resume.py", "/{resume_id}/export/image"),
    "analyze_resume": ("backend/app/routes/resume.py", "/{resume_id}/ai/analyze"),
    "apply_resume_ai_batch": ("backend/app/routes/resume.py", "/{resume_id}/ai/apply-batch"),
    "calendar_auto_fill": ("backend/app/routes/calendar.py", "/auto-fill"),
    "profile_generate_narrative": ("backend/app/routes/profile.py", "/generate-narrative"),
    "profile_instant_draft": ("backend/app/routes/profile.py", "/instant-draft"),
    "interview_extract_questions": ("backend/app/routes/interview.py", "/extract"),
    "interview_generate_answer": ("backend/app/routes/interview.py", "/generate-answer"),
}


FRONTEND_API_CLIENTS = ("frontend/src/lib/api.ts",)


def _inventory_source_candidates(relative_path: str, *, start: pathlib.Path | None = None) -> tuple[pathlib.Path, ...]:
    relative = pathlib.Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"operator inventory source path must be repo-relative: {relative_path}")

    origin = (start or pathlib.Path(__file__)).resolve()
    search_from = origin if origin.is_dir() else origin.parent
    relative_parts = relative.parts
    candidates: list[pathlib.Path] = []
    for root in (search_from, *search_from.parents):
        candidates.append(root / relative)
        if relative_parts and relative_parts[0] == "backend":
            candidates.append(root / pathlib.Path(*relative_parts[1:]))

    unique_candidates: list[pathlib.Path] = []
    seen: set[pathlib.Path] = set()
    for candidate in candidates:
        if candidate not in seen:
            unique_candidates.append(candidate)
            seen.add(candidate)
    return tuple(unique_candidates)


def _resolve_inventory_source_path(relative_path: str, *, start: pathlib.Path | None = None) -> pathlib.Path:
    for candidate in _inventory_source_candidates(relative_path, start=start):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"operator inventory source not found: {relative_path}")


def _validate_source_contains(relative_path: str, expected_text: str, *, required: bool = True) -> None:
    try:
        source_path = _resolve_inventory_source_path(relative_path)
    except FileNotFoundError:
        if required:
            raise
        return
    source = source_path.read_text(encoding="utf-8")
    if expected_text not in source:
        raise RuntimeError(f"operator inventory source missing {expected_text!r} in {relative_path}")


def build_expected_inventory() -> dict[str, Any]:
    orm_models: dict[str, dict[str, str]] = {}
    for class_name, registry_key in OPERABLE_ORM_MODELS.items():
        orm_models[class_name] = {"status": "registered", "registry_key": registry_key}
    for class_name, (registry_key, reason) in NON_OPERABLE_ORM_MODELS.items():
        orm_models[class_name] = {
            "status": "non_operable",
            "registry_key": registry_key,
            "reason": reason,
        }
    route_actions: dict[str, dict[str, str]] = {}
    for action_name, (source_path, route_fragment) in ROUTE_ACTION_SURFACES.items():
        if action_name not in ACTION_REGISTRY:
            raise RuntimeError(f"operator inventory route action is not registered: {action_name}")
        _validate_source_contains(source_path, route_fragment)
        route_actions[action_name] = {
            "status": "registered",
            "registry_key": action_name,
            "source": "backend_route",
            "path": source_path,
            "route": route_fragment,
        }

    frontend_api_clients: dict[str, dict[str, str]] = {}
    for source_path in FRONTEND_API_CLIENTS:
        _validate_source_contains(source_path, "api", required=False)
        frontend_api_clients[source_path] = {"status": "registered", "source": "frontend_api_client"}

    return {
        "orm_models": orm_models,
        "actions": {name: {"status": "registered"} for name in ACTION_REGISTRY},
        "skills": {name: {"status": "registered"} for name in SKILL_REGISTRY},
        "route_actions": route_actions,
        "frontend_api_clients": frontend_api_clients,
    }


