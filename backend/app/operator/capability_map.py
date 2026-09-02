from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import asdict
from typing import Any, Mapping

from app.operator.registry import (
    ACTION_REGISTRY,
    BACKEND_OWNED_ACTION_INPUT_FIELDS,
    MODEL_REGISTRY,
    SKILL_EXECUTION_CHANNEL_ACTIONS,
    SKILL_REGISTRY,
    TOOL_ARGUMENT_SCHEMAS,
    UNIVERSAL_TOOL_NAMES,
    UNIVERSAL_TOOL_SPECS,
    FieldSpec,
    ModelSpec,
    RegistryContractError,
    agent_visible_actions,
    get_skill_spec,
    operator_skill_root,
    validate_registry_contracts,
)
from app.operator.registry import _INTEGER as _SCHEMA_INTEGER
from app.operator.registry import _STRING as _SCHEMA_STRING


CAPABILITY_SCHEMA_VERSION = "part6-capability-v1"
_SESSION_OPERATIONS = tuple(
    TOOL_ARGUMENT_SCHEMAS["manage_session"]["properties"]["operation"]["enum"]
)

_MODEL_EXPORT_FIELDS = (
    "model",
    "label",
    "description",
    "primary_key",
    "readable_fields",
    "summary_fields",
    "detail_fields",
    "creatable_fields",
    "writable_fields",
    "filterable_fields",
    "search_fields",
    "long_text_fields",
    "sensitive_fields",
    "relations",
    "risk_profile",
    "default_sort",
    "ownership_scope",
    "version_field_or_hash",
)

_ACTION_EXPORT_FIELDS = (
    "action",
    "label",
    "description",
    "input_schema",
    "output_schema",
    "side_effects",
    "is_async",
    "cost_level",
    "risk_level",
    "confirmation_required",
    "result_model",
)

_SKILL_EXPORT_FIELDS = (
    "skill",
    "label",
    "description",
    "activation_examples",
    "required_context",
    "optional_context",
    "allowed_tools",
    "step_schema",
    "interrupt_policy",
    "exit_policy",
    "checkpoint_policy",
    "page_activation",
)


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_value(child) for key, child in value.items()}
    return value


def _export_spec(spec: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    raw = asdict(spec)
    return {name: _json_value(raw[name]) for name in fields}


def _field_contract(field_spec: FieldSpec) -> dict[str, Any]:
    return {
        "name": field_spec.name,
        "type": field_spec.data_type,
        "description": field_spec.description,
        "semantic_role": field_spec.semantic_role,
        "data_origin": field_spec.data_origin,
        "write_owner": field_spec.write_owner,
        "required_on_create": field_spec.required_on_create,
        "nullable": field_spec.nullable,
        "enum": list(field_spec.enum_values),
        "relation_target": field_spec.relation_target,
        "aliases": list(field_spec.aliases),
        "examples": _json_value(field_spec.examples),
        "write_guidance": field_spec.write_guidance,
        "forbidden_uses": list(field_spec.forbidden_uses),
        "permissions": {
            "readable": bool(field_spec.readable),
            "summary_visible": bool(field_spec.summary_visible),
            "detail_visible": bool(field_spec.detail_visible),
            "creatable": bool(field_spec.generic_creatable),
            "writable": bool(field_spec.generic_writable),
            "filterable": bool(field_spec.filterable),
            "searchable": bool(field_spec.searchable),
            "sortable": bool(field_spec.filterable or field_spec.name in {"id", "created_at", "updated_at"}),
            "long_text": bool(field_spec.long_text),
        },
    }


_QUERY_GRAMMAR = {
    "filter_suffixes": {
        "exact": "Equality on the plain field name; a list value means IN.",
        "_contains": "Substring/containment match on the field.",
        "_in": "Explicit IN membership; value must be an array of scalars.",
        "_gte": "Greater-than-or-equal comparison.",
        "_lte": "Less-than-or-equal comparison.",
    },
    "scalar_or_array_only": "Filter values must be scalars or arrays of scalars. Nested objects such as {\"eq\": ...} are rejected by the runtime.",
    "nested_eq_objects_rejected": True,
    "sort": "One field and one direction. Format: 'field' (asc), '-field' (desc), 'field:asc', or 'field:desc'. Only filterable fields, the primary key, and the model default sort field are sortable.",
    "search_vs_filters": "search is a keyword substring match across the model's search_fields; filters are structured comparisons on filterable_fields. They combine conjunctively.",
    "page_and_size": "page is 1-based; page_size caps returned records.",
}


def _model_operations(spec: ModelSpec) -> tuple[str, ...]:
    operations: list[str] = []
    if spec.readable_fields:
        operations.extend(["read", "query"])
    if spec.creatable_fields:
        operations.append("create")
    if spec.writable_fields:
        operations.append("patch")
    if spec.ownership_scope not in {"global_readonly", "non_operable"}:
        operations.append("delete_or_archive")
    return tuple(dict.fromkeys(operations))


def _model_operation_fields(spec: ModelSpec, operation: str) -> tuple[FieldSpec, ...]:
    if operation == "read":
        return tuple(item for item in spec.fields.values() if item.readable and item.detail_visible)
    if operation == "query":
        return tuple(
            item
            for item in spec.fields.values()
            if item.readable and (item.filterable or item.searchable or item.summary_visible)
        )
    if operation == "create":
        return tuple(item for item in spec.fields.values() if item.generic_creatable)
    if operation == "patch":
        return tuple(item for item in spec.fields.values() if item.generic_writable)
    if operation == "delete_or_archive":
        return tuple(
            item
            for item in spec.fields.values()
            if item.name in {spec.primary_key, spec.version_field_or_hash}
        )
    raise RegistryContractError(f"Unsupported model capability operation: {operation}")


def _confirmation_policy(*, risk_level: int, required: bool, write_mode: str | None = None) -> dict[str, Any]:
    is_write = required or write_mode == "plan_staged"
    return {
        "confirmation_class": "business_write" if is_write else "read_only",
        "risk_level": int(risk_level),
        "confirmations_required": 2 if int(risk_level) >= 5 else 1 if is_write else 0,
        "irreversible": False,
        "authorization_scope": "actor_session",
        "challenge_policy": "backend_issued" if int(risk_level) >= 5 else "none",
        "compensation_policy": "registry_only",
    }


def _without_digest(schema: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in schema.items() if key != "schema_digest"}


def capability_schema_digest(schema: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _without_digest(schema),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _skill_sop_contract(spec: Any) -> dict[str, Any]:
    path = (operator_skill_root() / str(spec.skill) / "SKILL.md").resolve()
    root = operator_skill_root().resolve()
    if root not in path.parents or path.name != "SKILL.md":
        raise RegistryContractError(f"Skill {spec.skill!r} has an invalid SOP path")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryContractError(
            f"Skill {spec.skill!r} SOP is unavailable at {spec.skill_path!r}"
        ) from exc
    if not content.strip():
        raise RegistryContractError(f"Skill {spec.skill!r} SOP is empty")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {"path": spec.skill_path, "digest": digest, "content": content}


def _resolve_action_spec(name: str) -> Any | None:
    """Resolve an action capability for loading.

    Agent-visible actions load normally. Skill-channel actions
    (``SKILL_EXECUTION_CHANNEL_ACTIONS``) are loadable so the model can obtain
    their exact contract before invoking them under the owning Skill; they are
    never advertised in the general catalog.
    """
    if name in agent_visible_actions():
        return agent_visible_actions()[name]
    spec = ACTION_REGISTRY.get(name)
    channel_skill = SKILL_EXECUTION_CHANNEL_ACTIONS.get(name)
    if spec is None or not channel_skill:
        return None
    try:
        skill_spec = get_skill_spec(channel_skill)
    except ValueError:
        return None
    if not getattr(skill_spec, "planner_visible", True):
        return None
    if name not in skill_spec.allowed_write_actions:
        return None
    return spec


def _required_skill_for_action(action_name: str) -> str | None:
    for skill_name, spec in SKILL_REGISTRY.items():
        if action_name in spec.allowed_write_actions:
            return skill_name
    return None


def _readiness_acquisition_plan(spec: Any) -> list[dict[str, Any]]:
    """Public acquisition path for each readiness gate.

    Readiness evidence is produced only by backend tool traces; the model
    satisfies each gate by performing the listed read operations in this
    session. The plan is advisory routing data, never trust evidence itself.
    """
    if not getattr(spec, "readiness_gates", ()):
        return []
    from app.operator.readiness import PUBLIC_READINESS_ACQUISITION

    plan = []
    for gate in spec.readiness_gates:
        entry = PUBLIC_READINESS_ACQUISITION.get(gate)
        plan.append(
            {
                "name": str(gate),
                "status": "required",
                "satisfy_with": entry or [],
            }
        )
    return plan


_SESSION_UPDATE_SCHEMAS: Mapping[str, Any] = {
    "activate_skill": {
        "type": "object",
        "properties": {
            "active_skill": {
                "type": "string",
                "description": "Registered operator Skill to activate for this session.",
            },
            "skill": {"type": "string", "description": "Alias for active_skill."},
            "name": {"type": "string", "description": "Alias for active_skill."},
            "current_step": {
                "type": "string",
                "description": "Optional initial skill step. Never satisfies readiness gates by itself.",
            },
        },
        "additionalProperties": False,
    },
    "deactivate_skill": {"type": "object", "properties": {}, "additionalProperties": False},
    "set_context": {
        "type": "object",
        "properties": {
            "current_job_id": {"oneOf": [_SCHEMA_STRING, _SCHEMA_INTEGER]},
            "current_resume_id": {"oneOf": [_SCHEMA_STRING, _SCHEMA_INTEGER]},
            "current_profile_section_id": {"oneOf": [_SCHEMA_STRING, _SCHEMA_INTEGER]},
            "current_application_id": {"oneOf": [_SCHEMA_STRING, _SCHEMA_INTEGER]},
        },
        "additionalProperties": False,
    },
    "clear_context": {"type": "object", "properties": {}, "additionalProperties": False},
    "set_skill_step": {
        "type": "object",
        "properties": {"current_step": _SCHEMA_STRING},
        "additionalProperties": False,
    },
    "restore_checkpoint": {
        "type": "object",
        "properties": {"checkpoint_id": _SCHEMA_STRING},
        "additionalProperties": False,
    },
}

_SESSION_OPERATION_EXAMPLES: Mapping[str, list[dict[str, Any]]] = {
    "activate_skill": [
        {
            "tool": "manage_session",
            "arguments": {"operation": "activate_skill", "updates": {"active_skill": "<skill_name>"}},
        }
    ],
    "set_context": [
        {
            "tool": "manage_session",
            "arguments": {"operation": "set_context", "updates": {"current_job_id": "<record_id<job>>"}},
        }
    ],
    "set_skill_step": [
        {
            "tool": "manage_session",
            "arguments": {"operation": "set_skill_step", "updates": {"current_step": "<step_name>"}},
        }
    ],
    "restore_checkpoint": [
        {
            "tool": "manage_session",
            "arguments": {"operation": "restore_checkpoint", "updates": {"checkpoint_id": "<checkpoint_id>"}},
        }
    ],
}


def _session_operation_updates_schema(operation: str) -> dict[str, Any]:
    schema = _SESSION_UPDATE_SCHEMAS.get(operation)
    if schema is None:
        raise RegistryContractError(f"Unknown session command capability: {operation}")
    return _json_value(schema)


def describe_capability_contract(kind: str, name: str, operation: str) -> dict[str, Any]:
    validate_registry_contracts()
    kind = str(kind or "").strip().lower()
    name = str(name or "").strip()
    operation = str(operation or "").strip().lower()

    if kind == "model":
        spec = MODEL_REGISTRY.get(name)
        if spec is None:
            raise RegistryContractError(f"Unknown model capability: {name}")
        if operation not in _model_operations(spec):
            raise RegistryContractError(f"Model capability {name!r} does not support operation {operation!r}")
        fields = _model_operation_fields(spec, operation)
        risk = int(spec.risk_profile.get(operation.split("_", 1)[0], 0))
        required = operation in {"create", "patch", "delete_or_archive"}
        schema: dict[str, Any] = {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "kind": kind,
            "name": name,
            "operation": operation,
            "purpose": spec.description,
            "record_envelope": {
                "id_field": spec.primary_key,
                "version_field_or_hash": spec.version_field_or_hash,
                "ownership_scope": spec.ownership_scope,
            },
            "fields": {item.name: _field_contract(item) for item in fields},
            "operation_examples": _model_examples(name, operation),
            "confirmation_policy": _confirmation_policy(risk_level=risk, required=required),
            "result_contract": _json_value(asdict(UNIVERSAL_TOOL_SPECS[_tool_for_model_operation(operation)].result_contract)),
        }
        if operation == "query":
            schema["query_grammar"] = _QUERY_GRAMMAR
        elif operation == "read":
            schema["query_grammar"] = {
                "filter_suffixes": _QUERY_GRAMMAR["filter_suffixes"],
                "scalar_or_array_only": _QUERY_GRAMMAR["scalar_or_array_only"],
                "nested_eq_objects_rejected": True,
            }
    elif kind == "action":
        spec = _resolve_action_spec(name)
        if spec is None:
            raise RegistryContractError(f"Unknown or non-operable action capability: {name}")
        if operation != "invoke":
            raise RegistryContractError(f"Action capability {name!r} only supports operation 'invoke'")
        required_skill = _required_skill_for_action(name)
        conditional_rules = list(spec.conditional_rules)
        if not conditional_rules and isinstance(spec.input_schema, Mapping):
            imported_rules = (spec.input_schema.get("x-conditional-rules") or ())
            conditional_rules = list(imported_rules)
        backend_owned_inputs = sorted(
            BACKEND_OWNED_ACTION_INPUT_FIELDS & set((spec.input_schema.get("properties") or {}))
        )
        typed_outputs = {
            parameter.name: parameter.semantic_type
            for parameter in spec.output_parameters.values()
            if parameter.referenceable
        }
        schema = {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "kind": kind,
            "name": name,
            "operation": operation,
            "purpose": spec.description,
            "input_schema": _json_value(spec.input_schema),
            "output_schema": _json_value(spec.output_schema),
            "input_parameters": {param_name: _json_value(asdict(parameter)) for param_name, parameter in spec.input_parameters.items()},
            "output_parameters": {param_name: _json_value(asdict(parameter)) for param_name, parameter in spec.output_parameters.items()},
            "required_input_names": [
                parameter.name for parameter in spec.input_parameters.values() if parameter.required
            ],
            "conditional_rules": conditional_rules,
            "backend_owned_inputs": backend_owned_inputs,
            "semantic_types": {
                param_name: parameter.semantic_type for param_name, parameter in spec.input_parameters.items()
            },
            "side_effects": list(spec.side_effects),
            "effect_specs": [effect.as_contract() for effect in spec.effect_specs],
            "allowed_surfaces": list(spec.allowed_on_surfaces),
            "related_domains": list(spec.related_domains),
            "related_capabilities": list(spec.related_capabilities),
            "required_skill": required_skill,
            "readiness_gates": list(spec.readiness_gates),
            "readiness_acquisition": _readiness_acquisition_plan(spec),
            "refine_contracts": list(spec.refine_contracts),
            "confirmation_points": list(spec.confirmation_points),
            "result_artifacts": list(spec.result_artifacts),
            "referenceable_typed_outputs": typed_outputs,
            "result_model": spec.result_model,
            "public_status_contract": {
                "status_type": "operation_status",
                "completion_reason_type": "completion_reason",
                "durable_result_required": True,
            },
            "operation_examples": [{"tool": "invoke_action", "arguments": {"action": name, "input": {parameter: f"<{contract.semantic_type}>" for parameter, contract in spec.input_parameters.items() if contract.required}}}],
            "write_mode": spec.write_mode,
            "confirmation_policy": _confirmation_policy(
                risk_level=int(spec.risk_level),
                required=bool(spec.confirmation_required),
                write_mode=spec.write_mode,
            ),
            "result_contract": _json_value(asdict(UNIVERSAL_TOOL_SPECS["invoke_action"].result_contract)),
        }
    elif kind == "session-command":
        if name != "manage_session" or operation not in _SESSION_OPERATIONS:
            raise RegistryContractError(f"Unknown session command capability: {name}.{operation}")
        schema = {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "kind": kind,
            "name": name,
            "operation": operation,
            "purpose": UNIVERSAL_TOOL_SPECS["manage_session"].description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "operation": {"const": operation},
                    "updates": _session_operation_updates_schema(operation),
                },
                "required": ["operation", "updates"],
                "additionalProperties": False,
            },
            "operation_examples": _SESSION_OPERATION_EXAMPLES.get(operation, []),
            "confirmation_policy": _confirmation_policy(risk_level=1, required=False),
            "result_contract": _json_value(asdict(UNIVERSAL_TOOL_SPECS["manage_session"].result_contract)),
        }
    elif kind == "skill":
        spec = SKILL_REGISTRY.get(name)
        if spec is None or not getattr(spec, "planner_visible", True):
            raise RegistryContractError(f"Unknown or non-visible Skill capability: {name}")
        if operation != "activate":
            raise RegistryContractError(f"Skill capability {name!r} only supports operation 'activate'")
        schema = {
            "schema_version": CAPABILITY_SCHEMA_VERSION,
            "kind": kind,
            "name": name,
            "operation": operation,
            "purpose": spec.description,
            "activation_examples": list(spec.activation_examples),
            "required_context": list(spec.required_context),
            "optional_context": list(spec.optional_context),
            "allowed_tools": list(spec.allowed_tools),
            "step_schema": _json_value(spec.step_schema),
            "interrupt_policy": _json_value(spec.interrupt_policy),
            "exit_policy": _json_value(spec.exit_policy),
            "checkpoint_policy": _json_value(spec.checkpoint_policy),
            "allowed_on_surfaces": list(spec.allowed_on_surfaces),
            "related_domains": list(spec.related_domains),
            "related_capabilities": list(spec.related_capabilities),
            "readiness_gates": list(spec.readiness_gates),
            "refine_contracts": list(spec.refine_contracts),
            "allowed_write_actions": list(spec.allowed_write_actions),
            "confirmation_points": list(spec.confirmation_points),
            "activation": {
                "tool": "manage_session",
                "operation": "activate_skill",
                "updates": {"active_skill": name},
            },
            "sop": _skill_sop_contract(spec),
            "confirmation_policy": _confirmation_policy(risk_level=1, required=False),
            "result_contract": _json_value(asdict(UNIVERSAL_TOOL_SPECS["manage_session"].result_contract)),
        }
    else:
        raise RegistryContractError(f"Unsupported capability kind: {kind}")

    schema["schema_digest"] = capability_schema_digest(schema)
    return schema


def _tool_for_model_operation(operation: str) -> str:
    return {
        "read": "get_record",
        "query": "query_records",
        "create": "create_record",
        "patch": "patch_record",
        "delete_or_archive": "delete_or_archive_record",
    }[operation]


def _model_examples(name: str, operation: str) -> list[dict[str, Any]]:
    if (name, operation) == ("job", "patch"):
        return [
            {
                "model": "job",
                "record_id": 1,
                "updates": {
                    "triage_status": "picked",
                    "user_notes": "适合 agent 产品和数据分析方向",
                },
                "patch_mode": "replace",
            }
        ]
    if (name, operation) == ("job", "create"):
        return [
            {
                "model": "job",
                "data": {
                    "title": "AI Agent PM",
                    "company": "Nova Labs",
                    "raw_description": "Own agent workflow and product analytics.",
                },
            }
        ]
    return []


def _catalog_load_via(kind: str, name: str, operation: str) -> dict[str, str]:
    # Compactly routed: kind/name/operation are sibling fields of the same
    # entry, so load_via only needs to name the loading tool.
    return {"tool": "describe_capability"}


def _compact_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Drop empty routing fields so the index stays minimal and deterministic.

    Absent fields mean "none" (no required skill, no readiness gates, no typed
    outputs). The loaded operation contract always carries the authoritative
    complete values.
    """
    return {
        key: value
        for key, value in entry.items()
        if value not in (None, "", [], {}, ())
    }


def export_capability_catalog() -> dict[str, Any]:
    validate_registry_contracts()
    visible_actions = agent_visible_actions()
    models = []
    for name, spec in MODEL_REGISTRY.items():
        operations = _model_operations(spec)
        models.append(
            _compact_entry(
                {
                    "name": name,
                    "kind": "model",
                    "purpose": spec.description,
                    "operations": list(operations),
                    "write_mode": "plan_staged" if (spec.creatable_fields or spec.writable_fields) else "read_only",
                    "schema_digests": {
                        operation: describe_capability_contract("model", name, operation)["schema_digest"]
                        for operation in operations
                    },
                    "load_via": _catalog_load_via("model", name, operations[0] if operations else "read"),
                }
            )
        )
    actions = [
        _compact_entry(
            {
                "name": name,
                "kind": "action",
                "domain": str(spec.related_domains[0]) if spec.related_domains else name,
                "purpose": spec.description,
                "operations": ["invoke"],
                "required_input_names": [
                    parameter.name for parameter in spec.input_parameters.values() if parameter.required
                ],
                "required_skill": _required_skill_for_action(name),
                "readiness_gate_names": list(spec.readiness_gates),
                "write_mode": spec.write_mode,
                "confirmation_class": _confirmation_policy(
                    risk_level=int(spec.risk_level),
                    required=bool(spec.confirmation_required),
                    write_mode=spec.write_mode,
                )["confirmation_class"],
                "typed_output_names": [
                    parameter.name for parameter in spec.output_parameters.values()
                    if parameter.referenceable
                ],
                "schema_digests": {
                    "invoke": describe_capability_contract("action", name, "invoke")["schema_digest"]
                },
                "load_via": _catalog_load_via("action", name, "invoke"),
            }
        )
        for name, spec in visible_actions.items()
    ]
    session_commands = [
        _compact_entry(
            {
                "name": "manage_session",
                "kind": "session-command",
                "purpose": UNIVERSAL_TOOL_SPECS["manage_session"].description,
                "operations": list(_SESSION_OPERATIONS),
                "write_mode": "plan_staged",
                "schema_digests": {
                    operation: describe_capability_contract("session-command", "manage_session", operation)["schema_digest"]
                    for operation in _SESSION_OPERATIONS
                },
                "load_via": _catalog_load_via("session-command", "manage_session", "set_context"),
            }
        )
    ]
    skills = [
        _compact_entry(
            {
                "name": name,
                "kind": "skill",
                "purpose": spec.description,
                "readiness_gate_names": list(spec.readiness_gates),
                "allowed_write_action_names": list(spec.allowed_write_actions),
                "schema_digests": {
                    "activate": describe_capability_contract("skill", name, "activate")["schema_digest"]
                },
                "load_via": _catalog_load_via("skill", name, "activate"),
                "activation": {
                    "tool": "manage_session",
                    "operation": "activate_skill",
                    "updates": {"active_skill": name},
                },
            }
        )
        for name, spec in SKILL_REGISTRY.items()
        if getattr(spec, "planner_visible", True)
    ]
    return {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "models": models,
        "actions": actions,
        "session_commands": session_commands,
        "skills": skills,
    }


def export_capability_map() -> dict[str, Any]:
    """Export the complete external capability surface; prompts use the compact catalog."""
    from app.operator.registry import verify_implemented_action_dependencies

    verify_implemented_action_dependencies(check_prepare_paths=True)
    validate_registry_contracts()
    visible_actions = agent_visible_actions()
    return {
        "tools": list(UNIVERSAL_TOOL_NAMES),
        "models": {
            name: {
                **_export_spec(spec, _MODEL_EXPORT_FIELDS),
                "fields": {field_name: _field_contract(field_spec) for field_name, field_spec in spec.fields.items()},
            }
            for name, spec in MODEL_REGISTRY.items()
        },
        "actions": {
            name: {
                **_export_spec(spec, _ACTION_EXPORT_FIELDS),
                "invoke_via": f'invoke_action(action="{name}", input={{...}})',
            }
            for name, spec in visible_actions.items()
        },
        "skills": {
            name: _export_spec(spec, _SKILL_EXPORT_FIELDS)
            for name, spec in SKILL_REGISTRY.items()
            if getattr(spec, "planner_visible", True)
        },
    }
