from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select

from app.models import models


class AuthorizationError(RuntimeError):
    pass


async def _scoped_plan(db: Any, actor: Any, plan_id: str, *, lock: bool = False) -> models.ProposalPlan:
    statement = select(models.ProposalPlan).where(
        models.ProposalPlan.plan_id == str(plan_id),
        models.ProposalPlan.actor_id == str(actor.actor_id),
        models.ProposalPlan.session_id == str(actor.session_id),
    )
    if lock:
        statement = statement.with_for_update()
    plan = (await db.execute(statement)).scalar_one_or_none()
    if plan is None:
        raise AuthorizationError("Plan was not found in the actor/session scope")
    return plan


async def replace_plan(
    db: Any,
    actor: Any,
    *,
    old_plan_id: str,
    replacement_plan_id: str,
) -> models.ProposalPlan:
    old = await _scoped_plan(db, actor, old_plan_id, lock=True)
    replacement = await _scoped_plan(db, actor, replacement_plan_id, lock=True)
    if old.execution_started or old.status in {"executing", "partially_completed", "completed", "manual_review"}:
        raise AuthorizationError("A Plan with execution history cannot be replaced; create a fresh follow-up Plan")
    if old.status != "sealed":
        raise AuthorizationError("Only the current sealed unexecuted Plan can be replaced")
    if replacement.status != "sealed" or replacement.execution_started:
        raise AuthorizationError("Replacement must be a distinct sealed unexecuted Plan")
    if old.plan_id == replacement.plan_id:
        raise AuthorizationError("A Plan cannot replace itself")
    old_lineage = str(old.lineage_id or old.draft_id)
    if str(replacement.lineage_id or "") != old_lineage or str(replacement.parent_plan_id or "") != str(old.plan_id):
        raise AuthorizationError("Replacement must be the next revision in the same durable Plan lineage")
    if replacement.current_lineage_key not in (None, ""):
        raise AuthorizationError("Replacement revision was already activated")
    old.status = "replaced"
    old.current_lineage_key = None
    old.replaced_by_plan_id = replacement.plan_id
    await db.flush()
    replacement.current_lineage_key = old_lineage
    await db.commit()
    return replacement


def group_authorization_digest(group: models.ConfirmationGroup) -> str:
    return str(getattr(group, "authorization_digest", "") or group.group_digest or "")


def _decision_identity_matches(event: models.ConfirmationDecision, values: Mapping[str, str]) -> bool:
    return all(str(getattr(event, key) or "") == str(value or "") for key, value in values.items())


async def _block_group_descendants(db: Any, plan_id: str, rejected_group_id: str) -> None:
    groups = (
        await db.execute(
            select(models.ConfirmationGroup).where(models.ConfirmationGroup.plan_id == plan_id)
        )
    ).scalars().all()
    blocked = {rejected_group_id}
    changed = True
    while changed:
        changed = False
        for group in groups:
            if group.group_id in blocked:
                continue
            if any(str(parent) in blocked for parent in list(group.dependency_group_ids or [])):
                blocked.add(group.group_id)
                changed = True
    descendant_ids = blocked - {rejected_group_id}
    for group in groups:
        if group.group_id in descendant_ids and group.status in {"pending", "awaiting_more_confirmations", "confirmed"}:
            group.status = "blocked"
    nodes = (
        await db.execute(
            select(models.OperationNode).where(
                models.OperationNode.plan_id == plan_id,
                models.OperationNode.confirmation_group_id.in_(blocked),
            )
        )
    ).scalars().all()
    for node in nodes:
        if node.confirmation_group_id == rejected_group_id:
            node.status = "rejected"
        elif node.status in {"pending", "authorized"}:
            node.status = "blocked"
    from app.operator.plan_execution import publish_authorization_terminal_facts

    await db.flush()
    groups_by_id = {str(group.group_id): group for group in groups}
    await publish_authorization_terminal_facts(
        db,
        await db.get(models.ProposalPlan, str(plan_id), populate_existing=True),
        [groups_by_id[group_id] for group_id in blocked if group_id in groups_by_id],
    )


async def record_group_decision(
    db: Any,
    actor: Any,
    *,
    plan_id: str,
    plan_digest: str,
    group_id: str,
    group_digest: str,
    decision: str,
    event_id: str,
    _defer_commit: bool = False,
) -> models.ConfirmationDecision:
    decision = str(decision or "").lower()
    if decision not in {"confirm", "reject"}:
        raise AuthorizationError("Group decision must be confirm or reject")
    identity = {
        "plan_id": str(plan_id),
        "group_id": str(group_id),
        "actor_id": str(actor.actor_id),
        "session_id": str(actor.session_id),
        "decision": decision,
        "plan_digest": str(plan_digest),
        "group_digest": str(group_digest),
    }
    existing = await db.get(models.ConfirmationDecision, str(event_id))
    if existing is not None:
        if not _decision_identity_matches(existing, identity):
            raise AuthorizationError("Confirmation event replay changed its digest-bound identity")
        if str(existing.decision or "") == "reject":
            plan = await _scoped_plan(db, actor, plan_id, lock=True)
            groups = list((await db.execute(select(models.ConfirmationGroup).where(models.ConfirmationGroup.plan_id == plan.plan_id))).scalars().all())
            from app.operator.plan_execution import publish_authorization_terminal_facts

            await publish_authorization_terminal_facts(db, plan, [group for group in groups if str(group.status or "") in {"rejected", "blocked"}])
            if _defer_commit:
                await db.flush()
            else:
                await db.commit()
        return existing

    plan = await _scoped_plan(db, actor, plan_id, lock=True)
    if plan.status not in {"sealed", "executing"}:
        raise AuthorizationError("Only the current sealed plan or its executing immutable continuation can receive group decisions")
    if str(plan.plan_digest) != str(plan_digest):
        raise AuthorizationError("Plan digest does not match the immutable sealed Plan")
    group = (
        await db.execute(
            select(models.ConfirmationGroup)
            .where(
                models.ConfirmationGroup.group_id == str(group_id),
                models.ConfirmationGroup.plan_id == plan.plan_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if group is None:
        raise AuthorizationError("Confirmation Group was not found in this Plan")
    if group_authorization_digest(group) != str(group_digest):
        raise AuthorizationError("Group digest does not match the snapshot-bound authorization boundary")
    if group.status not in {"pending", "awaiting_more_confirmations"}:
        raise AuthorizationError(f"Confirmation Group is already {group.status}")

    sequence = int(
        await db.scalar(
            select(func.coalesce(func.max(models.ConfirmationDecision.sequence), 0)).where(
                models.ConfirmationDecision.group_id == group.group_id
            )
        )
        or 0
    ) + 1
    event = models.ConfirmationDecision(event_id=str(event_id), sequence=sequence, **identity)
    db.add(event)
    if decision == "reject":
        group.status = "rejected"
        await _block_group_descendants(db, plan.plan_id, group.group_id)
    else:
        required = max(1, int((group.policy_json or {}).get("confirmations_required") or 1))
        received = sequence
        if received >= required:
            group.status = "confirmed"
            nodes = (
                await db.execute(
                    select(models.OperationNode).where(
                        models.OperationNode.confirmation_group_id == group.group_id,
                        models.OperationNode.status == "pending",
                    )
                )
            ).scalars().all()
            for node in nodes:
                node.status = "authorized"
        else:
            group.status = "awaiting_more_confirmations"
    if _defer_commit:
        await db.flush()
    else:
        await db.commit()
    return event

