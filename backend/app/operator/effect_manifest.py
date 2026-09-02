from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event, inspect as sa_inspect


EFFECT_MANIFEST_SCHEMA_VERSION = 2
EXECUTION_CONTRACT_SCHEMA_VERSION = 2


class EffectManifestError(RuntimeError):
    pass


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(child) for child in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _digest(value: Any) -> str:
    payload = json.dumps(_json_safe(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snake_case(name: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()


def _model_name(instance: Any) -> str:
    try:
        from app.operator.guards import MODEL_CLASSES

        for name, model_cls in MODEL_CLASSES.items():
            if isinstance(instance, model_cls):
                return str(name)
    except Exception:
        pass
    return _snake_case(type(instance).__name__)


def _column_values(instance: Any) -> dict[str, Any]:
    state = sa_inspect(instance)
    return {
        str(attribute.key): state.dict.get(attribute.key)
        for attribute in state.mapper.column_attrs
    }


def _version_from_values(instance: Any, values: Mapping[str, Any]) -> str:
    try:
        from app.operator.guards import canonical_version_from_values, get_model_spec

        return canonical_version_from_values(values, get_model_spec(_model_name(instance)))
    except Exception:
        stored = str(values.get("operator_version_hash") or "")
        if stored:
            return stored
        return _digest(values)


def _identity(instance: Any) -> str:
    state = sa_inspect(instance)
    identity = state.identity
    if identity:
        return ":".join(str(item) for item in identity)
    values = [state.dict.get(column.key) for column in state.mapper.primary_key]
    if values and all(item not in (None, "") for item in values):
        return ":".join(str(item) for item in values)
    return ""


def _changed_fields(instance: Any) -> tuple[str, ...]:
    state = sa_inspect(instance)
    changed: list[str] = []
    for attribute in state.mapper.column_attrs:
        history = state.attrs[attribute.key].history
        if history.has_changes():
            changed.append(str(attribute.key))
    return tuple(sorted(changed))


def _before_values(instance: Any) -> dict[str, Any]:
    state = sa_inspect(instance)
    values = _column_values(instance)
    for attribute in state.mapper.column_attrs:
        history = state.attrs[attribute.key].history
        if history.has_changes() and history.deleted:
            values[str(attribute.key)] = history.deleted[0]
    return values


@dataclass
class _ObservedEffect:
    instance: Any
    operation: str
    model: str
    sequence: int
    changed_fields: set[str] = field(default_factory=set)
    before_version: str = ""
    after_version: str = ""
    record_id: str = ""
    created_then_deleted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "database_record",
            "operation": self.operation,
            "model": self.model,
            "record_id": self.record_id,
            "changed_fields": sorted(self.changed_fields),
            "before_version": self.before_version,
            "after_version": self.after_version,
        }


_CURRENT_RECORDER: ContextVar[Any] = ContextVar(
    "offeru_transaction_effect_recorder", default=None
)


class TransactionEffectRecorder:
    """Observe the SQLAlchemy unit of work that actually belongs to one Plan node.

    The recorder is installed only around the business execution closure. It sees
    ORM create/patch/delete transitions across every flush, including composite
    actions, and deliberately ignores response serialization shapes.
    """

    def __init__(self, async_session: Any, *, node: Any | None = None, resolved_payload: Mapping[str, Any] | None = None):
        self.async_session = async_session
        self.session = async_session.sync_session
        self.node = node
        self.resolved_payload = dict(resolved_payload or {})
        self._effects: dict[int, _ObservedEffect] = {}
        self._explicit: list[dict[str, Any]] = []
        self._observations: list[dict[str, Any]] = []
        self._sequence = 0
        self._token = None
        self._installed = False

    def install(self) -> None:
        if self._installed:
            raise RuntimeError("effect recorder is already installed")
        event.listen(self.session, "before_flush", self._before_flush)
        event.listen(self.session, "after_flush_postexec", self._after_flush_postexec)
        event.listen(self.session, "do_orm_execute", self._do_orm_execute)
        self._token = _CURRENT_RECORDER.set(self)
        self._installed = True

    def close(self) -> None:
        if not self._installed:
            return
        event.remove(self.session, "before_flush", self._before_flush)
        event.remove(self.session, "after_flush_postexec", self._after_flush_postexec)
        event.remove(self.session, "do_orm_execute", self._do_orm_execute)
        if self._token is not None:
            _CURRENT_RECORDER.reset(self._token)
        self._installed = False

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def _observe_create(self, instance: Any) -> None:
        key = id(instance)
        effect = self._effects.get(key)
        if effect is None:
            effect = _ObservedEffect(
                instance=instance,
                operation="create",
                model=_model_name(instance),
                sequence=self._next_sequence(),
            )
            self._effects[key] = effect
        effect.changed_fields.update(_changed_fields(instance) or _column_values(instance).keys())

    def _observe_patch(self, instance: Any) -> None:
        key = id(instance)
        changed = set(_changed_fields(instance))
        if not changed:
            return
        effect = self._effects.get(key)
        if effect is None:
            before = _before_values(instance)
            effect = _ObservedEffect(
                instance=instance,
                operation="patch",
                model=_model_name(instance),
                sequence=self._next_sequence(),
                before_version=_version_from_values(instance, before),
                record_id=_identity(instance),
            )
            self._effects[key] = effect
        effect.changed_fields.update(changed)

    def _observe_delete(self, instance: Any) -> None:
        key = id(instance)
        effect = self._effects.get(key)
        if effect is not None and effect.operation == "create":
            effect.created_then_deleted = True
            return
        if effect is None:
            values = _column_values(instance)
            effect = _ObservedEffect(
                instance=instance,
                operation="delete",
                model=_model_name(instance),
                sequence=self._next_sequence(),
                before_version=_version_from_values(instance, values),
                record_id=_identity(instance),
            )
            self._effects[key] = effect
        else:
            effect.operation = "delete"
            effect.after_version = ""

    def _do_orm_execute(self, execute_state: Any) -> None:
        if bool(getattr(execute_state, "is_insert", False) or getattr(execute_state, "is_update", False) or getattr(execute_state, "is_delete", False)):
            raise EffectManifestError(
                "Core DML inside a Plan node requires an effect-aware adapter with explicit durable identities"
            )
    def _before_flush(self, session: Any, _flush_context: Any, _instances: Any) -> None:
        if session is not self.session:
            return
        for instance in list(session.new):
            self._observe_create(instance)
        for instance in list(session.dirty):
            self._observe_patch(instance)
        for instance in list(session.deleted):
            self._observe_delete(instance)

    def _after_flush_postexec(self, session: Any, _flush_context: Any) -> None:
        if session is not self.session:
            return
        for effect in self._effects.values():
            if effect.created_then_deleted:
                continue
            effect.record_id = effect.record_id or _identity(effect.instance)
            if effect.operation != "delete":
                values = _column_values(effect.instance)
                effect.after_version = _version_from_values(effect.instance, values)

    def record_explicit(
        self,
        *,
        kind: str,
        operation: str,
        model: str,
        record_id: Any,
        before_version: str = "",
        after_version: str = "",
        changed_fields: Sequence[str] = (),
    ) -> None:
        self._explicit.append(
            {
                "kind": str(kind),
                "operation": str(operation),
                "model": str(model),
                "record_id": str(record_id),
                "changed_fields": sorted(str(item) for item in changed_fields),
                "before_version": str(before_version or ""),
                "after_version": str(after_version or ""),
                "sequence": self._next_sequence(),
            }
        )

    def record_noop(self, *, model: str, record_id: Any, reason: str = "already_satisfied") -> None:
        self._observations.append({
            "kind": "no_op",
            "model": str(model),
            "record_id": str(record_id),
            "reason": str(reason),
            "sequence": self._next_sequence(),
        })

    async def finalize(self) -> dict[str, Any]:
        await self.async_session.flush()
        effects: list[tuple[int, dict[str, Any]]] = []
        for observed in self._effects.values():
            if observed.created_then_deleted:
                continue
            observed.record_id = observed.record_id or _identity(observed.instance)
            if not observed.record_id:
                raise EffectManifestError(
                    f"transaction effect {observed.operation}:{observed.model} has no durable record identity"
                )
            if observed.operation != "delete" and not observed.after_version:
                observed.after_version = _version_from_values(observed.instance, _column_values(observed.instance))
            effects.append((observed.sequence, observed.as_dict()))
        effects.extend((int(item.pop("sequence")), item) for item in self._explicit)
        ordered = [item for _, item in sorted(effects, key=lambda pair: pair[0])]
        observations = [
            {key: value for key, value in item.items() if key != "sequence"}
            for item in sorted(self._observations, key=lambda item: int(item.get("sequence") or 0))
        ]
        node = self.node
        bindings = {
            "plan_id": str(getattr(node, "plan_id", "") or ""),
            "group_id": str(getattr(node, "confirmation_group_id", "") or ""),
            "node_id": str(getattr(node, "node_id", "") or ""),
            "node_digest": str(getattr(node, "node_digest", "") or ""),
            "execution_contract_digest": str(getattr(node, "execution_contract_digest", "") or ""),
            "resolved_input_digest": _digest(self.resolved_payload),
            "resolved_authorization": _authorization_contract(
                str(getattr(node, "tool_name", "") or ""),
                str(getattr(node, "target_name", "") or ""),
                self.resolved_payload,
            ),
        }
        material = {
            "version": EFFECT_MANIFEST_SCHEMA_VERSION,
            "observation_mode": "orm_uow+explicit_adapter",
            "completeness": "complete",
            "effect_state": "committed" if ordered else "no_effect",
            "bindings": bindings,
            "effects": ordered,
            "observations": observations,
        }
        manifest = dict(material)
        manifest["digest"] = _digest(material)
        return manifest


async def execute_with_effect_manifest(
    async_session: Any,
    execution: Any,
    *,
    node: Any | None = None,
    resolved_payload: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    recorder = TransactionEffectRecorder(async_session, node=node, resolved_payload=resolved_payload)
    recorder.install()
    try:
        raw = dict(await execution())
        manifest = await recorder.finalize()
        return raw, manifest
    finally:
        recorder.close()


def record_explicit_effect(**kwargs: Any) -> None:
    recorder = _CURRENT_RECORDER.get()
    if recorder is None:
        raise EffectManifestError("explicit effect was recorded outside a node transaction recorder")
    recorder.record_explicit(**kwargs)


def record_explicit_effect_if_active(**kwargs: Any) -> None:
    """Record adapter-owned DML when running inside a Plan node recorder.

    Legacy/direct operator execution does not install a recorder; in that case the
    database fence still applies and there is no Plan manifest to extend.
    """
    recorder = _CURRENT_RECORDER.get()
    if recorder is not None:
        recorder.record_explicit(**kwargs)


def record_noop_effect(*, model: str, record_id: Any, reason: str = "already_satisfied") -> None:
    recorder = _CURRENT_RECORDER.get()
    if recorder is None:
        raise EffectManifestError("no-op observation was recorded outside a node transaction recorder")
    recorder.record_noop(model=model, record_id=record_id, reason=reason)


def empty_effect_manifest(
    *,
    effect_state: str = "no_effect",
    completeness: str = "complete",
    bindings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    material = {
        "version": EFFECT_MANIFEST_SCHEMA_VERSION,
        "observation_mode": "terminal_projection",
        "completeness": str(completeness),
        "effect_state": str(effect_state),
        "bindings": _json_safe(dict(bindings or {})),
        "effects": [],
        "observations": [],
    }
    manifest = dict(material)
    manifest["digest"] = _digest(material)
    return manifest


def build_effect_manifest(
    node: Any,
    resolved_payload: Mapping[str, Any],
    *,
    effects: Sequence[Mapping[str, Any]] = (),
    observations: Sequence[Mapping[str, Any]] = (),
    observation_mode: str = "explicit_adapter",
    completeness: str = "complete",
    effect_state: str | None = None,
) -> dict[str, Any]:
    """Build a sealed manifest for effect-aware non-ORM adapters.

    Callers must provide durable effect identities and version evidence. The
    normal validator still binds this envelope to the immutable Node contract.
    """
    normalized_effects = [_json_safe(dict(item)) for item in effects]
    normalized_observations = [_json_safe(dict(item)) for item in observations]
    state = str(effect_state or ("committed" if normalized_effects else "no_effect"))
    material = {
        "version": EFFECT_MANIFEST_SCHEMA_VERSION,
        "observation_mode": str(observation_mode),
        "completeness": str(completeness),
        "effect_state": state,
        "bindings": {
            "plan_id": str(getattr(node, "plan_id", "") or ""),
            "group_id": str(getattr(node, "confirmation_group_id", "") or ""),
            "node_id": str(getattr(node, "node_id", "") or ""),
            "node_digest": str(getattr(node, "node_digest", "") or ""),
            "execution_contract_digest": str(getattr(node, "execution_contract_digest", "") or ""),
            "resolved_input_digest": _digest(_json_safe(dict(resolved_payload))),
            "resolved_authorization": _authorization_contract(
                str(getattr(node, "tool_name", "") or ""),
                str(getattr(node, "target_name", "") or ""),
                resolved_payload,
            ),
        },
        "effects": normalized_effects,
        "observations": normalized_observations,
    }
    manifest = dict(material)
    manifest["digest"] = _digest(material)
    return manifest

def _authorization_contract(tool_name: str, target_name: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    input_payload = payload.get("input") if tool_name == "invoke_action" and isinstance(payload.get("input"), Mapping) else payload
    scopes: dict[str, list[str]] = {}
    field_scopes: dict[str, list[str]] = {}
    if tool_name == "invoke_action" and target_name == "batch_mutate":
        model_name = str(input_payload.get("model") or "").strip()
        target = input_payload.get("target")
        if not model_name or not isinstance(target, Mapping):
            raise EffectManifestError("batch_mutate authorization requires a model and materialized target")
        if str(target.get("mode") or "") != "by_ids":
            raise EffectManifestError("batch_mutate authorization requires by_filter to be materialized before sealing")
        raw_ids = target.get("record_ids")
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes, bytearray)):
            raise EffectManifestError("batch_mutate authorization record_ids must be an array")
        record_ids = sorted({str(item) for item in raw_ids if item not in (None, "")})
        if not record_ids:
            raise EffectManifestError("batch_mutate authorization cannot have an empty record scope")
        scopes[model_name] = record_ids
        if str(input_payload.get("operation") or "") == "patch":
            fields: set[str] = set()
            updates = input_payload.get("updates")
            if isinstance(updates, Mapping):
                fields.update(str(key) for key in updates)
            per_record_updates = input_payload.get("per_record_updates")
            if isinstance(per_record_updates, Mapping):
                for record_updates in per_record_updates.values():
                    if isinstance(record_updates, Mapping):
                        fields.update(str(key) for key in record_updates)
            elif isinstance(per_record_updates, Sequence) and not isinstance(per_record_updates, (str, bytes, bytearray)):
                for item in per_record_updates:
                    record_updates = item.get("updates") if isinstance(item, Mapping) else None
                    if isinstance(record_updates, Mapping):
                        fields.update(str(key) for key in record_updates)
            if fields:
                field_scopes[model_name] = sorted(fields)
    else:
        aliases = {
            "job": "job", "resume": "resume", "profile": "profile", "table": "application_table",
            "application": "application", "pool": "pool", "notification": "interview_notification",
            "interview_experience": "interview_experience", "question": "interview_question",
        }
        for key, value in input_payload.items():
            key_text = str(key)
            plural = key_text.endswith("_ids")
            if not plural and not key_text.endswith("_id"):
                continue
            stem = key_text[:-4] if plural else key_text[:-3]
            model = aliases.get(stem, stem)
            values = value if plural and isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else [value]
            normalized = sorted({str(item) for item in values if item not in (None, "")})
            if normalized:
                scopes.setdefault(model, []).extend(item for item in normalized if item not in scopes.setdefault(model, []))
        if tool_name in {"patch_record", "delete_or_archive_record"}:
            record_id = payload.get("record_id")
            if record_id not in (None, ""):
                scopes[str(target_name)] = [str(record_id)]
        if tool_name == "patch_record" and isinstance(payload.get("updates"), Mapping):
            field_scopes[str(target_name)] = sorted(str(key) for key in payload["updates"])
        elif tool_name == "invoke_action":
            mutable_fields = sorted(
                str(key) for key in input_payload
                if not str(key).endswith("_id") and not str(key).endswith("_ids")
                and str(key) not in {"archive", "instructions", "text", "message", "feedback", "format"}
            )
            if len(scopes) == 1 and mutable_fields:
                field_scopes[next(iter(scopes))] = mutable_fields
    if tool_name in {"create_record", "patch_record", "delete_or_archive_record"} and str(target_name) == "profile_section":
        field_scopes["profile"] = ["base_info_json"]
        create_data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        profile_id = str(create_data.get("profile_id") or "") if tool_name == "create_record" else str(payload.get("profile_id") or "")
        if profile_id:
            scopes["profile"] = [profile_id]
    return {
        "input_digest": _digest(_json_safe(dict(payload))),
        "record_scopes": {model: sorted(set(values)) for model, values in sorted(scopes.items())},
        "field_scopes": field_scopes,
    }


def _effect_contract_specs(tool_name: str, target_name: str, payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    if tool_name == "invoke_action":
        from app.operator.registry import ACTION_REGISTRY

        spec = ACTION_REGISTRY.get(target_name)
        if spec is None:
            raise EffectManifestError(f"action effect contract {target_name!r} is unavailable")
        return tuple(item.as_contract() for item in spec.effect_specs)
    specs: list[dict[str, Any]] = []
    for declaration in _effect_contract_strings(tool_name, target_name, payload):
        operation, _, model = declaration.partition(":")
        supporting_archive_projection = str(target_name) == "profile_section" and operation == "patch" and model == "profile"
        specs.append(
            {
                "operation": operation,
                "model": model,
                "kind": "database_record",
                "visibility": "supporting" if supporting_archive_projection else "public",
                "required": False,
                "description": (
                    "Synchronize the parent Profile personal-archive projection for the confirmed ProfileSection change."
                    if supporting_archive_projection
                    else ""
                ),
            }
        )
    return tuple(specs)


def _effect_contract_strings(tool_name: str, target_name: str, payload: Mapping[str, Any]) -> tuple[str, ...]:
    if tool_name == "create_record":
        declarations = (f"create:{target_name}",)
    elif tool_name == "patch_record":
        declarations = (f"patch:{target_name}",)
    elif tool_name == "delete_or_archive_record":
        operation = str(payload.get("operation") or "delete")
        declarations = (f"delete:{target_name}",) if operation == "delete" else (f"patch:{target_name}",)
    elif tool_name == "invoke_action":
        from app.operator.registry import ACTION_REGISTRY

        spec = ACTION_REGISTRY.get(target_name)
        if spec is None:
            raise EffectManifestError(f"action effect contract {target_name!r} is unavailable")
        return tuple(str(item) for item in spec.side_effects)
    else:
        declarations = ()
    if tool_name in {"create_record", "patch_record", "delete_or_archive_record"} and str(target_name) == "profile_section":
        return (*declarations, "patch:profile")
    return declarations

def build_execution_contract(
    *,
    tool_name: str,
    target_name: str,
    payload: Mapping[str, Any],
    typed_outputs: Mapping[str, Any],
) -> dict[str, Any]:
    result_model = ""
    if tool_name == "invoke_action":
        from app.operator.registry import ACTION_REGISTRY

        spec = ACTION_REGISTRY.get(target_name)
        result_model = str(getattr(spec, "result_model", "") or "")
    elif tool_name in {"create_record", "patch_record", "delete_or_archive_record"}:
        result_model = str(target_name)
    material = {
        "version": EXECUTION_CONTRACT_SCHEMA_VERSION,
        "tool_name": str(tool_name),
        "target_name": str(target_name),
        "result_model": result_model,
        "side_effects": list(_effect_contract_strings(str(tool_name), str(target_name), payload)),
        "effect_specs": list(_effect_contract_specs(str(tool_name), str(target_name), payload)),
        "authorization": _authorization_contract(str(tool_name), str(target_name), payload),
        "typed_outputs": _json_safe(dict(typed_outputs or {})),
    }
    material["digest"] = _digest(material)
    return material


def execution_contract_for_node(node: Any) -> dict[str, Any]:
    return build_execution_contract(
        tool_name=str(node.tool_name or ""),
        target_name=str(node.target_name or ""),
        payload=node.payload_json if isinstance(node.payload_json, Mapping) else {},
        typed_outputs=node.typed_outputs if isinstance(node.typed_outputs, Mapping) else {},
    )


def validate_node_contract(node: Any) -> dict[str, Any]:
    stored = node.execution_contract_json if isinstance(getattr(node, "execution_contract_json", None), Mapping) else {}
    stored_digest = str(getattr(node, "execution_contract_digest", "") or "")
    current = execution_contract_for_node(node)
    if not stored or not stored_digest:
        raise EffectManifestError("immutable Plan node has no versioned execution contract")
    if str(stored.get("digest") or "") != stored_digest or _digest({key: value for key, value in stored.items() if key != "digest"}) != stored_digest:
        raise EffectManifestError("immutable Plan node execution contract digest is invalid")
    if current != dict(stored):
        raise EffectManifestError("authoritative execution contract changed after Plan sealing")
    return dict(stored)


def _matches_effect_contract(declaration: Mapping[str, Any], effect: Mapping[str, Any]) -> bool:
    operation = str(declaration.get("operation") or "")
    model = str(declaration.get("model") or "")
    kind = str(declaration.get("kind") or "database_record")
    return (
        (operation == "*" or operation == str(effect.get("operation") or ""))
        and (model == "*" or model == str(effect.get("model") or ""))
        and kind == str(effect.get("kind") or "")
    )


def validate_effect_manifest(
    node: Any,
    manifest: Mapping[str, Any],
    *,
    resolved_payload: Mapping[str, Any] | None = None,
    expected_resolved_input_digest: str = "",
) -> dict[str, Any]:
    contract = validate_node_contract(node)
    version = int(manifest.get("version") or 0)
    effects = manifest.get("effects")
    observations = manifest.get("observations")
    completeness = str(manifest.get("completeness") or "")
    effect_state = str(manifest.get("effect_state") or "")
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), Mapping) else {}
    if version != EFFECT_MANIFEST_SCHEMA_VERSION or not isinstance(effects, list) or not isinstance(observations, list):
        raise EffectManifestError("node effect manifest schema version is unsupported")
    if completeness not in {"complete", "incomplete", "unknown"}:
        raise EffectManifestError("node effect manifest completeness is invalid")
    if effect_state not in {"committed", "no_effect", "rolled_back", "unknown_external", "legacy_unproven"}:
        raise EffectManifestError("node effect manifest effect state is invalid")
    if effect_state == "committed" and (completeness != "complete" or not effects):
        raise EffectManifestError("committed manifest must be complete and contain effects")
    if effect_state in {"no_effect", "rolled_back"} and (completeness != "complete" or effects):
        raise EffectManifestError("no-effect/rolled-back manifest must be complete and empty")
    if effect_state in {"unknown_external", "legacy_unproven"} and completeness == "complete":
        raise EffectManifestError("unknown effect state cannot claim complete observation")
    material = {str(key): _json_safe(value) for key, value in manifest.items() if str(key) != "digest"}
    if str(manifest.get("digest") or "") != _digest(material):
        raise EffectManifestError("node effect manifest digest is invalid")
    expected_bindings = {
        "plan_id": str(getattr(node, "plan_id", "") or ""),
        "group_id": str(getattr(node, "confirmation_group_id", "") or ""),
        "node_id": str(getattr(node, "node_id", "") or ""),
        "node_digest": str(getattr(node, "node_digest", "") or ""),
        "execution_contract_digest": str(getattr(node, "execution_contract_digest", "") or ""),
    }
    for key, value in expected_bindings.items():
        if str(bindings.get(key) or "") != value:
            raise EffectManifestError(f"node effect manifest binding {key} is invalid")
    contract_specs = contract.get("effect_specs") if isinstance(contract.get("effect_specs"), list) else []
    if not contract_specs:
        contract_specs = [
            {
                "operation": str(item).partition(":")[0],
                "model": str(item).partition(":")[2],
                "kind": "database_record",
                "visibility": "public",
                "required": False,
            }
            for item in contract.get("side_effects") or []
        ]
    authorization = contract.get("authorization") if isinstance(contract.get("authorization"), Mapping) else {}
    immutable_record_scopes = authorization.get("record_scopes") if isinstance(authorization.get("record_scopes"), Mapping) else {}
    immutable_field_scopes = authorization.get("field_scopes") if isinstance(authorization.get("field_scopes"), Mapping) else {}
    resolved_authorization = (
        bindings.get("resolved_authorization")
        if isinstance(bindings.get("resolved_authorization"), Mapping)
        else {}
    )
    bound_resolved_digest = str(bindings.get("resolved_input_digest") or "")
    trusted_resolved_authorization: Mapping[str, Any] = {}
    if resolved_payload is not None:
        expected_authorization = _authorization_contract(
            str(getattr(node, "tool_name", "") or ""),
            str(getattr(node, "target_name", "") or ""),
            resolved_payload,
        )
        expected_digest = _digest(_json_safe(dict(resolved_payload)))
        if bound_resolved_digest != expected_digest:
            raise EffectManifestError("node effect manifest resolved input digest is invalid")
        if dict(resolved_authorization) != expected_authorization:
            raise EffectManifestError("node effect manifest resolved authorization scope is invalid")
        trusted_resolved_authorization = expected_authorization
    elif expected_resolved_input_digest:
        if bound_resolved_digest != str(expected_resolved_input_digest):
            raise EffectManifestError("node effect manifest resolved input digest does not match durable execution evidence")
        if resolved_authorization:
            if str(resolved_authorization.get("input_digest") or "") != bound_resolved_digest:
                raise EffectManifestError("node effect manifest resolved authorization is not bound to its input digest")
            trusted_resolved_authorization = resolved_authorization
    resolved_record_scopes = (
        trusted_resolved_authorization.get("record_scopes")
        if isinstance(trusted_resolved_authorization.get("record_scopes"), Mapping)
        else {}
    )
    resolved_field_scopes = (
        trusted_resolved_authorization.get("field_scopes")
        if isinstance(trusted_resolved_authorization.get("field_scopes"), Mapping)
        else {}
    )
    record_scopes = {
        str(model): sorted(
            {
                str(item)
                for item in list(immutable_record_scopes.get(model) or [])
                + list(resolved_record_scopes.get(model) or [])
            }
        )
        for model in set(immutable_record_scopes) | set(resolved_record_scopes)
    }
    field_scopes = {
        str(model): sorted(
            {
                str(item)
                for item in list(immutable_field_scopes.get(model) or [])
                + list(resolved_field_scopes.get(model) or [])
            }
        )
        for model in set(immutable_field_scopes) | set(resolved_field_scopes)
    }
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[str, str, str]] = set()
    for raw in effects:
        if not isinstance(raw, Mapping):
            raise EffectManifestError("node effect manifest entry must be an object")
        item = {
            "kind": str(raw.get("kind") or ""),
            "operation": str(raw.get("operation") or ""),
            "model": str(raw.get("model") or ""),
            "record_id": str(raw.get("record_id") or ""),
            "changed_fields": sorted(str(value) for value in (raw.get("changed_fields") or [])),
            "before_version": str(raw.get("before_version") or ""),
            "after_version": str(raw.get("after_version") or ""),
        }
        if item["kind"] not in {"database_record", "external_resource", "artifact"}:
            raise EffectManifestError("node effect manifest entry kind is invalid")
        if item["operation"] not in {"create", "patch", "delete"}:
            raise EffectManifestError("node effect manifest operation is invalid")
        if not item["model"] or not item["record_id"]:
            raise EffectManifestError("node effect manifest entry identity is incomplete")
        if item["operation"] == "create" and not item["after_version"]:
            raise EffectManifestError("created effect has no after version")
        if item["operation"] == "patch" and (not item["before_version"] or not item["after_version"]):
            raise EffectManifestError("patched effect has incomplete version evidence")
        if item["operation"] == "delete" and not item["before_version"]:
            raise EffectManifestError("deleted effect has no before version")
        matched = next((spec for spec in contract_specs if isinstance(spec, Mapping) and _matches_effect_contract(spec, item)), None)
        if matched is None:
            raise EffectManifestError(
                f"transaction effect {item['operation']}:{item['model']} is outside the immutable ActionSpec contract"
            )
        item["visibility"] = str(matched.get("visibility") or "public")
        item["contract_effect"] = {
            "operation": str(matched.get("operation") or ""),
            "model": str(matched.get("model") or ""),
            "kind": str(matched.get("kind") or "database_record"),
        }
        allowed_ids = {str(value) for value in (record_scopes.get(item["model"]) or [])}
        if item["operation"] != "create" and allowed_ids and item["record_id"] not in allowed_ids:
            raise EffectManifestError(
                f"transaction effect {item['model']}:{item['record_id']} is outside the confirmed record scope"
            )
        if str(contract.get("tool_name") or "") == "patch_record":
            allowed_fields = {str(value) for value in (field_scopes.get(item["model"]) or [])}
            system_fields = {"updated_at", "operator_version_hash"}
            if allowed_fields and set(item["changed_fields"]) - allowed_fields - system_fields:
                raise EffectManifestError(
                    f"transaction effect changed fields are outside the confirmed field scope for {item['model']}"
                )
        identity = (item["operation"], item["model"], item["record_id"])
        if identity in identities:
            raise EffectManifestError("node effect manifest contains a duplicate effect identity")
        identities.add(identity)
        normalized.append(item)
    normalized_observations: list[dict[str, str]] = []
    for raw in observations:
        if not isinstance(raw, Mapping) or str(raw.get("kind") or "") != "no_op":
            raise EffectManifestError("node effect manifest observation is invalid")
        model = str(raw.get("model") or "")
        record_id = str(raw.get("record_id") or "")
        if not model or not record_id:
            raise EffectManifestError("node no-op observation identity is incomplete")
        normalized_observations.append({
            "kind": "no_op", "model": model, "record_id": record_id,
            "reason": str(raw.get("reason") or "already_satisfied"),
        })
    normalized_manifest = {
        "version": version,
        "observation_mode": str(manifest.get("observation_mode") or ""),
        "completeness": completeness,
        "effect_state": effect_state,
        "bindings": _json_safe(dict(bindings)),
        "effects": normalized,
        "observations": normalized_observations,
    }
    normalized_manifest["digest"] = _digest(normalized_manifest)
    return normalized_manifest


def has_applied_effects(manifest: Mapping[str, Any]) -> bool:
    return (
        str(manifest.get("completeness") or "") == "complete"
        and str(manifest.get("effect_state") or "") == "committed"
        and bool(manifest.get("effects"))
    )


def changed_records_from_manifest(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    if not has_applied_effects(manifest):
        return []
    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for effect in manifest.get("effects") or []:
        if (
            not isinstance(effect, Mapping)
            or str(effect.get("kind") or "") != "database_record"
            or str(effect.get("visibility") or "public") != "public"
        ):
            continue
        identity = (str(effect.get("model") or ""), str(effect.get("record_id") or ""))
        if not all(identity) or identity in seen:
            continue
        seen.add(identity)
        records.append({"model": identity[0], "id": identity[1]})
    return sorted(records, key=lambda item: (item["model"], item["id"]))


def no_op_records_from_manifest(manifest: Mapping[str, Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for observation in manifest.get("observations") or []:
        if not isinstance(observation, Mapping) or str(observation.get("kind") or "") != "no_op":
            continue
        identity = (str(observation.get("model") or ""), str(observation.get("record_id") or ""))
        if all(identity) and identity not in seen:
            seen.add(identity)
            records.append({"model": identity[0], "id": identity[1]})
    return records




