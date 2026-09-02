from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from sqlalchemy.exc import IntegrityError

from app.models import models
from app.operator.capability_map import describe_capability_contract
from app.operator.registry import ACTION_REGISTRY, MODEL_REGISTRY, compensation_spec_key


PLAN_STAGING_STATE_KEY = "plan_staging_enforced"
PLAN_TURN_KEY = "_plan_turn_key"
PLAN_DRAFT_STATE_KEY = "_plan_draft_id"


class PlanStateError(RuntimeError):
    pass


class PlanCompilationError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _target(tool_name: str, args: Mapping[str, Any]) -> tuple[str, str, str]:
    if tool_name in {"query_records", "get_record", "create_record", "patch_record", "delete_or_archive_record"}:
        return "model", str(args.get("model") or ""), str(args.get("record_id") or "")
    if tool_name == "invoke_action":
        return "action", str(args.get("action") or ""), ""
    if tool_name == "manage_session":
        return "session-command", "manage_session", ""
    raise PlanStateError(f"Tool {tool_name!r} cannot stage a Plan intent")


async def recover_collecting_plan_draft_id(db: Any, actor: Any, *, turn_key: str, session_id: str = "") -> str:
    """Return the durable collecting draft for a replayed logical turn."""
    draft_id = await db.scalar(
        select(models.AgentPlanDraft.draft_id).where(
            models.AgentPlanDraft.actor_id == str(actor.actor_id),
            models.AgentPlanDraft.session_id == str(session_id or getattr(actor, "session_id", "")),
            models.AgentPlanDraft.turn_key == str(turn_key or ""),
            models.AgentPlanDraft.status == "collecting",
        )
    )
    return str(draft_id or "")


async def stage_plan_intent(
    db: Any,
    actor: Any,
    *,
    turn_key: str,
    canonical_effect_key: str,
    tool_name: str,
    args: Mapping[str, Any],
    base_version: str = "",
    atomic_group_id: str = "",
    commit: bool = True,
) -> models.AgentPlanIntent:
    turn_key = str(turn_key or "").strip()
    effect_key = str(canonical_effect_key or "").strip()
    if not turn_key or not effect_key:
        raise PlanStateError("turn_key and canonical_effect_key are required")

    args_json = _json_copy(dict(args))
    base_version_value = str(base_version or "")
    atomic_group_value = str(atomic_group_id or "")
    args_digest = _digest({
        "tool_name": tool_name,
        "args": args_json,
        "base_version": base_version_value,
        "atomic_group_id": atomic_group_value,
    })
    target_kind, target_name, record_id = _target(tool_name, args_json)

    def validate_replay(existing: models.AgentPlanIntent) -> models.AgentPlanIntent:
        if (
            str(existing.args_digest or "") != args_digest
            or str(existing.tool_name or "") != str(tool_name)
            or dict(existing.args_json or {}) != args_json
            or str(existing.base_version or "") != base_version_value
            or str(existing.atomic_group_id or "") != atomic_group_value
            or str(existing.target_kind or "") != target_kind
            or str(existing.target_name or "") != target_name
            or str(existing.record_id or "") != record_id
        ):
            raise PlanStateError("Canonical effect identity replay changed its staged intent payload")
        return existing

    async def load_draft() -> models.AgentPlanDraft | None:
        return (
            await db.execute(
                select(models.AgentPlanDraft).where(
                    models.AgentPlanDraft.actor_id == str(actor.actor_id),
                    models.AgentPlanDraft.session_id == str(actor.session_id),
                    models.AgentPlanDraft.turn_key == turn_key,
                ).execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def load_existing(draft_id: str) -> models.AgentPlanIntent | None:
        return (
            await db.execute(
                select(models.AgentPlanIntent).where(
                    models.AgentPlanIntent.draft_id == str(draft_id),
                    models.AgentPlanIntent.canonical_effect_key == effect_key,
                ).execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    for attempt in range(5):
        draft = await load_draft()
        if draft is None:
            draft = models.AgentPlanDraft(
                draft_id=f"draft_{uuid.uuid4().hex}",
                actor_id=str(actor.actor_id),
                session_id=str(actor.session_id),
                turn_key=turn_key,
                status="collecting",
            )
            db.add(draft)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                if attempt == 4:
                    raise
                continue
        if draft.status != "collecting":
            raise PlanStateError(f"Plan draft is {draft.status}; sealed or terminal drafts are immutable")

        existing = await load_existing(str(draft.draft_id))
        if existing is not None:
            return validate_replay(existing)

        expected_revision = int(draft.revision or 0)
        next_sequence = expected_revision + 1

        async def claim_and_insert() -> models.AgentPlanIntent | None:
            claimed = await db.execute(
                update(models.AgentPlanDraft)
                .where(
                    models.AgentPlanDraft.draft_id == str(draft.draft_id),
                    models.AgentPlanDraft.actor_id == str(actor.actor_id),
                    models.AgentPlanDraft.session_id == str(actor.session_id),
                    models.AgentPlanDraft.status == "collecting",
                    models.AgentPlanDraft.revision == expected_revision,
                )
                .values(revision=next_sequence)
                .execution_options(synchronize_session=False)
            )
            if int(claimed.rowcount or 0) != 1:
                return None
            intent = models.AgentPlanIntent(
                intent_id=f"intent_{uuid.uuid4().hex}",
                draft_id=str(draft.draft_id),
                canonical_effect_key=effect_key,
                sequence=next_sequence,
                state="active",
                tool_name=tool_name,
                target_kind=target_kind,
                target_name=target_name,
                record_id=record_id,
                base_version=base_version_value,
                atomic_group_id=atomic_group_value,
                args_json=args_json,
                args_digest=args_digest,
            )
            db.add(intent)
            draft.revision = next_sequence
            return intent

        try:
            if commit:
                intent = await claim_and_insert()
                if intent is None:
                    await db.rollback()
                    continue
                await db.commit()
            else:
                intent = None
                async with db.begin_nested():
                    intent = await claim_and_insert()
                    if intent is not None:
                        await db.flush()
                if intent is None:
                    continue
            return intent
        except IntegrityError:
            await db.rollback()
            winner_draft = await load_draft()
            if winner_draft is not None:
                winner = await load_existing(str(winner_draft.draft_id))
                if winner is not None:
                    return validate_replay(winner)
            if attempt == 4:
                raise

    raise PlanStateError("Concurrent Plan intent staging could not establish durable canonical ownership")


def _typed_outputs(tool_name: str, target_name: str) -> dict[str, dict[str, Any]]:
    if tool_name in {"create_record", "patch_record", "delete_or_archive_record"}:
        return {
            "primary_record_id": {
                "semantic_type": f"record_id<{target_name}>",
                "referenceable": True,
            }
        }
    if tool_name == "invoke_action":
        action = ACTION_REGISTRY.get(target_name)
        if action is None:
            return {}
        return {
            name: {
                "json_type": parameter.json_type,
                "semantic_type": parameter.semantic_type,
                "referenceable": parameter.referenceable,
                "durable": parameter.durable,
                "required": parameter.required,
            }
            for name, parameter in action.output_parameters.items()
        }
    return {}


def _intent_descriptors(intents: list[models.AgentPlanIntent]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": int(intent.sequence or 0),
            "tool_name": str(intent.tool_name),
            "target_kind": str(intent.target_kind),
            "target_name": str(intent.target_name),
            "record_id": str(intent.record_id or ""),
            "base_version": str(intent.base_version or ""),
            "atomic_group_id": str(intent.atomic_group_id or ""),
            "args": _json_copy(intent.args_json or {}),
            "source_intent_ids": [str(intent.intent_id)],
            "source_effect_keys": [str(intent.canonical_effect_key)],
        }
        for intent in intents
    ]


def staged_intent_output_references(intent: models.AgentPlanIntent) -> dict[str, dict[str, Any]]:
    """Return copyable, same-Plan typed references for one durable staged intent."""
    declarations = _typed_outputs(str(intent.tool_name), str(intent.target_name))
    references: dict[str, dict[str, Any]] = {}
    for output_name, declaration in declarations.items():
        semantic_type = str(declaration.get("semantic_type") or "")
        if not semantic_type or declaration.get("referenceable") is not True:
            continue
        references[str(output_name)] = {
            "$output": {
                "intent_key": str(intent.intent_id),
                "name": str(output_name),
                "semantic_type": semantic_type,
            }
        }
    return references


def _batch_triage_job_ids(descriptor: Mapping[str, Any]) -> list[str]:
    if descriptor.get("tool_name") != "invoke_action" or descriptor.get("target_name") != "batch_triage_jobs":
        return []
    args = descriptor.get("args") if isinstance(descriptor.get("args"), Mapping) else {}
    input_payload = args.get("input") if isinstance(args.get("input"), Mapping) else {}
    raw_ids = input_payload.get("job_ids")
    if not isinstance(raw_ids, list):
        return []
    return list(dict.fromkeys(str(item) for item in raw_ids if item not in (None, "")))


def _coalesce_batch_triage_job_patches(descriptors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    triage_indexes = {
        index: set(_batch_triage_job_ids(descriptor))
        for index, descriptor in enumerate(descriptors)
        if _batch_triage_job_ids(descriptor)
    }
    patches_by_triage: dict[int, list[int]] = defaultdict(list)
    consumed_patches: set[int] = set()
    for patch_index, patch in enumerate(descriptors):
        if patch.get("tool_name") != "patch_record" or patch.get("target_name") != "job":
            continue
        record_id = str(patch.get("record_id") or "")
        overlaps = [index for index, job_ids in triage_indexes.items() if record_id in job_ids]
        if not overlaps:
            continue
        if len(overlaps) != 1:
            raise PlanCompilationError(
                f"job {record_id} is targeted by multiple batch_triage_jobs intents in one PlanDraft"
            )
        triage_index = overlaps[0]
        triage = descriptors[triage_index]
        if str(triage.get("base_version") or "") != str(patch.get("base_version") or ""):
            raise PlanCompilationError(
                f"batch triage and patch intents for job {record_id} have different base versions"
            )
        patch_args = patch.get("args") if isinstance(patch.get("args"), Mapping) else {}
        if str(patch_args.get("patch_mode") or "replace") != "replace":
            raise PlanCompilationError(
                f"batch triage cannot merge non-replace patch mode for job {record_id}"
            )
        patches_by_triage[triage_index].append(patch_index)
        consumed_patches.add(patch_index)

    normalized: list[dict[str, Any]] = []
    for index, descriptor in enumerate(descriptors):
        if index in consumed_patches:
            continue
        patch_indexes = patches_by_triage.get(index)
        if not patch_indexes:
            normalized.append(descriptor)
            continue
        input_payload = dict(descriptor["args"].get("input") or {})
        job_ids = _batch_triage_job_ids(descriptor)
        base_updates: dict[str, Any] = {}
        triage_status = input_payload.get("triage_status")
        if triage_status not in (None, ""):
            base_updates["triage_status"] = triage_status
        pool_id = input_payload.get("pool_id")
        if pool_id not in (None, "", 0, "0"):
            pool_text = str(pool_id)
            base_updates["pool_id"] = int(pool_text) if pool_text.isdigit() else pool_id
        per_record_updates = {record_id: _json_copy(base_updates) for record_id in job_ids}
        source_intent_ids = list(descriptor["source_intent_ids"])
        source_effect_keys = list(descriptor["source_effect_keys"])
        atomic_ids = {str(descriptor.get("atomic_group_id") or "")}
        for patch_index in patch_indexes:
            patch = descriptors[patch_index]
            patch_args = patch["args"]
            updates = patch_args.get("updates")
            if not isinstance(updates, Mapping):
                raise PlanCompilationError(
                    f"patch intent {patch['source_intent_ids'][0]} updates must be an object"
                )
            record_id = str(patch.get("record_id") or "")
            current = per_record_updates.setdefault(record_id, _json_copy(base_updates))
            for field, value in updates.items():
                if field in current and current[field] != value:
                    raise PlanCompilationError(
                        f"conflicting values for job.{record_id}.{field}; last-write-wins is forbidden"
                    )
                current[str(field)] = _json_copy(value)
            source_intent_ids.extend(str(item) for item in patch["source_intent_ids"])
            source_effect_keys.extend(str(item) for item in patch["source_effect_keys"])
            atomic_ids.add(str(patch.get("atomic_group_id") or ""))
        nonempty_atomic_ids = {item for item in atomic_ids if item}
        if len(nonempty_atomic_ids) > 1:
            raise PlanCompilationError("same-record cross-tool merged intents cannot cross AtomicGroup boundaries")
        normalized.append(
            {
                "sequence": int(descriptor.get("sequence") or 0),
                "tool_name": "invoke_action",
                "target_kind": "action",
                "target_name": "batch_mutate",
                "record_id": "",
                "base_version": str(descriptor.get("base_version") or ""),
                "atomic_group_id": next(iter(nonempty_atomic_ids), ""),
                "args": {
                    "action": "batch_mutate",
                    "input": {
                        "operation": "patch",
                        "model": "job",
                        "target": {"mode": "by_ids", "record_ids": job_ids},
                        "updates": {},
                        "per_record_updates": per_record_updates,
                        "patch_mode": "replace",
                    },
                },
                "source_intent_ids": list(dict.fromkeys(source_intent_ids)),
                "source_effect_keys": list(dict.fromkeys(source_effect_keys)),
            }
        )
    return normalized


async def _materialize_intent_descriptors(
    db: Any,
    actor: Any,
    intents: list[models.AgentPlanIntent],
) -> list[dict[str, Any]]:
    descriptors = _intent_descriptors(intents)
    for descriptor in descriptors:
        if descriptor.get("tool_name") != "invoke_action" or descriptor.get("target_name") != "batch_mutate":
            continue
        args = descriptor.get("args")
        input_payload = args.get("input") if isinstance(args, Mapping) else None
        if not isinstance(input_payload, Mapping):
            raise PlanCompilationError("batch_mutate input must be an object before Plan sealing")
        model_name = str(input_payload.get("model") or "").strip()
        if not model_name:
            raise PlanCompilationError("batch_mutate model is required before Plan sealing")
        if model_name not in MODEL_REGISTRY:
            raise PlanCompilationError(f"batch_mutate model {model_name!r} is not registered")
        target = input_payload.get("target")
        if not isinstance(target, Mapping):
            raise PlanCompilationError("batch_mutate target must be an object before Plan sealing")
        mode = str(target.get("mode") or "").strip().lower()
        if mode == "by_filter":
            filter_payload = target.get("filter")
            if not isinstance(filter_payload, Mapping) or not filter_payload:
                raise PlanCompilationError("batch_mutate by_filter requires a non-empty filter object")
            if target.get("record_ids"):
                raise PlanCompilationError("batch_mutate by_filter cannot also declare record_ids")
            from app.operator.tools import _resolve_record_ids_from_filter

            try:
                resolved_ids = await _resolve_record_ids_from_filter(
                    db, actor, model_name, dict(filter_payload)
                )
            except Exception as exc:
                raise PlanCompilationError(
                    f"batch_mutate by_filter could not be materialized in actor scope: {exc}"
                ) from exc
            record_ids = [str(record_id) for record_id in resolved_ids]
            if not record_ids:
                raise PlanCompilationError("batch_mutate by_filter resolved to zero actor-scoped records")
            if len(record_ids) > 500:
                raise PlanCompilationError("batch_mutate by_filter resolved to more than 500 records")
        elif mode == "by_ids":
            raw_ids = target.get("record_ids")
            if not isinstance(raw_ids, list):
                raise PlanCompilationError("batch_mutate by_ids record_ids must be an array")
            record_ids = list(
                dict.fromkeys(str(record_id) for record_id in raw_ids if record_id not in (None, ""))
            )
            if not record_ids:
                raise PlanCompilationError("batch_mutate by_ids requires at least one record id")
            if len(record_ids) > 500:
                raise PlanCompilationError("batch_mutate by_ids cannot contain more than 500 records")
        else:
            raise PlanCompilationError("batch_mutate target.mode must be by_ids or by_filter")
        record_ids.sort(key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
        sealed_input = _json_copy(dict(input_payload))
        sealed_input["target"] = {"mode": "by_ids", "record_ids": record_ids}
        sealed_args = _json_copy(dict(args))
        sealed_args["input"] = sealed_input
        descriptor["args"] = sealed_args
    return descriptors


def _normalize_descriptors(descriptors: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    nodes: list[dict[str, Any]] = []
    patch_indexes: dict[tuple[str, str, str], int] = {}
    intent_node_index: dict[str, int] = {}
    descriptors = _coalesce_batch_triage_job_patches(descriptors)
    for descriptor in descriptors:
        args = _json_copy(descriptor["args"])
        tool_name = str(descriptor["tool_name"])
        target_name = str(descriptor["target_name"])
        record_id = str(descriptor["record_id"])
        base_version = str(descriptor["base_version"])
        atomic_group_id = str(descriptor["atomic_group_id"])
        source_intent_ids = list(descriptor["source_intent_ids"])
        source_effect_keys = list(descriptor["source_effect_keys"])
        if tool_name == "patch_record":
            key = (target_name, record_id, base_version)
            updates = args.get("updates")
            if not isinstance(updates, Mapping):
                raise PlanCompilationError(f"patch intent {source_intent_ids[0]} updates must be an object")
            if key in patch_indexes:
                node_index = patch_indexes[key]
                if nodes[node_index]["atomic_group_id"] != atomic_group_id:
                    raise PlanCompilationError("same-record merged intents cannot cross AtomicGroup boundaries")
                current = nodes[node_index]["payload"]["updates"]
                for field, value in updates.items():
                    if field in current and current[field] != value:
                        raise PlanCompilationError(
                            f"conflicting values for {target_name}.{record_id}.{field}; last-write-wins is forbidden"
                        )
                    current[field] = value
                nodes[node_index]["source_intent_ids"].extend(source_intent_ids)
                nodes[node_index]["source_effect_keys"].extend(source_effect_keys)
                for intent_key in (*source_effect_keys, *source_intent_ids):
                    intent_node_index[intent_key] = node_index
                continue
            patch_indexes[key] = len(nodes)
        node_index = len(nodes)
        typed_outputs = _typed_outputs(tool_name, target_name)
        from app.operator.effect_manifest import build_execution_contract

        execution_contract = build_execution_contract(
            tool_name=tool_name,
            target_name=target_name,
            payload=args,
            typed_outputs=typed_outputs,
        )
        nodes.append(
            {
                "node_id": f"node_{uuid.uuid4().hex}",
                "sequence": node_index + 1,
                "source_intent_ids": list(dict.fromkeys(source_intent_ids)),
                "source_effect_keys": list(dict.fromkeys(source_effect_keys)),
                "tool_name": tool_name,
                "target_kind": str(descriptor["target_kind"]),
                "target_name": target_name,
                "record_id": record_id,
                "base_version": base_version,
                "atomic_group_id": atomic_group_id,
                "payload": args,
                "typed_outputs": typed_outputs,
                "execution_contract": execution_contract,
            }
        )
        for intent_key in (*source_effect_keys, *source_intent_ids):
            intent_node_index[intent_key] = node_index
    from app.operator.effect_manifest import build_execution_contract

    for node_index, node in enumerate(nodes):
        node["source_intent_ids"] = list(dict.fromkeys(node["source_intent_ids"]))
        node["source_effect_keys"] = list(dict.fromkeys(node["source_effect_keys"]))
        node["execution_contract"] = build_execution_contract(
            tool_name=str(node["tool_name"]),
            target_name=str(node["target_name"]),
            payload=node["payload"],
            typed_outputs=node["typed_outputs"],
        )
        for effect_key in node["source_effect_keys"]:
            intent_node_index[effect_key] = node_index
    return nodes, intent_node_index


def _collect_output_refs(value: Any, path: str = "$") -> list[tuple[str, Mapping[str, Any]]]:
    refs: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        ref = value.get("$output")
        if isinstance(ref, Mapping):
            refs.append((path, ref))
        for key, child in value.items():
            if key != "$output":
                refs.extend(_collect_output_refs(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            refs.extend(_collect_output_refs(child, f"{path}[{index}]"))
    return refs


def _destination_semantic_type(node: Mapping[str, Any], path: str) -> str:
    tool_name = str(node.get("tool_name") or "")
    model_name = str(node.get("target_name") or "")
    if tool_name in {"patch_record", "delete_or_archive_record"} and path == "$.record_id":
        return f"record_id<{model_name}>"
    field_name = ""
    if tool_name == "create_record" and path.startswith("$.data."):
        field_name = path[len("$.data."):].split(".", 1)[0]
    elif tool_name == "patch_record" and path.startswith("$.updates."):
        field_name = path[len("$.updates."):].split(".", 1)[0]
    if tool_name == "invoke_action" and path.startswith("$.input."):
        parameter_name = path[len("$.input."):].split(".", 1)[0]
        action = ACTION_REGISTRY.get(model_name)
        parameter = action.input_parameters.get(parameter_name) if action is not None else None
        return str(parameter.semantic_type) if parameter is not None else ""
    if not field_name:
        return ""
    model = MODEL_REGISTRY.get(model_name)
    field = model.fields.get(field_name) if model is not None else None
    if field is None:
        return ""
    return f"record_id<{field.relation_target}>" if field.relation_target else str(field.data_type)


def _build_dependencies(nodes: list[dict[str, Any]], intent_node_index: Mapping[str, int]) -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    graph: dict[str, set[str]] = defaultdict(set)
    seen: set[tuple[str, str, str]] = set()
    for node in nodes:
        for path, reference in _collect_output_refs(node["payload"]):
            intent_key = str(reference.get("intent_key") or "")
            output_name = str(reference.get("name") or "")
            claimed_type = str(reference.get("semantic_type") or "")
            if intent_key not in intent_node_index:
                raise PlanCompilationError(f"typed output reference names unknown intent {intent_key!r}")
            upstream = nodes[intent_node_index[intent_key]]
            output = upstream["typed_outputs"].get(output_name)
            if not output or not output.get("referenceable"):
                raise PlanCompilationError(f"output {output_name!r} is undeclared or not referenceable")
            actual_type = str(output.get("semantic_type") or "")
            if claimed_type != actual_type:
                raise PlanCompilationError(
                    f"typed output semantic type mismatch: claimed {claimed_type!r}, actual {actual_type!r}"
                )
            destination_type = _destination_semantic_type(node, path)
            if not destination_type or destination_type != actual_type:
                raise PlanCompilationError(
                    f"typed output destination semantic mismatch at {path}: destination {destination_type!r}, output {actual_type!r}"
                )
            if upstream["node_id"] == node["node_id"]:
                raise PlanCompilationError("typed output dependency cannot reference the same normalized node")
            key = (node["node_id"], upstream["node_id"], output_name)
            if key in seen:
                continue
            seen.add(key)
            graph[node["node_id"]].add(upstream["node_id"])
            dependencies.append(
                {
                    "node_id": node["node_id"],
                    "depends_on_node_id": upstream["node_id"],
                    "output_name": output_name,
                    "semantic_type": actual_type,
                    "reference_path": path,
                }
            )
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise PlanCompilationError("typed output dependency cycle detected")
        if node_id in visited:
            return
        visiting.add(node_id)
        for parent in graph.get(node_id, ()):
            visit(parent)
        visiting.remove(node_id)
        visited.add(node_id)
    for node in nodes:
        visit(node["node_id"])
    return dependencies


def _policy(node: Mapping[str, Any]) -> dict[str, Any]:
    operation = {
        "create_record": "create",
        "patch_record": "patch",
        "delete_or_archive_record": "delete_or_archive",
        "invoke_action": "invoke",
        "manage_session": str(node["payload"].get("operation") or ""),
    }[str(node["tool_name"])]
    if str(node["target_kind"]) == "action":
        spec = ACTION_REGISTRY.get(str(node["target_name"] or ""))
        if spec is None or str(spec.implementation_status or "") != "implemented":
            raise PlanCompilationError(f"Unknown or non-operable action capability: {node['target_name']}")
        risk_level = int(spec.risk_level or 0)
        return {
            "confirmation_class": "business_write" if spec.confirmation_required else "read_only",
            "risk_level": risk_level,
            "confirmations_required": 2 if risk_level >= 5 else 1 if spec.confirmation_required else 0,
            "irreversible": False,
            "authorization_scope": "actor_session",
            "challenge_policy": "backend_issued" if risk_level >= 5 else "none",
            "compensation_policy": "registry_only",
        }
    schema = describe_capability_contract(str(node["target_kind"]), str(node["target_name"]), operation)
    return _json_copy(schema["confirmation_policy"])


async def compile_plan(
    db: Any,
    actor: Any,
    draft_id: str,
    *,
    replacement_for_plan_id: str = "",
    commit: bool = True,
) -> models.ProposalPlan:
    existing = (
        await db.execute(select(models.ProposalPlan).where(models.ProposalPlan.draft_id == str(draft_id)))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.actor_id != str(actor.actor_id) or existing.session_id != str(actor.session_id):
            raise PlanStateError("Existing Plan is outside the actor/session scope")
        return existing
    draft = await db.get(models.AgentPlanDraft, str(draft_id))
    if draft is None or draft.actor_id != str(actor.actor_id) or draft.session_id != str(actor.session_id):
        raise PlanStateError("Plan draft was not found in the actor/session scope")
    if draft.status != "collecting":
        raise PlanStateError(f"Plan draft is {draft.status}; only collecting drafts can compile")
    intents = (
        await db.execute(
            select(models.AgentPlanIntent)
            .where(models.AgentPlanIntent.draft_id == draft.draft_id, models.AgentPlanIntent.state == "active")
            .order_by(models.AgentPlanIntent.sequence)
        )
    ).scalars().all()
    if not intents:
        raise PlanCompilationError("Plan draft has no active write intents")
    try:
        descriptors = await _materialize_intent_descriptors(db, actor, list(intents))
        nodes, intent_node_index = _normalize_descriptors(descriptors)
        dependencies = _build_dependencies(nodes, intent_node_index)
        # Atomicity is a compiler decision, never an LLM-controlled argument. Compatible
        # local ORM writes from the same durable turn share one transaction boundary.
        parent_ids: dict[str, tuple[str, ...]] = {node["node_id"]: () for node in nodes}
        for dependency in dependencies:
            parent_ids[dependency["node_id"]] = tuple(sorted((*parent_ids[dependency["node_id"]], dependency["depends_on_node_id"])))
        atomic_buckets: dict[tuple[str, str, tuple[str, ...]], list[dict[str, Any]]] = defaultdict(list)
        for node in nodes:
            if node["tool_name"] not in {"create_record", "patch_record"} or str(node.get("atomic_group_id") or ""):
                continue
            bucket_key = (str(node["tool_name"]), _digest(_policy(node)), parent_ids[node["node_id"]])
            atomic_buckets[bucket_key].append(node)
        for bucket in atomic_buckets.values():
            if len(bucket) < 2:
                continue
            derived_atomic_id = f"atomic_{_digest({'draft_id': draft.draft_id, 'nodes': [node['node_id'] for node in bucket]})[:24]}"
            for node in bucket:
                node["atomic_group_id"] = derived_atomic_id
        # Start with one deterministic authorization boundary per normalized node.
        # This preserves independent branches even when their registry policies match;
        # later atomic-group compilation may combine only explicitly compatible nodes.
        groups: list[dict[str, Any]] = []
        atomic_groups: dict[str, dict[str, Any]] = {}
        for node in nodes:
            policy = _policy(node)
            atomic_id = str(node.get("atomic_group_id") or "")
            group = atomic_groups.get(atomic_id) if atomic_id else None
            if group is not None and group["policy"] != policy:
                raise PlanCompilationError(f"AtomicGroup {atomic_id!r} contains incompatible registry policy boundaries")
            if group is None:
                group = {"group_id": f"group_{uuid.uuid4().hex}", "sequence": len(groups) + 1, "policy": policy, "node_ids": [], "dependency_group_ids": []}
                groups.append(group)
                if atomic_id: atomic_groups[atomic_id] = group
            group["node_ids"].append(node["node_id"])
            node["confirmation_group_id"] = group["group_id"]
            node["risk_level"] = int(policy.get("risk_level") or 0)
            node["compensation_policy"] = compensation_spec_key(str(node["tool_name"]), str(node["target_name"]))
        group_by_node = {node_id: group["group_id"] for group in groups for node_id in group["node_ids"]}
        by_group = {group["group_id"]: group for group in groups}
        for dependency in dependencies:
            child_group = group_by_node[dependency["node_id"]]
            parent_group = group_by_node[dependency["depends_on_node_id"]]
            if child_group != parent_group and parent_group not in by_group[child_group]["dependency_group_ids"]:
                by_group[child_group]["dependency_group_ids"].append(parent_group)
        for group in groups:
            group["group_digest"] = _digest(
                {"policy": group["policy"], "node_ids": group["node_ids"], "dependencies": group["dependency_group_ids"]}
            )
        for node in nodes:
            node["node_digest"] = _digest(
                {
                    "node_id": node["node_id"],
                    "sequence": node["sequence"],
                    "tool_name": node["tool_name"],
                    "target_kind": node["target_kind"],
                    "target_name": node["target_name"],
                    "record_id": node["record_id"],
                    "base_version": node["base_version"],
                    "atomic_group_id": node["atomic_group_id"],
                    "payload": node["payload"],
                    "typed_outputs": node["typed_outputs"],
                    "execution_contract": node["execution_contract"],
                    "confirmation_group_id": node["confirmation_group_id"],
                    "risk_level": node["risk_level"],
                    "compensation_policy": node["compensation_policy"],
                }
            )
        immutable = {
            "draft_id": draft.draft_id,
            "nodes": [
                {
                    "node_id": node["node_id"],
                    "sequence": node["sequence"],
                    "tool_name": node["tool_name"],
                    "target_kind": node["target_kind"],
                    "target_name": node["target_name"],
                    "record_id": node["record_id"],
                    "base_version": node["base_version"],
                    "atomic_group_id": node["atomic_group_id"],
                    "payload": node["payload"],
                    "typed_outputs": node["typed_outputs"],
                    "execution_contract": node["execution_contract"],
                    "node_digest": node["node_digest"],
                    "confirmation_group_id": node["confirmation_group_id"],
                    "risk_level": node["risk_level"],
                    "compensation_policy": node["compensation_policy"],
                }
                for node in nodes
            ],
            "dependencies": dependencies,
            "confirmation_groups": groups,
        }
        lineage_id = draft.draft_id
        revision = 1
        parent_plan_id = None
        current_lineage_key = draft.draft_id
        if replacement_for_plan_id:
            parent = await db.get(models.ProposalPlan, str(replacement_for_plan_id))
            if parent is None or parent.actor_id != draft.actor_id or parent.session_id != draft.session_id:
                raise PlanCompilationError("replacement parent is outside the actor/session scope")
            if parent.status != "sealed" or parent.execution_started:
                raise PlanCompilationError("replacement parent must be sealed and unexecuted")
            lineage_id = str(parent.lineage_id or parent.draft_id)
            revision = int(parent.revision or 1) + 1
            parent_plan_id = parent.plan_id
            current_lineage_key = None
        plan = models.ProposalPlan(
            plan_id=f"plan_{uuid.uuid4().hex}",
            draft_id=draft.draft_id,
            actor_id=draft.actor_id,
            session_id=draft.session_id,
            status="sealed",
            revision=revision,
            lineage_id=lineage_id,
            parent_plan_id=parent_plan_id,
            current_lineage_key=current_lineage_key,
            plan_digest=_digest(immutable),
            immutable_json=immutable,
        )
        db.add(plan)
        for group in groups:
            db.add(
                models.ConfirmationGroup(
                    group_id=group["group_id"], plan_id=plan.plan_id,
                    sequence=group["sequence"], status="pending",
                    group_digest=group["group_digest"], policy_json=group["policy"],
                    dependency_group_ids=group["dependency_group_ids"],
                )
            )
        for node in nodes:
            db.add(
                models.OperationNode(
                    node_id=node["node_id"], plan_id=plan.plan_id, sequence=node["sequence"],
                    source_intent_ids=node["source_intent_ids"], tool_name=node["tool_name"],
                    target_kind=node["target_kind"], target_name=node["target_name"], record_id=node["record_id"],
                    base_version=node["base_version"], payload_json=node["payload"], typed_outputs=node["typed_outputs"],
                    execution_contract_json=node["execution_contract"], execution_contract_digest=node["execution_contract"]["digest"],
                    node_digest=node["node_digest"], status="pending", confirmation_group_id=node["confirmation_group_id"], atomic_group_id=node["atomic_group_id"],
                    risk_level=node["risk_level"], compensation_policy=node["compensation_policy"],
                )
            )
        for dependency in dependencies:
            db.add(models.NodeDependency(plan_id=plan.plan_id, **dependency))
        merged_ids = {intent_id for node in nodes if len(node["source_intent_ids"]) > 1 for intent_id in node["source_intent_ids"][1:]}
        for intent in intents:
            if intent.intent_id in merged_ids:
                intent.state = "merged"
        draft.status = "sealed"
        draft.sealed_at = _now()
        try:
            if commit:
                await db.commit()
            else:
                await db.flush()
        except IntegrityError:
            await db.rollback()
            winner = (
                await db.execute(select(models.ProposalPlan).where(models.ProposalPlan.draft_id == str(draft_id)))
            ).scalar_one_or_none()
            if winner is None:
                raise
            if winner.actor_id != str(actor.actor_id) or winner.session_id != str(actor.session_id):
                raise PlanStateError("Concurrent Plan winner is outside the actor/session scope")
            return winner
        return plan
    except PlanCompilationError as exc:
        # Compilation validates and normalizes before any Plan/Node rows are added,
        # so preserve the loaded intent identities while durably rejecting the draft.
        draft = await db.get(models.AgentPlanDraft, str(draft_id))
        if draft is not None:
            draft.status = "rejected"
            draft.error = str(exc)
            if commit:
                await db.commit()
            else:
                await db.flush()
        raise
