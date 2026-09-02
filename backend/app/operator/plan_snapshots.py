from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.models import models


class PlanSnapshotIntegrityError(RuntimeError):
    pass


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_material(snapshot: Any) -> dict[str, Any]:
    return {
        "node_id": str(getattr(snapshot, "node_id", "") or ""),
        "plan_id": str(getattr(snapshot, "plan_id", "") or ""),
        "confirmation_group_id": str(getattr(snapshot, "confirmation_group_id", "") or ""),
        "tool_name": str(getattr(snapshot, "tool_name", "") or ""),
        "model_or_action": str(getattr(snapshot, "model_or_action", "") or ""),
        "record_id": str(getattr(snapshot, "record_id", "") or ""),
        "risk_level": int(getattr(snapshot, "risk_level", 0) or 0),
        "locked_payload": dict(getattr(snapshot, "locked_payload", {}) or {}),
        "affected_records": list(getattr(snapshot, "affected_records", []) or []),
        "before": getattr(snapshot, "before", None),
        "after": getattr(snapshot, "after", None),
        "expected_version_or_hash": str(getattr(snapshot, "expected_version_or_hash", "") or ""),
    }


def snapshot_digest(snapshot: Any) -> str:
    return canonical_digest(snapshot_material(snapshot))


def _sealed_group_entry(plan: models.ProposalPlan, group: models.ConfirmationGroup) -> Mapping[str, Any]:
    immutable = plan.immutable_json if isinstance(plan.immutable_json, Mapping) else {}
    if str(plan.plan_digest or "") != canonical_digest(immutable):
        raise PlanSnapshotIntegrityError("sealed ProposalPlan digest is invalid")
    entries = immutable.get("confirmation_groups") if isinstance(immutable, Mapping) else None
    if not isinstance(entries, list):
        raise PlanSnapshotIntegrityError("sealed ProposalPlan has no ConfirmationGroup material")
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, Mapping) and str(item.get("group_id") or "") == str(group.group_id)
        ),
        None,
    )
    if entry is None:
        raise PlanSnapshotIntegrityError("ConfirmationGroup is absent from sealed ProposalPlan material")
    if str(entry.get("plan_id") or "") not in {"", str(plan.plan_id)}:
        raise PlanSnapshotIntegrityError("ConfirmationGroup sealed Plan identity is invalid")
    sealed_digest = canonical_digest(
        {
            "policy": entry.get("policy") if isinstance(entry.get("policy"), Mapping) else {},
            "node_ids": list(entry.get("node_ids") or []),
            "dependencies": list(entry.get("dependency_group_ids") or []),
        }
    )
    if str(entry.get("group_digest") or "") != sealed_digest:
        raise PlanSnapshotIntegrityError("sealed ConfirmationGroup digest is invalid")
    return entry


def group_snapshot_binding(
    plan: models.ProposalPlan,
    group: models.ConfirmationGroup,
    snapshots: Sequence[models.PlanNodeExecutionSnapshot],
) -> tuple[str, str]:
    entry = _sealed_group_entry(plan, group)
    sealed_group_digest = str(entry.get("group_digest") or "")
    node_ids = [str(item) for item in list(entry.get("node_ids") or [])]
    by_node: dict[str, models.PlanNodeExecutionSnapshot] = {}
    for snapshot in snapshots:
        node_id = str(snapshot.node_id or "")
        if node_id in by_node:
            raise PlanSnapshotIntegrityError(f"duplicate execution snapshot for Node {node_id}")
        by_node[node_id] = snapshot
    if set(by_node) != set(node_ids) or len(by_node) != len(node_ids):
        raise PlanSnapshotIntegrityError("ConfirmationGroup execution snapshot membership is incomplete")
    refs: list[dict[str, str]] = []
    for node_id in node_ids:
        snapshot = by_node[node_id]
        if (
            str(snapshot.plan_id or "") != str(plan.plan_id)
            or str(snapshot.confirmation_group_id or "") != str(group.group_id)
            or str(snapshot.node_id or "") != node_id
        ):
            raise PlanSnapshotIntegrityError(f"execution snapshot identity is invalid for Node {node_id}")
        computed = snapshot_digest(snapshot)
        if str(snapshot.snapshot_digest or "") != computed:
            raise PlanSnapshotIntegrityError(f"execution snapshot digest is invalid for Node {node_id}")
        refs.append({"node_id": node_id, "snapshot_digest": computed})
    bound = canonical_digest(
        {
            "schema_version": 1,
            "sealed_group_digest": sealed_group_digest,
            "execution_snapshots": refs,
        }
    )
    return sealed_group_digest, bound


def validate_group_snapshot_binding(
    plan: models.ProposalPlan,
    group: models.ConfirmationGroup,
    snapshots: Sequence[models.PlanNodeExecutionSnapshot],
) -> str:
    sealed, expected = group_snapshot_binding(plan, group, snapshots)
    if str(group.group_digest or "") != sealed:
        raise PlanSnapshotIntegrityError("ConfirmationGroup sealed digest does not match the immutable Plan")
    if str(getattr(group, "authorization_digest", "") or "") != expected:
        raise PlanSnapshotIntegrityError("ConfirmationGroup authorization digest is not bound to immutable execution snapshots")
    return expected


def _sealed_node_material(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(entry.get("node_id") or ""),
        "sequence": int(entry.get("sequence") or 0),
        "tool_name": str(entry.get("tool_name") or ""),
        "target_kind": str(entry.get("target_kind") or ""),
        "target_name": str(entry.get("target_name") or ""),
        "record_id": str(entry.get("record_id") or ""),
        "base_version": str(entry.get("base_version") or ""),
        "atomic_group_id": str(entry.get("atomic_group_id") or ""),
        "payload": dict(entry.get("payload") or {}) if isinstance(entry.get("payload"), Mapping) else {},
        "typed_outputs": dict(entry.get("typed_outputs") or {}) if isinstance(entry.get("typed_outputs"), Mapping) else {},
        "execution_contract": dict(entry.get("execution_contract") or {}) if isinstance(entry.get("execution_contract"), Mapping) else {},
        "confirmation_group_id": str(entry.get("confirmation_group_id") or ""),
        "risk_level": int(entry.get("risk_level") or 0),
        "compensation_policy": str(entry.get("compensation_policy") or ""),
    }


def operation_node_material(node: models.OperationNode) -> dict[str, Any]:
    return {
        "node_id": str(node.node_id or ""),
        "sequence": int(node.sequence or 0),
        "tool_name": str(node.tool_name or ""),
        "target_kind": str(node.target_kind or ""),
        "target_name": str(node.target_name or ""),
        "record_id": str(node.record_id or ""),
        "base_version": str(node.base_version or ""),
        "atomic_group_id": str(node.atomic_group_id or ""),
        "payload": dict(node.payload_json or {}) if isinstance(node.payload_json, Mapping) else {},
        "typed_outputs": dict(node.typed_outputs or {}) if isinstance(node.typed_outputs, Mapping) else {},
        "execution_contract": dict(node.execution_contract_json or {}) if isinstance(node.execution_contract_json, Mapping) else {},
        "confirmation_group_id": str(node.confirmation_group_id or ""),
        "risk_level": int(node.risk_level or 0),
        "compensation_policy": str(node.compensation_policy or ""),
    }


def validate_operation_node_binding(
    plan: models.ProposalPlan,
    node: models.OperationNode,
) -> Mapping[str, Any]:
    immutable = plan.immutable_json if isinstance(plan.immutable_json, Mapping) else {}
    if str(plan.plan_digest or "") != canonical_digest(immutable):
        raise PlanSnapshotIntegrityError("sealed ProposalPlan digest is invalid")
    entries = immutable.get("nodes") if isinstance(immutable, Mapping) else None
    if not isinstance(entries, list):
        raise PlanSnapshotIntegrityError("sealed ProposalPlan has no OperationNode material")
    matches = [
        item for item in entries
        if isinstance(item, Mapping) and str(item.get("node_id") or "") == str(node.node_id)
    ]
    if len(matches) != 1:
        raise PlanSnapshotIntegrityError(
            f"OperationNode {node.node_id} is absent or duplicated in sealed ProposalPlan material"
        )
    entry = matches[0]
    expected = _sealed_node_material(entry)
    sealed_digest = str(entry.get("node_digest") or "")
    if not sealed_digest or sealed_digest != canonical_digest(expected):
        raise PlanSnapshotIntegrityError(f"sealed OperationNode {node.node_id} digest is invalid")
    if str(node.plan_id or "") != str(plan.plan_id):
        raise PlanSnapshotIntegrityError(f"OperationNode {node.node_id} Plan identity is invalid")
    if operation_node_material(node) != expected or str(node.node_digest or "") != sealed_digest:
        raise PlanSnapshotIntegrityError(
            f"OperationNode {node.node_id} differs from immutable ProposalPlan material"
        )
    contract = expected["execution_contract"]
    contract_digest = str(contract.get("digest") or "")
    if (
        not contract_digest
        or canonical_digest({key: value for key, value in contract.items() if key != "digest"}) != contract_digest
        or str(node.execution_contract_digest or "") != contract_digest
    ):
        raise PlanSnapshotIntegrityError(
            f"OperationNode {node.node_id} execution contract is not sealed by the ProposalPlan"
        )
    return entry


def validate_confirmation_group_binding(
    plan: models.ProposalPlan,
    group: models.ConfirmationGroup,
) -> Mapping[str, Any]:
    entry = _sealed_group_entry(plan, group)
    expected_policy = dict(entry.get("policy") or {}) if isinstance(entry.get("policy"), Mapping) else {}
    expected_dependencies = [str(item) for item in list(entry.get("dependency_group_ids") or [])]
    if (
        str(group.plan_id or "") != str(plan.plan_id)
        or int(group.sequence or 0) != int(entry.get("sequence") or 0)
        or dict(group.policy_json or {}) != expected_policy
        or [str(item) for item in list(group.dependency_group_ids or [])] != expected_dependencies
        or str(group.group_digest or "") != str(entry.get("group_digest") or "")
    ):
        raise PlanSnapshotIntegrityError(
            f"ConfirmationGroup {group.group_id} differs from immutable ProposalPlan policy or structure"
        )
    return entry
