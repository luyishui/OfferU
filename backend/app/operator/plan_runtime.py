from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models import models
from app.operator import tools
from app.operator.guards import remove_pending_proposal_ids, shape_proposal
from app.operator.manual_review_recovery import apply_plan_recovery_overlay
from app.operator.plan_snapshots import (
    PlanSnapshotIntegrityError,
    group_snapshot_binding,
    snapshot_digest,
    snapshot_material,
    validate_confirmation_group_binding,
    validate_group_snapshot_binding,
    validate_operation_node_binding,
)


class PlanMaterializationError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_outputs(value: Any, effect_receipts: Mapping[str, models.NodeExecutionReceipt]) -> Any:
    if isinstance(value, Mapping):
        output = value.get("$output")
        if isinstance(output, Mapping):
            effect = str(output.get("intent_key") or "")
            name = str(output.get("name") or "")
            receipt = effect_receipts.get(effect)
            if receipt is None or receipt.status != "completed" or name not in (receipt.typed_outputs or {}):
                raise PlanMaterializationError(f"Typed output {effect}.{name} is not durably available")
            return (receipt.typed_outputs or {})[name]
        return {str(key): _resolve_outputs(child, effect_receipts) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_outputs(child, effect_receipts) for child in value]
    return value


def _proposal_payload(proposal: models.ProposalCache, plan: models.ProposalPlan, group: models.ConfirmationGroup, nodes: list[models.OperationNode], operations: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "session_id": proposal.session_id,
        "actor_id": proposal.actor_id,
        "status": proposal.status,
        "risk_level": proposal.risk_level,
        "operation_type": proposal.operation_type,
        "tool_name": proposal.tool_name,
        "model_or_action": proposal.model_or_action,
        "record_id": proposal.record_id,
        "affected_records": list(proposal.affected_records or []),
        "summary": proposal.summary,
        "reason": proposal.reason,
        "before": proposal.before,
        "after": proposal.after,
        "diff": proposal.diff,
        "locked_payload": dict(proposal.locked_payload or {}),
        "operations": list(operations or []),
        "confirmations_required": proposal.confirmations_required,
        "confirmations_received": proposal.confirmations_received,
        "requires_second_confirmation": proposal.requires_second_confirmation,
        "confirmation_challenge": proposal.confirmation_challenge,
        "expires_at": proposal.expires_at.isoformat() if proposal.expires_at else None,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
        "plan_id": plan.plan_id,
        "plan_digest": plan.plan_digest,
        "plan_status": plan.status,
        "confirmation_group_id": group.group_id,
        "group_digest": str(getattr(group, "authorization_digest", "") or group.group_digest),
        "group_status": group.status,
        "node_ids": [node.node_id for node in nodes],
        "node_statuses": {node.node_id: node.status for node in nodes},
        "compensation_policy": sorted({node.compensation_policy for node in nodes}),
    }


async def record_confirmed_projection_execution(db: Any, actor: Any, proposal: models.ProposalCache, result: Mapping[str, Any]) -> models.NodeExecutionReceipt | None:
    """Legacy compatibility hook. Plan-backed cards never execute through this path."""
    if not str(getattr(proposal, "plan_id", "") or ""):
        return None
    raise PlanMaterializationError("Plan-backed projections must execute through the authorized Plan executor")


async def plan_state_envelope(db: Any, plan_id: str, *, resolved_proposal_ids: list[str] | None = None, new_proposals: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    plan = await db.get(models.ProposalPlan, str(plan_id), populate_existing=True)
    if plan is None:
        return {}
    groups = list((await db.execute(select(models.ConfirmationGroup).where(models.ConfirmationGroup.plan_id == plan.plan_id).order_by(models.ConfirmationGroup.sequence))).scalars().all())
    nodes = list((await db.execute(select(models.OperationNode).where(models.OperationNode.plan_id == plan.plan_id).order_by(models.OperationNode.sequence))).scalars().all())
    decisions = list((await db.execute(select(models.ConfirmationDecision).where(models.ConfirmationDecision.plan_id == plan.plan_id).order_by(models.ConfirmationDecision.sequence))).scalars().all())
    receipts = {item.node_id: item for item in (await db.execute(select(models.NodeExecutionReceipt).where(models.NodeExecutionReceipt.plan_id == plan.plan_id))).scalars().all()}
    outcomes = {item.node_id: item for item in (await db.execute(select(models.NodeExecutionOutcome).where(models.NodeExecutionOutcome.plan_id == plan.plan_id))).scalars().all()}
    group_results = {item.group_id: item for item in (await db.execute(select(models.PlanGroupResultReceipt).where(models.PlanGroupResultReceipt.plan_id == plan.plan_id))).scalars().all()}
    manual_cases = {}
    for item in (await db.execute(select(models.ManualReviewCase).where(models.ManualReviewCase.plan_id == plan.plan_id).order_by(models.ManualReviewCase.created_at.desc()))).scalars().all():
        if str(item.node_id or "") and str(item.node_id) not in manual_cases:
            manual_cases[str(item.node_id)] = item
    envelope = {
        "type": "plan_status",
        "plan_id": plan.plan_id,
        "status": plan.status,
        "resolved_proposal_ids": list(resolved_proposal_ids or []),
        "new_proposals": list(new_proposals or []),
        "groups": [{"group_id": group.group_id, "status": group.status, "group_digest": str(getattr(group, "authorization_digest", "") or group.group_digest), "confirmations_required": max(1, int((group.policy_json or {}).get("confirmations_required") or 1)), "confirmations_received": sum(1 for decision in decisions if decision.group_id == group.group_id and decision.decision == "confirm"), "authorized_at": max((decision.created_at.isoformat() for decision in decisions if decision.group_id == group.group_id and decision.decision == "confirm"), default=""), "result_receipt_id": group_results[group.group_id].result_receipt_id if group.group_id in group_results else "", "result_digest": group_results[group.group_id].canonical_result_digest if group.group_id in group_results else ""} for group in groups],
        "nodes": [{"node_id": node.node_id, "status": node.status, "confirmation_group_id": node.confirmation_group_id, "atomic_group_id": node.atomic_group_id, "receipt_status": receipts[node.node_id].status if node.node_id in receipts else "", "write_occurred": receipts[node.node_id].write_occurred if node.node_id in receipts else False, "completion_reason": receipts[node.node_id].completion_reason if node.node_id in receipts else "", "execution_started_at": receipts[node.node_id].created_at.isoformat() if node.node_id in receipts and receipts[node.node_id].created_at else "", "outcome_id": outcomes[node.node_id].outcome_id if node.node_id in outcomes else "", "execution_contract_digest": outcomes[node.node_id].execution_contract_digest if node.node_id in outcomes else "", "effect_manifest_digest": outcomes[node.node_id].effect_manifest_digest if node.node_id in outcomes else "", "effect_state": outcomes[node.node_id].effect_state if node.node_id in outcomes else "", "manual_review_case_id": manual_cases[node.node_id].case_id if node.node_id in manual_cases else "", "manual_review_reason_code": manual_cases[node.node_id].reason_code if node.node_id in manual_cases else "", "manual_review_summary": _manual_review_case_summary(manual_cases[node.node_id]) if node.node_id in manual_cases else ""} for node in nodes],
    }
    return await apply_plan_recovery_overlay(db, str(plan.plan_id), envelope)


async def pending_plan_bootstrap(db: Any, actor: Any) -> dict[str, Any]:
    """Rehydrate pending Plan/Group cards from durable authority after a UI reload."""
    rows = list((await db.execute(
        select(models.ProposalCache)
        .where(
            models.ProposalCache.actor_id == str(actor.actor_id),
            models.ProposalCache.session_id == str(actor.session_id),
            models.ProposalCache.tool_name == "confirm_plan_group",
            models.ProposalCache.status.in_(("pending", "awaiting_next_confirmation")),
        )
        .order_by(models.ProposalCache.created_at, models.ProposalCache.proposal_id)
    )).scalars().all())
    proposals: list[dict[str, Any]] = []
    scoped_plans = list((await db.execute(
        select(models.ProposalPlan)
        .where(
            models.ProposalPlan.actor_id == str(actor.actor_id),
            models.ProposalPlan.session_id == str(actor.session_id),
        )
        .order_by(models.ProposalPlan.created_at, models.ProposalPlan.plan_id)
    )).scalars().all())
    plan_ids: list[str] = [str(plan.plan_id) for plan in scoped_plans]
    for proposal in rows:
        plan = await db.get(models.ProposalPlan, str(proposal.plan_id), populate_existing=True)
        group = await db.scalar(select(models.ConfirmationGroup).where(
            models.ConfirmationGroup.plan_id == str(proposal.plan_id),
            models.ConfirmationGroup.group_id == str(proposal.confirmation_group_id),
        ))
        if plan is None or group is None:
            raise PlanMaterializationError(
                f"Pending Plan proposal {proposal.proposal_id} is missing its durable Plan/Group binding"
            )
        nodes = list((await db.execute(
            select(models.OperationNode)
            .where(
                models.OperationNode.plan_id == str(plan.plan_id),
                models.OperationNode.confirmation_group_id == str(group.group_id),
            )
            .order_by(models.OperationNode.sequence)
        )).scalars().all())
        snapshots = list((await db.execute(
            select(models.PlanNodeExecutionSnapshot)
            .where(
                models.PlanNodeExecutionSnapshot.plan_id == str(plan.plan_id),
                models.PlanNodeExecutionSnapshot.confirmation_group_id == str(group.group_id),
            )
        )).scalars().all())
        validate_group_snapshot_binding(plan, group, snapshots)
        proposals.append(_proposal_payload(
            proposal, plan, group, nodes,
            [dict(snapshot.locked_payload or {}) for snapshot in sorted(snapshots, key=lambda item: item.node_id)],
        ))
        if str(plan.plan_id) not in plan_ids:
            plan_ids.append(str(plan.plan_id))
    return {
        "proposals": proposals,
        "plan_events": [await plan_state_envelope(db, plan_id) for plan_id in plan_ids],
    }


# Confirmation/dispatch states that make a ProposalCache row a live
# user-confirmable authority member (mirrors guards/session confirmable set).
CONFIRMABLE_PROPOSAL_STATUSES = ("pending", "awaiting_next_confirmation")
# Group states that count as fully settled for dependency purposes. A
# compensated/rejected/cancelled/failed parent must never unblock its children.
GROUP_DEPENDENCY_SATISFIED_STATUSES = ("completed",)
# Terminal-ish group states used for envelope phase derivation.
_GROUP_EXECUTING_STATUSES = {"executing", "authorized"}
_GROUP_FAILED_STATUSES = {"failed", "blocked", "rejected", "manual_review"}


_MANUAL_REVIEW_NEXT_ALLOWED_OPERATIONS = (
    "view_manual_review_case",
    "list_manual_review_cases",
    "resolve_manual_review_case",
)


def _manual_review_case_summary(item: Any) -> str:
    """Model-safe failure summary for one ManualReviewCase.

    The durable case evidence (provided by the failing boundary) carries the
    concrete reason, e.g. ``{"classification": ..., "message": ...}`` from the
    node failure publisher. Exposing it makes the failure visible to the model
    next turn instead of a bare reason_code.
    """
    evidence = item.evidence_json if isinstance(item.evidence_json, Mapping) else {}
    message = str(evidence.get("message") or "")
    if not message:
        message = str(evidence.get("summary") or "")
        if not message:
            resolution = item.resolution_json if isinstance(item.resolution_json, Mapping) else {}
            message = str(resolution.get("summary") or resolution.get("message") or "")
    return message


def _proposal_next_action(
    status: str,
    confirmations_required: int,
    confirmations_received: int,
    *,
    group_blocked: bool = False,
) -> str:
    """Deterministic, registry/durable-fact-driven next action for one proposal.

    The model-facing projection uses this so the model can distinguish a
    first confirmation, a second confirmation, and a dependency-blocked card
    without needing any challenge/lease/authorization internals.
    """
    if group_blocked:
        return "await_dependency_groups"
    required = max(1, int(confirmations_required or 0) or 1)
    received = max(0, int(confirmations_received or 0))
    if str(status) == "awaiting_next_confirmation" or (received >= 1 and received < required and required > 1):
        return "await_user_second_confirmation"
    if received < required:
        return "await_user_confirmation"
    return "durable_settled"


def _group_blocking_dependency_ids(
    group: models.ConfirmationGroup,
    group_status_by_id: Mapping[str, str],
) -> list[str]:
    """Dependency groups that must settle before this group is confirmable."""
    blocked: list[str] = []
    for dependency_id in list(group.dependency_group_ids or []):
        dependency_id = str(dependency_id or "")
        if not dependency_id:
            continue
        if str(group_status_by_id.get(dependency_id) or "") not in GROUP_DEPENDENCY_SATISFIED_STATUSES:
            blocked.append(dependency_id)
    return blocked


async def build_public_execution_state_envelope(
    db: Any,
    actor: Any,
    *,
    plan_ids: list[str] | None = None,
    include_challenge: bool = False,
    include_durable_ids: bool = False,
) -> dict[str, Any]:
    """One durable-backed *derived projection* of execution state (SPEC 5.5).

    Derived strictly from the existing durable authorities — AgentSession
    pending authority, ProposalPlan/ConfirmationGroup/OperationNode/ProposalCache,
    NodeExecutionOutcome/PlanGroupResultReceipt, ManualReviewCase, and the
    collecting PlanDraft — and is read-only: this builder never mutates any of
    them and is not a second mutable authority.

    Projection variants:
      * model-safe (default): no confirmation challenge token, lease token,
        idempotency secret, raw locked_payload, or authorization internals;
        ``challenge_required`` is always False for the model projection.
      * ``include_challenge=True``: authenticated frontend projection — adds
        ``requires_second_confirmation`` and the backend-issued
        ``confirmation_challenge`` token (may only be consumed by the
        authenticated frontend surfaces: harness/optimize bootstrap, confirm
        round-trip).
      * ``include_durable_ids=True``: grader projection — adds durable digests.

    Consumed by system context, SSE final payload, session bootstrap,
    Optimize/Harness reducers, continuation results, the two-stage turn
    finalization pass, and (via WP7) the grader.
    """
    actor_id = str(getattr(actor, "actor_id", "") or "")
    session_id = str(getattr(actor, "session_id", "") or "")
    plans_query = select(models.ProposalPlan).where(models.ProposalPlan.actor_id == actor_id)
    if session_id:
        plans_query = plans_query.where(models.ProposalPlan.session_id == session_id)
    if plan_ids:
        plans_query = plans_query.where(models.ProposalPlan.plan_id.in_([str(item) for item in plan_ids if str(item or "").strip()]))
    plans = list((await db.execute(plans_query.order_by(models.ProposalPlan.created_at, models.ProposalPlan.plan_id))).scalars().all())
    plan_ids_in_scope = [str(plan.plan_id) for plan in plans]

    groups: list[models.ConfirmationGroup] = []
    group_results: list[models.PlanGroupResultReceipt] = []
    decisions: list[models.ConfirmationDecision] = []
    if plan_ids_in_scope:
        groups = list((await db.execute(
            select(models.ConfirmationGroup)
            .where(models.ConfirmationGroup.plan_id.in_(plan_ids_in_scope))
            .order_by(models.ConfirmationGroup.sequence, models.ConfirmationGroup.group_id)
        )).scalars().all())
        group_results = list((await db.execute(
            select(models.PlanGroupResultReceipt)
            .where(models.PlanGroupResultReceipt.plan_id.in_(plan_ids_in_scope))
            .order_by(models.PlanGroupResultReceipt.created_at, models.PlanGroupResultReceipt.result_receipt_id)
        )).scalars().all())
        decisions = list((await db.execute(
            select(models.ConfirmationDecision)
            .where(models.ConfirmationDecision.plan_id.in_(plan_ids_in_scope))
            .order_by(models.ConfirmationDecision.sequence, models.ConfirmationDecision.event_id)
        )).scalars().all())

    group_status_by_id = {str(group.group_id): str(group.status or "") for group in groups}
    confirmations_by_group: dict[str, int] = {}
    for decision in decisions:
        if str(decision.decision or "") == "confirm":
            group_key = str(decision.group_id or "")
            confirmations_by_group[group_key] = confirmations_by_group.get(group_key, 0) + 1
    group_projections: list[dict[str, Any]] = []
    for group in groups:
        blocked = _group_blocking_dependency_ids(group, group_status_by_id)
        confirmable = (
            str(group.status or "") in {"pending", "awaiting_more_confirmations"}
            and not blocked
        )
        entry: dict[str, Any] = {
            "group_id": str(group.group_id),
            "plan_id": str(group.plan_id),
            "status": str(group.status or ""),
            "dependency_group_ids": [str(item) for item in list(group.dependency_group_ids or []) if str(item or "").strip()],
            "confirmable_now": confirmable,
            "block_reason": "dependency_group_blocked" if blocked else "",
            "confirmations_required": max(1, int((group.policy_json or {}).get("confirmations_required") or 1)),
            "confirmations_received": int(confirmations_by_group.get(str(group.group_id), 0)),
        }
        if include_durable_ids:
            entry["group_digest"] = str(getattr(group, "authorization_digest", "") or group.group_digest)
        group_projections.append(entry)

    proposals: list[dict[str, Any]] = []
    agent_session = await db.get(models.AgentSession, str(session_id)) if session_id else None
    if agent_session is not None and str(agent_session.actor_id or "") == actor_id:
        pending_ids = [str(item) for item in list(agent_session.pending_proposal_ids or []) if str(item or "").strip()]
        rows: list[models.ProposalCache] = []
        if pending_ids:
            rows = list((await db.execute(
                select(models.ProposalCache).where(
                    models.ProposalCache.proposal_id.in_(pending_ids),
                    models.ProposalCache.actor_id == actor_id,
                )
            )).scalars().all())
        for row in rows:
            if str(row.status or "") not in CONFIRMABLE_PROPOSAL_STATUSES:
                continue
            group = None
            if str(row.confirmation_group_id or "").strip():
                group = next((item for item in groups if str(item.group_id) == str(row.confirmation_group_id)), None)
            blocked = bool(group is not None and _group_blocking_dependency_ids(group, group_status_by_id))
            next_action = _proposal_next_action(
                str(row.status or ""),
                int(row.confirmations_required or 0),
                int(row.confirmations_received or 0),
                group_blocked=blocked,
            )
            entry: dict[str, Any] = {
                "proposal_id": str(row.proposal_id),
                "plan_id": str(row.plan_id or ""),
                "group_id": str(row.confirmation_group_id or ""),
                "status": str(row.status or ""),
                "confirmations_required": max(1, int(row.confirmations_required or 0) or 1),
                "confirmations_received": max(0, int(row.confirmations_received or 0)),
                "challenge_required": False,
                "next_action": next_action,
                "summary": str(row.summary or ""),
            }
            if include_challenge:
                entry["requires_second_confirmation"] = bool(row.requires_second_confirmation)
                entry["confirmation_challenge"] = str(row.confirmation_challenge or "")
            if include_durable_ids:
                matched_plan = next((p for p in plans if str(p.plan_id) == str(row.plan_id)), None)
                entry["plan_digest"] = str(matched_plan.plan_digest or "") if matched_plan is not None else ""
            proposals.append(entry)

    completed_results: list[dict[str, Any]] = []
    for result in group_results:
        entry: dict[str, Any] = {
            "result_receipt_id": str(result.result_receipt_id),
            "plan_id": str(result.plan_id),
            "group_id": str(result.group_id),
            "terminal_status": str(result.terminal_status or ""),
        }
        if include_durable_ids:
            entry["canonical_result_digest"] = str(result.canonical_result_digest or "")
        completed_results.append(entry)

    manual_cases_query = select(models.ManualReviewCase).where(models.ManualReviewCase.actor_id == actor_id)
    if session_id:
        manual_cases_query = manual_cases_query.where(models.ManualReviewCase.session_id == session_id)
    manual_cases = list((await db.execute(manual_cases_query.order_by(models.ManualReviewCase.created_at.desc(), models.ManualReviewCase.case_id))).scalars().all())
    manual_review_cases = [
        {
            "case_id": str(item.case_id),
            "status": str(item.status or ""),
            "reason_code": str(item.reason_code or ""),
            "plan_id": str(item.plan_id or ""),
            "group_id": str(item.group_id or ""),
            "node_id": str(item.node_id or ""),
            "proposal_id": str(item.proposal_id or ""),
            "subject_type": str(item.subject_type or ""),
            "effect_state": str(item.effect_state or ""),
            "summary": _manual_review_case_summary(item),
            "next_allowed_operations": list(_MANUAL_REVIEW_NEXT_ALLOWED_OPERATIONS),
        }
        for item in manual_cases
    ]

    draft_query = select(models.AgentPlanDraft).where(
        models.AgentPlanDraft.actor_id == actor_id,
        models.AgentPlanDraft.status == "collecting",
    )
    if session_id:
        draft_query = draft_query.where(models.AgentPlanDraft.session_id == session_id)
    staged_drafts = list((await db.execute(draft_query.order_by(models.AgentPlanDraft.created_at, models.AgentPlanDraft.draft_id))).scalars().all())
    staged_intents_by_draft: dict[str, int] = {}
    if staged_drafts:
        intents = list((await db.execute(
            select(models.AgentPlanIntent.intent_id, models.AgentPlanIntent.draft_id, models.AgentPlanIntent.state).where(
                models.AgentPlanIntent.draft_id.in_([str(draft.draft_id) for draft in staged_drafts])
            )
        )).all())
        for _, draft_id, state in intents:
            if str(state or "") == "active":
                staged_intents_by_draft[str(draft_id)] = staged_intents_by_draft.get(str(draft_id), 0) + 1
    staged_drafts_projection = [
        {
            "draft_id": str(draft.draft_id),
            "status": str(draft.status or ""),
            "intent_count": int(staged_intents_by_draft.get(str(draft.draft_id), 0)),
        }
        for draft in staged_drafts
    ]

    plan_projections = [
        {"plan_id": str(plan.plan_id), "status": str(plan.status or "")}
        for plan in plans
    ]

    open_cases = [item for item in manual_review_cases if str(item["status"] or "") == "open"]
    executing = any(str(item["status"]) in _GROUP_EXECUTING_STATUSES for item in group_projections)
    failed_groups = any(str(item["status"]) in _GROUP_FAILED_STATUSES for item in group_projections)
    if open_cases:
        phase = "manual_review"
    elif proposals:
        phase = "awaiting_confirmation"
    elif executing:
        phase = "executing"
    elif completed_results and failed_groups:
        phase = "partially_completed"
    elif completed_results:
        phase = "completed"
    elif failed_groups:
        phase = "failed"
    elif staged_drafts_projection:
        phase = "intent_staged"
    elif plan_projections:
        phase = "sealed"
    else:
        phase = "idle"

    payload = {
        "phase": phase,
        "plans": plan_projections,
        "groups": group_projections,
        "proposals": proposals,
        "completed_results": completed_results,
        "manual_review_cases": manual_review_cases,
        "staged_drafts": staged_drafts_projection,
    }
    return payload


async def expire_plan_group_projection(db: Any, actor: Any, proposal: models.ProposalCache) -> None:
    if not str(proposal.plan_id or ""):
        return
    group = await db.get(models.ConfirmationGroup, proposal.confirmation_group_id)
    plan = await db.get(models.ProposalPlan, proposal.plan_id)
    if group is None or plan is None or plan.actor_id != str(actor.actor_id) or plan.session_id != str(actor.session_id):
        raise PlanMaterializationError("Expired Group projection is outside the immutable Plan scope")
    if group.status in {"pending", "awaiting_more_confirmations"}:
        group.status = "expired"
        nodes = list((await db.execute(select(models.OperationNode).where(models.OperationNode.plan_id == plan.plan_id, models.OperationNode.confirmation_group_id == group.group_id))).scalars().all())
        for node in nodes:
            if node.status in {"pending", "authorized"}:
                node.status = "expired"
        plan.status = "expired"
        sibling_cards = list((await db.execute(select(models.ProposalCache).where(models.ProposalCache.plan_id == plan.plan_id, models.ProposalCache.confirmation_group_id == group.group_id, models.ProposalCache.status.in_(["pending", "awaiting_next_confirmation"])))).scalars().all())
        for sibling in sibling_cards:
            sibling.status = "expired"
        await remove_pending_proposal_ids(db, actor, [sibling.proposal_id for sibling in sibling_cards])
        await db.flush()


async def materialize_plan_proposals(
    db: Any,
    actor: Any,
    plan: models.ProposalPlan,
    *,
    user_message: str = "",
    commit: bool = True,
) -> list[dict[str, Any]]:
    """Materialize one user-visible, digest-bound card per ready ConfirmationGroup.

    Per-node ProposalCache rows are retained only as immutable execution snapshots; they
    are never independently confirmable and are not execution authority.
    """
    if plan.actor_id != str(actor.actor_id) or plan.session_id != str(actor.session_id):
        raise PlanMaterializationError("Plan is outside the actor/session scope")
    nodes = list((await db.execute(select(models.OperationNode).where(models.OperationNode.plan_id == plan.plan_id).order_by(models.OperationNode.sequence))).scalars().all())
    groups = list((await db.execute(select(models.ConfirmationGroup).where(models.ConfirmationGroup.plan_id == plan.plan_id).order_by(models.ConfirmationGroup.sequence))).scalars().all())
    dependencies = list((await db.execute(select(models.NodeDependency).where(models.NodeDependency.plan_id == plan.plan_id))).scalars().all())
    try:
        for group in groups:
            validate_confirmation_group_binding(plan, group)
        for node in nodes:
            validate_operation_node_binding(plan, node)
    except PlanSnapshotIntegrityError as exc:
        raise PlanMaterializationError(str(exc)) from exc
    parents: dict[str, set[str]] = {node.node_id: set() for node in nodes}
    for dependency in dependencies:
        parents.setdefault(dependency.node_id, set()).add(dependency.depends_on_node_id)
    receipts = {receipt.node_id: receipt for receipt in (await db.execute(select(models.NodeExecutionReceipt).where(models.NodeExecutionReceipt.plan_id == plan.plan_id))).scalars().all()}
    intents = {intent.intent_id: intent for intent in (await db.execute(select(models.AgentPlanIntent).where(models.AgentPlanIntent.draft_id == plan.draft_id))).scalars().all()}
    effect_receipts: dict[str, models.NodeExecutionReceipt] = {}
    for node in nodes:
        receipt = receipts.get(node.node_id)
        if receipt is not None:
            for intent_id in list(node.source_intent_ids or []):
                intent = intents.get(str(intent_id))
                if intent is not None:
                    for reference_key in (str(intent.canonical_effect_key), str(intent.intent_id)):
                        if reference_key:
                            effect_receipts[reference_key] = receipt
    caches = list((await db.execute(select(models.ProposalCache).where(models.ProposalCache.plan_id == plan.plan_id))).scalars().all())
    snapshots = {item.node_id: item for item in (await db.execute(select(models.PlanNodeExecutionSnapshot).where(models.PlanNodeExecutionSnapshot.plan_id == plan.plan_id))).scalars().all()}
    proposals: list[dict[str, Any]] = []
    for group in groups:
        group_nodes = [node for node in nodes if node.confirmation_group_id == group.group_id]
        if not group_nodes or group.status not in {"pending", "awaiting_more_confirmations"}:
            continue
        if any(parent not in receipts or receipts[parent].status != "completed" for node in group_nodes for parent in parents.get(node.node_id, set()) if parent not in {member.node_id for member in group_nodes}):
            continue
        existing_card = next((cache for cache in caches if cache.confirmation_group_id == group.group_id and cache.tool_name == "confirm_plan_group" and cache.status in {"pending", "awaiting_next_confirmation"}), None)
        if existing_card is not None:
            existing_snapshots = [snapshot for snapshot in snapshots.values() if snapshot.confirmation_group_id == group.group_id]
            try:
                validate_group_snapshot_binding(plan, group, existing_snapshots)
            except PlanSnapshotIntegrityError as exc:
                raise PlanMaterializationError(str(exc)) from exc
            existing_operations = [dict(snapshot.locked_payload or {}) for snapshot in sorted(existing_snapshots, key=lambda item: item.node_id)]
            proposals.append(_proposal_payload(existing_card, plan, group, group_nodes, existing_operations))
            continue
        internal_ids: list[str] = []
        affected: list[dict[str, Any]] = []
        for node in group_nodes:
            internal = snapshots.get(node.node_id)
            if internal is None:
                node_id_value = str(node.node_id)
                node_tool_name = str(node.tool_name)
                args = _resolve_outputs(dict(node.payload_json or {}), effect_receipts)
                executor = getattr(tools, node_tool_name, None)
                if executor is None:
                    raise PlanMaterializationError(f"Plan node tool {node_tool_name!r} is unavailable")
                if node_tool_name == "patch_record":
                    args.setdefault("patch_mode", "replace")
                call_kwargs = dict(args)
                if node_tool_name == "invoke_action":
                    # Backend-owned inputs frozen into the staged intent (e.g.
                    # generate_resume.confirmed_scope) are re-supplied as the
                    # tool's backend keyword so the sealed evidence boundary is
                    # preserved through proposal snapshot creation.
                    bound_scope = None
                    input_payload = args.get("input")
                    if isinstance(input_payload, Mapping):
                        bound_scope = input_payload.get("confirmed_scope")
                    if bound_scope is not None:
                        call_kwargs["confirmed_scope"] = bound_scope
                result = await executor(session=db, actor=actor, user_message=user_message, _force_proposal=True, _defer_commit=True, **call_kwargs)
                if result.get("ok") is not True or result.get("status") != "proposal_required":
                    raise PlanMaterializationError(f"Plan node {node_id_value} could not create an execution snapshot: {result}")
                node = await db.get(models.OperationNode, node_id_value, populate_existing=True)
                if node is None:
                    raise PlanMaterializationError(f"Plan node {node_id_value} disappeared during snapshot creation")
                internal = await db.get(models.ProposalCache, result["proposal"]["proposal_id"])
                if internal is None:
                    raise PlanMaterializationError("Plan node ProposalCache snapshot was not persisted")
                snapshot_values = {
                    "node_id": str(node.node_id),
                    "plan_id": str(plan.plan_id),
                    "confirmation_group_id": str(group.group_id),
                    "tool_name": str(internal.tool_name or ""),
                    "model_or_action": str(internal.model_or_action or ""),
                    "record_id": str(internal.record_id or ""),
                    "risk_level": int(internal.risk_level or 0),
                    "locked_payload": dict(internal.locked_payload or {}),
                    "affected_records": list(internal.affected_records or []),
                    "before": internal.before,
                    "after": internal.after,
                    "expected_version_or_hash": str(internal.expected_version_or_hash or ""),
                }
                snapshot = models.PlanNodeExecutionSnapshot(**snapshot_values)
                snapshot.snapshot_digest = snapshot_digest(snapshot)
                db.add(snapshot)
                snapshots[node.node_id] = snapshot
                internal_ids.append(internal.proposal_id)
                affected.extend(list(internal.affected_records or []))
                await db.delete(internal)
                internal = snapshot
        if internal_ids:
            await remove_pending_proposal_ids(db, actor, internal_ids)
        internal_snapshots = [snapshot for snapshot in snapshots.values() if snapshot.confirmation_group_id == group.group_id]
        try:
            sealed_group_digest, bound_group_digest = group_snapshot_binding(plan, group, internal_snapshots)
        except PlanSnapshotIntegrityError as exc:
            raise PlanMaterializationError(str(exc)) from exc
        if str(group.group_digest or "") != sealed_group_digest:
            raise PlanMaterializationError("ConfirmationGroup sealed digest changed outside immutable Plan sealing")
        existing_decision = await db.scalar(
            select(models.ConfirmationDecision.event_id).where(models.ConfirmationDecision.group_id == str(group.group_id)).limit(1)
        )
        if existing_decision is not None and str(getattr(group, "authorization_digest", "") or "") != bound_group_digest:
            raise PlanMaterializationError("ConfirmationGroup execution snapshots cannot be rebound after authorization")
        group.authorization_digest = bound_group_digest
        operations = [dict(snapshot.locked_payload or {}) for snapshot in sorted(internal_snapshots, key=lambda item: item.node_id)]
        card = await shape_proposal(
            db,
            actor,
            tool_name="confirm_plan_group",
            operation_type="confirm_group",
            model_or_action="proposal_plan",
            risk_level=max(int(node.risk_level or 0) for node in group_nodes),
            locked_payload={"plan_id": plan.plan_id, "plan_digest": plan.plan_digest, "group_id": group.group_id, "group_digest": str(group.authorization_digest or ""), "node_ids": [node.node_id for node in group_nodes]},
            user_message=user_message,
            affected_records=affected,
            reason="This immutable Plan group requires authorization before any member executes.",
            summary=f"Confirm Plan group containing {len(group_nodes)} operation(s).",
            commit=False,
        )
        card_row = await db.get(models.ProposalCache, card["proposal_id"])
        if card_row is None:
            raise PlanMaterializationError("ConfirmationGroup card was not persisted")
        card_row.plan_id = plan.plan_id
        card_row.confirmation_group_id = group.group_id
        card_row.node_ids = [node.node_id for node in group_nodes]
        card_row.confirmations_required = max(1, int((group.policy_json or {}).get("confirmations_required") or 1))
        card_row.requires_second_confirmation = card_row.confirmations_required > 1
        proposals.append(_proposal_payload(card_row, plan, group, group_nodes, operations))
    if commit:
        await db.commit()
    else:
        await db.flush()
    return proposals


