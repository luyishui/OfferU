from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import models
from app.operator.manual_review_recovery import apply_plan_recovery_overlay
from app.operator.plan_authorization import group_authorization_digest
from app.operator.plan_snapshots import (
    PlanSnapshotIntegrityError,
    operation_node_material,
    validate_confirmation_group_binding,
    validate_operation_node_binding,
)


TERMINAL_RECEIPT_STATES = {"completed", "failed", "manual_review", "blocked", "rejected", "compensated"}


class NodeExecutionError(RuntimeError):
    def __init__(self, classification: str, message: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.classification = classification if classification in {"transient", "permanent", "integrity", "unknown"} else "unknown"
        self.details = dict(details or {})


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _idempotency_key(plan_id: str, node_id: str, input_digest: str) -> str:
    return f"plan-effect:{_digest({'plan_id': plan_id, 'node_id': node_id, 'input_digest': input_digest})}"


def _node_digest(node: models.OperationNode) -> str:
    stored = str(getattr(node, "node_digest", "") or "")
    calculated = _digest(operation_node_material(node))
    if stored and stored != calculated:
        raise NodeExecutionError("integrity", f"OperationNode {node.node_id} digest is invalid")
    return calculated


def _empty_effect_manifest(
    node: models.OperationNode | None = None,
    receipt: models.NodeExecutionReceipt | None = None,
    *,
    effect_state: str = "no_effect",
    completeness: str = "complete",
) -> dict[str, Any]:
    from app.operator.effect_manifest import empty_effect_manifest

    bindings = {
        "plan_id": str(getattr(node, "plan_id", "") or ""),
        "group_id": str(getattr(node, "confirmation_group_id", "") or ""),
        "node_id": str(getattr(node, "node_id", "") or ""),
        "node_digest": str(getattr(node, "node_digest", "") or ""),
        "execution_contract_digest": str(getattr(node, "execution_contract_digest", "") or ""),
        "resolved_input_digest": str(getattr(receipt, "input_digest", "") or ""),
    }
    return empty_effect_manifest(
        effect_state=effect_state,
        completeness=completeness,
        bindings=bindings,
    )


def _public_result(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): json.loads(json.dumps(value, default=str)) for key, value in raw.items() if not str(key).startswith("_")}


async def _ensure_manual_review_case(
    db: Any,
    *,
    plan: Any,
    reason_code: str,
    node: Any | None = None,
    group_id: str = "",
    proposal_id: str = "",
    effect_state: str = "unknown_external",
    evidence: Mapping[str, Any] | None = None,
    subject_type: str = "plan_execution",
) -> models.ManualReviewCase:
    """Create or return the durable deduplicated review case for one integrity boundary."""
    plan_id_value = str(getattr(plan, "plan_id", "") or "")
    group_id_value = str(group_id or getattr(node, "confirmation_group_id", "") or "")
    proposal_id_value = str(proposal_id or "")
    if not proposal_id_value and group_id_value:
        active_statuses = (
            "authorized",
            "confirmed",
            "pending",
            "awaiting_next_confirmation",
        )
        candidates = list((await db.execute(
            select(models.ProposalCache)
            .where(
                models.ProposalCache.plan_id == plan_id_value,
                models.ProposalCache.confirmation_group_id == group_id_value,
                models.ProposalCache.actor_id == str(getattr(plan, "actor_id", "") or ""),
                models.ProposalCache.session_id == str(getattr(plan, "session_id", "") or ""),
                models.ProposalCache.tool_name == "confirm_plan_group",
                models.ProposalCache.status.in_(active_statuses),
            )
            .order_by(models.ProposalCache.created_at.desc(), models.ProposalCache.proposal_id.desc())
        )).scalars().all())
        if len(candidates) > 1:
            raise NodeExecutionError(
                "integrity",
                f"Multiple active durable proposals match Plan {plan_id_value} Group {group_id_value}",
                {"plan_id": plan_id_value, "group_id": group_id_value, "proposal_ids": [str(item.proposal_id) for item in candidates]},
            )
        if candidates:
            proposal_id_value = str(candidates[0].proposal_id)
    evidence_material = json.loads(json.dumps(dict(evidence or {}), default=str))
    if proposal_id_value:
        evidence_material.setdefault("proposal_id", proposal_id_value)
    material = {
        "plan_id": plan_id_value,
        "group_id": group_id_value,
        "node_id": str(getattr(node, "node_id", "") or ""),
        "proposal_id": proposal_id_value,
        "reason_code": str(reason_code),
        "effect_state": str(effect_state),
        "evidence": evidence_material,
    }
    dedupe_key = f"manual-review:v1:{_digest(material)}"
    existing = await db.scalar(
        select(models.ManualReviewCase).where(models.ManualReviewCase.dedupe_key == dedupe_key)
    )
    if existing is not None:
        return existing
    case = models.ManualReviewCase(
        case_id=f"review:{uuid.uuid4().hex}",
        dedupe_key=dedupe_key,
        actor_id=str(getattr(plan, "actor_id", "") or ""),
        session_id=str(getattr(plan, "session_id", "") or ""),
        plan_id=material["plan_id"],
        group_id=material["group_id"],
        node_id=material["node_id"],
        proposal_id=material["proposal_id"],
        reason_code=str(reason_code),
        subject_type=str(subject_type or "plan_execution"),
        effect_state=str(effect_state),
        evidence_json=material["evidence"],
        case_generation=1,
        evidence_digest=_digest(material),
        status="open",
        resolution_json={},
        resolution_result_digest="",
        resolution_event_digest="",
    )
    try:
        async with db.begin_nested():
            db.add(case)
            await db.flush()
    except IntegrityError:
        # A concurrent boundary (for example an expired-lease takeover) created the same
        # deduplicated case first; the durable winner is the authoritative fact.
        existing = await db.scalar(
            select(models.ManualReviewCase).where(models.ManualReviewCase.dedupe_key == dedupe_key)
        )
        if existing is None:
            raise
        return existing
    return case

async def _preflight_terminal_node_facts(
    db: Any,
    plan: models.ProposalPlan,
    nodes: Sequence[models.OperationNode],
) -> dict[str, models.NodeExecutionReceipt]:
    """Validate existing terminal facts before any Plan execution state is mutated."""
    from app.operator.effect_manifest import EffectManifestError, validate_effect_manifest, validate_node_contract

    node_ids = [str(node.node_id) for node in nodes]
    receipts = {
        str(item.node_id): item
        for item in (await db.execute(
            select(models.NodeExecutionReceipt).where(models.NodeExecutionReceipt.node_id.in_(node_ids))
        )).scalars().all()
    } if node_ids else {}
    outcomes = {
        str(item.node_id): item
        for item in (await db.execute(
            select(models.NodeExecutionOutcome).where(models.NodeExecutionOutcome.node_id.in_(node_ids))
        )).scalars().all()
    } if node_ids else {}
    for node in nodes:
        receipt = receipts.get(str(node.node_id))
        node_terminal = str(node.status) in TERMINAL_RECEIPT_STATES
        receipt_terminal = receipt is not None and str(receipt.status) in TERMINAL_RECEIPT_STATES
        if not node_terminal and not receipt_terminal:
            continue
        outcome = outcomes.get(str(node.node_id))
        if receipt is None or outcome is None:
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} has incomplete durable terminal evidence")
        if (
            str(receipt.plan_id) != str(plan.plan_id)
            or str(receipt.actor_id) != str(plan.actor_id)
            or str(receipt.session_id) != str(plan.session_id)
            or str(outcome.plan_id) != str(plan.plan_id)
            or str(outcome.group_id) != str(node.confirmation_group_id)
            or str(outcome.actor_id) != str(plan.actor_id)
            or str(outcome.session_id) != str(plan.session_id)
        ):
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} terminal evidence identity mismatch")
        if not node_terminal or not receipt_terminal or str(node.status) != str(receipt.status) or str(node.status) != str(outcome.status):
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} terminal evidence status mismatch")
        try:
            contract = validate_node_contract(node)
            manifest = validate_effect_manifest(
                node,
                outcome.effect_manifest_json if isinstance(outcome.effect_manifest_json, Mapping) else {},
                expected_resolved_input_digest=str(receipt.input_digest or ""),
            )
        except EffectManifestError as exc:
            raise NodeExecutionError("integrity", str(exc)) from exc
        if str(outcome.node_digest or "") != _node_digest(node):
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} outcome node digest mismatch")
        if (
            str(receipt.execution_contract_digest or "") != str(contract.get("digest") or "")
            or str(outcome.execution_contract_digest or "") != str(contract.get("digest") or "")
            or str(outcome.resolved_input_digest or "") != str(receipt.input_digest or "")
            or str((manifest.get("bindings") or {}).get("resolved_input_digest") or "") != str(receipt.input_digest or "")
            or int(outcome.attempt_count or 0) != int(receipt.attempt_count or 0)
            or str(receipt.effect_manifest_digest or "") != str(manifest.get("digest") or "")
            or str(outcome.effect_manifest_digest or "") != str(manifest.get("digest") or "")
            or dict(receipt.effect_manifest_json or {}) != dict(manifest)
            or dict(receipt.result_json or {}) != dict(outcome.public_result_json or {})
            or dict(receipt.typed_outputs or {}) != dict(outcome.typed_outputs or {})
            or str(receipt.completion_reason or "") != str(outcome.completion_reason or "")
        ):
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} terminal evidence binding mismatch")
        if (
            str(outcome.public_result_digest or "") != _digest(outcome.public_result_json or {})
            or str(outcome.typed_outputs_digest or "") != _digest(outcome.typed_outputs or {})
        ):
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} terminal evidence digest mismatch")
    return receipts

async def _publish_node_outcome(
    db: Any,
    node: models.OperationNode,
    receipt: models.NodeExecutionReceipt,
    public_result: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    status: str,
    effect_state: str,
    attempts: int,
    error_classification: str = "",
    error_message: str = "",
) -> models.NodeExecutionOutcome:
    result_digest = _digest(public_result)
    typed_outputs = public_result.get("typed_outputs") if isinstance(public_result.get("typed_outputs"), Mapping) else {}
    typed_digest = _digest(typed_outputs)
    outcome = await db.scalar(select(models.NodeExecutionOutcome).where(models.NodeExecutionOutcome.node_id == str(node.node_id)))
    if outcome is not None:
        expected = {
            "node_digest": _node_digest(node),
            "execution_contract_digest": str(getattr(node, "execution_contract_digest", "") or ""),
            "resolved_input_digest": str(receipt.input_digest or ""),
            "status": str(status),
            "effect_state": str(effect_state),
            "completion_reason": str(public_result.get("completion_reason") or "terminal"),
            "attempt_count": max(0, int(attempts)),
            "public_result_digest": result_digest,
            "typed_outputs_digest": typed_digest,
            "effect_manifest_digest": str(manifest.get("digest") or ""),
            "error_classification": str(error_classification or ""),
            "error_message": str(error_message or ""),
        }
        actual = {key: str(getattr(outcome, key, "") or "") for key in (
            "node_digest", "execution_contract_digest", "resolved_input_digest", "status", "effect_state",
            "completion_reason", "public_result_digest", "typed_outputs_digest", "effect_manifest_digest",
            "error_classification", "error_message",
        )}
        actual["attempt_count"] = int(outcome.attempt_count or 0)
        for key, value in expected.items():
            if actual.get(key) != value:
                raise NodeExecutionError("integrity", f"Node {node.node_id} terminal outcome binding {key} is invalid")
        if dict(outcome.public_result_json or {}) != dict(public_result) or dict(outcome.typed_outputs or {}) != dict(typed_outputs):
            raise NodeExecutionError("integrity", f"Node {node.node_id} terminal outcome content was modified after publication")
        return outcome
    outcome = models.NodeExecutionOutcome(
        outcome_id=f"outcome:{node.node_id}",
        node_id=str(node.node_id),
        plan_id=str(node.plan_id),
        group_id=str(node.confirmation_group_id),
        actor_id=str(receipt.actor_id),
        session_id=str(receipt.session_id),
        receipt_schema_version=1,
        node_digest=_node_digest(node),
        execution_contract_digest=str(getattr(node, "execution_contract_digest", "") or ""),
        resolved_input_digest=str(receipt.input_digest or ""),
        status=str(status),
        effect_state=str(effect_state),
        completion_reason=str(public_result.get("completion_reason") or "terminal"),
        attempt_count=max(0, int(attempts)),
        public_result_json=dict(public_result),
        public_result_digest=result_digest,
        typed_outputs=dict(typed_outputs),
        typed_outputs_digest=typed_digest,
        effect_manifest_json=dict(manifest),
        effect_manifest_digest=str(manifest.get("digest") or ""),
        error_classification=str(error_classification or ""),
        error_message=str(error_message or ""),
    )
    db.add(outcome)
    await db.flush()
    return outcome


def _json_type_matches(value: Any, json_type: str, semantic_type: str) -> bool:
    if semantic_type.startswith("record_id<") and not semantic_type.endswith("[]"):
        return isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip() != ""
    if semantic_type.startswith("record_id<") and semantic_type.endswith("[]"):
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and all(
            isinstance(item, (str, int)) and not isinstance(item, bool) and str(item).strip() != "" for item in value
        )
    return {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, Mapping),
        "array": lambda item: isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)),
        "null": lambda item: item is None,
    }.get(str(json_type or "object"), lambda _item: True)(value)


def _declared_output_value(node: models.OperationNode, raw: Mapping[str, Any], name: str) -> Any:
    if name in raw:
        return raw[name]
    supplied_outputs = raw.get("typed_outputs") if isinstance(raw.get("typed_outputs"), Mapping) else {}
    if name in supplied_outputs:
        return supplied_outputs[name]
    record = raw.get("record") if isinstance(raw.get("record"), Mapping) else {}
    if name in {"record_id", "primary_record_id"}:
        candidate = raw.get("record_id") or record.get("id")
        if candidate not in (None, ""):
            return candidate
    if name.endswith("_id"):
        artifact_name = name[:-3]
        artifact = raw.get(artifact_name)
        if isinstance(artifact, Mapping):
            candidate = artifact.get("id") or artifact.get(name)
            if candidate not in (None, ""):
                return candidate
    return None


def canonicalize_node_execution_result(
    node: models.OperationNode,
    raw: Mapping[str, Any],
    *,
    resolved_payload: Mapping[str, Any] | None = None,
    expected_resolved_input_digest: str = "",
) -> dict[str, Any]:
    """Bind one handler result to its immutable Node contract and actual transaction manifest."""
    from app.operator.effect_manifest import (
        EffectManifestError,
        changed_records_from_manifest,
        has_applied_effects,
        no_op_records_from_manifest,
        validate_effect_manifest,
        validate_node_contract,
    )
    from app.operator.registry import ACTION_REGISTRY

    result = dict(raw)
    manifest_raw = result.pop("_effect_manifest", None)
    try:
        contract = validate_node_contract(node)
        if not isinstance(manifest_raw, Mapping):
            raise EffectManifestError("node handler did not publish a transaction-grounded effect manifest")
        manifest = validate_effect_manifest(
            node,
            manifest_raw,
            resolved_payload=resolved_payload,
            expected_resolved_input_digest=expected_resolved_input_digest,
        )
    except EffectManifestError as exc:
        raise NodeExecutionError("integrity", str(exc)) from exc

    action_spec = None
    if str(node.tool_name) == "invoke_action":
        action_name = str(result.get("action") or node.target_name or "")
        if action_name != str(node.target_name or ""):
            raise NodeExecutionError("integrity", "action result identity does not match the immutable Plan node")
        action_spec = ACTION_REGISTRY.get(action_name)
        if action_spec is None:
            raise NodeExecutionError("integrity", f"action result contract {action_name!r} is unavailable")
        result["action"] = action_name
        expected_model = str(action_spec.result_model or "")
        returned_model = str(result.get("model") or "")
        if expected_model and returned_model and returned_model != expected_model:
            raise NodeExecutionError(
                "integrity",
                f"action result model {returned_model!r} does not match immutable result model {expected_model!r}",
            )
        if expected_model:
            result["model"] = expected_model
        execution_metadata = {
            "already_satisfied", "before", "after", "before_version", "after_version",
            "write_occurred", "completion_reason", "typed_outputs", "changed_records", "no_op_records",
            "affected_resources", "checkpoint_id", "pre_confirmation_checkpoint_id",
            "effect_manifest_digest", "execution_result_digest",
        }
        unexpected_outputs = sorted(set(result) - set(action_spec.output_parameters) - execution_metadata)
        if unexpected_outputs:
            raise NodeExecutionError(
                "integrity",
                f"action result contains undeclared output field(s): {unexpected_outputs}",
            )

    declared_outputs = node.typed_outputs if isinstance(node.typed_outputs, Mapping) else {}
    supplied_outputs = result.get("typed_outputs") if isinstance(result.get("typed_outputs"), Mapping) else {}
    undeclared_supplied = sorted(set(supplied_outputs) - set(declared_outputs))
    if undeclared_supplied:
        raise NodeExecutionError("integrity", f"action result contains undeclared typed output(s): {undeclared_supplied}")
    typed_outputs: dict[str, Any] = {}
    for name, declaration_raw in declared_outputs.items():
        declaration = declaration_raw if isinstance(declaration_raw, Mapping) else {}
        if declaration.get("durable", True) is False:
            continue
        value = _declared_output_value(node, result, str(name))
        parameter = action_spec.output_parameters.get(str(name)) if action_spec is not None else None
        required = bool(declaration.get("required") or getattr(parameter, "required", False))
        if value is None:
            if required:
                raise NodeExecutionError("integrity", f"action output {name!r} is required but missing")
            continue
        json_type = str(declaration.get("json_type") or getattr(parameter, "json_type", "object"))
        semantic_type = str(declaration.get("semantic_type") or getattr(parameter, "semantic_type", json_type))
        if not _json_type_matches(value, json_type, semantic_type):
            raise NodeExecutionError("integrity", f"action output {name!r} violates its declared {json_type}/{semantic_type} contract")
        typed_outputs[str(name)] = json.loads(json.dumps(value, default=str))

    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), Mapping) else {}
    resolved_authorization = bindings.get("resolved_authorization") if isinstance(bindings.get("resolved_authorization"), Mapping) else {}
    if resolved_payload is not None:
        from app.operator.effect_manifest import _authorization_contract, _digest as _effect_digest

        expected_resolved_authorization = _authorization_contract(
            str(node.tool_name or ""), str(node.target_name or ""), resolved_payload
        )
        if str(bindings.get("resolved_input_digest") or "") != _effect_digest(dict(resolved_payload)):
            raise NodeExecutionError("integrity", "node effect manifest resolved input digest is invalid")
        if dict(resolved_authorization) != expected_resolved_authorization:
            raise NodeExecutionError("integrity", "node effect manifest resolved authorization scope is invalid")
    elif not str(bindings.get("resolved_input_digest") or ""):
        raise NodeExecutionError("integrity", "node effect manifest has no resolved input binding")

    manifest_ids: dict[str, set[str]] = {}
    for effect in manifest.get("effects") or []:
        if isinstance(effect, Mapping) and str(effect.get("kind") or "") == "database_record":
            manifest_ids.setdefault(str(effect.get("model") or ""), set()).add(str(effect.get("record_id") or ""))
    authorization = contract.get("authorization") if isinstance(contract.get("authorization"), Mapping) else {}
    immutable_record_scopes = authorization.get("record_scopes") if isinstance(authorization.get("record_scopes"), Mapping) else {}
    resolved_record_scopes = resolved_authorization.get("record_scopes") if isinstance(resolved_authorization.get("record_scopes"), Mapping) else {}
    record_scopes = {
        str(model): sorted({str(item) for item in list(immutable_record_scopes.get(model) or []) + list(resolved_record_scopes.get(model) or [])})
        for model in set(immutable_record_scopes) | set(resolved_record_scopes)
    }
    effect_specs = contract.get("effect_specs") if isinstance(contract.get("effect_specs"), list) else []
    for name, value in typed_outputs.items():
        declaration_raw = declared_outputs.get(name) if isinstance(declared_outputs, Mapping) else {}
        declaration = declaration_raw if isinstance(declaration_raw, Mapping) else {}
        semantic_type = str(declaration.get("semantic_type") or "")
        if not semantic_type.startswith("record_id<"):
            continue
        model_name = semantic_type[len("record_id<"):].split(">", 1)[0]
        values = value if semantic_type.endswith("[]") and isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else [value]
        identities = {str(item) for item in values if item not in (None, "")}
        allowed = set(manifest_ids.get(model_name, set())) | {str(item) for item in (record_scopes.get(model_name) or [])}
        creates_model = any(
            isinstance(item, Mapping)
            and str(item.get("kind") or "database_record") == "database_record"
            and str(item.get("operation") or "") == "create"
            and str(item.get("model") or "") == model_name
            for item in effect_specs
        )
        if creates_model and name in {"record_id", "primary_record_id"} and not identities <= set(manifest_ids.get(model_name, set())):
            raise NodeExecutionError("integrity", f"typed output {name!r} is not bound to a created manifest identity")
        if allowed and not identities <= allowed:
            raise NodeExecutionError("integrity", f"typed output {name!r} is outside the confirmed/manifest identity set")
    result_model = str(contract.get("result_model") or "")
    if result_model:
        identity_values: list[str] = []
        for key in ("record_id", "primary_record_id", f"{result_model}_id"):
            value = result.get(key)
            if value not in (None, ""):
                identity_values.append(str(value))
        artifact = result.get(result_model)
        if isinstance(artifact, Mapping) and artifact.get("id") not in (None, ""):
            identity_values.append(str(artifact.get("id")))
        if (result.get("record_id") not in (None, "") or isinstance(artifact, Mapping)) and len(set(identity_values)) > 1:
            raise NodeExecutionError("integrity", "action result primary record identity aliases disagree")

    result["typed_outputs"] = typed_outputs
    result["changed_records"] = changed_records_from_manifest(manifest)
    result["no_op_records"] = no_op_records_from_manifest(manifest)
    result["write_occurred"] = has_applied_effects(manifest)
    result["completion_reason"] = "plan_node_completed" if result["write_occurred"] else "already_satisfied"
    result["effect_manifest_digest"] = str(manifest.get("digest") or "")
    result["_effect_manifest"] = manifest
    result["_execution_contract_digest"] = str(contract.get("digest") or "")
    return result


def _resolve_refs(value: Any, receipts: Mapping[str, models.NodeExecutionReceipt], effect_nodes: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        reference = value.get("$output")
        if isinstance(reference, Mapping):
            source_node_id = effect_nodes.get(str(reference.get("intent_key") or ""))
            receipt = receipts.get(str(source_node_id or ""))
            output_name = str(reference.get("name") or "")
            if receipt is None or receipt.status != "completed" or output_name not in (receipt.typed_outputs or {}):
                raise NodeExecutionError("unknown", f"durable typed output {output_name!r} is unavailable")
            return (receipt.typed_outputs or {})[output_name]
        return {str(key): _resolve_refs(child, receipts, effect_nodes) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(child, receipts, effect_nodes) for child in value]
    return value


async def claim_node_execution(
    db: Any,
    plan: models.ProposalPlan,
    node: models.OperationNode,
    payload: Mapping[str, Any],
    *,
    owner_token: str | None = None,
    lease_seconds: int = 90,
) -> models.NodeExecutionReceipt | None:
    """Durably claim a Node before invoking any effect.

    A running unexpired receipt is an exclusive lease. Terminal receipts are replay
    evidence and are never re-executed. Expired claims are recovered with a fenced
    generation increment.
    """
    token = str(owner_token or f"node-worker-{uuid.uuid4().hex}")
    input_digest = _digest(payload)
    now = _now()
    lease_expires_at = now + timedelta(seconds=max(5, int(lease_seconds)))
    existing = await db.get(models.NodeExecutionReceipt, node.node_id, populate_existing=True)
    if existing is None:
        receipt = models.NodeExecutionReceipt(
            node_id=node.node_id,
            plan_id=plan.plan_id,
            actor_id=plan.actor_id,
            session_id=plan.session_id,
            input_digest=input_digest,
            status="running",
            attempt_count=0,
            claim_token=token,
            claim_generation=1,
            lease_expires_at=lease_expires_at,
            idempotency_key=_idempotency_key(plan.plan_id, node.node_id, input_digest),
        )
        db.add(receipt)
        try:
            await db.commit()
            return await db.get(models.NodeExecutionReceipt, node.node_id, populate_existing=True)
        except IntegrityError:
            await db.rollback()
            existing = await db.get(models.NodeExecutionReceipt, node.node_id, populate_existing=True)
    if existing is None or existing.plan_id != plan.plan_id or existing.actor_id != plan.actor_id or existing.session_id != plan.session_id:
        raise NodeExecutionError("unknown", "node execution receipt identity mismatch")
    if existing.status in TERMINAL_RECEIPT_STATES:
        if existing.input_digest != input_digest:
            raise NodeExecutionError("integrity", "resolved input digest changed after terminal execution claim")
        return None
    if existing.status == "running" and existing.lease_expires_at is not None and existing.lease_expires_at > now:
        return existing if existing.claim_token == token else None
    if existing.status == "running" and existing.input_digest != input_digest:
        expired = await db.execute(
            update(models.NodeExecutionReceipt)
            .where(
                models.NodeExecutionReceipt.node_id == node.node_id,
                models.NodeExecutionReceipt.status == "running",
                models.NodeExecutionReceipt.claim_token == str(existing.claim_token or ""),
                models.NodeExecutionReceipt.claim_generation == int(existing.claim_generation or 0),
                models.NodeExecutionReceipt.lease_expires_at <= now,
            )
            .values(lease_expires_at=existing.lease_expires_at)
            .execution_options(synchronize_session=False)
        )
        if int(expired.rowcount or 0) != 1:
            await db.rollback()
            return None
        existing.status = "manual_review"
        existing.attempt_count = max(1, int(existing.attempt_count or 0))
        existing.error_classification = "integrity"
        existing.error_message = "Expired node claim resolved input digest changed; automatic replay is forbidden."
        existing.completion_reason = "resolved_input_digest_drift_requires_manual_review"
        existing.completed_at = now
        existing.lease_expires_at = None
        existing.effect_manifest_json = _empty_effect_manifest(node, existing, effect_state="unknown_external", completeness="unknown")
        existing.effect_manifest_schema_version = int(existing.effect_manifest_json.get("version") or 0)
        existing.effect_manifest_digest = existing.effect_manifest_json["digest"]
        existing.execution_contract_digest = str(getattr(node, "execution_contract_digest", "") or "")
        existing.write_occurred = False
        public_result = {
            "status": "manual_review",
            "write_occurred": False,
            "changed_records": [],
            "typed_outputs": {},
            "completion_reason": existing.completion_reason,
        }
        existing.result_json = dict(public_result)
        existing.typed_outputs = {}
        node.status = "manual_review"
        await _ensure_manual_review_case(
            db,
            plan=plan,
            node=node,
            reason_code="expired_node_claim_input_drift",
            effect_state="unknown_external",
            evidence={
                "stored_input_digest": str(existing.input_digest or ""),
                "resolved_input_digest": str(input_digest),
                "claim_generation": int(existing.claim_generation or 0),
                "claim_token": str(existing.claim_token or ""),
            },
        )
        await _publish_node_outcome(
            db,
            node,
            existing,
            public_result,
            existing.effect_manifest_json,
            status="manual_review",
            effect_state="unknown_external",
            attempts=existing.attempt_count,
            error_classification="integrity",
            error_message=existing.error_message,
        )
        await db.commit()
        return None
    if existing.status == "running" and node.tool_name == "invoke_action":
        expired = await db.execute(
            update(models.NodeExecutionReceipt)
            .where(
                models.NodeExecutionReceipt.node_id == node.node_id,
                models.NodeExecutionReceipt.status == "running",
                models.NodeExecutionReceipt.claim_token == str(existing.claim_token or ""),
                models.NodeExecutionReceipt.claim_generation == int(existing.claim_generation or 0),
                models.NodeExecutionReceipt.lease_expires_at <= now,
            )
            .values(lease_expires_at=existing.lease_expires_at)
            .execution_options(synchronize_session=False)
        )
        if int(expired.rowcount or 0) != 1:
            await db.rollback()
            return None
        existing.status = "manual_review"
        existing.attempt_count = max(1, int(existing.attempt_count or 0))
        existing.error_classification = "unknown"
        existing.error_message = "External action claim expired without a durable provider receipt; automatic replay is forbidden."
        existing.completion_reason = "external_effect_receipt_lookup_required"
        existing.completed_at = now
        existing.lease_expires_at = None
        existing.effect_manifest_json = _empty_effect_manifest(node, existing, effect_state="unknown_external", completeness="unknown")
        existing.effect_manifest_schema_version = int(existing.effect_manifest_json.get("version") or 0)
        existing.effect_manifest_digest = existing.effect_manifest_json["digest"]
        existing.execution_contract_digest = str(getattr(node, "execution_contract_digest", "") or "")
        existing.write_occurred = False
        public_result = {
            "status": "manual_review",
            "write_occurred": False,
            "changed_records": [],
            "typed_outputs": {},
            "completion_reason": existing.completion_reason,
        }
        existing.result_json = dict(public_result)
        existing.typed_outputs = {}
        node.status = "manual_review"
        await _ensure_manual_review_case(
            db, plan=plan, node=node, reason_code="expired_external_action_claim",
            effect_state="unknown_external",
            evidence={
                "stored_input_digest": str(existing.input_digest or ""),
                "resolved_input_digest": str(input_digest),
                "claim_generation": int(existing.claim_generation or 0),
            },
        )
        await _publish_node_outcome(
            db, node, existing,
            public_result,
            existing.effect_manifest_json,
            status="manual_review", effect_state="unknown_external", attempts=existing.attempt_count,
            error_classification="unknown", error_message=existing.error_message,
        )
        await db.commit()
        return None
    old_token = str(existing.claim_token or "")
    old_generation = int(existing.claim_generation or 0)
    changed = await db.execute(
        update(models.NodeExecutionReceipt)
        .where(
            models.NodeExecutionReceipt.node_id == node.node_id,
            models.NodeExecutionReceipt.status == "running",
            models.NodeExecutionReceipt.claim_token == old_token,
            models.NodeExecutionReceipt.claim_generation == old_generation,
            models.NodeExecutionReceipt.lease_expires_at <= now,
        )
        .values(claim_token=token, claim_generation=old_generation + 1, lease_expires_at=lease_expires_at)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        await db.rollback()
        return None
    await db.commit()
    return await db.get(models.NodeExecutionReceipt, node.node_id, populate_existing=True)


async def renew_node_execution_claim(
    db: Any,
    node_id: str,
    claim_token: str,
    claim_generation: int,
    *,
    lease_seconds: int = 90,
) -> bool:
    """Heartbeat a running Node lease using token+generation fencing."""
    now = _now()
    changed = await db.execute(
        update(models.NodeExecutionReceipt)
        .where(
            models.NodeExecutionReceipt.node_id == str(node_id),
            models.NodeExecutionReceipt.status == "running",
            models.NodeExecutionReceipt.claim_token == str(claim_token),
            models.NodeExecutionReceipt.claim_generation == int(claim_generation),
            models.NodeExecutionReceipt.lease_expires_at > now,
        )
        .values(lease_expires_at=now + timedelta(seconds=max(1, int(lease_seconds))))
        .execution_options(synchronize_session=False)
    )
    if int(changed.rowcount or 0) != 1:
        await db.rollback()
        return False
    await db.commit()
    return True


async def renew_atomic_group_execution_claim(
    db: Any,
    atomic_group_id: str,
    claim_token: str,
    claim_generation: int,
    member_claims: Mapping[str, int],
    *,
    lease_seconds: int = 90,
) -> bool:
    """Renew a Group lease and every member lease in one fenced transaction."""
    now = _now()
    expires = now + timedelta(seconds=max(1, int(lease_seconds)))
    changed = await db.execute(
        update(models.AtomicGroupExecutionClaim)
        .where(
            models.AtomicGroupExecutionClaim.atomic_group_id == str(atomic_group_id),
            models.AtomicGroupExecutionClaim.status == "running",
            models.AtomicGroupExecutionClaim.claim_token == str(claim_token),
            models.AtomicGroupExecutionClaim.claim_generation == int(claim_generation),
            models.AtomicGroupExecutionClaim.lease_expires_at > now,
        )
        .values(lease_expires_at=expires)
        .execution_options(synchronize_session=False)
    )
    if int(changed.rowcount or 0) != 1:
        await db.rollback()
        return False
    for node_id, generation in member_claims.items():
        member = await db.execute(
            update(models.NodeExecutionReceipt)
            .where(
                models.NodeExecutionReceipt.node_id == str(node_id),
                models.NodeExecutionReceipt.status == "running",
                models.NodeExecutionReceipt.claim_token == str(claim_token),
                models.NodeExecutionReceipt.claim_generation == int(generation),
                models.NodeExecutionReceipt.lease_expires_at > now,
            )
            .values(lease_expires_at=expires)
            .execution_options(synchronize_session=False)
        )
        if int(member.rowcount or 0) != 1:
            await db.rollback()
            return False
    await db.commit()
    return True


def _is_transient_db_lock_error(exc: BaseException) -> bool:
    """Return true only for database lock contention that may clear before lease expiry."""
    texts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        texts.append(str(current).lower())
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException):
            current = orig
            continue
        current = current.__cause__ if isinstance(current.__cause__, BaseException) else None
    joined = " | ".join(texts)
    return any(
        token in joined
        for token in (
            "database is locked",
            "database is busy",
            "sqlite_busy",
            "could not obtain lock",
            "lock timeout",
            "deadlock detected",
        )
    )


async def _run_lease_heartbeat(
    bind: Any,
    renew: Any,
    stop: asyncio.Event,
    lost: asyncio.Event,
    *,
    interval: float,
    lease_seconds: float,
    initial_lease_expires_at: datetime | None,
) -> None:
    """Renew a durable lease, failing closed at the locally-known monotonic deadline."""
    if bind is None or initial_lease_expires_at is None:
        lost.set()
        return
    lease_window = max(0.01, float(lease_seconds))
    initial_remaining = max(0.0, (initial_lease_expires_at - _now()).total_seconds())
    deadline = time.monotonic() + initial_remaining
    factory = async_sessionmaker(bind, expire_on_commit=False)
    while not stop.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            lost.set()
            return
        try:
            await asyncio.wait_for(
                stop.wait(),
                timeout=min(max(0.01, float(interval)), remaining),
            )
            return
        except TimeoutError:
            pass
        if time.monotonic() >= deadline:
            lost.set()
            return
        renewal_started = time.monotonic()
        try:
            async with factory() as heartbeat_db:
                if not await renew(heartbeat_db):
                    lost.set()
                    return
        except OperationalError as exc:
            if not _is_transient_db_lock_error(exc):
                lost.set()
                return
            # Lock contention may be retried only while the last proven lease is
            # locally authoritative. It can never extend that deadline.
            if time.monotonic() >= deadline:
                lost.set()
                return
            continue
        except Exception:
            # Schema, permission, programming, connection-configuration, and all
            # other non-lock failures invalidate heartbeat authority immediately.
            lost.set()
            return
        renewed_deadline = renewal_started + lease_window
        if time.monotonic() >= renewed_deadline:
            lost.set()
            return
        deadline = renewed_deadline


async def claim_atomic_group_execution(
    db: Any,
    plan: models.ProposalPlan,
    nodes: list[models.OperationNode],
    payloads: Mapping[str, Mapping[str, Any]],
    *,
    owner_token: str | None = None,
    lease_seconds: int = 90,
) -> tuple[models.AtomicGroupExecutionClaim, dict[str, models.NodeExecutionReceipt]] | None:
    """Claim the AtomicGroup and all member receipts in one transaction."""
    if not nodes:
        return None
    token = str(owner_token or f"atomic-worker-{uuid.uuid4().hex}")
    atomic_ids = {str(node.atomic_group_id or "") for node in nodes}
    group_ids = {str(node.confirmation_group_id or "") for node in nodes}
    if len(atomic_ids) != 1 or "" in atomic_ids or len(group_ids) != 1:
        raise NodeExecutionError("permanent", "AtomicGroup membership is inconsistent")
    atomic_id = next(iter(atomic_ids))
    confirmation_group_id = next(iter(group_ids))
    now = _now()
    expires = now + timedelta(seconds=max(1, int(lease_seconds)))
    group_claim = await db.get(models.AtomicGroupExecutionClaim, atomic_id, populate_existing=True)
    try:
        if group_claim is None:
            group_claim = models.AtomicGroupExecutionClaim(
                atomic_group_id=atomic_id,
                plan_id=str(plan.plan_id),
                confirmation_group_id=confirmation_group_id,
                actor_id=str(plan.actor_id),
                session_id=str(plan.session_id),
                status="running",
                claim_token=token,
                claim_generation=1,
                lease_expires_at=expires,
                idempotency_key=f"atomic-group:{_digest({'plan_id': str(plan.plan_id), 'atomic_group_id': atomic_id})}",
            )
            db.add(group_claim)
            await db.flush()
        else:
            if (
                str(group_claim.plan_id) != str(plan.plan_id)
                or str(group_claim.actor_id) != str(plan.actor_id)
                or str(group_claim.session_id) != str(plan.session_id)
                or str(group_claim.confirmation_group_id) != confirmation_group_id
            ):
                raise NodeExecutionError("unknown", "AtomicGroup execution claim identity mismatch")
            if group_claim.status == "completed":
                return None
            if group_claim.status == "running" and group_claim.lease_expires_at and group_claim.lease_expires_at > now:
                return None if group_claim.claim_token != token else (group_claim, {})
            old_token = str(group_claim.claim_token or "")
            old_generation = int(group_claim.claim_generation or 0)
            changed = await db.execute(
                update(models.AtomicGroupExecutionClaim)
                .where(
                    models.AtomicGroupExecutionClaim.atomic_group_id == atomic_id,
                    models.AtomicGroupExecutionClaim.status == "running",
                    models.AtomicGroupExecutionClaim.claim_token == old_token,
                    models.AtomicGroupExecutionClaim.claim_generation == old_generation,
                    models.AtomicGroupExecutionClaim.lease_expires_at <= now,
                )
                .values(claim_token=token, claim_generation=old_generation + 1, lease_expires_at=expires)
                .execution_options(synchronize_session=False)
            )
            if int(changed.rowcount or 0) != 1:
                await db.rollback()
                return None
            await db.flush()
            group_claim = await db.get(models.AtomicGroupExecutionClaim, atomic_id, populate_existing=True)

        member_claims: dict[str, models.NodeExecutionReceipt] = {}
        for node in nodes:
            payload = payloads[str(node.node_id)]
            input_digest = _digest(payload)
            receipt = await db.get(models.NodeExecutionReceipt, node.node_id, populate_existing=True)
            if receipt is None:
                receipt = models.NodeExecutionReceipt(
                    node_id=node.node_id, plan_id=str(plan.plan_id), actor_id=str(plan.actor_id), session_id=str(plan.session_id),
                    input_digest=input_digest, status="running", attempt_count=0, claim_token=token, claim_generation=1,
                    lease_expires_at=expires, idempotency_key=_idempotency_key(str(plan.plan_id), str(node.node_id), input_digest),
                )
                db.add(receipt)
            else:
                if receipt.input_digest != input_digest or receipt.plan_id != str(plan.plan_id):
                    raise NodeExecutionError("unknown", "AtomicGroup member receipt identity mismatch")
                if receipt.status in TERMINAL_RECEIPT_STATES:
                    raise NodeExecutionError("unknown", "AtomicGroup has partially terminal member receipts")
                if receipt.lease_expires_at and receipt.lease_expires_at > now and receipt.claim_token != token:
                    await db.rollback()
                    return None
                old_member_token = str(receipt.claim_token or "")
                old_member_generation = int(receipt.claim_generation or 0)
                changed_member = await db.execute(
                    update(models.NodeExecutionReceipt)
                    .where(
                        models.NodeExecutionReceipt.node_id == str(node.node_id),
                        models.NodeExecutionReceipt.status == "running",
                        models.NodeExecutionReceipt.claim_token == old_member_token,
                        models.NodeExecutionReceipt.claim_generation == old_member_generation,
                        models.NodeExecutionReceipt.lease_expires_at <= now,
                    )
                    .values(
                        claim_token=token,
                        claim_generation=old_member_generation + (0 if old_member_token == token else 1),
                        lease_expires_at=expires,
                    )
                    .execution_options(synchronize_session=False)
                )
                if int(changed_member.rowcount or 0) != 1:
                    await db.rollback()
                    return None
                receipt = await db.get(models.NodeExecutionReceipt, node.node_id, populate_existing=True)
            member_claims[str(node.node_id)] = receipt
        await db.commit()
        group_claim = await db.get(models.AtomicGroupExecutionClaim, atomic_id, populate_existing=True)
        member_claims = {node_id: await db.get(models.NodeExecutionReceipt, node_id, populate_existing=True) for node_id in member_claims}
        return group_claim, member_claims
    except IntegrityError:
        await db.rollback()
        return None


async def _fenced_receipt(db: Any, node_id: str, token: str, generation: int) -> models.NodeExecutionReceipt:
    receipt = await db.get(models.NodeExecutionReceipt, node_id, populate_existing=True)
    if (
        receipt is None
        or receipt.status != "running"
        or receipt.claim_token != token
        or int(receipt.claim_generation or 0) != int(generation)
        or receipt.lease_expires_at is None
        or receipt.lease_expires_at <= _now()
    ):
        raise NodeExecutionError("unknown", "node execution claim lease was lost before receipt publication")
    return receipt


async def _fenced_atomic_group_claim(
    db: Any, atomic_group_id: str, token: str, generation: int
) -> models.AtomicGroupExecutionClaim:
    claim = await db.get(models.AtomicGroupExecutionClaim, atomic_group_id, populate_existing=True)
    if (
        claim is None
        or claim.status != "running"
        or claim.claim_token != token
        or int(claim.claim_generation or 0) != int(generation)
        or claim.lease_expires_at is None
        or claim.lease_expires_at <= _now()
    ):
        raise NodeExecutionError("unknown", "AtomicGroup execution claim lease was lost before publication")
    return claim


async def _publish_success(db: Any, node: models.OperationNode, claim: models.NodeExecutionReceipt, payload: Mapping[str, Any], raw: Mapping[str, Any], *, attempts: int) -> models.NodeExecutionReceipt:
    receipt = await _fenced_receipt(db, node.node_id, claim.claim_token, claim.claim_generation)
    manifest = raw.get("_effect_manifest") if isinstance(raw.get("_effect_manifest"), Mapping) else _empty_effect_manifest(node, receipt)
    public_result = _public_result(raw)
    receipt.status = "completed"
    receipt.attempt_count = attempts
    receipt.result_json = public_result
    receipt.typed_outputs = dict(public_result.get("typed_outputs") or {})
    receipt.effect_manifest_schema_version = int(manifest.get("version") or 0)
    receipt.effect_manifest_json = dict(manifest)
    receipt.effect_manifest_digest = str(manifest.get("digest") or "")
    receipt.execution_contract_digest = str(raw.get("_execution_contract_digest") or getattr(node, "execution_contract_digest", "") or "")
    receipt.before_version = str(public_result.get("before_version") or "")
    receipt.after_version = str(public_result.get("after_version") or "")
    receipt.write_occurred = bool(public_result.get("write_occurred"))
    receipt.completion_reason = str(public_result.get("completion_reason") or "completed")
    receipt.error_classification = ""
    receipt.error_message = ""
    receipt.completed_at = _now()
    receipt.lease_expires_at = None
    node.status = "completed"
    await _publish_node_outcome(
        db, node, receipt, public_result, manifest, status="completed",
        effect_state=str(manifest.get("effect_state") or ("committed" if manifest.get("effects") else "no_effect")), attempts=attempts
    )
    return receipt


async def _publish_failure(db: Any, node: models.OperationNode, claim: models.NodeExecutionReceipt, error: NodeExecutionError, *, attempts: int) -> models.NodeExecutionReceipt:
    receipt = await _fenced_receipt(db, node.node_id, claim.claim_token, claim.claim_generation)
    status = "manual_review" if error.classification in {"unknown", "integrity"} else "failed"
    reason = "manual_review_required" if status == "manual_review" else "permanent_failure"
    receipt.status = status
    receipt.attempt_count = max(1, attempts)
    receipt.error_classification = error.classification
    receipt.error_message = str(error)
    receipt.completion_reason = reason
    receipt.completed_at = _now()
    receipt.lease_expires_at = None
    receipt.effect_manifest_json = _empty_effect_manifest(
        node, receipt,
        effect_state="unknown_external" if status == "manual_review" else "rolled_back",
        completeness="unknown" if status == "manual_review" else "complete",
    )
    receipt.effect_manifest_schema_version = int(receipt.effect_manifest_json.get("version") or 0)
    receipt.effect_manifest_digest = receipt.effect_manifest_json["digest"]
    receipt.execution_contract_digest = str(getattr(node, "execution_contract_digest", "") or "")
    receipt.write_occurred = False
    node.status = status
    public_result = {
        "status": status,
        "write_occurred": False,
        "changed_records": [],
        "typed_outputs": {},
        "completion_reason": reason,
    }
    receipt.result_json = dict(public_result)
    receipt.typed_outputs = {}
    if status == "manual_review":
        plan = await db.get(models.ProposalPlan, str(node.plan_id), populate_existing=True)
        if plan is not None:
            await _ensure_manual_review_case(
                db, plan=plan, node=node, reason_code="node_terminal_failure_requires_review",
                effect_state="unknown_external",
                evidence={"classification": error.classification, "message": str(error)},
            )
    await _publish_node_outcome(
        db, node, receipt, public_result, receipt.effect_manifest_json,
        status=status,
        effect_state="unknown_external" if status == "manual_review" else "no_effect",
        attempts=max(1, attempts),
        error_classification=error.classification,
        error_message=str(error),
    )
    return receipt


async def _publish_blocked(
    db: Any,
    node: models.OperationNode,
    claim: models.NodeExecutionReceipt,
    *,
    reason: str,
) -> models.NodeExecutionReceipt:
    receipt = await _fenced_receipt(db, node.node_id, claim.claim_token, claim.claim_generation)
    manifest = _empty_effect_manifest(node, receipt)
    public_result = {
        "status": "blocked",
        "write_occurred": False,
        "changed_records": [],
        "typed_outputs": {},
        "completion_reason": str(reason),
    }
    receipt.status = "blocked"
    receipt.attempt_count = max(0, int(receipt.attempt_count or 0))
    receipt.result_json = dict(public_result)
    receipt.typed_outputs = {}
    receipt.effect_manifest_schema_version = int(manifest["version"])
    receipt.effect_manifest_json = dict(manifest)
    receipt.effect_manifest_digest = str(manifest["digest"])
    receipt.execution_contract_digest = str(getattr(node, "execution_contract_digest", "") or "")
    receipt.write_occurred = False
    receipt.error_classification = "permanent"
    receipt.error_message = "dependency failed"
    receipt.completion_reason = str(reason)
    receipt.completed_at = _now()
    receipt.lease_expires_at = None
    node.status = "blocked"
    await _publish_node_outcome(
        db, node, receipt, public_result, manifest,
        status="blocked", effect_state="no_effect", attempts=receipt.attempt_count,
        error_classification="permanent", error_message="dependency failed",
    )
    return receipt

async def publish_authorization_terminal_facts(
    db: Any,
    plan: models.ProposalPlan,
    groups: Sequence[models.ConfirmationGroup],
) -> None:
    """Publish durable no-effect facts for authorization-terminal nodes.

    Rejecting a group is a terminal decision, not an execution attempt.  The
    decision transaction still needs the same receipt/outcome/result chain as
    every other terminal path so replay and recovery never depend on SSE state.
    """
    affected_groups = [
        group for group in groups
        if str(group.status or "") in {"rejected", "blocked"}
    ]
    if not affected_groups:
        return
    group_ids = {str(group.group_id) for group in affected_groups}
    nodes = list((await db.execute(
        select(models.OperationNode).where(
            models.OperationNode.plan_id == str(plan.plan_id),
            models.OperationNode.confirmation_group_id.in_(group_ids),
        ).order_by(models.OperationNode.sequence)
    )).scalars().all())
    for node in nodes:
        status = str(node.status or "")
        if status not in {"rejected", "blocked"}:
            continue
        receipt = await db.get(models.NodeExecutionReceipt, str(node.node_id), populate_existing=True)
        input_digest = _digest(node.payload_json if isinstance(node.payload_json, Mapping) else {})
        reason = "authorization_rejected" if status == "rejected" else "dependency_blocked_by_rejection"
        error_message = "Group was rejected before execution" if status == "rejected" else "Dependency Group was rejected before execution"
        if receipt is None:
            receipt = models.NodeExecutionReceipt(
                node_id=str(node.node_id),
                plan_id=str(plan.plan_id),
                actor_id=str(plan.actor_id),
                session_id=str(plan.session_id),
                input_digest=input_digest,
                status=status,
                attempt_count=0,
                claim_token="",
                claim_generation=0,
                lease_expires_at=None,
                idempotency_key=_idempotency_key(str(plan.plan_id), str(node.node_id), input_digest),
                completion_reason=reason,
                error_classification="permanent",
                error_message=error_message,
                completed_at=_now(),
                write_occurred=False,
            )
            manifest = _empty_effect_manifest(node, receipt, effect_state="no_effect", completeness="complete")
            receipt.effect_manifest_schema_version = int(manifest.get("version") or 0)
            receipt.effect_manifest_json = dict(manifest)
            receipt.effect_manifest_digest = str(manifest.get("digest") or "")
            receipt.execution_contract_digest = str(getattr(node, "execution_contract_digest", "") or "")
            receipt.result_json = {
                "status": status,
                "write_occurred": False,
                "changed_records": [],
                "typed_outputs": {},
                "completion_reason": reason,
            }
            receipt.typed_outputs = {}
            db.add(receipt)
            await db.flush()
            await _publish_node_outcome(
                db, node, receipt, receipt.result_json, manifest,
                status=status, effect_state="no_effect", attempts=0,
                error_classification="permanent", error_message=error_message,
            )
        else:
            if (
                str(receipt.plan_id) != str(plan.plan_id)
                or str(receipt.actor_id) != str(plan.actor_id)
                or str(receipt.session_id) != str(plan.session_id)
                or str(receipt.status) != status
                or str(receipt.input_digest) != input_digest
            ):
                raise NodeExecutionError("integrity", f"Authorization terminal receipt binding is invalid for Node {node.node_id}")
            outcome = await db.scalar(select(models.NodeExecutionOutcome).where(models.NodeExecutionOutcome.node_id == str(node.node_id)))
            if outcome is None:
                manifest = receipt.effect_manifest_json if isinstance(receipt.effect_manifest_json, Mapping) else _empty_effect_manifest(node, receipt)
                await _publish_node_outcome(
                    db, node, receipt, receipt.result_json if isinstance(receipt.result_json, Mapping) else {}, manifest,
                    status=status, effect_state="no_effect", attempts=int(receipt.attempt_count or 0),
                    error_classification=str(receipt.error_classification or "permanent"), error_message=str(receipt.error_message or error_message),
                )
    await db.flush()
    for group in affected_groups:
        await execution_result_from_receipts(db, str(plan.plan_id), group_id=str(group.group_id))
    all_nodes = list((await db.execute(select(models.OperationNode).where(models.OperationNode.plan_id == str(plan.plan_id)))).scalars().all())
    all_groups = list((await db.execute(select(models.ConfirmationGroup).where(models.ConfirmationGroup.plan_id == str(plan.plan_id)))).scalars().all())
    group_terminal_states = {"rejected", "blocked", "completed", "failed", "manual_review", "partially_completed", "compensated"}
    if all_nodes and all(node.status in TERMINAL_RECEIPT_STATES for node in all_nodes) and all(str(group.status or "") in group_terminal_states for group in all_groups):
        if any(str(node.status or "") in {"rejected", "failed", "blocked", "manual_review"} for node in all_nodes):
            plan.status = "failed" if not any(str(node.status or "") == "manual_review" for node in all_nodes) else "manual_review"


async def _execute_atomic_group(db: Any, plan: models.ProposalPlan, nodes: list[models.OperationNode], handler: Any, parents: Mapping[str, set[str]], receipts: dict[str, models.NodeExecutionReceipt], effect_nodes: Mapping[str, str], *, max_attempts: int, lease_seconds: int, heartbeat_interval: float) -> bool:
    token = f"atomic-worker-{uuid.uuid4().hex}"
    member_ids = [str(node.node_id) for node in nodes]
    node_ids = set(member_ids)
    payloads: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        external_parents = parents.get(node.node_id, set()) - node_ids
        if any(parent not in receipts or receipts[parent].status != "completed" for parent in external_parents):
            return False
        payloads[node.node_id] = _resolve_refs(dict(node.payload_json or {}), receipts, effect_nodes)
    acquired = await claim_atomic_group_execution(db, plan, nodes, payloads, owner_token=token, lease_seconds=lease_seconds)
    if acquired is None:
        return False
    group_claim, claims = acquired
    group_claim_id = str(group_claim.atomic_group_id)
    group_claim_token = str(group_claim.claim_token)
    group_claim_generation = int(group_claim.claim_generation or 0)
    initial_group_lease_expires_at = group_claim.lease_expires_at
    stop_heartbeat = asyncio.Event()
    lost_heartbeat = asyncio.Event()
    member_generations = {node_id: int(claim.claim_generation or 0) for node_id, claim in claims.items()}
    member_idempotency_keys = {node_id: str(claim.idempotency_key or "") for node_id, claim in claims.items()}
    async def renew_group(heartbeat_db: Any) -> bool:
        return await renew_atomic_group_execution_claim(
            heartbeat_db, group_claim_id, group_claim_token,
            group_claim_generation, member_generations, lease_seconds=lease_seconds,
        )
    heartbeat_task = asyncio.create_task(
        _run_lease_heartbeat(
            db.bind, renew_group, stop_heartbeat, lost_heartbeat,
            interval=heartbeat_interval, lease_seconds=lease_seconds,
            initial_lease_expires_at=initial_group_lease_expires_at,
        )
    )
    attempts = 0
    last_error = NodeExecutionError("unknown", "AtomicGroup did not execute")
    while attempts < max(1, int(max_attempts)):
        attempts += 1
        staged: dict[str, Mapping[str, Any]] = {}
        local_receipts: dict[str, Any] = dict(receipts)
        try:
            active_nodes: dict[str, models.OperationNode] = {}
            active_claims: dict[str, models.NodeExecutionReceipt] = {}
            for node_id in member_ids:
                active_node = await db.get(models.OperationNode, node_id, populate_existing=True)
                if active_node is None:
                    raise NodeExecutionError("integrity", "AtomicGroup member disappeared before execution")
                active_nodes[node_id] = active_node
                active_claims[node_id] = await _fenced_receipt(
                    db, node_id, group_claim_token, member_generations[node_id]
                )
            for node_id in member_ids:
                if lost_heartbeat.is_set():
                    raise NodeExecutionError(
                        "unknown",
                        "AtomicGroup execution lease was lost before the next member effect",
                    )
                node = active_nodes[node_id]
                setattr(node, "execution_idempotency_key", member_idempotency_keys[node_id])
                raw = dict(await handler(node, payloads[node_id]))
                if lost_heartbeat.is_set():
                    raise NodeExecutionError(
                        "unknown",
                        "AtomicGroup execution lease was lost during a member effect",
                    )
                if str(raw.get("status") or "") not in {"completed", "success"}:
                    raise NodeExecutionError("unknown", "node handler returned a non-terminal success envelope")
                raw = canonicalize_node_execution_result(node, raw, resolved_payload=payloads[node_id])
                staged[node_id] = raw
                local_receipts[node_id] = type("ReceiptView", (), {"status": "completed", "typed_outputs": dict(raw.get("typed_outputs") or {})})()
            if lost_heartbeat.is_set():
                raise NodeExecutionError("unknown", "AtomicGroup execution lease was lost before receipt publication")
            stop_heartbeat.set()
            await heartbeat_task
            group_claim = await _fenced_atomic_group_claim(
                db, group_claim_id, group_claim_token, group_claim_generation,
            )
            for node_id in member_ids:
                node = active_nodes[node_id]
                receipt = await _publish_success(
                    db, node, active_claims[node_id], payloads[node_id], staged[node_id], attempts=attempts
                )
                receipts[node_id] = receipt
            group_claim.status = "completed"
            group_claim.completed_at = _now()
            group_claim.lease_expires_at = None
            await db.commit()
            return True
        except NodeExecutionError as exc:
            await db.rollback()
            last_error = exc
            if exc.classification == "transient" and attempts < max(1, int(max_attempts)):
                continue
            break
        except Exception as exc:
            await db.rollback()
            last_error = NodeExecutionError("unknown", str(exc))
            break
    stop_heartbeat.set()
    await heartbeat_task
    group_claim = await _fenced_atomic_group_claim(
        db, group_claim_id, group_claim_token, group_claim_generation,
    )
    for node_id in member_ids:
        node = await db.get(models.OperationNode, node_id, populate_existing=True)
        claim = await db.get(models.NodeExecutionReceipt, node_id, populate_existing=True)
        if node is not None and claim is not None:
            receipt = await _publish_failure(db, node, claim, last_error, attempts=attempts)
            receipts[node_id] = receipt
    group_claim.status = "failed" if last_error.classification != "unknown" else "manual_review"
    group_claim.completed_at = _now()
    group_claim.lease_expires_at = None
    await db.commit()
    return False


async def execution_result_from_receipts(
    db: Any,
    plan_id: str,
    *,
    group_id: str | None = None,
) -> dict[str, Any]:
    """Project the terminal result from immutable NodeExecutionOutcome facts."""
    from app.operator.effect_manifest import (
        EffectManifestError, changed_records_from_manifest, has_applied_effects,
        no_op_records_from_manifest, validate_effect_manifest, validate_node_contract,
    )

    plan = await db.get(models.ProposalPlan, str(plan_id), populate_existing=True)
    if plan is None:
        raise NodeExecutionError("permanent", "Plan was not found while projecting durable execution receipts")
    immutable = plan.immutable_json if isinstance(plan.immutable_json, Mapping) else {}
    if str(plan.plan_digest or "") != _digest(immutable):
        raise NodeExecutionError("integrity", "sealed ProposalPlan digest is invalid")
    node_query = select(models.OperationNode).where(models.OperationNode.plan_id == str(plan_id))
    if group_id is not None:
        node_query = node_query.where(models.OperationNode.confirmation_group_id == str(group_id))
    nodes = list((await db.execute(
        node_query.order_by(models.OperationNode.sequence).execution_options(populate_existing=True)
    )).scalars().all())
    if not nodes:
        raise NodeExecutionError("unknown", "Plan Group has no OperationNodes to project")
    try:
        for node in nodes:
            validate_operation_node_binding(plan, node)
    except PlanSnapshotIntegrityError as exc:
        raise NodeExecutionError("integrity", str(exc)) from exc
    node_ids = [str(node.node_id) for node in nodes]
    receipts = {
        str(receipt.node_id): receipt
        for receipt in (await db.execute(
            select(models.NodeExecutionReceipt)
            .where(models.NodeExecutionReceipt.node_id.in_(node_ids))
            .execution_options(populate_existing=True)
        )).scalars().all()
    }
    outcomes = {
        str(outcome.node_id): outcome
        for outcome in (await db.execute(
            select(models.NodeExecutionOutcome)
            .where(models.NodeExecutionOutcome.node_id.in_(node_ids))
            .execution_options(populate_existing=True)
        )).scalars().all()
    }

    changed_records: list[dict[str, str]] = []
    no_op_records: list[dict[str, str]] = []
    typed_outputs: dict[str, dict[str, Any]] = {}
    writes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    node_results: list[dict[str, Any]] = []
    outcome_material: list[dict[str, str]] = []
    for node in nodes:
        receipt = receipts.get(str(node.node_id))
        outcome = outcomes.get(str(node.node_id))
        if receipt is None:
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} has a missing durable receipt")
        if outcome is None:
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} has no immutable terminal outcome")
        if (
            str(receipt.node_id) != str(node.node_id)
            or str(receipt.plan_id) != str(plan.plan_id)
            or str(receipt.actor_id) != str(plan.actor_id)
            or str(receipt.session_id) != str(plan.session_id)
            or str(outcome.node_id) != str(node.node_id)
            or str(outcome.plan_id) != str(plan.plan_id)
            or str(outcome.actor_id) != str(plan.actor_id)
            or str(outcome.session_id) != str(plan.session_id)
        ):
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} durable outcome identity mismatch")
        if str(receipt.status) not in TERMINAL_RECEIPT_STATES or str(outcome.status) not in TERMINAL_RECEIPT_STATES:
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} has a non-terminal durable outcome")
        if str(node.status) != str(receipt.status) or str(node.status) != str(outcome.status):
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} and durable outcome status mismatch")
        if receipt.completed_at is None or not str(receipt.completion_reason or "").strip():
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} terminal receipt metadata is incomplete")
        if receipt.status not in {"blocked", "rejected"} and int(receipt.attempt_count or 0) < 1:
            raise NodeExecutionError("integrity", f"OperationNode {node.node_id} terminal receipt attempt metadata is incomplete")
        try:
            contract = validate_node_contract(node)
            manifest = validate_effect_manifest(
                node,
                outcome.effect_manifest_json if isinstance(outcome.effect_manifest_json, Mapping) else {},
                expected_resolved_input_digest=str(receipt.input_digest or ""),
            )
        except EffectManifestError as exc:
            raise NodeExecutionError("integrity", str(exc)) from exc
        raw = outcome.public_result_json if isinstance(outcome.public_result_json, Mapping) else {}
        if str(outcome.status) == "completed":
            canonical = canonicalize_node_execution_result(
                node,
                {**dict(raw), "_effect_manifest": manifest},
                expected_resolved_input_digest=str(receipt.input_digest or ""),
            )
            canonical_public = _public_result(canonical)
            stored_public = json.loads(json.dumps(dict(raw), default=str))
            if canonical_public != stored_public:
                raise NodeExecutionError(
                    "integrity",
                    f"Node {node.node_id} public result is not derived from its effect manifest",
                )
        elif manifest.get("effects"):
            raise NodeExecutionError("integrity", f"Node {node.node_id} non-completed outcome contains committed effects")
        if dict(receipt.result_json or {}) != dict(raw) or dict(receipt.typed_outputs or {}) != dict(outcome.typed_outputs or {}):
            raise NodeExecutionError("integrity", f"Node {node.node_id} receipt and immutable outcome disagree")
        if str(receipt.execution_contract_digest or "") != str(contract.get("digest") or ""):
            raise NodeExecutionError("integrity", f"Node {node.node_id} receipt contract binding is invalid")
        if str(outcome.execution_contract_digest or "") != str(contract.get("digest") or ""):
            raise NodeExecutionError("integrity", f"Node {node.node_id} outcome contract binding is invalid")
        if str(outcome.public_result_digest or "") != _digest(raw) or str(outcome.typed_outputs_digest or "") != _digest(outcome.typed_outputs or {}):
            raise NodeExecutionError("integrity", f"Node {node.node_id} immutable outcome digest is invalid")
        if str(outcome.effect_manifest_digest or "") != str(manifest.get("digest") or ""):
            raise NodeExecutionError("integrity", f"Node {node.node_id} effect manifest digest is invalid")
        if (
            str(outcome.node_digest or "") != _node_digest(node)
            or str(outcome.resolved_input_digest or "") != str(receipt.input_digest or "")
            or str((manifest.get("bindings") or {}).get("resolved_input_digest") or "") != str(receipt.input_digest or "")
            or int(outcome.attempt_count or 0) != int(receipt.attempt_count or 0)
            or str(outcome.effect_state or "") == "committed" and not manifest.get("effects")
            or str(outcome.effect_state or "") == "no_effect" and manifest.get("effects")
            or dict(receipt.effect_manifest_json or {}) != dict(manifest)
            or str(receipt.completion_reason or "") != str(outcome.completion_reason or "")
        ):
            raise NodeExecutionError("integrity", f"Node {node.node_id} terminal evidence binding is invalid")

        safe_outputs = json.loads(json.dumps(outcome.typed_outputs or {}, default=str))
        receipt_changed = changed_records_from_manifest(manifest)
        if receipt_changed != list(raw.get("changed_records") or []):
            raise NodeExecutionError("integrity", f"Node {node.node_id} changed records are not manifest-derived")
        write_occurred = has_applied_effects(manifest)
        if bool(raw.get("write_occurred")) != write_occurred:
            raise NodeExecutionError("integrity", f"Node {node.node_id} write state is not manifest-derived")
        detail = {
            "node_id": str(node.node_id),
            "status": str(node.status),
            "receipt_status": str(receipt.status),
            "write_occurred": write_occurred,
            "effect_state": str(outcome.effect_state or ""),
            "completion_reason": str(outcome.completion_reason or ""),
            "typed_outputs": safe_outputs,
            "attempt_count": int(outcome.attempt_count or 0),
            "error_classification": str(outcome.error_classification or ""),
            "error_message": str(outcome.error_message or ""),
        }
        typed_outputs[str(node.node_id)] = dict(safe_outputs)
        if write_occurred:
            writes.append({
                "node_id": str(node.node_id),
                "completion_reason": str(outcome.completion_reason or ""),
                "typed_outputs": dict(safe_outputs),
                "changed_records": receipt_changed,
                "effect_manifest_digest": str(manifest.get("digest") or ""),
            })
            for item in receipt_changed:
                if item not in changed_records:
                    changed_records.append(item)
        elif receipt_changed:
            raise NodeExecutionError("integrity", f"Node {node.node_id} no-effect outcome declares changed records")
        for item in no_op_records_from_manifest(manifest):
            if item not in no_op_records:
                no_op_records.append(item)
        if receipt.status in {"failed", "manual_review", "blocked", "rejected"}:
            failures.append({
                "node_id": str(node.node_id),
                "status": str(receipt.status),
                "classification": str(outcome.error_classification or receipt.error_classification or ""),
                "message": str(outcome.error_message or receipt.error_message or ""),
            })
        node_results.append(detail)
        outcome_material.append({
            "node_id": str(node.node_id),
            "outcome_id": str(outcome.outcome_id),
            "node_digest": str(outcome.node_digest or ""),
            "execution_contract_digest": str(outcome.execution_contract_digest or ""),
            "resolved_input_digest": str(outcome.resolved_input_digest or ""),
            "status": str(outcome.status or ""),
            "effect_state": str(outcome.effect_state or ""),
            "attempt_count": int(outcome.attempt_count or 0),
            "public_result_digest": str(outcome.public_result_digest or ""),
            "typed_outputs_digest": str(outcome.typed_outputs_digest or ""),
            "effect_manifest_digest": str(outcome.effect_manifest_digest or ""),
            "error_classification": str(outcome.error_classification or ""),
            "error_message": str(outcome.error_message or ""),
        })

    group = await db.get(models.ConfirmationGroup, str(group_id), populate_existing=True) if group_id is not None else None
    if group is not None:
        try:
            validate_confirmation_group_binding(plan, group)
        except PlanSnapshotIntegrityError as exc:
            raise NodeExecutionError("integrity", str(exc)) from exc
    if group_id is not None and (
        group is None
        or str(group.plan_id) != str(plan.plan_id)
        or any(str(node.confirmation_group_id) != str(group_id) for node in nodes)
    ):
        raise NodeExecutionError("integrity", "Plan Group receipt projection binding mismatch")
    compensation = None
    if group is None:
        saga = await db.get(models.SagaGroup, str(plan_id), populate_existing=True)
        if saga is not None:
            compensation_receipts = list((await db.execute(
                select(models.SagaCompensationReceipt).where(models.SagaCompensationReceipt.plan_id == str(plan_id))
            )).scalars().all())
            compensation = {
                "status": str(saga.status),
                "receipts": [
                    {
                        "node_id": str(item.node_id), "status": str(item.status), "operation": str(item.operation or ""),
                        "attempt_count": int(item.attempt_count or 0), "fence_verified": bool(item.fence_verified),
                        "error_classification": str(item.error_classification or ""), "error_message": str(item.error_message or ""),
                    }
                    for item in compensation_receipts
                ],
            }
    status = str(group.status if group is not None else plan.status)
    result = {
        "ok": status not in {"failed", "manual_review", "rejected", "blocked", "partially_completed"},
        "plan_id": str(plan.plan_id),
        "status": status,
        "group_id": str(group_id or ""),
        "group_status": str(group.status) if group is not None else "",
        "all_nodes_terminal": True,
        "write_occurred": bool(writes),
        "nodes": node_results,
        "changed_records": changed_records,
        "no_op_records": no_op_records,
        "typed_outputs": typed_outputs,
        "writes": writes,
        "failures": failures,
        "compensation": compensation,
    }
    if group is not None:
        node_outcome_set_digest = _digest(sorted(outcome_material, key=lambda item: item["node_id"]))
        result_digest = _digest(result)
        existing_group_receipt = await db.scalar(
            select(models.PlanGroupResultReceipt).where(
                models.PlanGroupResultReceipt.plan_id == str(plan.plan_id),
                models.PlanGroupResultReceipt.group_id == str(group.group_id),
            )
        )
        if existing_group_receipt is None:
            group_receipt = models.PlanGroupResultReceipt(
                result_receipt_id=f"group-result:{plan.plan_id}:{group.group_id}",
                plan_id=str(plan.plan_id), group_id=str(group.group_id), actor_id=str(plan.actor_id), session_id=str(plan.session_id),
                projection_schema_version=1, plan_digest=str(plan.plan_digest), group_digest=group_authorization_digest(group),
                node_outcome_set_digest=node_outcome_set_digest, canonical_result_json=dict(result),
                canonical_result_digest=result_digest, terminal_status=str(status),
            )
            db.add(group_receipt)
            await db.flush()
            existing_group_receipt = group_receipt
        else:
            stored_result = existing_group_receipt.canonical_result_json if isinstance(existing_group_receipt.canonical_result_json, Mapping) else {}
            if (
                int(existing_group_receipt.projection_schema_version or 0) != 1
                or str(existing_group_receipt.plan_id) != str(plan.plan_id)
                or str(existing_group_receipt.group_id) != str(group.group_id)
                or str(existing_group_receipt.actor_id) != str(plan.actor_id)
                or str(existing_group_receipt.session_id) != str(plan.session_id)
                or str(existing_group_receipt.plan_digest or "") != str(plan.plan_digest or "")
                or str(existing_group_receipt.group_digest or "") != group_authorization_digest(group)
                or str(existing_group_receipt.node_outcome_set_digest or "") != node_outcome_set_digest
                or str(existing_group_receipt.terminal_status or "") != status
                or str(existing_group_receipt.canonical_result_digest or "") != _digest(stored_result)
                or json.loads(json.dumps(dict(stored_result), default=str)) != result
                or str(existing_group_receipt.canonical_result_digest or "") != result_digest
            ):
                raise NodeExecutionError("integrity", "Plan Group result receipt binding is invalid")
        result = json.loads(json.dumps(dict(existing_group_receipt.canonical_result_json or {}), default=str))
        result["result_receipt_id"] = str(existing_group_receipt.result_receipt_id)
        result["result_digest"] = str(existing_group_receipt.canonical_result_digest)
    if group is None:
        result = await apply_plan_recovery_overlay(db, str(plan.plan_id), result)
    return result


async def _preflight_group_authorization(
    db: Any, plan: models.ProposalPlan, groups: list[models.ConfirmationGroup]
) -> None:
    executable = [
        group for group in groups
        if str(group.status or "") in {"confirmed", "executing", "completed", "partially_completed"}
    ]
    if not executable:
        return
    group_ids = [str(group.group_id) for group in executable]
    decisions = list((await db.execute(
        select(models.ConfirmationDecision)
        .where(
            models.ConfirmationDecision.plan_id == str(plan.plan_id),
            models.ConfirmationDecision.group_id.in_(group_ids),
        )
        .order_by(models.ConfirmationDecision.group_id, models.ConfirmationDecision.sequence)
    )).scalars().all())
    by_group: dict[str, list[models.ConfirmationDecision]] = {group_id: [] for group_id in group_ids}
    for decision in decisions:
        by_group.setdefault(str(decision.group_id), []).append(decision)
    for group in executable:
        journal = by_group.get(str(group.group_id), [])
        required = max(1, int((group.policy_json or {}).get("confirmations_required") or 1))
        expected_sequences = list(range(1, len(journal) + 1))
        if [int(item.sequence or 0) for item in journal] != expected_sequences:
            raise NodeExecutionError("integrity", f"Confirmation authorization journal sequence is invalid for Group {group.group_id}")
        if any(str(item.decision or "") != "confirm" for item in journal):
            raise NodeExecutionError("integrity", f"Confirmation authorization journal contains a non-confirm decision for Group {group.group_id}")
        for item in journal:
            if (
                str(item.actor_id or "") != str(plan.actor_id)
                or str(item.session_id or "") != str(plan.session_id)
                or str(item.plan_digest or "") != str(plan.plan_digest)
                or str(item.group_digest or "") != group_authorization_digest(group)
            ):
                raise NodeExecutionError("integrity", f"Confirmation authorization digest/scope binding is invalid for Group {group.group_id}")
        if len(journal) < required:
            raise NodeExecutionError("integrity", f"Confirmation authorization is missing or insufficient for Group {group.group_id}")


async def execute_authorized_plan(
    db: Any, actor: Any, plan_id: str, handler: Any, *, max_transient_attempts: int = 3,
    lease_seconds: int = 90, heartbeat_interval: float | None = None,
) -> dict[str, Any]:
    plan = (
        await db.execute(
            select(models.ProposalPlan)
            .where(models.ProposalPlan.plan_id == str(plan_id), models.ProposalPlan.actor_id == str(actor.actor_id), models.ProposalPlan.session_id == str(actor.session_id))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if plan is None:
        raise NodeExecutionError("permanent", "Plan was not found in actor/session scope")
    plan_id_value = str(plan.plan_id)
    heartbeat_interval = float(heartbeat_interval if heartbeat_interval is not None else max(0.25, lease_seconds / 3))
    plan_identity = type("PlanIdentity", (), {"plan_id": plan_id_value, "actor_id": str(plan.actor_id), "session_id": str(plan.session_id)})()
    nodes = list((await db.execute(
        select(models.OperationNode).where(models.OperationNode.plan_id == plan_id_value).order_by(models.OperationNode.sequence)
    )).scalars().all())
    try:
        for node in nodes:
            validate_operation_node_binding(plan, node)
    except PlanSnapshotIntegrityError as exc:
        error = NodeExecutionError("integrity", str(exc))
        await _ensure_manual_review_case(
            db,
            plan=plan,
            reason_code="sealed_plan_projection_mismatch",
            effect_state="unknown_external",
            evidence={"classification": error.classification, "message": str(error)},
        )
        await db.commit()
        raise error from exc
    try:
        receipts = await _preflight_terminal_node_facts(db, plan, nodes)
    except NodeExecutionError as exc:
        await _ensure_manual_review_case(
            db,
            plan=plan,
            reason_code="terminal_integrity_preflight_failed",
            effect_state="legacy_unproven" if "manifest" in str(exc).lower() else "unknown_external",
            evidence={"classification": exc.classification, "message": str(exc)},
        )
        await db.commit()
        raise
    if plan.status in {"completed", "failed", "manual_review", "partially_completed", "compensated"}:
        return await execution_result_from_receipts(db, plan_id_value)
    if plan.status in {"expired", "replaced"}:
        return {"ok": False, "plan_id": plan_id_value, "status": str(plan.status), "all_nodes_terminal": False, "nodes": []}
    groups = list((await db.execute(
        select(models.ConfirmationGroup)
        .where(models.ConfirmationGroup.plan_id == plan_id_value)
        .order_by(models.ConfirmationGroup.sequence)
    )).scalars().all())
    try:
        for group in groups:
            validate_confirmation_group_binding(plan, group)
    except PlanSnapshotIntegrityError as exc:
        error = NodeExecutionError("integrity", str(exc))
        await _ensure_manual_review_case(
            db,
            plan=plan,
            reason_code="sealed_plan_projection_mismatch",
            effect_state="unknown_external",
            evidence={"classification": error.classification, "message": str(error)},
        )
        await db.commit()
        raise error from exc
    await _preflight_group_authorization(db, plan, groups)
    plan.execution_started = True
    plan.status = "executing"
    await db.commit()

    groups = {group.group_id: group for group in (await db.execute(select(models.ConfirmationGroup).where(models.ConfirmationGroup.plan_id == plan_id_value))).scalars().all()}
    dependencies = list((await db.execute(select(models.NodeDependency).where(models.NodeDependency.plan_id == plan_id_value))).scalars().all())
    parents: dict[str, set[str]] = {node.node_id: set() for node in nodes}
    for dependency in dependencies:
        parents.setdefault(dependency.node_id, set()).add(dependency.depends_on_node_id)
    intents = {intent.intent_id: intent for intent in (await db.execute(select(models.AgentPlanIntent).where(models.AgentPlanIntent.draft_id == plan.draft_id))).scalars().all()}
    effect_nodes: dict[str, str] = {}
    for node in nodes:
        for intent_id in list(node.source_intent_ids or []):
            intent = intents.get(str(intent_id))
            if intent is not None:
                for reference_key in (str(intent.canonical_effect_key), str(intent.intent_id)):
                    if reference_key:
                        effect_nodes[reference_key] = node.node_id

    progress = True
    while progress:
        progress = False
        nodes = list((await db.execute(select(models.OperationNode).where(models.OperationNode.plan_id == plan_id_value).order_by(models.OperationNode.sequence))).scalars().all())
        for atomic_id in dict.fromkeys(str(node.atomic_group_id or "") for node in nodes if str(node.atomic_group_id or "")):
            members = [node for node in nodes if str(node.atomic_group_id or "") == atomic_id]
            if all(node.status in TERMINAL_RECEIPT_STATES for node in members):
                continue
            group = await db.get(models.ConfirmationGroup, members[0].confirmation_group_id, populate_existing=True)
            if group is None or group.status not in {"confirmed", "executing", "completed", "partially_completed"}:
                continue
            member_ids = [str(member.node_id) for member in members]
            if await _execute_atomic_group(
                db, plan_identity, members, handler, parents, receipts, effect_nodes,
                max_attempts=max_transient_attempts, lease_seconds=lease_seconds, heartbeat_interval=heartbeat_interval,
            ):
                progress = True
            else:
                refreshed = [await db.get(models.OperationNode, member_id, populate_existing=True) for member_id in member_ids]
                if any(node and node.status in {"failed", "manual_review"} for node in refreshed):
                    progress = True
        non_atomic_node_ids = [str(item.node_id) for item in nodes if not str(item.atomic_group_id or "")]
        for node_id_iter in non_atomic_node_ids:
            node = await db.get(models.OperationNode, node_id_iter, populate_existing=True)
            if node is None or str(node.atomic_group_id or ""):
                continue
            receipt = await db.get(models.NodeExecutionReceipt, node.node_id, populate_existing=True)
            if receipt is not None and receipt.status in TERMINAL_RECEIPT_STATES:
                receipts[node.node_id] = receipt
                continue
            group = await db.get(models.ConfirmationGroup, node.confirmation_group_id, populate_existing=True)
            if group is None or group.status not in {"confirmed", "executing", "completed", "partially_completed"}:
                continue
            parent_states = {parent: receipts[parent].status if parent in receipts else "pending" for parent in parents.get(node.node_id, set())}
            if any(status in {"failed", "manual_review", "blocked"} for status in parent_states.values()):
                payload = dict(node.payload_json or {})
                claim = await claim_node_execution(db, plan_identity, node, payload, lease_seconds=lease_seconds)
                if claim is not None:
                    blocked = await _publish_blocked(db, node, claim, reason="dependency_failed")
                    receipts[node.node_id] = blocked
                    await db.commit()
                    progress = True
                continue
            if any(status != "completed" for status in parent_states.values()):
                continue
            payload = _resolve_refs(dict(node.payload_json or {}), receipts, effect_nodes)
            claim = await claim_node_execution(db, plan_identity, node, payload, lease_seconds=lease_seconds)
            if claim is None:
                continue
            node_id = str(node.node_id)
            claim_token = str(claim.claim_token)
            claim_generation = int(claim.claim_generation or 0)
            idempotency_key = str(claim.idempotency_key or "")
            retry_allowed = node.tool_name in {"create_record", "patch_record", "delete_or_archive_record"}
            attempts = int(claim.attempt_count or 0)
            while attempts < max(1, int(max_transient_attempts)):
                attempts += 1
                try:
                    node = await db.get(models.OperationNode, node_id, populate_existing=True)
                    claim = await _fenced_receipt(db, node_id, claim_token, claim_generation)
                    setattr(node, "execution_idempotency_key", idempotency_key)
                    stop_heartbeat = asyncio.Event()
                    lost_heartbeat = asyncio.Event()
                    async def renew_node(heartbeat_db: Any) -> bool:
                        return await renew_node_execution_claim(
                            heartbeat_db, node_id, claim_token, claim_generation, lease_seconds=lease_seconds
                        )
                    heartbeat_task = asyncio.create_task(
                        _run_lease_heartbeat(
                            db.bind, renew_node, stop_heartbeat, lost_heartbeat,
                            interval=heartbeat_interval, lease_seconds=lease_seconds,
                            initial_lease_expires_at=claim.lease_expires_at,
                        )
                    )
                    try:
                        raw = dict(await handler(node, payload))
                    finally:
                        stop_heartbeat.set()
                        await heartbeat_task
                    if lost_heartbeat.is_set():
                        raise NodeExecutionError("unknown", "node execution lease was lost before receipt publication")
                    if str(raw.get("status") or "") not in {"completed", "success"}:
                        raise NodeExecutionError("unknown", "node handler returned a non-terminal success envelope")
                    raw = canonicalize_node_execution_result(node, raw, resolved_payload=payload)
                    receipt = await _publish_success(db, node, claim, payload, raw, attempts=attempts)
                    receipts[node.node_id] = receipt
                    await db.commit()
                    progress = True
                    break
                except NodeExecutionError as exc:
                    await db.rollback()
                    if exc.classification == "transient" and retry_allowed and attempts < max(1, int(max_transient_attempts)):
                        continue
                    node = await db.get(models.OperationNode, node_id, populate_existing=True)
                    claim = await _fenced_receipt(db, node_id, claim_token, claim_generation)
                    receipt = await _publish_failure(db, node, claim, exc, attempts=attempts)
                    receipts[node_id] = receipt
                    await db.commit()
                    progress = True
                    break
                except Exception as exc:
                    await db.rollback()
                    node = await db.get(models.OperationNode, node_id, populate_existing=True)
                    claim = await _fenced_receipt(db, node_id, claim_token, claim_generation)
                    receipt = await _publish_failure(db, node, claim, NodeExecutionError("unknown", str(exc)), attempts=attempts)
                    receipts[node_id] = receipt
                    await db.commit()
                    progress = True
                    break

    nodes = list((await db.execute(select(models.OperationNode).where(models.OperationNode.plan_id == plan_id_value).order_by(models.OperationNode.sequence))).scalars().all())
    states = {node.status for node in nodes}
    all_nodes_terminal = bool(states) and states <= TERMINAL_RECEIPT_STATES
    if not all_nodes_terminal:
        # A ConfirmationGroup may finish before independent sibling Groups are authorized.
        # Keep the immutable Plan open so later Group decisions remain valid and never
        # require receipts from nodes that have not crossed an authorization boundary.
        status = "executing"
    elif states <= {"completed"}:
        status = "completed"
    elif "manual_review" in states:
        status = "manual_review"
    elif "completed" in states and states & {"failed", "blocked"}:
        status = "partially_completed"
    elif states & {"failed", "blocked"}:
        status = "failed"
    else:
        status = "failed"
    plan = await db.get(models.ProposalPlan, plan_id_value, populate_existing=True)
    plan.status = status
    groups = {group.group_id: group for group in (await db.execute(select(models.ConfirmationGroup).where(models.ConfirmationGroup.plan_id == plan_id_value))).scalars().all()}
    for group in groups.values():
        group_nodes = [node for node in nodes if node.confirmation_group_id == group.group_id]
        group_states = {node.status for node in group_nodes}
        if group_states and group_states <= {"completed"}:
            group.status = "completed"
        elif "manual_review" in group_states:
            group.status = "manual_review"
        elif "completed" in group_states and group_states & {"failed", "blocked"}:
            group.status = "partially_completed"
        elif group_states & {"failed", "blocked"}:
            group.status = "failed"
        elif group.status == "confirmed":
            group.status = "executing"
    await db.commit()
    compensation = None
    if getattr(handler, "production_plan_handler", False) and status in {"failed", "partially_completed", "manual_review"}:
        completed_writes = [receipt for receipt in receipts.values() if receipt.status == "completed" and receipt.write_occurred]
        if completed_writes:
            from app.operator.saga import compensate_plan_registered
            compensation = await compensate_plan_registered(db, actor, plan_id_value)
            plan = await db.get(models.ProposalPlan, plan_id_value, populate_existing=True)
            status = str(plan.status)
    if status in {"completed", "failed", "manual_review", "partially_completed", "compensated"}:
        return await execution_result_from_receipts(db, plan_id_value)
    return {
        "ok": True,
        "plan_id": plan_id_value,
        "status": status,
        "all_nodes_terminal": all_nodes_terminal,
        "nodes": [{"node_id": str(node.node_id), "status": str(node.status)} for node in nodes],
        "changed_records": [],
        "typed_outputs": {},
        "writes": [],
        "failures": [],
        "compensation": compensation,
    }




