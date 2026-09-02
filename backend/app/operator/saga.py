from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.exc import IntegrityError

from app.models import models
from app.operator.registry import COMPENSATION_REGISTRY


class CompensationExecutionError(RuntimeError):
    def __init__(self, classification: str, message: str):
        super().__init__(message)
        self.classification = classification if classification in {"transient", "permanent", "unknown"} else "unknown"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _compensation_idempotency_key(plan_id: str, node_id: str, operation: str) -> str:
    digest = hashlib.sha256(f"{plan_id}:{node_id}:{operation}".encode("utf-8")).hexdigest()
    return f"plan-compensation:{digest}"


async def _reverse_topological_nodes(db: Any, plan_id: str) -> list[models.OperationNode]:
    nodes = list((await db.execute(select(models.OperationNode).where(models.OperationNode.plan_id == plan_id))).scalars().all())
    deps = list((await db.execute(select(models.NodeDependency).where(models.NodeDependency.plan_id == plan_id))).scalars().all())
    by_id = {node.node_id: node for node in nodes}
    parents = {node.node_id: set() for node in nodes}
    children = {node.node_id: set() for node in nodes}
    for dep in deps:
        parents.setdefault(dep.node_id, set()).add(dep.depends_on_node_id)
        children.setdefault(dep.depends_on_node_id, set()).add(dep.node_id)
    ready = sorted(
        [node_id for node_id, values in children.items() if not values],
        key=lambda value: by_id[value].sequence,
        reverse=True,
    )
    ordered: list[models.OperationNode] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(by_id[node_id])
        for parent in sorted(parents.get(node_id, set()), key=lambda value: by_id[value].sequence, reverse=True):
            children[parent].discard(node_id)
            if not children[parent] and parent not in [item.node_id for item in ordered] and parent not in ready:
                ready.append(parent)
    if len(ordered) != len(nodes):
        raise CompensationExecutionError("unknown", "compensation graph contains a cycle")
    return ordered


async def _get_or_create_saga(db: Any, plan: models.ProposalPlan) -> models.SagaGroup:
    plan_id = str(plan.plan_id)
    actor_id = str(plan.actor_id)
    session_id = str(plan.session_id)
    saga = await db.get(models.SagaGroup, plan_id, populate_existing=True)
    if saga is not None:
        return saga
    db.add(models.SagaGroup(plan_id=plan_id, actor_id=actor_id, session_id=session_id, status="running"))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
    saga = await db.get(models.SagaGroup, plan_id, populate_existing=True)
    if saga is None:
        raise CompensationExecutionError("unknown", "durable SagaGroup could not be created")
    return saga


async def _claim_compensation(
    db: Any,
    plan: models.ProposalPlan,
    node: models.OperationNode,
    operation: str,
    *,
    owner_token: str,
    lease_seconds: int,
) -> models.SagaCompensationReceipt | None:
    plan_id = str(plan.plan_id)
    actor_id = str(plan.actor_id)
    session_id = str(plan.session_id)
    node_id = str(node.node_id)
    now = _now()
    expires = now + timedelta(seconds=max(5, int(lease_seconds)))
    receipt = await db.get(models.SagaCompensationReceipt, node_id, populate_existing=True)
    if receipt is None:
        receipt = models.SagaCompensationReceipt(
            node_id=node_id, plan_id=plan_id, actor_id=actor_id, session_id=session_id,
            operation=operation, status="running", attempt_count=0, claim_token=owner_token, claim_generation=1,
            lease_expires_at=expires, idempotency_key=_compensation_idempotency_key(plan_id, node_id, operation),
        )
        db.add(receipt)
        try:
            await db.commit()
            return await db.get(models.SagaCompensationReceipt, node_id, populate_existing=True)
        except IntegrityError:
            await db.rollback()
            receipt = await db.get(models.SagaCompensationReceipt, node_id, populate_existing=True)
    if receipt is None or receipt.plan_id != plan_id or receipt.actor_id != actor_id or receipt.session_id != session_id:
        raise CompensationExecutionError("unknown", "compensation receipt identity mismatch")
    if receipt.operation != operation:
        raise CompensationExecutionError("unknown", "compensation operation changed after claim")
    if receipt.status in {"completed", "manual_review"}:
        return None
    if receipt.status == "running" and receipt.lease_expires_at and receipt.lease_expires_at > now:
        return receipt if receipt.claim_token == owner_token else None
    old_token = str(receipt.claim_token or "")
    old_generation = int(receipt.claim_generation or 0)
    changed = await db.execute(
        update(models.SagaCompensationReceipt)
        .where(
            models.SagaCompensationReceipt.node_id == node_id,
            models.SagaCompensationReceipt.status == "running",
            models.SagaCompensationReceipt.claim_token == old_token,
            models.SagaCompensationReceipt.claim_generation == old_generation,
            models.SagaCompensationReceipt.lease_expires_at <= now,
        )
        .values(claim_token=owner_token, claim_generation=old_generation + 1, lease_expires_at=expires)
        .execution_options(synchronize_session=False)
    )
    if int(changed.rowcount or 0) != 1:
        await db.rollback()
        return None
    await db.commit()
    return await db.get(models.SagaCompensationReceipt, node_id, populate_existing=True)


async def renew_compensation_claim(
    db: Any, node_id: str, token: str, generation: int, *, lease_seconds: int = 90
) -> bool:
    now = _now()
    changed = await db.execute(
        update(models.SagaCompensationReceipt)
        .where(
            models.SagaCompensationReceipt.node_id == str(node_id),
            models.SagaCompensationReceipt.status == "running",
            models.SagaCompensationReceipt.claim_token == str(token),
            models.SagaCompensationReceipt.claim_generation == int(generation),
            models.SagaCompensationReceipt.lease_expires_at > now,
        )
        .values(lease_expires_at=now + timedelta(seconds=max(1, int(lease_seconds))))
        .execution_options(synchronize_session=False)
    )
    if int(changed.rowcount or 0) != 1:
        await db.rollback()
        return False
    await db.commit()
    return True


async def _fenced_compensation(db: Any, node_id: str, token: str, generation: int) -> models.SagaCompensationReceipt:
    receipt = await db.get(models.SagaCompensationReceipt, node_id, populate_existing=True)
    if (
        receipt is None or receipt.status != "running" or receipt.claim_token != token
        or int(receipt.claim_generation or 0) != int(generation)
        or receipt.lease_expires_at is None or receipt.lease_expires_at <= _now()
    ):
        raise CompensationExecutionError("unknown", "compensation execution claim lease was lost before receipt publication")
    return receipt


async def _ensure_compensation_manual_review_case(
    db: Any,
    plan: models.ProposalPlan,
    node: models.OperationNode,
    *,
    operation: str,
    classification: str,
    message: str,
) -> None:
    from app.operator.plan_execution import _ensure_manual_review_case

    receipt = await db.get(models.SagaCompensationReceipt, str(node.node_id), populate_existing=True)
    await _ensure_manual_review_case(
        db,
        plan=plan,
        node=node,
        reason_code="saga_compensation_requires_review",
        effect_state="compensation_unknown",
        subject_type="saga_compensation",
        evidence={
            "compensation_receipt_id": str(node.node_id),
            "operation": str(operation),
            "classification": str(classification),
            "message": str(message),
            "claim_generation": int(getattr(receipt, "claim_generation", 0) or 0),
            "receipt_status": str(getattr(receipt, "status", "") or ""),
        },
    )


async def _mark_manual_review(
    db: Any,
    plan: models.ProposalPlan,
    node: models.OperationNode,
    *,
    owner_token: str,
    operation: str,
    classification: str,
    message: str,
) -> models.SagaCompensationReceipt:
    node_id = str(node.node_id)
    receipt = await db.get(models.SagaCompensationReceipt, node_id, populate_existing=True)
    if receipt is None:
        receipt = models.SagaCompensationReceipt(
            node_id=node_id,
            plan_id=str(plan.plan_id),
            actor_id=str(plan.actor_id),
            session_id=str(plan.session_id),
            operation=str(operation),
            status="manual_review",
            attempt_count=0,
            claim_token=owner_token,
            claim_generation=1,
            idempotency_key=_compensation_idempotency_key(str(plan.plan_id), node_id, str(operation)),
        )
        db.add(receipt)
    elif (
        str(receipt.plan_id) != str(plan.plan_id)
        or str(receipt.actor_id) != str(plan.actor_id)
        or str(receipt.session_id) != str(plan.session_id)
    ):
        raise CompensationExecutionError("unknown", "compensation receipt identity mismatch")
    receipt.status = "manual_review"
    receipt.error_classification = str(classification)
    receipt.error_message = str(message)
    receipt.completed_at = _now()
    receipt.lease_expires_at = None
    await _ensure_compensation_manual_review_case(
        db, plan, node, operation=operation, classification=classification, message=message
    )
    await db.commit()
    return receipt


def _validate_outcome(
    plan: models.ProposalPlan,
    node: models.OperationNode,
    outcome: models.NodeExecutionOutcome | None,
) -> tuple[models.NodeExecutionOutcome, dict[str, Any]]:
    from app.operator.effect_manifest import EffectManifestError, validate_effect_manifest, validate_node_contract
    from app.operator.plan_execution import _digest, _node_digest

    if outcome is None:
        raise CompensationExecutionError("permanent", "immutable NodeExecutionOutcome is missing")
    if (
        str(outcome.node_id) != str(node.node_id)
        or str(outcome.plan_id) != str(plan.plan_id)
        or str(outcome.group_id) != str(node.confirmation_group_id)
        or str(outcome.actor_id) != str(plan.actor_id)
        or str(outcome.session_id) != str(plan.session_id)
    ):
        raise CompensationExecutionError("permanent", "immutable NodeExecutionOutcome identity mismatch")
    if int(outcome.receipt_schema_version or 0) != 1:
        raise CompensationExecutionError("permanent", "immutable NodeExecutionOutcome schema version is unsupported")
    if str(outcome.status or "") != "completed" or str(node.status or "") != "completed":
        raise CompensationExecutionError("permanent", "immutable NodeExecutionOutcome is not a completed effect fact")
    if int(outcome.attempt_count or 0) < 1 or not str(outcome.completion_reason or "").strip():
        raise CompensationExecutionError("permanent", "immutable NodeExecutionOutcome terminal metadata is incomplete")
    try:
        contract = validate_node_contract(node)
        raw_manifest = outcome.effect_manifest_json if isinstance(outcome.effect_manifest_json, Mapping) else {}
        manifest = validate_effect_manifest(
            node,
            raw_manifest,
            expected_resolved_input_digest=str(outcome.resolved_input_digest or ""),
        )
        node_digest = _node_digest(node)
    except EffectManifestError as exc:
        raise CompensationExecutionError("permanent", str(exc)) from exc
    except Exception as exc:
        raise CompensationExecutionError("permanent", str(exc)) from exc
    public_result = outcome.public_result_json if isinstance(outcome.public_result_json, Mapping) else {}
    typed_outputs = outcome.typed_outputs if isinstance(outcome.typed_outputs, Mapping) else {}
    public_typed_outputs = public_result.get("typed_outputs") if isinstance(public_result.get("typed_outputs"), Mapping) else {}
    bindings = manifest.get("bindings") if isinstance(manifest.get("bindings"), Mapping) else {}
    if (
        str(outcome.node_digest or "") != str(node_digest)
        or str(outcome.execution_contract_digest or "") != str(contract.get("digest") or "")
        or str(outcome.resolved_input_digest or "") != str(bindings.get("resolved_input_digest") or "")
        or str(outcome.effect_state or "") != str(manifest.get("effect_state") or "")
        or str(outcome.effect_manifest_digest or "") != str(manifest.get("digest") or "")
        or dict(raw_manifest) != dict(manifest)
    ):
        raise CompensationExecutionError("permanent", "immutable NodeExecutionOutcome manifest binding is invalid")
    if (
        str(outcome.public_result_digest or "") != _digest(dict(public_result))
        or str(outcome.typed_outputs_digest or "") != _digest(dict(typed_outputs))
        or dict(typed_outputs) != dict(public_typed_outputs)
    ):
        raise CompensationExecutionError("permanent", "immutable NodeExecutionOutcome digest is invalid")
    effect_state = str(manifest.get("effect_state") or "")
    if effect_state != "committed":
        return outcome, manifest
    payload = dict(node.payload_json or {})
    expected_operation = {"create_record": "create", "patch_record": "patch"}.get(str(node.tool_name or ""))
    if expected_operation is not None:
        model_name = str(node.target_name or payload.get("model") or "")
        effects = [
            effect
            for effect in manifest.get("effects") or []
            if isinstance(effect, Mapping)
            and str(effect.get("kind") or "") == "database_record"
            and str(effect.get("operation") or "") == expected_operation
            and str(effect.get("model") or "") == model_name
        ]
        if len(effects) != 1:
            raise CompensationExecutionError(
                "permanent", "immutable outcome identity is not bound to exactly one database effect"
            )
        effect_record_id = str(effects[0].get("record_id") or "")
        if public_result.get("model") not in (None, "", model_name):
            raise CompensationExecutionError("permanent", "immutable outcome model identity conflicts with its manifest")
        for source in (public_result, typed_outputs):
            for key in ("primary_record_id", "record_id"):
                value = source.get(key)
                if value not in (None, "") and str(value) != effect_record_id:
                    raise CompensationExecutionError(
                        "permanent", "immutable outcome record identity conflicts with its manifest"
                    )
        sealed_record_id = payload.get("record_id")
        symbolic_record_id = (
            sealed_record_id.get("$output")
            if isinstance(sealed_record_id, Mapping)
            and isinstance(sealed_record_id.get("$output"), Mapping)
            else None
        )
        if expected_operation == "patch" and sealed_record_id not in (None, "") and symbolic_record_id is None:
            if str(sealed_record_id) != effect_record_id:
                raise CompensationExecutionError(
                    "permanent", "patch identity conflicts with immutable manifest evidence"
                )
    return outcome, manifest

async def compensate_plan(db: Any, actor: Any, plan_id: str, handler: Any, *, lease_seconds: int = 90) -> dict[str, Any]:
    plan = (
        await db.execute(
            select(models.ProposalPlan).where(
                models.ProposalPlan.plan_id == str(plan_id),
                models.ProposalPlan.actor_id == str(actor.actor_id),
                models.ProposalPlan.session_id == str(actor.session_id),
            )
        )
    ).scalar_one_or_none()
    if not plan:
        raise CompensationExecutionError("permanent", "Plan outside actor/session scope")
    plan_id_value = str(plan.plan_id)
    plan_actor_id = str(plan.actor_id)
    plan_session_id = str(plan.session_id)
    saga = await _get_or_create_saga(db, plan)
    plan = await db.get(models.ProposalPlan, plan_id_value, populate_existing=True)
    if plan is None or str(plan.actor_id) != plan_actor_id or str(plan.session_id) != plan_session_id:
        raise CompensationExecutionError("unknown", "Plan identity changed while creating SagaGroup")
    if saga.status == "compensated":
        return {"ok": True, "plan_id": plan_id_value, "status": "compensated"}

    nodes = await _reverse_topological_nodes(db, plan_id_value)
    outcomes = {
        str(outcome.node_id): outcome
        for outcome in (
            await db.execute(
                select(models.NodeExecutionOutcome).where(models.NodeExecutionOutcome.plan_id == plan_id_value)
            )
        ).scalars().all()
    }
    owner_token = f"compensator-{uuid.uuid4().hex}"
    manual = False
    in_progress = False
    validated: list[tuple[models.OperationNode, models.NodeExecutionOutcome, dict[str, Any], Any]] = []
    for node in nodes:
        node_id = str(node.node_id)
        if str(node.status or "") != "completed":
            # Non-completed terminal nodes carry durable outcomes too. Only nodes whose
            # manifests prove the absence of an applied effect are skippable; unknown or
            # partial external effects still require a human compensation decision.
            outcome_row = outcomes.get(node_id)
            if outcome_row is None:
                continue
            skip_state = str(outcome_row.effect_state or "")
            if skip_state in {"no_effect", "rolled_back"}:
                continue
            await _mark_manual_review(
                db, plan, node, owner_token=owner_token, operation="unproven_effect",
                classification="permanent",
                message=f"{skip_state or 'unknown'} effects on a non-completed node are not eligible for automatic compensation",
            )
            manual = True
            break
        try:
            outcome, manifest = _validate_outcome(plan, node, outcomes.get(node_id))
        except CompensationExecutionError as exc:
            await _mark_manual_review(
                db, plan, node, owner_token=owner_token, operation="outcome_validation",
                classification=exc.classification, message=str(exc),
            )
            manual = True
            break
        effect_state = str(manifest.get("effect_state") or "")
        if effect_state in {"no_effect", "rolled_back"}:
            continue
        if effect_state in {"unknown_external", "legacy_unproven"}:
            await _mark_manual_review(
                db, plan, node, owner_token=owner_token, operation="unproven_effect",
                classification="permanent",
                message=f"{effect_state} effects are not eligible for automatic compensation",
            )
            manual = True
            break
        if effect_state != "committed":
            await _mark_manual_review(
                db, plan, node, owner_token=owner_token, operation="unproven_effect",
                classification="permanent", message=f"unsupported compensation effect state: {effect_state}",
            )
            manual = True
            break
        policy = COMPENSATION_REGISTRY.get(str(node.compensation_policy or ""))
        if not policy or not policy.automatic:
            await _mark_manual_review(
                db, plan, node, owner_token=owner_token, operation="unregistered",
                classification="permanent", message="No registered automatic compensation",
            )
            manual = True
            break
        validated.append((node, outcome, manifest, policy))

    if manual:
        saga = await db.get(models.SagaGroup, plan.plan_id, populate_existing=True)
        plan = await db.get(models.ProposalPlan, plan.plan_id, populate_existing=True)
        saga.status = "manual_review"
        saga.completed_at = _now()
        plan.status = "manual_review"
        await db.commit()
        return {"ok": False, "plan_id": plan.plan_id, "status": "manual_review"}

    for node, outcome, manifest, policy in validated:
        node_id = str(node.node_id)
        receipt = await _claim_compensation(
            db, plan, node, policy.operation, owner_token=owner_token, lease_seconds=lease_seconds
        )
        if receipt is None:
            current = await db.get(models.SagaCompensationReceipt, node_id, populate_existing=True)
            if current is not None and current.status == "completed":
                continue
            if current is not None and current.status == "manual_review":
                manual = True
                break
            in_progress = True
            break
        token = str(receipt.claim_token)
        generation = int(receipt.claim_generation or 0)
        while int(receipt.attempt_count or 0) < int(policy.max_attempts):
            receipt.attempt_count = int(receipt.attempt_count or 0) + 1
            await db.commit()
            try:
                from app.operator.plan_execution import _run_lease_heartbeat

                stop_heartbeat = asyncio.Event()
                lost_heartbeat = asyncio.Event()

                async def renew_claim(heartbeat_db: Any) -> bool:
                    return await renew_compensation_claim(
                        heartbeat_db, node_id, token, generation, lease_seconds=lease_seconds
                    )

                heartbeat_task = asyncio.create_task(
                    _run_lease_heartbeat(
                        db.bind, renew_claim, stop_heartbeat, lost_heartbeat,
                        interval=max(0.25, float(lease_seconds) / 3),
                        lease_seconds=lease_seconds,
                        initial_lease_expires_at=receipt.lease_expires_at,
                    ),
                    name=f"saga-compensation-heartbeat:{node_id}",
                )
                try:
                    raw = dict(await handler(node, policy, outcome))
                finally:
                    stop_heartbeat.set()
                    await heartbeat_task
                if lost_heartbeat.is_set():
                    raise CompensationExecutionError("unknown", "compensation execution lease was lost before publication")
                if str(raw.get("status") or "") not in {"completed", "success"} or (
                    policy.requires_version_fence and raw.get("fence_verified") is not True
                ):
                    raise CompensationExecutionError("unknown", "compensation did not prove its required fence")
                receipt = await _fenced_compensation(db, node_id, token, generation)
                receipt.status = "completed"
                receipt.fence_verified = True
                receipt.result_json = raw
                receipt.error_classification = ""
                receipt.error_message = ""
                receipt.completed_at = _now()
                receipt.lease_expires_at = None
                await db.commit()
                break
            except CompensationExecutionError as exc:
                await db.rollback()
                receipt = await _fenced_compensation(db, node_id, token, generation)
                if exc.classification == "transient" and int(receipt.attempt_count or 0) < int(policy.max_attempts):
                    continue
                receipt.status = "manual_review"
                receipt.error_classification = exc.classification
                receipt.error_message = str(exc)
                receipt.completed_at = _now()
                receipt.lease_expires_at = None
                review_plan = await db.get(models.ProposalPlan, plan_id_value, populate_existing=True)
                review_node = await db.get(models.OperationNode, node_id, populate_existing=True)
                if review_plan is None or review_node is None:
                    raise CompensationExecutionError("unknown", "compensation manual-review identity disappeared")
                await _ensure_compensation_manual_review_case(
                    db, review_plan, review_node, operation=policy.operation, classification=exc.classification, message=str(exc)
                )
                manual = True
                await db.commit()
                break
            except Exception as exc:
                await db.rollback()
                receipt = await _fenced_compensation(db, node_id, token, generation)
                receipt.status = "manual_review"
                receipt.error_classification = "unknown"
                receipt.error_message = str(exc)
                receipt.completed_at = _now()
                receipt.lease_expires_at = None
                review_plan = await db.get(models.ProposalPlan, plan_id_value, populate_existing=True)
                review_node = await db.get(models.OperationNode, node_id, populate_existing=True)
                if review_plan is None or review_node is None:
                    raise CompensationExecutionError("unknown", "compensation manual-review identity disappeared")
                await _ensure_compensation_manual_review_case(
                    db, review_plan, review_node, operation=policy.operation, classification="unknown", message=str(exc)
                )
                manual = True
                await db.commit()
                break
        if manual or in_progress:
            break

    if in_progress:
        return {"ok": True, "plan_id": plan_id_value, "status": "compensation_in_progress"}
    saga = await db.get(models.SagaGroup, plan_id_value, populate_existing=True)
    plan = await db.get(models.ProposalPlan, plan_id_value, populate_existing=True)
    saga.status = "manual_review" if manual else "compensated"
    saga.completed_at = _now()
    plan.status = saga.status
    await db.commit()
    return {"ok": not manual, "plan_id": plan.plan_id, "status": saga.status}


def _compensation_record_cas_predicates(
    record: Any, spec: Any, actor: Any, *, expected_fields: tuple[str, ...]
) -> list[Any]:
    table = type(record).__table__
    primary_key = table.columns.get(str(spec.primary_key))
    if primary_key is None:
        raise CompensationExecutionError("permanent", "compensation model has no durable primary key")
    predicates: list[Any] = [primary_key == getattr(record, spec.primary_key)]
    if getattr(spec, "ownership_scope", "") == "actor_owned" and table.columns.get("owner_actor_id") is not None:
        predicates.append(table.columns.owner_actor_id == str(actor.actor_id))
    stored_hash = str(getattr(record, "operator_version_hash", "") or "")
    version_column = table.columns.get("operator_version_hash")
    if stored_hash and version_column is not None:
        predicates.append(version_column == stored_hash)
    for field in expected_fields:
        column = table.columns.get(str(field))
        if column is None:
            raise CompensationExecutionError("permanent", f"compensation field {field} is not durably mapped")
        value = getattr(record, field, None)
        predicates.append(column.is_(None) if value is None else column == value)
    return predicates


async def _compensation_claim_predicate(db: Any, node_id: str) -> Any:
    claim = await db.get(models.SagaCompensationReceipt, str(node_id), populate_existing=True)
    if (
        claim is None
        or str(claim.status or "") != "running"
        or not str(claim.claim_token or "")
        or int(claim.claim_generation or 0) < 1
        or claim.lease_expires_at is None
        or claim.lease_expires_at <= _now()
    ):
        raise CompensationExecutionError("unknown", "compensation execution claim lease was lost before the inverse write")
    return exists(
        select(1).where(
            models.SagaCompensationReceipt.node_id == str(node_id),
            models.SagaCompensationReceipt.status == "running",
            models.SagaCompensationReceipt.claim_token == str(claim.claim_token),
            models.SagaCompensationReceipt.claim_generation == int(claim.claim_generation or 0),
            models.SagaCompensationReceipt.lease_expires_at > _now(),
        )
    )


async def _registered_compensation_handler(
    db: Any,
    actor: Any,
    node: models.OperationNode,
    policy: Any,
    outcome: models.NodeExecutionOutcome,
) -> dict[str, Any]:
    from app.operator.effect_manifest import EffectManifestError, validate_effect_manifest
    from app.operator.guards import canonical_version, fetch_scoped_record, get_model_class, get_model_spec

    try:
        manifest = validate_effect_manifest(
            node,
            outcome.effect_manifest_json if isinstance(outcome.effect_manifest_json, Mapping) else {},
            expected_resolved_input_digest=str(outcome.resolved_input_digest or ""),
        )
    except EffectManifestError as exc:
        raise CompensationExecutionError("permanent", str(exc)) from exc
    payload = dict(node.payload_json or {})
    model_name = str(node.target_name or payload.get("model") or "")
    expected_operation = {
        "delete_created_record": "create",
        "restore_previous_fields": "patch",
    }.get(str(policy.operation or ""))
    if not model_name or expected_operation is None:
        raise CompensationExecutionError("permanent", "registered compensation operation is unsupported")
    effects = [
        item
        for item in manifest.get("effects") or []
        if isinstance(item, Mapping)
        and str(item.get("kind") or "") == "database_record"
        and str(item.get("operation") or "") == expected_operation
        and str(item.get("model") or "") == model_name
    ]
    if len(effects) != 1:
        raise CompensationExecutionError(
            "permanent", "compensation requires exactly one manifest-bound database effect"
        )
    effect = effects[0]
    record_id = str(effect.get("record_id") or "")
    after_version = str(effect.get("after_version") or "")
    if not record_id or not after_version:
        raise CompensationExecutionError("permanent", "compensation manifest omitted identity/version evidence")
    public_result = outcome.public_result_json if isinstance(outcome.public_result_json, Mapping) else {}
    typed_outputs = outcome.typed_outputs if isinstance(outcome.typed_outputs, Mapping) else {}
    if public_result.get("model") not in (None, "", model_name):
        raise CompensationExecutionError("permanent", "immutable outcome model identity conflicts with its manifest")
    for key in ("primary_record_id", "record_id"):
        value = typed_outputs.get(key)
        if value not in (None, "") and str(value) != record_id:
            raise CompensationExecutionError("permanent", "immutable outcome record identity conflicts with its manifest")
    sealed_record_id = payload.get("record_id")
    symbolic_record_id = (
        sealed_record_id.get("$output")
        if isinstance(sealed_record_id, Mapping)
        and isinstance(sealed_record_id.get("$output"), Mapping)
        else None
    )
    if (
        expected_operation == "patch"
        and sealed_record_id not in (None, "")
        and symbolic_record_id is None
        and str(sealed_record_id) != record_id
    ):
        raise CompensationExecutionError("permanent", "patch compensation identity is outside the immutable manifest")

    spec = get_model_spec(model_name)
    model_cls = get_model_class(model_name)
    record = await fetch_scoped_record(db, actor, spec, model_cls, record_id)
    current = canonical_version(record, spec)
    if current != after_version:
        raise CompensationExecutionError(
            "permanent", "current record version does not match the immutable manifest effect"
        )
    claim_predicate = await _compensation_claim_predicate(db, node.node_id)
    connection = await db.connection()
    if policy.operation == "delete_created_record":
        version_fields = tuple(
            field for field in dict.fromkeys((*tuple(getattr(spec, "detail_fields", ()) or ()), *tuple(getattr(spec, "writable_fields", ()) or ())))
            if str(field) not in {"created_at", "updated_at"}
        )
        record_predicates = _compensation_record_cas_predicates(record, spec, actor, expected_fields=version_fields)
        deleted = await connection.execute(
            delete(model_cls)
            .where(*record_predicates, claim_predicate)
        )
        if int(deleted.rowcount or 0) != 1:
            raise CompensationExecutionError("permanent", "created record changed concurrently or compensation claim expired")
        db.expunge(record)
        return {
            "status": "completed", "operation": policy.operation,
            "record_id": record_id, "effect_manifest_digest": str(manifest.get("digest") or ""),
            "fence_verified": True,
        }
    before = public_result.get("before")
    updates = payload.get("updates")
    changed_fields = {str(value) for value in (effect.get("changed_fields") or [])}
    if not isinstance(before, Mapping) or not isinstance(updates, Mapping):
        raise CompensationExecutionError("permanent", "patch compensation lacks immutable before values")
    requested_fields = {str(field) for field in updates}
    if not requested_fields or not requested_fields <= changed_fields:
        raise CompensationExecutionError("permanent", "patch compensation fields are not bound to manifest evidence")
    for field in requested_fields:
        if field not in before:
            raise CompensationExecutionError("permanent", f"patch compensation lacks before value for {field}")
    restore_values = {field: before[field] for field in sorted(requested_fields)}
    updated = await connection.execute(
        update(model_cls)
        .where(*_compensation_record_cas_predicates(record, spec, actor, expected_fields=tuple(sorted(requested_fields))), claim_predicate)
        .values(**restore_values)
    )
    if int(updated.rowcount or 0) != 1:
        raise CompensationExecutionError("permanent", "patched record changed concurrently or compensation claim expired")
    return {
        "status": "completed", "operation": policy.operation,
        "record_id": record_id, "effect_manifest_digest": str(manifest.get("digest") or ""),
        "fence_verified": True,
    }


async def compensate_plan_registered(db: Any, actor: Any, plan_id: str) -> dict[str, Any]:
    async def handler(node: models.OperationNode, policy: Any, outcome: models.NodeExecutionOutcome) -> dict[str, Any]:
        return await _registered_compensation_handler(db, actor, node, policy, outcome)

    return await compensate_plan(db, actor, plan_id, handler)




