from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.models import models
from app.operator.audit import log_agent_audit
from app.operator.plan_runtime import materialize_plan_proposals, plan_state_envelope
from app.operator.planning import compile_plan, stage_plan_intent


ALLOWED_RESOLUTIONS = {
    "effect_absent_retry",
    "effect_present_accept",
    "compensation_completed",
    "abort_plan",
}


class ManualReviewError(RuntimeError):
    pass


class ManualReviewNotFound(ManualReviewError):
    pass


class ManualReviewConflict(ManualReviewError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _case_evidence_material(case: models.ManualReviewCase) -> dict[str, Any]:
    return {
        "plan_id": str(case.plan_id or ""),
        "group_id": str(case.group_id or ""),
        "node_id": str(case.node_id or ""),
        "proposal_id": str(case.proposal_id or ""),
        "reason_code": str(case.reason_code or ""),
        "effect_state": str(case.effect_state or ""),
        "evidence": _json_copy(case.evidence_json or {}),
    }


def _effective_case_fence(case: models.ManualReviewCase) -> tuple[int, str]:
    """Return the authoritative (generation, evidence_digest) fence without mutating the row."""
    generation = int(case.case_generation or 0)
    if generation < 1:
        generation = 1
    calculated = _digest(_case_evidence_material(case))
    stored = str(case.evidence_digest or "")
    if stored and stored != calculated:
        raise ManualReviewConflict("manual-review evidence fence is internally inconsistent")
    return generation, calculated


def _ensure_case_fence(case: models.ManualReviewCase) -> None:
    generation, calculated = _effective_case_fence(case)
    if int(case.case_generation or 0) < 1:
        case.case_generation = generation
    if not str(case.evidence_digest or ""):
        case.evidence_digest = calculated


def _public_case(
    case: models.ManualReviewCase,
    *,
    case_generation: int | None = None,
    evidence_digest: str | None = None,
) -> dict[str, Any]:
    resolution_json = dict(case.resolution_json or {})
    return {
        "case_id": str(case.case_id),
        "actor_id": str(case.actor_id or ""),
        "session_id": str(case.session_id or ""),
        "plan_id": str(case.plan_id or ""),
        "group_id": str(case.group_id or ""),
        "node_id": str(case.node_id or ""),
        "proposal_id": str(case.proposal_id or ""),
        "reason_code": str(case.reason_code or ""),
        "subject_type": str(case.subject_type or ""),
        "effect_state": str(case.effect_state or ""),
        "evidence": _json_copy(case.evidence_json or {}),
        "evidence_json": _json_copy(case.evidence_json or {}),
        "case_generation": int(case.case_generation or 0) if case_generation is None else int(case_generation),
        "evidence_digest": str(case.evidence_digest or "") if evidence_digest is None else str(evidence_digest),
        "status": str(case.status or ""),
        "resolution": str(resolution_json.get("resolution") or ""),
        "resolution_json": _json_copy(resolution_json),
        "resolution_result_digest": str(case.resolution_result_digest or ""),
        "resolution_event_digest": str(case.resolution_event_digest or ""),
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
    }


def _scope_predicates(actor: Any) -> tuple[Any, Any]:
    return (
        models.ManualReviewCase.actor_id == str(actor.actor_id),
        models.ManualReviewCase.session_id == str(actor.session_id),
    )


async def list_manual_review_cases(db: Any, actor: Any) -> list[dict[str, Any]]:
    """Read-only listing; legacy fence backfill is computed, never persisted here."""
    rows = list(
        (
            await db.scalars(
                select(models.ManualReviewCase)
                .where(*_scope_predicates(actor))
                .order_by(models.ManualReviewCase.created_at.desc(), models.ManualReviewCase.case_id)
            )
        ).all()
    )
    cases: list[dict[str, Any]] = []
    for case in rows:
        generation, digest = _effective_case_fence(case)
        cases.append(_public_case(case, case_generation=generation, evidence_digest=digest))
    return cases


async def get_manual_review_case(db: Any, actor: Any, case_id: str) -> dict[str, Any]:
    """Read-only detail; legacy fence backfill is computed, never persisted here."""
    case = await db.scalar(
        select(models.ManualReviewCase).where(
            models.ManualReviewCase.case_id == str(case_id),
            *_scope_predicates(actor),
        )
    )
    if case is None:
        raise ManualReviewNotFound("manual-review case was not found in this actor/session scope")
    generation, digest = _effective_case_fence(case)
    return _public_case(case, case_generation=generation, evidence_digest=digest)


async def _create_recovery_plan(
    db: Any,
    actor: Any,
    case: models.ManualReviewCase,
    *,
    resolution: str,
    evidence: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    source_plan = await db.get(models.ProposalPlan, str(case.plan_id or ""))
    if source_plan is None or source_plan.actor_id != str(actor.actor_id) or source_plan.session_id != str(actor.session_id):
        raise ManualReviewConflict("manual-review recovery source Plan is outside the actor/session scope")
    source_node = await db.get(models.OperationNode, str(case.node_id or ""))
    if source_node is None or str(source_node.plan_id) != str(source_plan.plan_id):
        raise ManualReviewConflict("manual-review recovery source Node binding is invalid")

    nodes = list(
        (
            await db.scalars(
                select(models.OperationNode)
                .where(models.OperationNode.plan_id == source_plan.plan_id)
                .order_by(models.OperationNode.sequence)
            )
        ).all()
    )
    by_node = {str(node.node_id): node for node in nodes}
    dependencies = list(
        (await db.scalars(select(models.NodeDependency).where(models.NodeDependency.plan_id == source_plan.plan_id))).all()
    )
    children: dict[str, set[str]] = {node_id: set() for node_id in by_node}
    for dependency in dependencies:
        children.setdefault(str(dependency.depends_on_node_id), set()).add(str(dependency.node_id))
    descendants: set[str] = set()
    frontier = list(children.get(str(source_node.node_id), set()))
    while frontier:
        node_id = frontier.pop()
        if node_id in descendants:
            continue
        descendants.add(node_id)
        frontier.extend(children.get(node_id, set()))

    selected_ids = set(descendants)
    if resolution == "effect_absent_retry":
        selected_ids.add(str(source_node.node_id))
    selected_ids = {
        node_id
        for node_id in selected_ids
        if node_id == str(source_node.node_id) or str(by_node[node_id].status or "") != "completed"
    }
    if not selected_ids:
        return "", []

    atomic_members: dict[str, set[str]] = {}
    for node in nodes:
        atomic_id = str(node.atomic_group_id or "")
        if atomic_id:
            atomic_members.setdefault(atomic_id, set()).add(str(node.node_id))
    # An AtomicGroup replays as the same all-or-nothing boundary it was sealed with:
    # expand the selection with every non-completed member and that member's blocked
    # descendants until the closure is stable.
    expanded = True
    while expanded:
        expanded = False
        for node_id in list(selected_ids):
            atomic_id = str(by_node[node_id].atomic_group_id or "")
            if not atomic_id:
                continue
            for member_id in atomic_members.get(atomic_id, set()):
                if member_id in selected_ids or str(by_node[member_id].status or "") == "completed":
                    continue
                selected_ids.add(member_id)
                expanded = True
                member_frontier = list(children.get(member_id, set()))
                while member_frontier:
                    child_id = member_frontier.pop()
                    if child_id in selected_ids or str(by_node[child_id].status or "") == "completed":
                        continue
                    selected_ids.add(child_id)
                    member_frontier.extend(children.get(child_id, set()))
    for node_id in selected_ids:
        atomic_id = str(by_node[node_id].atomic_group_id or "")
        if atomic_id and not atomic_members[atomic_id] <= selected_ids:
            raise ManualReviewConflict("manual-review recovery cannot split an AtomicGroup")

    replayed_sibling_ids = sorted(selected_ids - {str(source_node.node_id)})
    if replayed_sibling_ids:
        sibling_open_cases = list(
            (
                await db.scalars(
                    select(models.ManualReviewCase).where(
                        models.ManualReviewCase.plan_id == str(source_plan.plan_id),
                        models.ManualReviewCase.status.in_(["open", "resolving"]),
                        models.ManualReviewCase.case_id != str(case.case_id),
                        models.ManualReviewCase.node_id.in_(replayed_sibling_ids),
                    )
                )
            ).all()
        )
        if sibling_open_cases:
            listed = ", ".join(sorted(str(item.case_id) for item in sibling_open_cases))
            raise ManualReviewConflict(
                f"recovery would replay Nodes owned by sibling open manual-review case(s) {listed}; "
                "resolve those sibling cases first (for example abort_plan), then retry from this case"
            )

    all_intents = list(
        (
            await db.scalars(
                select(models.AgentPlanIntent)
                .where(models.AgentPlanIntent.draft_id == source_plan.draft_id)
                .order_by(models.AgentPlanIntent.sequence, models.AgentPlanIntent.intent_id)
            )
        ).all()
    )
    intents_by_id = {str(intent.intent_id): intent for intent in all_intents}
    selected_intent_ids = {
        str(intent_id)
        for node_id in selected_ids
        for intent_id in list(by_node[node_id].source_intent_ids or [])
        if str(intent_id)
    }
    if not selected_intent_ids or not selected_intent_ids <= set(intents_by_id):
        raise ManualReviewConflict("manual-review recovery source Nodes have incomplete durable intent bindings")
    selected_reference_keys: dict[str, str] = {}
    for intent_id in selected_intent_ids:
        selected_intent = intents_by_id[intent_id]
        canonical_key = str(selected_intent.canonical_effect_key)
        selected_reference_keys[canonical_key] = canonical_key
        selected_reference_keys[str(selected_intent.intent_id)] = canonical_key

    replacements: dict[str, dict[str, Any]] = {}
    if resolution == "effect_present_accept":
        from app.operator.plan_execution import _json_type_matches

        accepted_outputs = evidence.get("typed_outputs") if isinstance(evidence.get("typed_outputs"), Mapping) else {}
        declared_specs = dict(source_node.typed_outputs or {})
        for name, value in dict(accepted_outputs).items():
            spec = declared_specs.get(str(name))
            if not isinstance(spec, Mapping):
                raise ManualReviewConflict(f"effect-present evidence provided undeclared typed output {str(name)!r}")
            semantic_type = str(spec.get("semantic_type") or "")
            json_type = str(spec.get("json_type") or "")
            if (semantic_type.startswith("record_id<") or json_type) and not _json_type_matches(
                value, json_type or "object", semantic_type
            ):
                raise ManualReviewConflict(
                    f"effect-present typed output {str(name)!r} violates its declared type contract"
                )
        required_outputs = {
            str(name)
            for name, spec in declared_specs.items()
            if not isinstance(spec, Mapping) or bool(spec.get("referenceable", True))
        }
        if descendants and (not required_outputs or not required_outputs <= set(str(key) for key in accepted_outputs)):
            raise ManualReviewConflict("effect-present recovery requires durable typed_outputs for dependent Nodes")
        for intent_id in list(source_node.source_intent_ids or []):
            source_intent = intents_by_id.get(str(intent_id))
            if source_intent is not None:
                accepted = _json_copy(dict(accepted_outputs))
                replacements[str(source_intent.canonical_effect_key)] = accepted
                replacements[str(source_intent.intent_id)] = accepted

    excluded_completed = [node for node in nodes if str(node.node_id) not in selected_ids and str(node.status or "") == "completed"]
    if excluded_completed:
        receipts = {
            str(receipt.node_id): receipt
            for receipt in (
                await db.scalars(
                    select(models.NodeExecutionReceipt).where(
                        models.NodeExecutionReceipt.node_id.in_([str(node.node_id) for node in excluded_completed])
                    )
                )
            ).all()
        }
        for completed in excluded_completed:
            receipt = receipts.get(str(completed.node_id))
            if receipt is None or str(receipt.status or "") != "completed":
                continue
            for intent_id in list(completed.source_intent_ids or []):
                intent = intents_by_id.get(str(intent_id))
                if intent is not None:
                    completed_outputs = _json_copy(dict(receipt.typed_outputs or {}))
                    replacements[str(intent.canonical_effect_key)] = completed_outputs
                    replacements[str(intent.intent_id)] = completed_outputs

    def resolve_external_outputs(value: Any) -> Any:
        if isinstance(value, Mapping):
            output = value.get("$output")
            if isinstance(output, Mapping):
                effect_key = str(output.get("intent_key") or "")
                if effect_key in selected_reference_keys:
                    canonical_output = _json_copy(dict(output))
                    canonical_output["intent_key"] = selected_reference_keys[effect_key]
                    return {"$output": canonical_output}
                output_name = str(output.get("name") or "")
                if effect_key not in replacements or output_name not in replacements[effect_key]:
                    raise ManualReviewConflict(
                        f"manual-review recovery cannot resolve excluded dependency output {effect_key}.{output_name}"
                    )
                return _json_copy(replacements[effect_key][output_name])
            return {str(key): resolve_external_outputs(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve_external_outputs(child) for child in value]
        return _json_copy(value)

    turn_key = f"manual-review-recovery:{case.case_id}:{int(case.case_generation or 0)}:{resolution}"
    staged = []
    for source in all_intents:
        if str(source.intent_id) not in selected_intent_ids:
            continue
        staged.append(
            await stage_plan_intent(
                db,
                actor,
                turn_key=turn_key,
                canonical_effect_key=str(source.canonical_effect_key),
                tool_name=str(source.tool_name),
                args=resolve_external_outputs(dict(source.args_json or {})),
                base_version=str(source.base_version or ""),
                atomic_group_id=str(source.atomic_group_id or ""),
                commit=False,
            )
        )
    recovery_plan = await compile_plan(db, actor, staged[0].draft_id, commit=False)
    action = "retry" if resolution == "effect_absent_retry" else "continue from the accepted effect"
    proposals = await materialize_plan_proposals(
        db,
        actor,
        recovery_plan,
        user_message=f"Manual review {case.case_id} resolved the prior effect; authorize a fresh Plan to {action}.",
        commit=False,
    )
    return str(recovery_plan.plan_id), proposals


def _validate_resolution_evidence(resolution: str, evidence: Mapping[str, Any]) -> None:
    if resolution == "compensation_completed":
        durable_markers = {
            "compensation_receipt_id",
            "compensation_result_digest",
            "effect_manifest_digest",
            "provider_reference",
        }
        if not any(str(evidence.get(key) or "").strip() for key in durable_markers):
            raise ManualReviewConflict("compensation evidence requires a durable receipt, digest, manifest, or provider reference")
        return
    if resolution == "effect_present_accept":
        if not any(str(evidence.get(key) or "").strip() for key in ("effect_manifest_digest", "provider_reference")):
            raise ManualReviewConflict(
                "effect-present acceptance requires durable evidence: an effect_manifest_digest or provider_reference"
            )


async def resolve_manual_review_case(
    db: Any,
    actor: Any,
    case_id: str,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    resolution = str(request.get("resolution") or "").strip()
    if resolution not in ALLOWED_RESOLUTIONS:
        raise ManualReviewConflict("unsupported manual-review resolution")
    idempotency_key = str(request.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise ManualReviewConflict("manual-review idempotency_key is required")
    if len(idempotency_key) > 160:
        # Durable resolution/audit columns are VARCHAR(160); PostgreSQL enforces that
        # while SQLite does not. Normalize deterministically so oversize client keys
        # keep exact replay semantics instead of failing only in production.
        idempotency_key = f"manual-review-key:v1:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
    try:
        requested_generation = int(request.get("case_generation"))
    except (TypeError, ValueError):
        raise ManualReviewConflict("manual-review case generation fence is required") from None
    requested_evidence_digest = str(request.get("evidence_digest") or "").strip()
    evidence = request.get("evidence") if isinstance(request.get("evidence"), Mapping) else {}
    evidence = _json_copy(dict(evidence))

    request_material = {
        "case_id": str(case_id),
        "actor_id": str(actor.actor_id),
        "session_id": str(actor.session_id),
        "resolution": resolution,
        "case_generation": requested_generation,
        "evidence_digest": requested_evidence_digest,
        "idempotency_key": idempotency_key,
        "evidence": evidence,
    }
    request_digest = _digest(request_material)

    replay = await db.scalar(
        select(models.ManualReviewResolution).where(
            models.ManualReviewResolution.case_id == str(case_id),
            models.ManualReviewResolution.actor_id == str(actor.actor_id),
            models.ManualReviewResolution.session_id == str(actor.session_id),
            models.ManualReviewResolution.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if str(replay.request_digest or "") != request_digest:
            raise ManualReviewConflict("manual-review idempotency key was reused with a different request")
        return _json_copy(replay.result_json or {})

    # Evidence gates apply to new decisions only; an exact durable replay above must
    # keep returning its committed result even if gate requirements tighten later.
    _validate_resolution_evidence(resolution, evidence)

    case = await db.scalar(
        select(models.ManualReviewCase)
        .where(models.ManualReviewCase.case_id == str(case_id), *_scope_predicates(actor))
        .with_for_update()
    )
    if case is None:
        raise ManualReviewNotFound("manual-review case was not found in this actor/session scope")
    _ensure_case_fence(case)
    if str(case.status or "") != "open":
        raise ManualReviewConflict("manual-review case is already resolved")
    if requested_generation != int(case.case_generation or 0) or requested_evidence_digest != str(case.evidence_digest or ""):
        raise ManualReviewConflict("manual-review evidence fence does not match the current case generation/digest")
    if str(case.subject_type or "") == "saga_compensation" and resolution not in {"compensation_completed", "abort_plan"}:
        raise ManualReviewConflict("Saga compensation cases only support compensation_completed or abort_plan")
    if resolution in {"effect_absent_retry", "effect_present_accept"} and not str(case.node_id or ""):
        raise ManualReviewConflict(
            "manual-review case has no bound OperationNode; only compensation_completed or abort_plan are supported"
        )

    claimed = await db.execute(
        update(models.ManualReviewCase)
        .where(
            models.ManualReviewCase.case_id == str(case_id),
            *_scope_predicates(actor),
            models.ManualReviewCase.status == "open",
            models.ManualReviewCase.case_generation == requested_generation,
            models.ManualReviewCase.evidence_digest == requested_evidence_digest,
        )
        .values(status="resolving")
        .execution_options(synchronize_session=False)
    )
    if int(claimed.rowcount or 0) != 1:
        raise ManualReviewConflict("manual-review case changed concurrently")
    case.status = "resolving"

    plan = await db.get(models.ProposalPlan, str(case.plan_id or ""))
    group = await db.get(models.ConfirmationGroup, str(case.group_id or "")) if str(case.group_id or "") else None
    node = await db.get(models.OperationNode, str(case.node_id or "")) if str(case.node_id or "") else None
    if plan is None or plan.actor_id != str(actor.actor_id) or plan.session_id != str(actor.session_id):
        raise ManualReviewConflict("manual-review Plan binding is invalid")
    if group is not None and str(group.plan_id) != str(plan.plan_id):
        raise ManualReviewConflict("manual-review Group binding is invalid")
    if node is not None and (str(node.plan_id) != str(plan.plan_id) or (group is not None and str(node.confirmation_group_id) != str(group.group_id))):
        raise ManualReviewConflict("manual-review Node binding is invalid")

    retry_plan_id = ""
    recovery_plan_id = ""
    next_proposals: list[dict[str, Any]] = []
    if resolution in {"effect_absent_retry", "effect_present_accept"}:
        from app.operator.plan_runtime import PlanMaterializationError
        from app.operator.planning import PlanCompilationError, PlanStateError

        try:
            recovery_plan_id, next_proposals = await _create_recovery_plan(
                db, actor, case, resolution=resolution, evidence=evidence
            )
        except ManualReviewConflict:
            raise
        except (PlanMaterializationError, PlanCompilationError, PlanStateError) as exc:
            raise ManualReviewConflict(f"manual-review recovery could not be materialized: {exc}") from exc
        if resolution == "effect_absent_retry":
            retry_plan_id = recovery_plan_id
    # The source Plan/Group/Node/Outcome/ResultReceipt remain immutable manual-review
    # facts. The durable ManualReviewResolution is the authoritative recovery overlay;
    # rewriting those terminal facts would make replay and integrity preflight diverge.

    case.status = "resolved"
    case.case_generation = requested_generation + 1
    case.resolved_at = _now()
    event_id = f"manual-review-event:{uuid.uuid4().hex}"
    resolution_id = f"manual-review-resolution:{uuid.uuid4().hex}"
    sequence = int(
        await db.scalar(
            select(func.coalesce(func.max(models.ManualReviewResolution.sequence), 0)).where(
                models.ManualReviewResolution.case_id == str(case.case_id)
            )
        )
        or 0
    ) + 1

    if resolution in {"effect_absent_retry", "effect_present_accept"} and recovery_plan_id:
        effective_status = "recovery_pending_authorization"
    elif resolution == "effect_present_accept":
        effective_status = "accepted_effect"
    elif resolution == "compensation_completed":
        effective_status = "compensated"
    else:
        effective_status = "aborted"
    recovery = {
        "resolution": resolution,
        "effective_status": effective_status,
        "plan_id": recovery_plan_id,
        "source_plan_id": str(plan.plan_id),
        "source_node_id": str(case.node_id or ""),
    }
    plan_event = await plan_state_envelope(db, str(plan.plan_id), new_proposals=next_proposals)
    plan_event["effective_status"] = effective_status
    plan_event["recovery"] = recovery
    case.resolution_json = {
        "resolution": resolution,
        "resolution_id": resolution_id,
        "idempotency_key": idempotency_key,
        "retry_plan_id": retry_plan_id,
        "recovery_plan_id": recovery_plan_id,
        "recovery": recovery,
        "evidence": evidence,
        "resolved_generation": requested_generation,
    }
    public_case = _public_case(case)
    result_material = {
        "schema_version": 1,
        "resolution_id": resolution_id,
        "case_id": str(case.case_id),
        "actor_id": str(actor.actor_id),
        "session_id": str(actor.session_id),
        "plan_id": str(case.plan_id or ""),
        "group_id": str(case.group_id or ""),
        "node_id": str(case.node_id or ""),
        "proposal_id": str(case.proposal_id or ""),
        "resolution": resolution,
        "source_case_generation": requested_generation,
        "resolved_case_generation": int(case.case_generation),
        "evidence_digest": requested_evidence_digest,
        "request_digest": request_digest,
        "plan_event": plan_event,
        "retry_plan_id": retry_plan_id,
        "recovery_plan_id": recovery_plan_id,
        "recovery": recovery,
        "next_proposals": next_proposals,
    }
    result_digest = _digest(result_material)
    event_material = {
        "schema_version": 1,
        "event_id": event_id,
        "resolution_id": resolution_id,
        "case_id": str(case.case_id),
        "actor_id": str(actor.actor_id),
        "session_id": str(actor.session_id),
        "resolution": resolution,
        "result_digest": result_digest,
        "plan_event": plan_event,
    }
    event_digest = _digest(event_material)
    core_result = {
        "ok": True,
        "case_id": str(case.case_id),
        "status": "resolved",
        "resolution": resolution,
        "case": public_case,
        "manual_review_case": public_case,
        "plan_event": plan_event,
        "retry_plan_id": retry_plan_id,
        "recovery_plan_id": recovery_plan_id,
        "recovery": recovery,
        "next_proposals": next_proposals,
        "result_material": result_material,
        "event_material": event_material,
    }
    audit = await log_agent_audit(
        db,
        actor=actor,
        proposal_id=str(case.proposal_id or ""),
        tool_name="resolve_manual_review_case",
        args_snapshot=request_material,
        confirmation_status="manual_review_resolved",
        result_status=str(plan.status or ""),
        result_summary=f"Manual review {case.case_id} resolved as {resolution}",
        confirmation_event_id=event_id,
        idempotency_key=idempotency_key,
        before_version_or_hash=f"{requested_generation}:{requested_evidence_digest}",
        after_version_or_hash=f"{int(case.case_generation)}:{requested_evidence_digest}",
        result_digest=result_digest,
    )
    result = {**core_result, "result_digest": result_digest, "event_id": event_id, "event_digest": event_digest, "audit_id": audit.audit_id}
    case.resolution_result_digest = result_digest
    case.resolution_event_digest = event_digest
    case.resolution_json = {**dict(case.resolution_json or {}), "result_digest": result_digest, "event_id": event_id, "event_digest": event_digest, "audit_id": audit.audit_id}
    result["case"] = _public_case(case)
    result["manual_review_case"] = result["case"]

    row = models.ManualReviewResolution(
        resolution_id=resolution_id,
        case_id=str(case.case_id),
        sequence=sequence,
        actor_id=str(actor.actor_id),
        session_id=str(actor.session_id),
        resolution=resolution,
        case_generation=requested_generation,
        evidence_digest=requested_evidence_digest,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        evidence_json=evidence,
        result_json=_json_copy(result),
        result_digest=result_digest,
        event_id=event_id,
        event_digest=event_digest,
        audit_id=str(audit.audit_id),
        retry_plan_id=retry_plan_id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        replay = await db.scalar(
            select(models.ManualReviewResolution).where(
                models.ManualReviewResolution.case_id == str(case_id),
                models.ManualReviewResolution.actor_id == str(actor.actor_id),
                models.ManualReviewResolution.session_id == str(actor.session_id),
                models.ManualReviewResolution.idempotency_key == idempotency_key,
            )
        )
        if replay is not None and str(replay.request_digest or "") == request_digest:
            return _json_copy(replay.result_json or {})
        raise ManualReviewConflict("manual-review resolution conflicted with a concurrent decision") from exc
    return _json_copy(result)
