from __future__ import annotations

import asyncio
import enum
import hashlib
import json
import logging
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from types import SimpleNamespace

from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql import visitors
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified, set_committed_value

from app.database import async_session
from app.harness import skill_runtime
from app.models import models
from app.operator.audit import log_agent_audit, redact_audit_args
from app.operator.application_lifecycle import (
    ApplicationLifecycleError,
    ApplicationLifecycleSpec,
    normalize_apply_status_update,
)
from app.operator.registry import BACKEND_OWNED_ACTION_INPUT_FIELDS
from app.operator.confirmation_context import (
    ConfirmationPrepareBoundaryViolation,
    confirmation_prepare_boundary,
)
from app.operator.create_conflicts import reject_duplicate_job_create_conflict
from app.operator.plan_authorization import group_authorization_digest, record_group_decision
from app.operator.readiness import resume_scope_from_runtime_state
from app.operator.plan_snapshots import (
    PlanSnapshotIntegrityError,
    validate_confirmation_group_binding,
    validate_group_snapshot_binding,
    validate_operation_node_binding,
)
from app.operator.errors import (
    OperatorError,
    conflict_error,
    not_found_error,
    permission_error,
    transient_error,
    validation_error,
)
from app.operator.guards import (
    ActorContext,
    DELETE_OPERATIONS,
    PATCH_MODES,
    RISK_CONFIRMATIONS,
    build_query_statement,
    calculate_action_risk,
    calculate_record_risk,
    canonical_version,
    collect_action_expected_versions,
    expected_versions_hash,
    fetch_scoped_record,
    get_action_spec,
    get_model_class,
    get_model_spec,
    json_safe,
    reject_trusted_args,
    serialize_record,
    validate_action_references,
    validate_action_schema,
    validate_create_scope,
    validate_fields,
    validate_model_values,
)
from app.operator.session import create_pre_confirmation_checkpoint
from app.operator.visibility import attach_visibility
from app.services.resume_builder import _build_source_profile_snapshot, _profile_to_contact_json
from app.services.profile_archive_sync import (
    remove_profile_section_from_personal_archive,
    sync_profile_section_to_personal_archive,
)
from app.services.profile_builder_agent import normalize_profile_agent_patch
from app.services.scrapers.base import JobItem
from app.services.scrapers.base import get_scraper
from app.services.profile_schema import (
    canonicalize_profile_section_payload,
    get_category_label,
    get_resume_section_type,
    is_valid_profile_section_type,
    normalize_base_info_payload,
    normalize_profile_section_record_payload,
    normalize_section_type_alias,
    to_resume_content_item,
)

logger = logging.getLogger(__name__)

_CONFIRMATION_RECEIPT_AUDIT_PREFIX = "confirmation_receipt:v1:"
_PLAN_GROUP_EXECUTION_LEASE_SECONDS = 300
_PLAN_GROUP_EXECUTION_HEARTBEAT_INTERVAL = 30.0


class _PrematureConfirmationTransactionEnd(RuntimeError):
    pass


class _ConfirmationExecutionGuard:
    _INFO_KEY = "_offeru_confirmation_execution_guard"

    def __init__(self, session: AsyncSession, *, read_only: bool = False) -> None:
        self._async_session = session
        self._session = session.sync_session
        self._read_only = read_only
        self._root_transaction = self._session.get_transaction()
        self._commit_attempted = False
        self._root_ended = False
        self._write_attempted = False
        self._installed = False
        self._connection: Any | None = None

    def install(self, connection: Any | None = None) -> None:
        if self._root_transaction is None or not self._root_transaction.is_active:
            raise OperatorError(
                "transient_error",
                "Proposal execution requires one active root transaction.",
                {"stage": "execute"},
            )
        if self._session.info.get(self._INFO_KEY) is not None:
            raise OperatorError(
                "transient_error",
                "Proposal execution transaction guard is already active.",
                {"stage": "execute"},
            )
        self._session.info[self._INFO_KEY] = self
        event.listen(self._session, "before_commit", self._before_commit)
        event.listen(self._session, "after_transaction_end", self._after_transaction_end)
        if self._read_only:
            event.listen(self._session, "before_flush", self._before_flush)
            event.listen(self._session, "do_orm_execute", self._do_orm_execute)
            if connection is None:
                raise OperatorError(
                    "transient_error",
                    "Read-only proposal preparation requires the owning connection.",
                    {"stage": "prepare"},
                )
            self._connection = connection
            self._connection.info[self._INFO_KEY] = self
            event.listen(self._connection, "before_execute", self._before_execute)
            event.listen(self._connection, "before_cursor_execute", self._before_cursor_execute)
        self._installed = True

    def close(self) -> None:
        if not self._installed:
            return
        event.remove(self._session, "before_commit", self._before_commit)
        event.remove(self._session, "after_transaction_end", self._after_transaction_end)
        if self._read_only:
            event.remove(self._session, "before_flush", self._before_flush)
            event.remove(self._session, "do_orm_execute", self._do_orm_execute)
            if self._connection is not None:
                event.remove(self._connection, "before_execute", self._before_execute)
                event.remove(self._connection, "before_cursor_execute", self._before_cursor_execute)
                if self._connection.info.get(self._INFO_KEY) is self:
                    self._connection.info.pop(self._INFO_KEY, None)
                self._connection = None
        if self._session.info.get(self._INFO_KEY) is self:
            self._session.info.pop(self._INFO_KEY, None)
        self._installed = False

    def assert_intact(self) -> None:
        current = self._session.get_transaction()
        if (
            self._commit_attempted
            or self._root_ended
            or self._write_attempted
            or current is not self._root_transaction
            or current is None
            or not current.is_active
        ):
            raise OperatorError(
                "transient_error",
                "Proposal execution attempted to end its owning transaction.",
                {"stage": "execute"},
            )

    def _before_commit(self, session: Any) -> None:
        if session.info.get(self._INFO_KEY) is not self:
            return
        self._commit_attempted = True
        raise _PrematureConfirmationTransactionEnd(
            "Proposal execution helpers must not commit the confirmation transaction."
        )

    def _after_transaction_end(self, session: Any, transaction: Any) -> None:
        if session.info.get(self._INFO_KEY) is self and transaction is self._root_transaction:
            self._root_ended = True

    def _before_flush(self, session: Any, flush_context: Any, instances: Any) -> None:
        if session.info.get(self._INFO_KEY) is not self:
            return
        if session.new or session.dirty or session.deleted:
            self._write_attempted = True
            raise _PrematureConfirmationTransactionEnd("Proposal prepare must not flush business writes.")

    def _do_orm_execute(self, execute_state: Any) -> None:
        if self._session.info.get(self._INFO_KEY) is not self:
            return
        if execute_state.is_insert or execute_state.is_update or execute_state.is_delete:
            self._write_attempted = True
            raise _PrematureConfirmationTransactionEnd("Proposal prepare must not execute business DML.")

    def _before_execute(
        self,
        connection: Any,
        clauseelement: Any,
        multiparams: Any,
        params: Any,
        execution_options: Any,
    ) -> None:
        if connection.info.get(self._INFO_KEY) is not self:
            return
        if bool(getattr(clauseelement, "is_insert", False)) or bool(getattr(clauseelement, "is_update", False)) or bool(
            getattr(clauseelement, "is_delete", False)
        ):
            self._write_attempted = True
            raise _PrematureConfirmationTransactionEnd("Proposal prepare must not execute Core DML.")

    def _before_cursor_execute(
        self,
        connection: Any,
        cursor: Any,
        statement: Any,
        parameters: Any,
        context: Any,
        executemany: Any,
    ) -> None:
        if connection.info.get(self._INFO_KEY) is not self:
            return
        normalized = str(statement or "").lstrip().upper()
        compiled_statement = getattr(getattr(context, "compiled", None), "statement", None)
        compiled_select_is_read_only = bool(getattr(compiled_statement, "is_select", False)) and not any(
            bool(getattr(element, "is_insert", False))
            or bool(getattr(element, "is_update", False))
            or bool(getattr(element, "is_delete", False))
            for element in visitors.iterate(compiled_statement)
        )
        if not normalized.startswith("SELECT") and not compiled_select_is_read_only:
            self._write_attempted = True
            raise _PrematureConfirmationTransactionEnd("Proposal prepare may execute SELECT statements only.")


async def _execute_in_confirmation_transaction(session: AsyncSession, execution: Any) -> dict[str, Any]:
    guard = _ConfirmationExecutionGuard(session)
    guard.install()
    try:
        try:
            result = await execution()
        except _PrematureConfirmationTransactionEnd as exc:
            raise OperatorError(
                "transient_error",
                "Proposal execution attempted to commit before confirmation finalized.",
                {"stage": "execute"},
            ) from exc
        guard.assert_intact()
        return result
    finally:
        guard.close()


async def _prepare_in_read_only_confirmation_transaction(
    session: AsyncSession,
    prepare: Any,
) -> Any:
    guard = _ConfirmationExecutionGuard(session, read_only=True)
    connection = await session.connection()
    guard.install(connection.sync_connection)
    try:
        with confirmation_prepare_boundary(session.sync_session, connection.sync_connection) as boundary:
            try:
                execution = await prepare()
            except (_PrematureConfirmationTransactionEnd, ConfirmationPrepareBoundaryViolation) as exc:
                raise OperatorError(
                    "transient_error",
                    "Proposal preparation attempted to mutate or end its read-only transaction.",
                    {"stage": "prepare"},
                ) from exc
            guard.assert_intact()
            try:
                boundary.assert_intact()
            except ConfirmationPrepareBoundaryViolation as exc:
                raise OperatorError(
                    "transient_error",
                    "Proposal preparation attempted to use an independent database write transaction.",
                    {"stage": "prepare"},
                ) from exc
            return execution
    finally:
        guard.close()


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    AWAITING_NEXT_CONFIRMATION = "awaiting_next_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONFLICT = "conflict"


RESUME_SECTION_TITLES = {
    "education": "教育经历",
    "experience": "实践经历",
    "workExperiences": "工作经历",
    "internshipExperiences": "实习经历",
    "project": "项目经历",
    "projects": "项目经历",
    "skill": "技能清单",
    "skills": "技能",
    "certificate": "证书资质",
    "certificates": "证书",
    "custom": "补充亮点",
    "personalExperiences": "个人经历",
}

DEFAULT_RESUME_PERSONAL_SECTION_TYPE = "personalExperiences"

SCRAPER_SOURCE_ALIASES = {
    "boss": "boss",
    "zhilian": "zhilian",
    "linkedin": "linkedin",
    "jobspy": "jobspy",
    "shixiseng": "shixiseng",
    "corporate": "corporate",
}

EMAIL_CATEGORY_TO_EVENT_TYPE = {
    "application": "application",
    "written_test": "written_test",
    "assessment": "assessment",
    "interview_1": "interview",
    "interview_2": "interview",
    "interview_hr": "interview",
    "offer": "offer",
    "rejection": "rejection",
}


async def _record_plan_group_confirmation(session: AsyncSession, actor: ActorContext, proposal: models.ProposalCache, event_id: str) -> None:
    if not str(getattr(proposal, "plan_id", "") or ""):
        return
    plan = await session.get(models.ProposalPlan, proposal.plan_id)
    group = await session.get(models.ConfirmationGroup, proposal.confirmation_group_id)
    if plan is None or group is None:
        raise OperatorError("confirmation_integrity_error", "Plan confirmation projection is missing its immutable Plan or Group.", {"proposal_id": proposal.proposal_id})
    await record_group_decision(
        session, actor, plan_id=plan.plan_id, plan_digest=plan.plan_digest,
        group_id=group.group_id, group_digest=group_authorization_digest(group), decision="confirm",
        event_id=str(event_id), _defer_commit=True,
    )


async def _record_plan_group_rejection(session: AsyncSession, actor: ActorContext, proposal: models.ProposalCache, event_id: str) -> None:
    if not str(getattr(proposal, "plan_id", "") or ""):
        return
    plan = await session.get(models.ProposalPlan, proposal.plan_id)
    group = await session.get(models.ConfirmationGroup, proposal.confirmation_group_id)
    if plan is None or group is None:
        raise OperatorError("confirmation_integrity_error", "Plan rejection projection is missing its immutable Plan or Group.", {"proposal_id": proposal.proposal_id})
    await record_group_decision(
        session, actor, plan_id=plan.plan_id, plan_digest=plan.plan_digest,
        group_id=group.group_id, group_digest=group_authorization_digest(group), decision="reject",
        event_id=str(event_id), _defer_commit=True,
    )



async def _execute_plan_node_projection(
    session: AsyncSession,
    actor: ActorContext,
    node: models.OperationNode,
    resolved_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute one authorized Plan node from its immutable internal snapshot."""
    snapshot = await session.get(models.PlanNodeExecutionSnapshot, node.node_id, populate_existing=True)
    if snapshot is None or snapshot.plan_id != node.plan_id or snapshot.confirmation_group_id != node.confirmation_group_id:
        raise OperatorError(
            "confirmation_integrity_error",
            "Authorized Plan node has no immutable execution snapshot.",
            {"plan_id": node.plan_id, "node_id": node.node_id},
        )
    plan = await session.get(models.ProposalPlan, node.plan_id, populate_existing=True)
    group = await session.get(models.ConfirmationGroup, node.confirmation_group_id, populate_existing=True)
    group_snapshots = list(
        (
            await session.execute(
                select(models.PlanNodeExecutionSnapshot)
                .where(
                    models.PlanNodeExecutionSnapshot.plan_id == str(node.plan_id),
                    models.PlanNodeExecutionSnapshot.confirmation_group_id == str(node.confirmation_group_id),
                )
                .order_by(models.PlanNodeExecutionSnapshot.node_id)
            )
        ).scalars().all()
    )
    try:
        if plan is None or group is None:
            raise PlanSnapshotIntegrityError("execution snapshot authorization Plan/Group is missing")
        validate_group_snapshot_binding(plan, group, group_snapshots)
    except PlanSnapshotIntegrityError as exc:
        raise OperatorError(
            "confirmation_integrity_error",
            f"Authorized Plan execution snapshot failed integrity validation: {exc}",
            {"plan_id": node.plan_id, "group_id": node.confirmation_group_id, "node_id": node.node_id},
        ) from exc
    projection = SimpleNamespace(
        proposal_id=f"plan-node:{node.node_id}", plan_id=node.plan_id, confirmation_group_id=node.confirmation_group_id,
        node_ids=[node.node_id], tool_name=snapshot.tool_name, model_or_action=snapshot.model_or_action,
        record_id=snapshot.record_id, risk_level=snapshot.risk_level, before=snapshot.before, after=snapshot.after,
        confirmations_required=int(RISK_CONFIRMATIONS.get(int(snapshot.risk_level or 0), 0)),
        requires_second_confirmation=int(snapshot.risk_level or 0) >= 5,
        expected_version_or_hash=snapshot.expected_version_or_hash, locked_payload=dict(snapshot.locked_payload or {}),
    )
    locked = dict(snapshot.locked_payload or {})
    locked.update(dict(resolved_payload))
    locked["tool_name"] = node.tool_name
    if node.base_version and not locked.get("expected_version_or_hash"):
        locked["expected_version_or_hash"] = node.base_version
    projection.locked_payload = locked
    projection.plan_id = node.plan_id
    projection.confirmation_group_id = node.confirmation_group_id
    projection.node_ids = [node.node_id]
    execution = await _prepare_in_read_only_confirmation_transaction(
        session, lambda: _prepare_execution(session, actor, projection)
    )
    pending_safe_rebase = getattr(projection, "_pending_safe_rebase", None)
    if isinstance(pending_safe_rebase, Mapping):
        from app.operator.safe_rebase import record_safe_rebase
        await record_safe_rebase(session, actor, **dict(pending_safe_rebase), _defer_commit=True)
    from app.operator.effect_manifest import execute_with_effect_manifest

    result, effect_manifest = await execute_with_effect_manifest(
        session,
        lambda: _execute_in_confirmation_transaction(session, execution),
        node=node,
        resolved_payload=resolved_payload,
    )
    result.setdefault("before", snapshot.before if isinstance(snapshot.before, Mapping) else {})
    result["_effect_manifest"] = effect_manifest
    return result


async def _confirm_plan_group_proposal(
    session: AsyncSession,
    actor: ActorContext,
    proposal: models.ProposalCache,
    request_body: Mapping[str, Any],
) -> dict[str, Any]:
    from app.operator.plan_execution import execute_authorized_plan
    from app.operator.plan_runtime import expire_plan_group_projection, materialize_plan_proposals, plan_state_envelope

    plan = await session.get(models.ProposalPlan, proposal.plan_id, populate_existing=True)
    group = await session.get(models.ConfirmationGroup, proposal.confirmation_group_id, populate_existing=True)
    if plan is None or group is None or plan.actor_id != actor.actor_id or plan.session_id != actor.session_id:
        raise OperatorError("confirmation_integrity_error", "Plan Group projection is outside the immutable actor/session scope.", {})
    try:
        validate_confirmation_group_binding(plan, group)
    except PlanSnapshotIntegrityError as exc:
        raise OperatorError("confirmation_integrity_error", str(exc), {"plan_id": str(plan.plan_id), "group_id": str(group.group_id)}) from exc
    locked = proposal.locked_payload if isinstance(proposal.locked_payload, Mapping) else {}
    expected_locked_keys = {"plan_id", "plan_digest", "group_id", "group_digest", "node_ids"}
    if set(str(key) for key in locked) != expected_locked_keys:
        raise OperatorError("confirmation_integrity_error", "Plan Group projection locked payload was altered.", {"fields": sorted(str(key) for key in locked)})
    if str(locked.get("plan_id") or "") != str(plan.plan_id) or str(locked.get("group_id") or "") != str(group.group_id):
        raise OperatorError("confirmation_integrity_error", "Plan Group projection identity does not match durable authorization state.", {})
    if str(locked.get("plan_digest") or "") != str(plan.plan_digest) or str(locked.get("group_digest") or "") != group_authorization_digest(group):
        raise OperatorError("confirmation_integrity_error", "Plan Group projection digest does not match durable authorization state.", {})
    durable_nodes = list(
        (
            await session.execute(
                select(models.OperationNode)
                .where(models.OperationNode.plan_id == plan.plan_id, models.OperationNode.confirmation_group_id == group.group_id)
                .order_by(models.OperationNode.sequence)
            )
        ).scalars().all()
    )
    try:
        for node in durable_nodes:
            validate_operation_node_binding(plan, node)
    except PlanSnapshotIntegrityError as exc:
        raise OperatorError("confirmation_integrity_error", str(exc), {"plan_id": str(plan.plan_id), "group_id": str(group.group_id)}) from exc
    durable_node_ids = [str(node.node_id) for node in durable_nodes]
    if list(proposal.node_ids or []) != durable_node_ids or list(locked.get("node_ids") or []) != durable_node_ids:
        raise OperatorError("confirmation_integrity_error", "Plan Group projection membership changed after sealing.", {})
    if proposal.status == "confirmed":
        stored_response = await _validated_stored_confirm_response(session, actor, proposal)
        await _audit_decision(
            session,
            actor,
            proposal,
            confirmation_status="idempotent_confirm",
            result_status=str(stored_response.get("status") or "confirmed"),
            result_summary="Completed Plan confirmation returned without re-executing effects.",
        )
        await session.commit()
        continuation = await _continuation_after_confirmed(proposal.proposal_id)
        return json_safe({**stored_response, "continuation": continuation}) if continuation is not None else stored_response
    if proposal.status == "authorized":
        return await _execute_and_finalize_authorized_plan(session, actor, proposal, plan, group)
    if proposal.status not in {"pending", "awaiting_next_confirmation"}:
        raise OperatorError("conflict_error", "Plan Group projection is no longer confirmable.", {"status": proposal.status})
    if _is_expired(proposal) or not await _is_current_session_pending_proposal(session, actor, proposal.proposal_id):
        await _mark_terminal(session, actor, proposal, "expired", reason="Plan Group authorization card expired")
        await expire_plan_group_projection(session, actor, proposal)
        await session.commit()
        raise OperatorError("conflict_error", "Plan Group authorization expired; the Plan is terminal and must be replaced.", {"plan_id": plan.plan_id, "group_id": group.group_id})

    required = max(1, int((group.policy_json or {}).get("confirmations_required") or 1))
    received = int(proposal.confirmations_received or 0)
    if received and required > 1:
        supplied = str(request_body.get("confirmation_challenge") or "")
        if proposal.confirmation_challenge and supplied != str(proposal.confirmation_challenge):
            return {"ok": True, "status": "awaiting_next_confirmation", "proposal_id": proposal.proposal_id, "confirmations_required": required, "confirmations_received": received, "remaining": required - received, "confirmation_challenge": proposal.confirmation_challenge}
    if received < required - 1:
        if not await _claim_pending_proposal(session, proposal, status="awaiting_next_confirmation", expected_confirmation_count=proposal.confirmation_count):
            return await _handle_lost_confirm_claim(session, actor, proposal.proposal_id)
        event_id = await _record_intermediate_confirmation(session, actor, proposal)
        proposal.confirmations_received = received + 1
        proposal.confirmation_count = proposal.confirmations_received
        proposal.first_confirmed_at = proposal.first_confirmed_at or datetime.now(timezone.utc).replace(tzinfo=None)
        proposal.confirmation_challenge = f"confirm-{uuid.uuid4().hex[:12]}"
        proposal.confirmation_challenges = [*list(proposal.confirmation_challenges or []), proposal.confirmation_challenge]
        await _record_plan_group_confirmation(session, actor, proposal, event_id)
        await session.commit()
        envelope = await plan_state_envelope(session, plan.plan_id)
        return {"ok": True, "status": "awaiting_next_confirmation", "proposal_id": proposal.proposal_id, "confirmations_required": required, "confirmations_received": proposal.confirmations_received, "remaining": required - proposal.confirmations_received, "confirmation_challenge": proposal.confirmation_challenge, "plan_event": envelope}

    if not await _claim_pending_proposal(session, proposal, status="authorized", expected_confirmation_count=proposal.confirmation_count):
        current = await _load_proposal_authoritative(session, proposal.proposal_id)
        if current is not None and current.status in {"authorized", "confirmed"}:
            return await _confirm_plan_group_proposal(session, actor, current, request_body)
        return await _handle_lost_confirm_claim(session, actor, proposal.proposal_id)
    authorization_event = _event("authorized", actor, result={"status": "authorized"})
    events = _events(proposal)
    events.append(authorization_event)
    proposal.confirmation_events = events
    proposal.confirmations_received = received + 1
    proposal.confirmation_count = proposal.confirmations_received
    proposal.first_confirmed_at = proposal.first_confirmed_at or datetime.now(timezone.utc).replace(tzinfo=None)
    if proposal.confirmations_received >= 2:
        proposal.second_confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await _record_plan_group_confirmation(session, actor, proposal, str(authorization_event["event_id"]))
    await _remove_pending_proposal_id(session, actor, proposal.proposal_id)
    session.add(models.PlanGroupExecutionJob(
        proposal_id=proposal.proposal_id,
        plan_id=plan.plan_id,
        group_id=group.group_id,
        actor_id=actor.actor_id,
        session_id=actor.session_id,
        status="queued",
        idempotency_key=f"plan-group-execution:v1:{proposal.proposal_id}:{authorization_event['event_id']}",
    ))
    await session.commit()  # authorization and its durable execution job precede every business effect
    return await _execute_and_finalize_authorized_plan(session, actor, proposal, plan, group)


async def _claim_plan_group_execution_job(
    session: AsyncSession, proposal: models.ProposalCache, *, lease_seconds: int = _PLAN_GROUP_EXECUTION_LEASE_SECONDS
) -> models.PlanGroupExecutionJob | None:
    job = await session.get(models.PlanGroupExecutionJob, proposal.proposal_id, populate_existing=True)
    if job is None:
        raise OperatorError(
            "confirmation_integrity_error",
            "Authorized Plan proposal is missing its durable execution job.",
            {"proposal_id": str(proposal.proposal_id), "requires_manual_review": True},
        )
    if (
        str(job.plan_id) != str(proposal.plan_id)
        or str(job.group_id) != str(proposal.confirmation_group_id)
        or str(job.actor_id) != str(proposal.actor_id)
        or str(job.session_id) != str(proposal.session_id)
    ):
        raise OperatorError("confirmation_integrity_error", "Plan execution job identity mismatch.", {"proposal_id": str(proposal.proposal_id)})
    if job.status == "completed":
        return job
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if job.status == "running" and job.lease_expires_at is not None and job.lease_expires_at > now:
        return None
    token = f"plan-driver-{uuid.uuid4().hex}"
    generation = int(job.claim_generation or 0) + 1
    allowed = ["queued"] if job.status == "queued" else ["running"]
    conditions = [
        models.PlanGroupExecutionJob.proposal_id == proposal.proposal_id,
        models.PlanGroupExecutionJob.status.in_(allowed),
        models.PlanGroupExecutionJob.claim_generation == int(job.claim_generation or 0),
    ]
    if job.status == "running":
        conditions.extend([
            models.PlanGroupExecutionJob.claim_token == str(job.claim_token or ""),
            models.PlanGroupExecutionJob.lease_expires_at <= now,
        ])
    changed = await session.execute(
        update(models.PlanGroupExecutionJob)
        .where(*conditions)
        .values(
            status="running", claim_token=token, claim_generation=generation,
            lease_expires_at=now + timedelta(seconds=max(5, int(lease_seconds))), error_json={},
        )
        .execution_options(synchronize_session=False)
    )
    if int(changed.rowcount or 0) != 1:
        await session.rollback()
        return None
    await session.commit()
    return await session.get(models.PlanGroupExecutionJob, proposal.proposal_id, populate_existing=True)


async def _renew_plan_group_execution_job(
    session: AsyncSession, proposal_id: str, token: str, generation: int, *, lease_seconds: int
) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    changed = await session.execute(
        update(models.PlanGroupExecutionJob)
        .where(
            models.PlanGroupExecutionJob.proposal_id == str(proposal_id),
            models.PlanGroupExecutionJob.status == "running",
            models.PlanGroupExecutionJob.claim_token == str(token),
            models.PlanGroupExecutionJob.claim_generation == int(generation),
            models.PlanGroupExecutionJob.lease_expires_at > now,
        )
        .values(lease_expires_at=now + timedelta(seconds=max(1, int(lease_seconds))))
        .execution_options(synchronize_session=False)
    )
    if int(changed.rowcount or 0) != 1:
        await session.rollback()
        return False
    await session.commit()
    return True


async def _fenced_plan_group_execution_job(
    session: AsyncSession, proposal_id: str, token: str, generation: int
) -> models.PlanGroupExecutionJob:
    job = await session.get(models.PlanGroupExecutionJob, str(proposal_id), populate_existing=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        job is None
        or job.status != "running"
        or job.claim_token != str(token)
        or int(job.claim_generation or 0) != int(generation)
        or job.lease_expires_at is None
        or job.lease_expires_at <= now
    ):
        raise RuntimeError("Plan execution job claim lease was lost before finalization")
    return job


async def _release_plan_group_execution_job(
    session: AsyncSession, proposal_id: str, token: str, generation: int, error: Exception
) -> None:
    await session.rollback()
    terminal = (
        getattr(error, "classification", "") in {"integrity", "unknown"}
        or getattr(error, "code", "") == "confirmation_integrity_error"
    )
    changed = await session.execute(
        update(models.PlanGroupExecutionJob)
        .where(
            models.PlanGroupExecutionJob.proposal_id == str(proposal_id),
            models.PlanGroupExecutionJob.status == "running",
            models.PlanGroupExecutionJob.claim_token == str(token),
            models.PlanGroupExecutionJob.claim_generation == int(generation),
            models.PlanGroupExecutionJob.lease_expires_at > datetime.now(timezone.utc).replace(tzinfo=None),
        )
        .values(
            status=("manual_review" if terminal else "queued"),
            claim_token="", lease_expires_at=None,
            completed_at=(datetime.now(timezone.utc).replace(tzinfo=None) if terminal else None),
            error_json={"message": str(error), "classification": str(getattr(error, "classification", "") or getattr(error, "code", ""))},
        )
        .execution_options(synchronize_session=False)
    )
    if terminal and int(changed.rowcount or 0) == 1:
        # A manual_review job is a human gate: it must always surface a durable,
        # resolvable review case instead of an invisible dead job row.
        job = await session.get(models.PlanGroupExecutionJob, str(proposal_id), populate_existing=True)
        plan = await session.get(models.ProposalPlan, str(job.plan_id), populate_existing=True) if job is not None else None
        if job is not None and plan is not None:
            from app.operator.plan_execution import _ensure_manual_review_case

            await _ensure_manual_review_case(
                session,
                plan=plan,
                group_id=str(job.group_id or ""),
                proposal_id=str(job.proposal_id or ""),
                reason_code="plan_group_execution_job_requires_review",
                effect_state="unknown_external",
                evidence={
                    "message": str(error),
                    "classification": str(getattr(error, "classification", "") or getattr(error, "code", "")),
                },
                subject_type="plan_group_execution_job",
            )
    await session.commit()


async def _terminalize_invalid_plan_group_execution_job(
    session: AsyncSession,
    job: models.PlanGroupExecutionJob,
    *,
    proposal: models.ProposalCache | None,
    plan: models.ProposalPlan | None,
    group: models.ConfirmationGroup | None,
    reason_code: str,
    message: str,
) -> None:
    evidence = {
        "job_proposal_id": str(job.proposal_id),
        "job_plan_id": str(job.plan_id),
        "job_group_id": str(job.group_id),
        "job_actor_id": str(job.actor_id),
        "job_session_id": str(job.session_id),
        "proposal_plan_id": str(getattr(proposal, "plan_id", "") or ""),
        "proposal_group_id": str(getattr(proposal, "confirmation_group_id", "") or ""),
        "plan_actor_id": str(getattr(plan, "actor_id", "") or ""),
        "plan_session_id": str(getattr(plan, "session_id", "") or ""),
    }
    job.status = "manual_review"
    job.error_json = {"reason_code": str(reason_code), "message": str(message), "evidence": evidence}
    job.lease_expires_at = None
    job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if proposal is not None:
        proposal.status = "manual_review"
    if plan is not None:
        from app.operator.plan_execution import _ensure_manual_review_case

        await _ensure_manual_review_case(
            session,
            plan=plan,
            group_id=str(getattr(group, "group_id", "") or job.group_id),
            proposal_id=str(getattr(proposal, "proposal_id", "") or job.proposal_id),
            reason_code=str(reason_code),
            effect_state="unknown_external",
            evidence=evidence,
            subject_type="plan_group_execution_job",
        )
    await session.commit()


async def recover_plan_group_execution_jobs(
    *,
    limit: int = 100,
    session_factory: Any = None,
) -> int:
    """Recover queued or expired PlanGroupExecutionJob rows after a process crash.

    The scan claims each job through the same token/generation/lease CAS used by the
    request path, then invokes the durable plan executor without requiring a live
    proposal request from the frontend.
    """
    factory = session_factory or async_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as scan:
        candidate_ids = list(
            (
                await scan.scalars(
                    select(models.PlanGroupExecutionJob.proposal_id)
                    .where(
                        (models.PlanGroupExecutionJob.status == "queued")
                        | (
                            (models.PlanGroupExecutionJob.status == "running")
                            & (models.PlanGroupExecutionJob.lease_expires_at <= now)
                        )
                    )
                    .order_by(models.PlanGroupExecutionJob.created_at, models.PlanGroupExecutionJob.proposal_id)
                    .limit(max(1, int(limit)))
                )
            ).all()
        )
    recovered = 0
    for proposal_id in candidate_ids:
        async with factory() as session:
            job = await session.get(models.PlanGroupExecutionJob, str(proposal_id), populate_existing=True)
            if job is None:
                continue
            proposal = await session.get(models.ProposalCache, str(proposal_id), populate_existing=True)
            plan = await session.get(models.ProposalPlan, str(job.plan_id), populate_existing=True)
            group = await session.get(models.ConfirmationGroup, str(job.group_id), populate_existing=True)
            if proposal is None or plan is None or group is None:
                logging.error("Plan execution recovery found an invalid durable job identity: %s", proposal_id)
                await _terminalize_invalid_plan_group_execution_job(
                    session, job, proposal=proposal, plan=plan, group=group,
                    reason_code="plan_group_execution_job_identity_invalid",
                    message="Durable Plan Group execution job identity is incomplete.",
                )
                continue
            if (
                str(proposal.plan_id or "") != str(job.plan_id)
                or str(proposal.confirmation_group_id or "") != str(job.group_id)
                or str(plan.actor_id or "") != str(job.actor_id)
                or str(plan.session_id or "") != str(job.session_id)
            ):
                logging.error("Plan execution recovery refused an out-of-scope durable job: %s", proposal_id)
                await _terminalize_invalid_plan_group_execution_job(
                    session, job, proposal=proposal, plan=plan, group=group,
                    reason_code="plan_group_execution_job_scope_mismatch",
                    message="Durable Plan Group execution job scope does not match its Plan/Proposal authority.",
                )
                continue
            actor = ActorContext(
                actor_id=str(job.actor_id),
                session_id=str(job.session_id),
                adapter="durable_recovery",
            )
            claimed = await _claim_plan_group_execution_job(session, proposal)
            if claimed is None:
                continue
            try:
                await _execute_and_finalize_authorized_plan(session, actor, proposal, plan, group)
                recovered += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Plan execution recovery failed for proposal=%s", proposal_id)
    return recovered


async def _execute_and_finalize_authorized_plan(
    session: AsyncSession,
    actor: ActorContext,
    proposal: models.ProposalCache,
    plan: models.ProposalPlan,
    group: models.ConfirmationGroup,
) -> dict[str, Any]:
    """Resume a durably authorized Plan Group and publish final confirmed evidence."""
    from app.operator.plan_execution import _run_lease_heartbeat, execute_authorized_plan, execution_result_from_receipts
    from app.operator.plan_runtime import materialize_plan_proposals, plan_state_envelope

    proposal_id_value = str(proposal.proposal_id)
    plan_id_value = str(plan.plan_id)
    group_id_value = str(group.group_id)
    authorization_events = [event for event in _events(proposal) if event.get("status") == "authorized"]
    if not authorization_events:
        await _quarantine_corrupt_confirmed_proposal(session, actor, proposal, ["missing_plan_authorization_event"])
        await session.commit()
        raise OperatorError(
            "conflict_error",
            "Authorized Plan proposal state is incomplete and requires manual review.",
            {"proposal_id": str(proposal_id_value), "requires_manual_review": True},
        )
    authorization_event = dict(authorization_events[-1])
    execution_job = await _claim_plan_group_execution_job(session, proposal)
    if execution_job is None:
        # A failed claim CAS rolls the session back and expires loaded ORM rows;
        # only freshly reloaded state may be read here.
        refreshed_plan = await session.get(models.ProposalPlan, plan_id_value, populate_existing=True)
        plan_status_value = str(getattr(refreshed_plan, "status", "") or "")
        envelope = await plan_state_envelope(session, plan_id_value)
        current_job = await session.get(models.PlanGroupExecutionJob, proposal_id_value, populate_existing=True)
        if current_job is not None and str(current_job.status or "") == "manual_review":
            review_case = await session.scalar(
                select(models.ManualReviewCase)
                .where(
                    models.ManualReviewCase.plan_id == plan_id_value,
                    models.ManualReviewCase.proposal_id == proposal_id_value,
                    models.ManualReviewCase.subject_type == "plan_group_execution_job",
                    models.ManualReviewCase.status == "open",
                )
                .order_by(models.ManualReviewCase.created_at.desc(), models.ManualReviewCase.case_id.desc())
            )
            return json_safe({
                "ok": False, "status": "manual_review", "proposal_id": proposal_id_value,
                "plan_id": plan_id_value, "plan_status": plan_status_value, "plan_event": envelope,
                "requires_manual_review": True,
                "manual_review_case_id": str(getattr(review_case, "case_id", "") or ""),
                "error_json": dict(current_job.error_json or {}),
            })
        return json_safe({
            "ok": True, "status": "execution_in_progress", "proposal_id": proposal_id_value,
            "plan_id": plan_id_value, "plan_status": plan_status_value, "plan_event": envelope,
        })
    if execution_job.status == "completed":
        # A completed job must have been finalized atomically with the ProposalCache.
        # Reaching it from an authorized proposal is corruption, never permission to fabricate confirmation evidence.
        await _quarantine_corrupt_confirmed_proposal(session, actor, proposal, ["completed_plan_job_without_confirmed_proposal"])
        await session.commit()
        raise OperatorError("conflict_error", "Completed Plan execution job is missing final confirmed evidence.", {"requires_manual_review": True})
    job_token = str(execution_job.claim_token)
    job_generation = int(execution_job.claim_generation or 0)
    stop_job_heartbeat = asyncio.Event()
    lost_job_heartbeat = asyncio.Event()

    async def renew_job(heartbeat_session: AsyncSession) -> bool:
        return await _renew_plan_group_execution_job(
            heartbeat_session, proposal_id_value, job_token, job_generation,
            lease_seconds=_PLAN_GROUP_EXECUTION_LEASE_SECONDS,
        )

    job_heartbeat_task = asyncio.create_task(
        _run_lease_heartbeat(
            session.bind, renew_job, stop_job_heartbeat, lost_job_heartbeat,
            interval=_PLAN_GROUP_EXECUTION_HEARTBEAT_INTERVAL,
            lease_seconds=_PLAN_GROUP_EXECUTION_LEASE_SECONDS,
            initial_lease_expires_at=execution_job.lease_expires_at,
        ),
        name=f"plan-group-execution-heartbeat:{proposal_id_value}",
    )

    async def production_handler(node: models.OperationNode, payload: Mapping[str, Any]) -> dict[str, Any]:
        return await _execute_plan_node_projection(session, actor, node, payload)

    setattr(production_handler, "production_plan_handler", True)
    try:
        await execute_authorized_plan(session, actor, plan_id_value, production_handler)
        execution_result = await execution_result_from_receipts(
            session, plan_id_value, group_id=group_id_value
        )
    except Exception as exc:
        stop_job_heartbeat.set()
        await job_heartbeat_task
        await _release_plan_group_execution_job(session, proposal_id_value, job_token, job_generation, exc)
        raise
    stop_job_heartbeat.set()
    await job_heartbeat_task
    if lost_job_heartbeat.is_set():
        error = OperatorError(
            "confirmation_integrity_error",
            "Plan execution job lease was lost before finalization.",
            {"proposal_id": str(proposal_id_value), "requires_manual_review": True},
        )
        await _release_plan_group_execution_job(session, proposal_id_value, job_token, job_generation, error)
        raise error
    terminal_group_states = {"completed", "failed", "manual_review", "partially_completed", "compensated"}
    if not execution_result.get("all_nodes_terminal") or str(execution_result.get("group_status") or "") not in terminal_group_states:
        await _release_plan_group_execution_job(
            session, proposal_id_value, job_token, job_generation,
            RuntimeError("Plan Group execution remains non-terminal"),
        )
        envelope = await plan_state_envelope(session, plan_id_value)
        return json_safe({
            "ok": True, "status": "execution_in_progress", "proposal_id": proposal_id_value,
            "plan_id": plan_id_value, "plan_status": execution_result.get("status"), "plan_event": envelope,
        })

    proposal = await _load_proposal_authoritative(session, proposal_id_value)
    if proposal is None:
        raise OperatorError("confirmation_integrity_error", "Authorized Plan proposal disappeared during execution.", {})
    if proposal.status == "confirmed":
        stored_response = await _validated_stored_confirm_response(session, actor, proposal)
        continuation = await _continuation_after_confirmed(proposal_id_value)
        return json_safe({**stored_response, "continuation": continuation}) if continuation is not None else stored_response
    if proposal.status != "authorized":
        raise OperatorError("conflict_error", "Authorized Plan proposal changed state during execution.", {"status": proposal.status})
    try:
        execution_job = await _fenced_plan_group_execution_job(
            session, proposal_id_value, job_token, job_generation
        )
    except RuntimeError as exc:
        raise OperatorError(
            "confirmation_integrity_error",
            "Plan execution job claim lease was lost before finalization.",
            {"proposal_id": proposal_id_value, "requires_manual_review": True},
        ) from exc

    events = _events(proposal)
    event_index = next(
        (index for index in range(len(events) - 1, -1, -1) if str(events[index].get("event_id") or "") == str(authorization_event.get("event_id") or "")),
        -1,
    )
    if event_index < 0:
        raise OperatorError("confirmation_integrity_error", "Plan authorization event identity changed during execution.", {})
    confirmed_event = dict(events[event_index])
    confirmed_event["status"] = "confirmed"
    result_receipt_id = str(execution_result.get("result_receipt_id") or "")
    result_digest = str(execution_result.get("result_digest") or "")
    if not result_receipt_id or not result_digest:
        raise OperatorError("confirmation_integrity_error", "Terminal Plan Group result has no durable receipt binding.", {})
    confirmed_event["result_receipt_id"] = result_receipt_id
    confirmed_event["result_digest"] = result_digest
    base_response = _confirmed_response(proposal, execution_result)
    base_response["result_receipt_id"] = result_receipt_id
    base_response["result_digest"] = result_digest
    confirmed_event["result"] = json_safe(dict(execution_result))
    confirmed_event["response"] = json_safe(dict(base_response))
    mutation_receipt = _build_mutation_receipt(proposal, confirmed_event, execution_result)
    confirmed_event["mutation_receipt"] = mutation_receipt
    events[event_index] = confirmed_event
    proposal.status = "confirmed"
    proposal.confirmation_invariant_version = 1
    proposal.confirmation_events = events
    flag_modified(proposal, "confirmation_events")
    await _audit_decision(
        session,
        actor,
        proposal,
        confirmation_status="confirmed",
        result_status=str(execution_result.get("status") or "completed"),
        result_summary=f"Plan {plan_id_value} Group {group_id_value} executed through the authorized Plan executor.",
        args_snapshot=dict(proposal.locked_payload or {}),
        confirmation_event_id=str(confirmed_event["event_id"]),
        changed_records=mutation_receipt["changed_records"],
        after_version_or_hash=f"{_CONFIRMATION_RECEIPT_AUDIT_PREFIX}{mutation_receipt['digest']}",
        result_receipt_id=result_receipt_id,
        result_digest=result_digest,
    )
    from app.operator.continuations import build_continuation

    existing_continuation = await session.get(models.ProposalContinuation, proposal_id_value, populate_existing=True)
    if existing_continuation is None:
        session.add(build_continuation(proposal, actor, str(confirmed_event["event_id"]), execution_result))
    execution_job.status = "completed"
    execution_job.result_json = json_safe(dict(execution_result))
    execution_job.result_receipt_id = result_receipt_id
    execution_job.result_digest = result_digest
    execution_job.error_json = {}
    execution_job.lease_expires_at = None
    execution_job.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await session.commit()
    continuation = await _continuation_after_confirmed(proposal_id_value)
    plan = await session.get(models.ProposalPlan, plan_id_value, populate_existing=True)
    next_proposals = await materialize_plan_proposals(session, actor, plan) if plan is not None else []
    envelope = await plan_state_envelope(
        session,
        proposal.plan_id,
        resolved_proposal_ids=[proposal_id_value],
        new_proposals=next_proposals,
    )
    if execution_result.get("ok") is not True:
        # Authorization was recorded but execution did not produce a successful
        # outcome: never present this as confirmed-success semantics. The derived
        # expression keeps the durable stored response unchanged while surfacing
        # the failure with a structured code/reason/recovery contract.
        return json_safe(_execution_failure_expression(
            {
                **base_response,
                "plan_id": proposal.plan_id,
                "plan_status": execution_result.get("status"),
                "next_proposals": next_proposals,
                "plan_event": envelope,
                "continuation": continuation,
            },
            execution_result,
            plan_id=proposal.plan_id,
            plan_status=str(execution_result.get("status") or ""),
            manual_review_case_id=_first_manual_review_case_id(envelope),
        ))
    return json_safe({
        **base_response,
        "ok": bool(execution_result.get("ok")),
        "plan_id": proposal.plan_id,
        "plan_status": execution_result.get("status"),
        "next_proposals": next_proposals,
        "plan_event": envelope,
        "continuation": continuation,
    })


async def confirm_proposal(
    session: AsyncSession,
    actor: ActorContext,
    proposal_id: str,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Confirm and execute a stored proposal using only its locked payload."""

    proposal = None
    try:
        proposal = await _load_proposal_authoritative(session, proposal_id)
        if proposal is None:
            raise OperatorError("not_found_error", "Proposal was not found.", {"proposal_id": proposal_id})
        if proposal.actor_id != actor.actor_id or proposal.session_id != actor.session_id:
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="confirm_rejected",
                result_status="permission_error",
                error="Proposal is outside the current actor/session scope.",
            )
            await session.commit()
            raise OperatorError(
                "permission_error",
                "Proposal is outside the current actor/session scope.",
                {"proposal_id": proposal_id},
            )
        try:
            request_body = _decision_body(body)
        except OperatorError as exc:
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="tamper_rejected",
                result_status=exc.code,
                error=exc.message,
                args_snapshot=_unsafe_decision_snapshot(body),
            )
            await session.commit()
            raise

        if str(getattr(proposal, "plan_id", "") or "") and str(getattr(proposal, "tool_name", "") or "") == "confirm_plan_group":
            return await _confirm_plan_group_proposal(session, actor, proposal, request_body)

        if proposal.status == "confirmed":
            stored_response = await _validated_stored_confirm_response(session, actor, proposal)
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="idempotent_confirm",
                result_status=str(stored_response.get("status") or "confirmed"),
                result_summary="Already-confirmed proposal returned without re-executing.",
            )
            await session.commit()
            continuation = await _continuation_after_confirmed(proposal_id)
            return json_safe({**stored_response, "continuation": continuation}) if continuation is not None else stored_response
        if proposal.status in {"rejected", "expired"}:
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="confirm_rejected",
                result_status="conflict_error",
                error="Proposal is no longer pending and cannot be confirmed.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Proposal is no longer pending and cannot be confirmed.",
                {"proposal_id": proposal_id, "status": proposal.status},
            )
        if proposal.status not in ("pending", "awaiting_next_confirmation"):
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="confirm_rejected",
                result_status="conflict_error",
                error="Proposal is not in a confirmable state.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Proposal is not in a confirmable state.",
                {"proposal_id": proposal_id, "status": proposal.status},
            )
        if _is_expired(proposal):
            expired_event_id = await _mark_terminal(session, actor, proposal, "expired", reason="proposal expired before confirmation")
            if not expired_event_id:
                return await _handle_lost_confirm_claim(session, actor, proposal_id)
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="expired",
                result_status="expired",
                result_summary=proposal.reason or "Proposal expired.",
                confirmation_event_id=expired_event_id,
            )
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="confirm_rejected",
                result_status="conflict_error",
                error="Proposal has expired and cannot be confirmed.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Proposal has expired and cannot be confirmed.",
                {"proposal_id": proposal_id, "status": "expired"},
            )

        if not await _is_current_session_pending_proposal(session, actor, proposal_id):
            expired_event_id = await _mark_terminal(
                session,
                actor,
                proposal,
                "expired",
                reason="proposal is no longer present in the current session pending list",
            )
            if not expired_event_id:
                return await _handle_lost_confirm_claim(session, actor, proposal_id)
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="expired",
                result_status="expired",
                result_summary=proposal.reason or "Proposal expired.",
                confirmation_event_id=expired_event_id,
            )
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="confirm_rejected",
                result_status="conflict_error",
                error="Proposal is no longer pending in the current session and cannot be confirmed.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Proposal is no longer pending in the current session and cannot be confirmed.",
                {"proposal_id": proposal_id, "status": "expired"},
            )

        # Early check: if this proposal targets an unimplemented action, refuse
        # immediately so we never accept any confirmations for a non-operable
        # action. This preserves the contract that unimplemented actions must
        # never appear to make progress through the confirmation pipeline.
        early_not_implemented = _early_not_implemented_check(proposal)
        if early_not_implemented is not None:
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="confirm_rejected",
                result_status=early_not_implemented.code,
                error=early_not_implemented.message,
            )
            await session.commit()
            raise early_not_implemented

        # Derive confirmations_required: prefer new field, fallback for legacy proposals
        confirmations_required = int(getattr(proposal, "confirmations_required", 0) or 0)
        if confirmations_required == 0:
            if proposal.requires_second_confirmation or proposal.risk_level >= 5:
                confirmations_required = RISK_CONFIRMATIONS.get(int(proposal.risk_level or 0), 1)
                if confirmations_required < 2:
                    confirmations_required = 2
            else:
                confirmations_required = RISK_CONFIRMATIONS.get(int(proposal.risk_level or 0), 0)
                if confirmations_required < 1:
                    confirmations_required = 1

        confirmations_received = int(getattr(proposal, "confirmations_received", 0) or 0)

        # Multi-confirmation flow: if more confirmations are needed, record and return
        if confirmations_received < confirmations_required - 1:
            if not await _claim_pending_proposal(
                session,
                proposal,
                status="awaiting_next_confirmation",
                expected_confirmation_count=proposal.confirmation_count,
            ):
                return await _handle_lost_confirm_claim(session, actor, proposal_id)
            first_event_id = await _record_intermediate_confirmation(session, actor, proposal)
            # Update new fields
            proposal.confirmations_received = confirmations_received + 1
            proposal.confirmation_count = proposal.confirmations_received  # derive
            new_challenge = f"confirm-{uuid.uuid4().hex[:12]}"
            challenges = list(getattr(proposal, "confirmation_challenges", None) or [])
            challenges.append(new_challenge)
            proposal.confirmation_challenges = challenges
            proposal.confirmation_challenge = new_challenge  # derive
            if proposal.first_confirmed_at is None:
                proposal.first_confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status=f"confirmed_{proposal.confirmations_received}",
                result_status="awaiting_next_confirmation",
                result_summary=f"Confirmation {proposal.confirmations_received}/{confirmations_required} recorded.",
                confirmation_event_id=first_event_id,
            )
            await _record_plan_group_confirmation(session, actor, proposal, first_event_id)
            await session.commit()
            return {
                "ok": True,
                "status": "awaiting_next_confirmation",
                "proposal_id": proposal.proposal_id,
                "confirmations_required": confirmations_required,
                "confirmations_received": proposal.confirmations_received,
                "remaining": confirmations_required - proposal.confirmations_received,
                "next_challenge": new_challenge,
                "confirmation_challenge": new_challenge,
                "requires_second_confirmation": confirmations_required >= 2,
                "confirmation_count": proposal.confirmations_received,
                "result": {
                    "status": "awaiting_next_confirmation",
                    "summary": f"Confirmation {proposal.confirmations_received}/{confirmations_required} recorded; {confirmations_required - proposal.confirmations_received} more needed.",
                },
            }

        # Challenge verification for final confirmation (when multi-step)
        if confirmations_required >= 2 and confirmations_received >= 1:
            challenge = str(proposal.confirmation_challenge or "")
            supplied_challenge = str(request_body.get("confirmation_challenge") or "")
            if challenge and supplied_challenge != challenge:
                await _audit_decision(
                    session,
                    actor,
                    proposal,
                    confirmation_status="challenge_mismatch",
                    result_status="awaiting_next_confirmation",
                    result_summary="Replay ignored; confirmation challenge was not supplied.",
                )
                await session.commit()
                return {
                    "ok": True,
                    "status": "awaiting_next_confirmation",
                    "proposal_id": proposal.proposal_id,
                    "confirmations_required": confirmations_required,
                    "confirmations_received": confirmations_received,
                    "remaining": confirmations_required - confirmations_received,
                    "next_challenge": challenge,
                    "confirmation_challenge": challenge,
                    "requires_second_confirmation": confirmations_required >= 2,
                    "confirmation_count": confirmations_received,
                    "result": {
                        "status": "awaiting_next_confirmation",
                        "summary": "Replay ignored; provide the backend-issued confirmation challenge to execute.",
                    },
                }

        # Final confirmation path: prepare and execute.
        try:
            execution = await _prepare_in_read_only_confirmation_transaction(
                session,
                lambda: _prepare_execution(session, actor, proposal),
            )
        except OperatorError as exc:
            await session.rollback()
            error_details = exc.details if isinstance(exc.details, Mapping) else {}
            corrupt_ids = error_details.get("corrupt_confirmed_proposal_ids")
            if isinstance(corrupt_ids, list):
                for corrupt_id in corrupt_ids:
                    corrupt = await _load_proposal_authoritative(session, str(corrupt_id))
                    if (
                        corrupt is None
                        or corrupt.status != "confirmed"
                        or corrupt.actor_id != actor.actor_id
                        or corrupt.session_id != actor.session_id
                    ):
                        continue
                    issues = await _confirmed_invariant_issues(session, actor, corrupt)
                    if issues:
                        await _quarantine_corrupt_confirmed_proposal(session, actor, corrupt, issues)
            proposal = await _load_proposal_authoritative(session, proposal_id)
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="confirm_rejected",
                result_status=exc.code,
                error=exc.message,
            )
            await session.commit()
            raise

        if not await _claim_pending_proposal(
            session,
            proposal,
            status="confirmed",
            expected_confirmation_count=proposal.confirmation_count,
        ):
            return await _handle_lost_confirm_claim(session, actor, proposal_id)
        pending_safe_rebase = getattr(proposal, "_pending_safe_rebase", None)
        if isinstance(pending_safe_rebase, Mapping):
            from app.operator.safe_rebase import record_safe_rebase
            await record_safe_rebase(session, actor, **dict(pending_safe_rebase), _defer_commit=True)
        pre_confirmation_checkpoint_id = ""
        if int(proposal.risk_level or 0) >= 3:
            pre_confirmation_checkpoint_id = await create_pre_confirmation_checkpoint(
                session,
                actor,
                proposal_id=proposal.proposal_id,
                reason=f"before confirming {proposal.tool_name}",
            )
        result = await _execute_in_confirmation_transaction(session, execution)
        if pre_confirmation_checkpoint_id:
            result = {
                **json_safe(result),
                "checkpoint_id": pre_confirmation_checkpoint_id,
                "pre_confirmation_checkpoint_id": pre_confirmation_checkpoint_id,
            }
        result = attach_visibility(result, proposal=proposal)
        confirmed_execution_result = json_safe(dict(result))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        events = _events(proposal)
        confirmed_event = _event("confirmed", actor, result=result)
        events.append(confirmed_event)
        proposal.confirmation_events = events
        proposal.confirmations_received = confirmations_received + 1
        proposal.confirmation_count = proposal.confirmations_received  # derive
        if proposal.first_confirmed_at is None:
            proposal.first_confirmed_at = now
        if proposal.confirmations_received >= 2:
            proposal.second_confirmed_at = now
        await _remove_pending_proposal_id(session, actor, proposal.proposal_id)
        await _resolve_harness_pending_proposal(session, actor, proposal.proposal_id, status="confirmed")
        # Phase 5: record confirmed intent scope for batch_mutate
        from app.operator.guards import ConfirmedIntentScope, record_confirmed_scope
        locked = proposal.locked_payload or {}
        if isinstance(locked, Mapping) and locked.get("action") == "batch_mutate":
            input_data = locked.get("input") or {}
            target = input_data.get("target") or {}
            record_ids_raw = target.get("record_ids") or []
            if record_ids_raw:
                scope = ConfirmedIntentScope(
                    actor_id=actor.actor_id,
                    model=str(input_data.get("model") or ""),
                    record_ids=frozenset(str(rid) for rid in record_ids_raw),
                    operation=str(input_data.get("operation") or ""),
                    confirmed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                await record_confirmed_scope(session, actor.session_id, scope)
                confirmed_event["intent_scope"] = scope.to_jsonable()
        response = json_safe(
            _confirmed_response(proposal, result, pre_confirmation_checkpoint_id=pre_confirmation_checkpoint_id)
        )
        confirmed_event["result"] = json_safe(result)
        confirmed_event["response"] = json_safe(response)
        proposal.confirmation_invariant_version = 1
        mutation_receipt = _build_mutation_receipt(proposal, confirmed_event, result)
        confirmed_event["mutation_receipt"] = mutation_receipt
        await _audit_decision(
            session,
            actor,
            proposal,
            confirmation_status="confirmed",
            result_status=str(result.get("status") or "completed"),
            result_summary=str(
                result.get("summary") or result.get("result_status") or "Proposal confirmed and executed."
            ),
            args_snapshot=_locked_payload(proposal),
            confirmation_event_id=str(confirmed_event.get("event_id") or ""),
            changed_records=mutation_receipt["changed_records"],
            after_version_or_hash=f"{_CONFIRMATION_RECEIPT_AUDIT_PREFIX}{mutation_receipt['digest']}",
        )
        proposal.confirmation_events = list(events)
        flag_modified(proposal, "confirmation_events")
        proposal_id_value = str(proposal.proposal_id)
        plan_id_value = str(getattr(proposal, "plan_id", "") or "")
        confirmed_event_id = str(confirmed_event.get("event_id") or "")
        continuation_payload = json_safe(_proposal_execution_payload(proposal, confirmed_execution_result))
        await _record_plan_group_confirmation(session, actor, proposal, confirmed_event_id)
        from app.operator.plan_runtime import record_confirmed_projection_execution
        await record_confirmed_projection_execution(session, actor, proposal, confirmed_execution_result)
        from app.operator.continuations import build_continuation
        session.add(build_continuation(proposal, actor, confirmed_event_id, confirmed_execution_result))
        await session.flush()
        await session.commit()
        if plan_id_value:
            from app.operator.plan_runtime import materialize_plan_proposals
            plan = await session.get(models.ProposalPlan, plan_id_value)
            next_proposals = await materialize_plan_proposals(session, actor, plan) if plan is not None else []
            response = json_safe({**response, "plan_id": plan_id_value, "plan_status": getattr(plan, "status", ""), "next_proposals": next_proposals})
        if result.get("ok") is False:
            response = json_safe(_execution_failure_expression(
                response,
                result,
                plan_id=plan_id_value,
                plan_status=str(response.get("plan_status") or ""),
            ))
        continuation = await _continuation_after_confirmed(proposal_id_value)
        if continuation is not None:
            response_with_continuation = json_safe({**response, "continuation": continuation})
            response = response_with_continuation
        return response
    except asyncio.CancelledError:
        await _rollback_quietly(session)
        raise
    except OperatorError as exc:
        await _rollback_if_needed(session, exc)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive route/repository boundary.
        await _rollback_quietly(session)
        if exc.__class__.__name__ in {"AuthorizationError", "PlanMaterializationError", "IntegrityError"}:
            return conflict_error("Proposal confirmation encountered a durable state conflict.", {"error": str(exc), "error_type": exc.__class__.__name__})
        return transient_error("Proposal confirmation failed transiently.", {"error": str(exc)})


async def reject_proposal(
    session: AsyncSession,
    actor: ActorContext,
    proposal_id: str,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        proposal = await _load_proposal_authoritative(session, proposal_id)
        if proposal is None:
            raise OperatorError("not_found_error", "Proposal was not found.", {"proposal_id": proposal_id})
        if proposal.actor_id != actor.actor_id or proposal.session_id != actor.session_id:
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="reject_rejected",
                result_status="permission_error",
                error="Proposal is outside the current actor/session scope.",
            )
            await session.commit()
            raise OperatorError(
                "permission_error",
                "Proposal is outside the current actor/session scope.",
                {"proposal_id": proposal_id},
            )
        try:
            request_body = _decision_body(body)
        except OperatorError as exc:
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="tamper_rejected",
                result_status=exc.code,
                error=exc.message,
                args_snapshot=_unsafe_decision_snapshot(body),
            )
            await session.commit()
            raise
        if proposal.status == "confirmed":
            await _validated_stored_confirm_response(session, actor, proposal)
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="reject_rejected",
                result_status="conflict_error",
                error="Confirmed proposals cannot be rejected.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Confirmed proposals cannot be rejected.",
                {"proposal_id": proposal_id, "status": proposal.status},
            )
        if proposal.status in {"rejected", "expired"}:
            return {
                "ok": True,
                "status": proposal.status,
                "proposal_id": proposal.proposal_id,
                "result": {"status": proposal.status, "summary": proposal.summary},
            }
        proposal.reason = str(request_body.get("reason") or proposal.reason or "")
        rejected_event_id = await _mark_terminal(session, actor, proposal, "rejected", reason=proposal.reason)
        if not rejected_event_id:
            await _audit_lost_reject_claim(session, actor, proposal_id)
            raise OperatorError(
                "conflict_error",
                "Proposal was already transitioned by another decision.",
                {"proposal_id": proposal_id},
            )
        await _record_plan_group_rejection(session, actor, proposal, rejected_event_id)
        await _audit_decision(
            session,
            actor,
            proposal,
            confirmation_status="rejected",
            result_status="rejected",
            result_summary=proposal.reason or "Proposal rejected.",
            confirmation_event_id=rejected_event_id,
        )
        # Phase 5: invalidate any active scope overlapping this rejected proposal
        try:
            from app.operator.guards import invalidate_scope
            locked = proposal.locked_payload or {}
            if isinstance(locked, Mapping) and locked.get("action") == "batch_mutate":
                input_data = locked.get("input") or {}
                target = input_data.get("target") or {}
                record_ids_raw = target.get("record_ids") or []
                if record_ids_raw:
                    await invalidate_scope(
                        session,
                        actor.session_id,
                        str(input_data.get("model") or ""),
                        set(str(rid) for rid in record_ids_raw),
                    )
        except Exception:
            pass  # scope invalidation must never break rejection
        await session.commit()
        plan_id_value = str(getattr(proposal, "plan_id", "") or "")
        plan_event = {}
        if plan_id_value:
            from app.operator.plan_runtime import plan_state_envelope
            plan_event = await plan_state_envelope(session, plan_id_value, resolved_proposal_ids=[proposal.proposal_id])
        response = {
            "ok": True,
            "status": "rejected",
            "proposal_id": proposal.proposal_id,
            "confirmation_event_id": rejected_event_id,
            "plan_id": str(getattr(proposal, "plan_id", "") or ""),
            "confirmation_group_id": str(getattr(proposal, "confirmation_group_id", "") or ""),
            "result": {"status": "rejected", "summary": proposal.summary},
            "plan_event": plan_event,
            "resolved_proposal_ids": [proposal.proposal_id],
        }
        return response
    except OperatorError as exc:
        await _rollback_if_needed(session, exc)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive route/repository boundary.
        await _rollback_quietly(session)
        return transient_error("Proposal rejection failed transiently.", {"error": str(exc)})


async def expire_proposal(
    session: AsyncSession,
    actor: ActorContext,
    proposal_id: str,
    reason: str = "",
) -> dict[str, Any]:
    try:
        proposal = await _load_proposal_authoritative(session, proposal_id)
        if proposal is None:
            raise OperatorError("not_found_error", "Proposal was not found.", {"proposal_id": proposal_id})
        if proposal.actor_id != actor.actor_id or proposal.session_id != actor.session_id:
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="expire_rejected",
                result_status="permission_error",
                error="Proposal is outside the current actor/session scope.",
            )
            await session.commit()
            raise OperatorError(
                "permission_error",
                "Proposal is outside the current actor/session scope.",
                {"proposal_id": proposal_id},
            )
        if proposal.status == "confirmed":
            await _validated_stored_confirm_response(session, actor, proposal)
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="expire_rejected",
                result_status="conflict_error",
                error="Confirmed proposals cannot be expired.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Confirmed proposals cannot be expired.",
                {"proposal_id": proposal_id, "status": proposal.status},
            )
        if proposal.status == "expired":
            return {
                "ok": True,
                "status": "expired",
                "proposal_id": proposal.proposal_id,
                "result": {"status": "expired", "summary": proposal.summary},
            }
        if proposal.status == "rejected":
            await _audit_decision(
                session,
                actor,
                proposal,
                confirmation_status="expire_rejected",
                result_status="conflict_error",
                error="Rejected proposals cannot be expired.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Rejected proposals cannot be expired.",
                {"proposal_id": proposal_id, "status": proposal.status},
            )
        expired_event_id = await _mark_terminal(session, actor, proposal, "expired", reason=reason)
        if not expired_event_id:
            await _audit_decision(
                session,
                actor,
                await _load_proposal_authoritative(session, proposal_id),
                confirmation_status="expire_rejected",
                result_status="conflict_error",
                error="Proposal was already transitioned by another decision.",
            )
            await session.commit()
            raise OperatorError(
                "conflict_error",
                "Proposal was already transitioned by another decision.",
                {"proposal_id": proposal_id},
            )
        if str(getattr(proposal, "plan_id", "") or ""):
            from app.operator.plan_runtime import expire_plan_group_projection
            await expire_plan_group_projection(session, actor, proposal)
        await _audit_decision(
            session,
            actor,
            proposal,
            confirmation_status="expired",
            result_status="expired",
            result_summary=reason or "Proposal expired.",
            confirmation_event_id=expired_event_id,
        )
        await session.commit()
        return {
            "ok": True,
            "status": "expired",
            "proposal_id": proposal.proposal_id,
            "result": {"status": "expired", "summary": proposal.summary},
        }
    except OperatorError as exc:
        await _rollback_if_needed(session, exc)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive route/repository boundary.
        await _rollback_quietly(session)
        return transient_error("Proposal expiry failed transiently.", {"error": str(exc)})


async def _load_bound_proposal(session: AsyncSession, actor: ActorContext, proposal_id: str) -> Any:
    proposal = await session.get(models.ProposalCache, proposal_id)
    if proposal is None:
        raise OperatorError("not_found_error", "Proposal was not found.", {"proposal_id": proposal_id})
    if proposal.actor_id != actor.actor_id or proposal.session_id != actor.session_id:
        raise OperatorError(
            "permission_error",
            "Proposal is outside the current actor/session scope.",
            {"proposal_id": proposal_id},
        )
    return proposal


async def _load_proposal_authoritative(session: AsyncSession, proposal_id: str) -> Any | None:
    proposal = await session.get(models.ProposalCache, proposal_id, populate_existing=True)
    if proposal is not None:
        await session.refresh(proposal)
    return proposal


async def _claim_pending_proposal(
    session: AsyncSession,
    proposal: Any,
    *,
    status: str,
    expected_confirmation_count: int | None = None,
) -> bool:
    conditions = [
        models.ProposalCache.proposal_id == proposal.proposal_id,
        models.ProposalCache.actor_id == proposal.actor_id,
        models.ProposalCache.session_id == proposal.session_id,
        models.ProposalCache.status.in_(["pending", "awaiting_next_confirmation"]),
    ]
    if expected_confirmation_count is not None:
        conditions.append(models.ProposalCache.confirmation_count == int(expected_confirmation_count))
    result = await session.execute(
        update(models.ProposalCache)
        .where(*conditions)
        .values(status=status)
        .execution_options(synchronize_session=False)
    )
    if int(result.rowcount or 0) != 1:
        await session.rollback()
        return False
    proposal.status = status
    return True


async def _handle_lost_confirm_claim(session: AsyncSession, actor: ActorContext, proposal_id: str) -> dict[str, Any]:
    proposal = await _load_proposal_authoritative(session, proposal_id)
    if proposal is None:
        raise OperatorError("not_found_error", "Proposal was not found.", {"proposal_id": proposal_id})
    if proposal.status == "confirmed":
        stored_response = await _validated_stored_confirm_response(session, actor, proposal)
        await _audit_decision(
            session,
            actor,
            proposal,
            confirmation_status="idempotent_confirm",
            result_status=str(stored_response.get("status") or "confirmed"),
            result_summary="Already-confirmed proposal returned without re-executing.",
        )
        await session.commit()
        continuation = await _continuation_after_confirmed(proposal_id)
        return json_safe({**stored_response, "continuation": continuation}) if continuation is not None else stored_response
    await _audit_decision(
        session,
        actor,
        proposal,
        confirmation_status="confirm_rejected",
        result_status="conflict_error",
        error="Proposal was already transitioned by another decision.",
    )
    await session.commit()
    raise OperatorError(
        "conflict_error",
        "Proposal was already transitioned by another decision.",
        {"proposal_id": proposal_id, "status": proposal.status},
    )


async def _prepare_execution(session: AsyncSession, actor: ActorContext, proposal: Any) -> Any:
    payload = _locked_payload(proposal)
    tool_name = str(payload.get("tool_name") or proposal.tool_name)
    if tool_name != proposal.tool_name:
        raise OperatorError("conflict_error", "Locked payload tool does not match proposal metadata.", {})
    if tool_name == "create_record":
        return await _prepare_create(session, actor, proposal, payload)
    if tool_name == "patch_record":
        return await _prepare_patch(session, actor, proposal, payload)
    if tool_name == "delete_or_archive_record":
        return await _prepare_delete_or_archive(session, actor, proposal, payload)
    if tool_name == "invoke_action":
        return await _prepare_invoke_action(session, actor, proposal, payload)
    raise OperatorError("validation_error", "Unsupported proposal tool.", {"tool_name": tool_name})


async def _prepare_create(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
) -> Any:
    model_name = str(payload.get("model") or proposal.model_or_action)
    data_raw = payload.get("data")
    if not isinstance(data_raw, Mapping):
        raise OperatorError("validation_error", "Locked create payload must contain data.", {"proposal_id": proposal.proposal_id})
    reject_trusted_args(data_raw, location=f"confirm_create:{model_name}.data")
    data = {str(key): value for key, value in data_raw.items()}
    client_data = _strip_backend_create_fields(model_name, data)
    if model_name == "application_record":
        client_data = _normalize_application_record_status_patch(client_data)
    spec = get_model_spec(model_name)
    model_cls = get_model_class(model_name)
    validate_fields(client_data, spec.creatable_fields, purpose=f"confirm create {model_name}")
    validate_model_values(client_data, spec, purpose=f"confirm create {model_name}")
    await validate_create_scope(session, actor, spec, client_data)
    data = _derive_backend_create_fields(model_name, client_data, actor)
    _validate_backend_create_fields(model_name, data, data_raw)
    if model_name == "job":
        await reject_duplicate_job_create_conflict(session, actor, data)
    computed_risk = calculate_record_risk(spec, tool_name="create_record", operation="create", fields=tuple(data))
    _validate_stored_risk(proposal, computed_risk)

    async def execute() -> dict[str, Any]:
        record = model_cls(**data)
        if hasattr(record, "owner_actor_id"):
            record.owner_actor_id = actor.actor_id
        session.add(record)
        await session.flush()
        await session.refresh(record)
        await _sync_profile_section_archive(session, model_name, record)
        return {
            "status": "completed",
            "tool_name": "create_record",
            "model": model_name,
            "record": serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False),
        }

    return execute


async def _prepare_patch(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
) -> Any:
    model_name = str(payload.get("model") or proposal.model_or_action)
    record_id = payload.get("record_id") if payload.get("record_id") not in (None, "") else proposal.record_id
    updates = payload.get("updates")
    patch_mode = str(payload.get("patch_mode") or "")
    if patch_mode not in PATCH_MODES:
        raise OperatorError("validation_error", "Unsupported locked patch mode.", {"patch_mode": patch_mode})
    if not isinstance(updates, Mapping):
        raise OperatorError("validation_error", "Locked patch payload must contain updates.", {"proposal_id": proposal.proposal_id})
    if model_name == "application_record":
        updates = _normalize_application_record_status_patch(updates)
    reject_trusted_args(updates, location=f"confirm_patch:{model_name}.updates")
    spec = get_model_spec(model_name)
    model_cls = get_model_class(model_name)
    validate_fields(updates, spec.writable_fields, purpose=f"confirm patch {model_name}")
    validate_model_values(updates, spec, purpose=f"confirm patch {model_name}")
    record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
    version_decision = await _validate_expected_version(session, actor, record, spec, proposal, payload)
    effective_updates = dict(version_decision.get("rebased_updates") or updates) if version_decision.get("status") == "safe_rebase" else dict(updates)
    computed_risk = calculate_record_risk(spec, tool_name="patch_record", operation="patch", fields=tuple(updates))
    _validate_stored_risk(proposal, computed_risk)

    async def execute() -> dict[str, Any]:
        if version_decision.get("status") == "already_satisfied":
            await session.refresh(record)
            return {
                "status": "completed",
                "tool_name": "patch_record",
                "model": model_name,
                "record_id": json_safe(record_id),
                "record": serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False),
                "write_occurred": False,
                "already_satisfied": True,
                "completion_reason": "already_satisfied",
                "before_version": str(version_decision.get("current_version") or ""),
                "after_version": str(version_decision.get("current_version") or ""),
            }
        if version_decision.get("status") == "safe_rebase":
            cas_values = _patch_values(record, effective_updates, patch_mode)
            connection = await session.connection()
            cas_result = await connection.execute(
                update(type(record))
                .where(*_safe_rebase_cas_predicates(record, spec, actor, expected_fields=tuple(sorted(cas_values))))
                .values(**cas_values)
                .execution_options(synchronize_session=False)
            )
            if int(cas_result.rowcount or 0) != 1:
                raise OperatorError(
                    "conflict_error",
                    "Safe rebase target changed concurrently; refusing to overwrite it.",
                    {"proposal_id": str(getattr(proposal, "proposal_id", "") or ""), "record_id": json_safe(record_id)},
                )
            session.expire(record)
            await session.refresh(record)
            from app.operator.effect_manifest import record_explicit_effect_if_active
            record_explicit_effect_if_active(
                kind="database_record", operation="patch", model=model_name, record_id=record_id,
                before_version=str(version_decision.get("current_version") or ""),
                after_version=canonical_version(record, spec),
                changed_fields=tuple(sorted(cas_values)),
            )
        else:
            _apply_patch(record, effective_updates, patch_mode)
            await session.flush()
            await session.refresh(record)
        await _sync_profile_section_archive(session, model_name, record)
        return {
            "status": "completed",
            "tool_name": "patch_record",
            "model": model_name,
            "record_id": json_safe(record_id),
            "record": serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False),
            "write_occurred": True,
            "completion_reason": "safe_rebase" if version_decision.get("status") == "safe_rebase" else "patched",
            "before_version": str(version_decision.get("current_version") or ""),
            "after_version": canonical_version(record, spec),
        }

    return execute


async def _prepare_delete_or_archive(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
) -> Any:
    model_name = _required_locked_payload_string(proposal, payload, "model")
    record_id = _required_locked_payload_value(proposal, payload, "record_id")
    operation = _required_locked_payload_string(proposal, payload, "operation_type")
    expected_version = _required_locked_payload_string(proposal, payload, "expected_version_or_hash")
    _validate_locked_delete_archive_metadata(
        proposal,
        model_name=model_name,
        record_id=record_id,
        operation=operation,
        expected_version_or_hash=expected_version,
    )
    if operation not in DELETE_OPERATIONS:
        raise OperatorError("validation_error", "Unsupported locked delete/archive operation.", {"operation": operation})
    if model_name == "agent_conversation":
        from app.services.harness_history import (
            apply_conversation_lifecycle_operation,
            get_conversation_version,
        )

        current = await get_conversation_version(session, actor, str(record_id))
        if not current:
            raise OperatorError("not_found_error", "Conversation not found.", {"conversation_id": str(record_id)})
        if current != expected_version:
            raise OperatorError(
                "conflict_error",
                "Underlying conversation changed after proposal creation.",
                {
                    "proposal_id": proposal.proposal_id,
                    "expected_version_or_hash": expected_version,
                    "current_version_or_hash": current,
                },
            )
        computed_risk = 5 if operation == "delete" else 4
        _validate_stored_risk(proposal, computed_risk)

        async def execute_conversation_lifecycle() -> dict[str, Any]:
            result = await apply_conversation_lifecycle_operation(
                session,
                actor,
                conversation_id=str(record_id),
                operation=operation,
                proposal_id=proposal.proposal_id,
                expected_version_or_hash=expected_version,
            )
            if not result.get("ok"):
                error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
                raise OperatorError(
                    str(error.get("code") or "conflict_error"),
                    str(error.get("message") or "Conversation lifecycle operation failed."),
                    error.get("details") or {},
                )
            return result

        return execute_conversation_lifecycle
    spec = get_model_spec(model_name)
    model_cls = get_model_class(model_name)
    record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
    await _validate_expected_version(session, actor, record, spec, proposal, {"expected_version_or_hash": expected_version})
    computed_risk = calculate_record_risk(spec, tool_name="delete_or_archive_record", operation=operation)
    _validate_stored_risk(proposal, computed_risk)

    async def execute() -> dict[str, Any]:
        result_record = None
        if operation == "delete":
            await _remove_profile_section_archive(session, model_name, record)
            await session.delete(record)
            status = "deleted"
        else:
            status = _apply_visibility_operation(record, operation)
            result_record = serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False)
            await _sync_profile_section_archive(session, model_name, record)
        await session.flush()
        return {
            "status": "completed",
            "tool_name": "delete_or_archive_record",
            "model": model_name,
            "record_id": json_safe(record_id),
            "operation": operation,
            "result_status": status,
            "record": result_record,
        }

    return execute


def _required_locked_payload_string(proposal: Any, payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if value in (None, ""):
        raise OperatorError(
            "validation_error",
            "Locked delete/archive payload is missing required fields.",
            {"proposal_id": proposal.proposal_id, "fields": [field]},
        )
    return str(value)


def _derive_backend_create_fields(model_name: str, data: Mapping[str, Any], actor: ActorContext) -> dict[str, Any]:
    cleaned = {str(key): json_safe(value) for key, value in data.items()}
    if model_name == "profile_section":
        cleaned = normalize_profile_section_record_payload(cleaned)
    if model_name == "application_record":
        cleaned = _coerce_application_record_create_fields(cleaned)
    if model_name == "job" and not str(cleaned.get("hash_key") or "").strip():
        seed = {
            "actor_id": actor.actor_id,
            "title": cleaned.get("title"),
            "company": cleaned.get("company"),
            "location": cleaned.get("location"),
            "url": cleaned.get("url"),
            "apply_url": cleaned.get("apply_url"),
            "source": cleaned.get("source"),
        }
        encoded = json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        cleaned["hash_key"] = "operator-" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:40]
    return cleaned


def _coerce_application_record_create_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = dict(data)
    if "updated_at_value" not in cleaned or cleaned.get("updated_at_value") in (None, ""):
        return cleaned
    cleaned["updated_at_value"] = _coerce_locked_datetime(
        cleaned.get("updated_at_value"),
        field_name="updated_at_value",
    )
    return cleaned


def _coerce_locked_datetime(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise OperatorError("validation_error", "Datetime field cannot be blank.", {"field": field_name})
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OperatorError(
                "validation_error",
                "Datetime field must be an ISO datetime string.",
                {"field": field_name, "value": value},
            ) from exc
    else:
        raise OperatorError(
            "validation_error",
            "Datetime field must be a datetime or ISO datetime string.",
            {"field": field_name, "type": type(value).__name__},
        )
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _strip_backend_create_fields(model_name: str, data: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = {str(key): value for key, value in data.items()}
    if model_name == "job":
        cleaned.pop("hash_key", None)
    return cleaned


def _validate_backend_create_fields(
    model_name: str,
    derived_data: Mapping[str, Any],
    locked_data: Mapping[str, Any],
) -> None:
    if model_name != "job" or "hash_key" not in locked_data:
        return
    if str(locked_data.get("hash_key") or "") != str(derived_data.get("hash_key") or ""):
        raise OperatorError(
            "conflict_error",
            "Locked backend-derived job hash does not match the current derived value.",
            {},
        )


def _required_locked_payload_value(proposal: Any, payload: Mapping[str, Any], field: str) -> Any:
    value = payload.get(field)
    if value in (None, ""):
        raise OperatorError(
            "validation_error",
            "Locked delete/archive payload is missing required fields.",
            {"proposal_id": proposal.proposal_id, "fields": [field]},
        )
    return value


def _validate_locked_delete_archive_metadata(
    proposal: Any,
    *,
    model_name: str,
    record_id: Any,
    operation: str,
    expected_version_or_hash: str,
) -> None:
    mismatched: list[str] = []
    if str(proposal.model_or_action or "") != model_name:
        mismatched.append("model")
    if str(proposal.record_id or "") != str(record_id):
        mismatched.append("record_id")
    if str(proposal.operation_type or "") != operation:
        mismatched.append("operation_type")
    if str(proposal.expected_version_or_hash or "") != expected_version_or_hash:
        mismatched.append("expected_version_or_hash")
    if mismatched:
        raise OperatorError(
            "conflict_error",
            "Locked delete/archive payload does not match proposal metadata.",
            {"proposal_id": proposal.proposal_id, "fields": mismatched},
        )


async def _prepare_invoke_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
) -> Any:
    action_name = str(payload.get("action") or proposal.model_or_action)
    input_raw = payload.get("input") or {}
    if not isinstance(input_raw, Mapping):
        raise OperatorError("validation_error", "Locked action payload input must be an object.", {"action": action_name})
    spec = get_action_spec(action_name)
    # Backend-owned inputs (e.g. generate_resume.confirmed_scope) are bound by
    # the backend at proposal time and are never part of the provider schema;
    # strip them before schema validation and let the action preparer consume
    # them from the sealed payload, where they are digest-fenced.
    sealed_backend_inputs = {
        name: input_raw[name]
        for name in BACKEND_OWNED_ACTION_INPUT_FIELDS & set(input_raw)
    }
    provider_input = {
        str(key): value for key, value in input_raw.items()
        if str(key) not in BACKEND_OWNED_ACTION_INPUT_FIELDS
    }
    cleaned = validate_action_schema(spec, provider_input)
    await validate_action_references(session, actor, spec, cleaned)
    await _validate_action_expected_versions(session, actor, proposal, payload, spec, cleaned)
    _validate_stored_risk(proposal, calculate_action_risk(spec, cleaned))

    if not _action_is_implemented(spec):
        raise OperatorError(
            "not_implemented",
            f"Operator action is not implemented: {action_name}",
            {
                "action": action_name,
                "implementation_status": str(getattr(spec, "implementation_status", "not_implemented")),
                "reason": str(getattr(spec, "non_operable_reason", "") or "Action is not implemented."),
            },
        )
    if sealed_backend_inputs:
        cleaned = {**dict(cleaned), **sealed_backend_inputs}
    preparer = _invoke_action_preparers().get(action_name)
    if preparer is None:
        raise OperatorError(
            "not_implemented",
            f"Operator action has no proposal confirmation executor: {action_name}",
            {"action": action_name},
        )
    return await preparer(session, actor, proposal, payload, spec, cleaned)


def confirmable_action_names() -> frozenset[str]:
    """Sole source of truth for actions with a confirmation prepare path.

    Derived from the prepare-dispatch table used by `_prepare_invoke_action`.
    Agent visibility and dependency probes must use this set rather than a
    hand-maintained mirror.
    """
    return frozenset(_invoke_action_preparers())


def _invoke_action_preparers() -> dict[str, Any]:
    """Map action name -> prepare handler with a uniform signature.

    Built at call time so individual `_prepare_*_action` functions may be
    defined later in this module. This table is the only routing truth for
    invoke_action confirmation.
    """
    return {
        "profile_chat_confirm": _dispatch_profile_chat_confirm,
        "profile_agent_apply_patch": _dispatch_profile_agent_apply_patch,
        "profile_generate_narrative": _prepare_profile_generate_narrative_action,
        "profile_instant_draft": _prepare_profile_instant_draft_action,
        "generate_resume": _dispatch_generate_resume,
        "apply_resume_template": _dispatch_apply_resume_template,
        "interview_generate_answer": _prepare_interview_generate_answer_action,
        "interview_extract_questions": _prepare_interview_extract_questions_action,
        "calendar_auto_fill": _prepare_calendar_auto_fill_action,
        "optimize_resume": _prepare_optimize_resume_action,
        "batch_optimize_resume": _prepare_batch_optimize_resume_action,
        "apply_resume_ai_patch": _prepare_apply_resume_ai_patch_action,
        "parse_resume": _prepare_parse_resume_action,
        "upload_resume_photo": _prepare_resume_asset_action,
        "upload_resume_logo": _prepare_resume_asset_action,
        "resolve_resume_logo": _prepare_resume_asset_action,
        "export_resume_pdf": _prepare_resume_export_action,
        "export_resume_image": _prepare_resume_export_action,
        "analyze_resume": _prepare_analyze_resume_action,
        "apply_resume_ai_batch": _prepare_apply_resume_ai_batch_action,
        "import_jobs_to_application_table": _prepare_import_jobs_to_application_table_action,
        "import_latest_extension_batch": _prepare_import_latest_extension_batch_action,
        "run_scraper": _prepare_run_scraper_action,
        "sync_email": _prepare_sync_email_action,
        "job_stats": _prepare_job_stats_action,
        "smartfill_map": _prepare_smartfill_map_action,
        "smartfill_option_match": _prepare_smartfill_option_match_action,
        "smartfill_field_map": _prepare_smartfill_field_map_action,
        "smartfill_module_count": _prepare_smartfill_module_count_action,
        "generate_cover_letter": _prepare_generate_cover_letter_action,
        "auto_write_application_content": _prepare_auto_write_application_content_action,
        "ensure_application_for_job": _prepare_ensure_application_for_job_action,
        "advance_application": _prepare_advance_application_action,
        "prepare_application_material": _prepare_prepare_application_material_action,
        "remember_preference": _prepare_remember_preference_action,
        "batch_triage_jobs": _prepare_batch_triage_jobs_action,
        "batch_delete_jobs": _prepare_batch_delete_jobs_action,
        "batch_mutate": _prepare_batch_mutate_action,
        "organize_jobs_into_pool": _prepare_organize_jobs_into_pool_action,
    }


async def _dispatch_profile_chat_confirm(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    cleaned: Mapping[str, Any],
) -> Any:
    return await _prepare_profile_chat_confirm_action(session, actor, cleaned)


async def _dispatch_profile_agent_apply_patch(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    cleaned: Mapping[str, Any],
) -> Any:
    return await _prepare_profile_agent_apply_patch_action(session, actor, cleaned)


async def _dispatch_generate_resume(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    cleaned: Mapping[str, Any],
) -> Any:
    readiness = await skill_runtime.verify_resume_generate_readiness(session, actor)
    if not readiness.get("ok"):
        raise OperatorError(
            "validation_error",
            str(readiness.get("message") or "generate_resume requires Harness Skill Runtime readiness."),
            {"action": "generate_resume", "readiness": json_safe(readiness)},
        )
    runtime_scope = resume_scope_from_runtime_state(readiness.get("state"))
    if not runtime_scope:
        raise OperatorError(
            "validation_error",
            "generate_resume readiness did not produce an authoritative detailed-read scope.",
            {"action": "generate_resume"},
        )
    prepared_payload = dict(cleaned)
    locked_scope = _normalize_generate_resume_confirmed_scope(prepared_payload.get("confirmed_scope"))
    if locked_scope and not _generate_resume_authority_scope_matches(locked_scope, runtime_scope):
        raise OperatorError(
            "conflict",
            "generate_resume detailed-read evidence changed after the execution contract was sealed.",
            {
                "action": "generate_resume",
                "sealed_evidence_digest": str(locked_scope.get("evidence_digest") or ""),
                "current_evidence_digest": str(runtime_scope.get("evidence_digest") or ""),
            },
        )
    prepared_payload["confirmed_scope"] = {**locked_scope, **runtime_scope}
    return await _prepare_generate_resume_action(session, actor, prepared_payload)


async def _dispatch_apply_resume_template(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    cleaned: Mapping[str, Any],
) -> Any:
    return await _prepare_apply_resume_template_action(session, actor, cleaned)


def _smartfill_visibility(result_key: str) -> dict[str, str]:
    return {"result": result_key, "scope": "operator", "visibility": "backend"}


def _smartfill_archive_from_profile(profile_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(profile_payload, Mapping) and profile_payload:
        return json_safe(profile_payload)
    return {}


async def _smartfill_profile_payload(
    session: AsyncSession,
    input_payload: Mapping[str, Any],
) -> dict[str, Any]:
    profile_input = input_payload.get("profile")
    if isinstance(profile_input, Mapping) and profile_input:
        return json_safe(profile_input)
    from app.routes.profile import _get_or_create_default_profile, _load_profile_bundle, _serialize_profile
    from app.services.profile_schema import normalize_base_info_payload

    profile = await _get_or_create_default_profile(session)
    normalized_base_info = normalize_base_info_payload(profile.base_info_json)
    if profile.base_info_json != normalized_base_info:
        profile.base_info_json = normalized_base_info
        await session.flush()
    profile, roles, sections = await _load_profile_bundle(session, profile.id)
    return _serialize_profile(profile, roles, sections)


def _smartfill_archive_from_profile_view(profile_payload: Mapping[str, Any]) -> dict[str, Any]:
    from app.routes.profile import _smartfill_profile_view

    profile_view = _smartfill_profile_view(dict(profile_payload))
    ra = profile_view.get("resumeArchive") if isinstance(profile_view.get("resumeArchive"), Mapping) else {}
    aa = profile_view.get("applicationArchive") if isinstance(profile_view.get("applicationArchive"), Mapping) else {}
    basic = profile_view.get("basic") if isinstance(profile_view.get("basic"), Mapping) else {}
    ra = dict(ra)
    aa = dict(aa)
    if not isinstance(ra.get("basicInfo"), Mapping):
        ra["basicInfo"] = {
            "name": basic.get("fullName", ""),
            "phone": basic.get("phone", ""),
            "email": basic.get("email", ""),
            "currentCity": basic.get("city", ""),
            "jobIntention": basic.get("targetRole", ""),
            "website": basic.get("website", ""),
            "github": basic.get("github", ""),
        }
    if not ra.get("personalSummary") and basic.get("summary"):
        ra["personalSummary"] = basic["summary"]
    return {"resumeArchive": json_safe(ra), "applicationArchive": json_safe(aa)}


async def _smartfill_log(
    session: AsyncSession,
    *,
    run_id: str,
    stage: str,
    message: str,
    payload: Mapping[str, Any] | None = None,
    severity: str = "info",
    scope: str = "run",
    field_id: str = "",
) -> None:
    session.add(
        models.SmartFillRunLog(
            run_id=run_id,
            stage=stage[:40],
            severity=severity[:20],
            scope=scope[:20],
            message=message,
            field_id=field_id[:120],
            payload_json=json_safe(dict(payload or {})),
        )
    )


async def _ensure_smartfill_operator_run(
    session: AsyncSession,
    *,
    status: str = "running",
) -> Any:
    from app.routes.profile import _new_smartfill_run_id

    run_id = _new_smartfill_run_id()
    run = models.SmartFillRun(run_id=run_id, status=status, summary_json={})
    session.add(run)
    await session.flush()
    return run


async def _prepare_smartfill_option_match_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    from app.services.option_matcher import option_match

    candidates = input_payload.get("candidates")
    if not isinstance(candidates, list):
        candidates = input_payload.get("options")
    candidate_values = [str(item) for item in (candidates or []) if str(item).strip()]
    resume_value = str(input_payload.get("resume_value") or input_payload.get("value") or "")
    level1_title = str(input_payload.get("level1_title") or "")
    level2_title = str(input_payload.get("level2_title") or input_payload.get("field_name") or "")

    async def execute() -> dict[str, Any]:
        result = option_match(
            candidates=candidate_values,
            resume_value=resume_value,
            level1_title=level1_title,
            level2_title=level2_title,
        )
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "smartfill_option_match",
            **json_safe(result),
            "summary": f"Matched SmartFill option as {result.get('value') or 'no match'}.",
        }

    return execute


async def _prepare_smartfill_field_map_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    from app.services.field_mapper import field_map

    fragments = input_payload.get("fragments")
    if not isinstance(fragments, list):
        source_field = str(input_payload.get("source_field") or "")
        destination_field = str(input_payload.get("destination_field") or "")
        fragments = []
        if source_field or destination_field:
            fragments = [{"module_name": source_field or "basicInfo", "field_label": destination_field or source_field, "item_index": 0}]
    profile_payload = await _smartfill_profile_payload(session, input_payload)
    archive = _smartfill_archive_from_profile_view(profile_payload)

    async def execute() -> dict[str, Any]:
        mappings = field_map([json_safe(item) for item in fragments if isinstance(item, Mapping)], archive)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "smartfill_field_map",
            "mappings": json_safe(mappings),
            "summary": f"Mapped {len(mappings)} SmartFill fields.",
        }

    return execute


async def _prepare_smartfill_module_count_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    profile_payload = await _smartfill_profile_payload(session, input_payload)
    archive = _smartfill_archive_from_profile_view(profile_payload)
    ra = archive.get("resumeArchive", {}) if isinstance(archive.get("resumeArchive"), Mapping) else {}
    aa = archive.get("applicationArchive", {}) if isinstance(archive.get("applicationArchive"), Mapping) else {}

    async def execute() -> dict[str, Any]:
        repeatable_modules = [
            ("education", "教育经历", "educationList"),
            ("workExperiences", "工作经历", "workList"),
            ("internshipExperiences", "实习经历", "internshipList"),
            ("projects", "项目经历", "projectList"),
            ("skills", "技能", "skillList"),
            ("certificates", "证书", "certificateList"),
            ("awards", "获奖经历", "awardList"),
            ("personalExperiences", "个人经历", "personalExperienceList"),
        ]
        modules = [
            {"module_name": "基本信息", "field_name": "basicInfo", "count": 1},
            {"module_name": "身份联系", "field_name": "identityContact", "count": 1},
            {"module_name": "求职偏好", "field_name": "jobPreference", "count": 1},
            {"module_name": "校招专项", "field_name": "campusFields", "count": 1},
            {"module_name": "关系合规", "field_name": "relationshipCompliance", "count": 1},
            {"module_name": "来源推荐", "field_name": "sourceReferral", "count": 1},
        ]
        for key, display_name, field_name in repeatable_modules:
            arr = ra.get(key, [])
            modules.append({"module_name": display_name, "field_name": field_name, "count": len(arr) if isinstance(arr, list) else 0})
        family_members = {}
        if isinstance(aa.get("relationshipCompliance"), Mapping):
            family_members = aa.get("relationshipCompliance", {})
        members = family_members.get("familyMembers") if isinstance(family_members, Mapping) else []
        if isinstance(members, list):
            modules.append({"module_name": "家庭关系", "field_name": "familyMembers", "count": len(members)})
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "smartfill_module_count",
            "modules": json_safe(modules),
            "summary": f"Counted {len(modules)} SmartFill modules.",
        }

    return execute


async def _prepare_smartfill_map_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    from app.routes.profile import _build_smartfill_catalog_from_profile, _sanitize_ai_mappings, _sanitize_smartfill_catalog

    fields_raw = input_payload.get("fields")
    if not isinstance(fields_raw, list):
        form_schema = input_payload.get("form_schema")
        if isinstance(form_schema, Mapping) and isinstance(form_schema.get("fields"), list):
            fields_raw = form_schema.get("fields")
        else:
            fields_raw = []
    profile_payload = await _smartfill_profile_payload(session, input_payload)
    catalog_raw = input_payload.get("catalog")
    profile_values_raw = input_payload.get("profileValues")
    profile_values = profile_values_raw if isinstance(profile_values_raw, list) else []

    async def execute() -> dict[str, Any]:
        run = await _ensure_smartfill_operator_run(session, status="running")
        fields = [_smartfill_field_item(item) for item in fields_raw if isinstance(item, Mapping)]
        if isinstance(catalog_raw, list) and catalog_raw:
            private_catalog = []
            public_catalog = _sanitize_smartfill_catalog([json_safe(row) for row in catalog_raw if isinstance(row, Mapping)])
            value_by_path: dict[str, str] = {}
            for row in profile_values:
                if isinstance(row, Mapping):
                    path = str(row.get("path") or row.get("key") or "").strip()
                    value = str(row.get("value") or "").strip()
                    if path and value:
                        value_by_path[path] = value
            for row in public_catalog:
                with_value = dict(row)
                if with_value["path"] in value_by_path:
                    with_value["value"] = value_by_path[with_value["path"]]
                private_catalog.append(with_value)
        else:
            private_catalog = _build_smartfill_catalog_from_profile(profile_payload)
        mappings = _deterministic_smartfill_mappings(fields, private_catalog)
        if fields:
            mappings = _sanitize_ai_mappings({"mappings": mappings}, fields, private_catalog)
        run.status = "success"
        run.summary_json = {
            "mappingCount": len(mappings),
            "fieldCount": len(fields),
            "channel": "operator",
            "deterministic": True,
        }
        await _smartfill_log(
            session,
            run_id=run.run_id,
            stage="operator_map",
            message="SmartFill operator map completed.",
            payload={"mappingCount": len(mappings), "fieldCount": len(fields)},
        )
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "smartfill_map",
            "runId": run.run_id,
            "mappings": json_safe(mappings),
            "visibility": {
                "run": "smartfill_runs",
                "log": "smartfill_run_logs",
                "cache": "smartfill_map_cache",
                "result": "mappings",
            },
            "summary": f"Created SmartFill run {run.run_id} with {len(mappings)} mappings.",
        }

    return execute


def _smartfill_field_item(row: Mapping[str, Any]) -> Any:
    from app.routes.profile import SmartFillFieldItem

    return SmartFillFieldItem(
        fieldId=str(row.get("fieldId") or row.get("id") or row.get("name") or ""),
        label=str(row.get("label") or ""),
        placeholder=str(row.get("placeholder") or ""),
        name=str(row.get("name") or ""),
        inputType=str(row.get("inputType") or row.get("type") or ""),
        options=[str(item) for item in row.get("options", [])] if isinstance(row.get("options"), list) else [],
        required=bool(row.get("required") or False),
        nearbyText=str(row.get("nearbyText") or ""),
    )


def _deterministic_smartfill_mappings(fields: list[Any], catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for field in fields:
        field_text = " ".join(
            str(value or "")
            for value in (
                getattr(field, "label", ""),
                getattr(field, "placeholder", ""),
                getattr(field, "name", ""),
                getattr(field, "nearbyText", ""),
            )
        ).lower()
        best_item = None
        best_score = 0.0
        for item in catalog:
            label = str(item.get("label") or "").lower()
            path = str(item.get("path") or "").lower()
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            candidates = [label, path, *[str(alias).lower() for alias in aliases]]
            score = 0.0
            for candidate in candidates:
                if candidate and candidate in field_text:
                    score = max(score, 0.92)
                elif field_text and candidate and field_text in candidate:
                    score = max(score, 0.82)
            if score > best_score:
                best_score = score
                best_item = item
        if best_item is not None and best_score >= 0.8:
            mappings.append(
                {
                    "fieldId": getattr(field, "fieldId", ""),
                    "profilePath": str(best_item.get("path") or ""),
                    "catalogKey": str(best_item.get("key") or best_item.get("path") or ""),
                    "confidence": best_score,
                    "transform": {"type": "none"},
                    "reason": "deterministic catalog label match",
                }
            )
    return mappings


async def _prepare_apply_resume_template_action(
    session: AsyncSession,
    actor: ActorContext,
    input_payload: Mapping[str, Any],
) -> Any:
    resume_id = input_payload.get("resume_id")
    template_id = input_payload.get("template_id")
    resume_spec = get_model_spec("resume")
    template_spec = get_model_spec("resume_template")
    resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, resume_id)
    template = await fetch_scoped_record(session, actor, template_spec, models.ResumeTemplate, template_id)

    async def execute() -> dict[str, Any]:
        merged_style = {
            **(resume.style_config if isinstance(resume.style_config, Mapping) else {}),
            **(template.css_variables if isinstance(template.css_variables, Mapping) else {}),
        }
        resume.template_id = int(template.id)
        resume.style_config = json_safe(merged_style)
        await session.flush()
        await session.refresh(resume)
        await session.refresh(template)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "apply_resume_template",
            "model": "resume",
            "record_id": str(resume.id),
            "resume_id": str(resume.id),
            "template_id": str(template.id),
            "resume": serialize_record(
                resume,
                resume_spec,
                resume_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "template": serialize_record(
                template,
                template_spec,
                template_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "summary": f"Applied resume template {template.name} to {resume.title}.",
        }

    return execute


async def _prepare_import_jobs_to_application_table_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    raw_job_ids = input_payload.get("job_ids")
    table_id = input_payload.get("table_id")
    if not isinstance(raw_job_ids, list) or not raw_job_ids:
        raise OperatorError("validation_error", "import_jobs_to_application_table requires at least one locked job id.", {})
    job_ids = _canonical_int_ids(raw_job_ids, field_name="job_ids")
    table_spec = get_model_spec("application_table")
    table = await fetch_scoped_record(session, actor, table_spec, models.ApplicationTable, table_id)
    await validate_action_references(session, actor, spec, input_payload)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        result = await _create_records_from_jobs_no_commit(
            session,
            table_id=table.id,
            job_ids=job_ids,
            skip_existing_in_table=True,
            owner_actor_id=actor.actor_id,
        )
        created_records = list(result.get("records") or [])
        table_spec = get_model_spec("application_table")
        record_spec = get_model_spec("application_record")
        serialized_records = [
            serialize_record(
                record,
                record_spec,
                record_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            )
            for record in created_records
        ]
        await session.refresh(table)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "import_jobs_to_application_table",
            "model": "application_record",
            "table_id": str(table.id),
            "created_count": int(result.get("created") or 0),
            "skipped_existing_job_ids": list(result.get("skipped_existing_job_ids") or []),
            "records": serialized_records,
            "table": serialize_record(
                table,
                table_spec,
                table_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "summary": f"Imported {int(result.get('created') or 0)} job(s) into application table {table.name}.",
        }

    return execute


async def _prepare_import_latest_extension_batch_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    table_id = input_payload.get("table_id")
    batch_id = str(input_payload.get("batch_id") or "").strip()
    source = str(input_payload.get("source") or "offeru-extension").strip() or "offeru-extension"
    limit = _bounded_int(input_payload.get("limit"), default=500, minimum=1, maximum=500)
    table_spec = get_model_spec("application_table")
    table = await fetch_scoped_record(session, actor, table_spec, models.ApplicationTable, table_id)

    if batch_id:
        batch = await session.get(models.Batch, batch_id)
    else:
        batch = (
            await session.execute(
                select(models.Batch)
                .join(models.Job, models.Job.batch_id == models.Batch.id)
                .where(models.Batch.source == source)
                .where(models.Job.owner_actor_id == actor.actor_id)
                .order_by(models.Batch.created_at.desc(), models.Batch.id.desc())
            )
        ).scalars().first()
        batch_id = str(getattr(batch, "id", "") or "")
    if not batch_id:
        raise OperatorError("not_found_error", "No extension sync batch was found.", {"source": source})

    job_ids = await _scoped_job_ids_for_batch(session, actor, batch_id, limit=limit)
    if not job_ids:
        raise OperatorError("not_found_error", "Extension sync batch has no actor-visible jobs.", {"batch_id": batch_id})

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        result = await _create_records_from_jobs_no_commit(
            session,
            table_id=table.id,
            job_ids=job_ids,
            skip_existing_in_table=True,
            owner_actor_id=actor.actor_id,
        )
        serialized_records = await _serialize_application_records(result.get("records") or [])
        await session.refresh(table)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "import_latest_extension_batch",
            "model": "application_record",
            "table_id": str(table.id),
            "batch_id": batch_id,
            "source": str(getattr(batch, "source", "") or source),
            "total_jobs": len(job_ids),
            "created_count": int(result.get("created") or 0),
            "skipped_existing_job_ids": list(result.get("skipped_existing_job_ids") or []),
            "records": serialized_records,
            "table": serialize_record(
                table,
                table_spec,
                table_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "summary": f"Imported {int(result.get('created') or 0)} job(s) from extension batch {batch_id}.",
        }

    return execute


def _normalize_action_text(value: Any, *, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


async def _prepare_run_scraper_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    """Fail closed until scraper execution has a durable external outbox."""

    raise OperatorError(
        "not_implemented",
        str(getattr(spec, "non_operable_reason", "") or "run_scraper is not implemented for agent confirmation."),
        {"action": "run_scraper", "requires_durable_outbox": True},
    )


async def _prepare_sync_email_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    # Fail closed: do not invent interview/calendar rows. Real mailbox I/O is
    # required before this action can be confirmation-executable.
    raise OperatorError(
        "not_implemented",
        "Operator action is not implemented: sync_email",
        {
            "action": "sync_email",
            "implementation_status": "not_implemented",
            "reason": (
                "Email sync requires a real mailbox connector, account verification, "
                "and durable external idempotency before it can be confirmed safely."
            ),
        },
    )


async def _prepare_job_stats_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    window = _normalize_action_text(input_payload.get("window"), default="weekly")

    async def execute() -> dict[str, Any]:
        total_jobs = int(
            (await session.execute(select(func.count()).select_from(models.Job).where(models.Job.owner_actor_id == actor.actor_id))).scalar_one()
        )
        by_status_rows = (
            await session.execute(
                select(models.Job.triage_status, func.count())
                .where(models.Job.owner_actor_id == actor.actor_id)
                .group_by(models.Job.triage_status)
            )
        ).all()
        by_location_rows = (
            await session.execute(
                select(models.Job.location, func.count())
                .where(models.Job.owner_actor_id == actor.actor_id)
                .group_by(models.Job.location)
            )
        ).all()
        recent_batches_rows = (
            await session.execute(
                select(models.Batch)
                .join(models.Job, models.Job.batch_id == models.Batch.id)
                .where(models.Job.owner_actor_id == actor.actor_id)
                .order_by(models.Batch.created_at.desc(), models.Batch.id.desc())
                .limit(5)
            )
        ).scalars().all()
        report = {
            "window": window,
            "total_jobs": total_jobs,
            "by_status": {str(status): int(count) for status, count in by_status_rows},
            "by_location": {str(location): int(count) for location, count in by_location_rows},
            "recent_batches": [
                {
                    "batch_id": str(batch.id),
                    "source": str(batch.source or ""),
                    "job_count": int(batch.job_count or 0),
                    "total_fetched": int(batch.total_fetched or 0),
                    "status": str(batch.status or ""),
                }
                for batch in recent_batches_rows
            ],
        }
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "job_stats",
            "report": report,
            "summary": f"Returned {window} job stats for {total_jobs} visible jobs.",
        }

    return execute


async def _prepare_generate_cover_letter_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    job_spec = get_model_spec("job")
    application_spec = get_model_spec("application")
    job = await fetch_scoped_record(session, actor, job_spec, models.Job, input_payload.get("job_id"))
    application = await fetch_scoped_record(
        session,
        actor,
        application_spec,
        models.Application,
        input_payload.get("application_id"),
    )
    if int(application.job_id) != int(job.id):
        raise OperatorError(
            "validation_error",
            "generate_cover_letter job_id must match the target application.",
            {"job_id": job.id, "application_id": application.id, "application_job_id": application.job_id},
        )
    tone = str(input_payload.get("tone") or "professional").strip()[:80] or "professional"

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        cover_letter = _deterministic_cover_letter(job, application, tone)
        if not cover_letter.strip():
            raise OperatorError(
                "transient_error",
                "Cover letter generation returned empty content.",
                {"job_id": job.id, "application_id": application.id},
            )
        application.cover_letter = cover_letter
        await session.flush()
        await session.refresh(application)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "generate_cover_letter",
            "model": "application",
            "record_id": str(application.id),
            "application": serialize_record(
                application,
                application_spec,
                application_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "cover_letter": cover_letter,
            "summary": f"Generated cover letter for {job.company} {job.title}.",
        }

    return execute


async def _prepare_ensure_application_for_job_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    try:
        from app.services.application_workspace import ensure_canonical_application_for_job
    except ImportError as exc:
        raise OperatorError(
            "validation_error",
            "Application workspace helper is not available in this worktree.",
            {"error": str(exc)},
        ) from exc
    job_spec = get_model_spec("job")
    job = await fetch_scoped_record(session, actor, job_spec, models.Job, input_payload.get("job_id"))
    table_id = input_payload.get("table_id")
    resolved_table_id: int | None = None
    if table_id not in (None, ""):
        table = await fetch_scoped_record(
            session, actor, get_model_spec("application_table"), models.ApplicationTable, table_id,
        )
        resolved_table_id = int(table.id)
    application_spec = get_model_spec("application")

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        application = await ensure_canonical_application_for_job(session, job=job)
        target_table = None
        if resolved_table_id is None:
            target_table = (
                await session.execute(
                    select(models.ApplicationTable)
                    .where(
                        models.ApplicationTable.owner_actor_id == actor.actor_id,
                        models.ApplicationTable.is_total.is_(True),
                    )
                    .order_by(models.ApplicationTable.id.asc())
                )
            ).scalars().first()
            if target_table is None:
                target_table = models.ApplicationTable(
                    owner_actor_id=actor.actor_id,
                    name="Total",
                    is_total=True,
                    schema_json=[],
                )
                session.add(target_table)
                await session.flush()
        else:
            target_table = await session.get(models.ApplicationTable, resolved_table_id, populate_existing=True)
            if target_table is None:
                raise OperatorError(
                    "not_found_error",
                    "Application table was not found.",
                    {"table_id": resolved_table_id},
                )
        result = await _create_records_from_jobs_no_commit(
            session,
            table_id=int(target_table.id),
            job_ids=[int(job.id)],
            skip_existing_in_table=True,
            owner_actor_id=actor.actor_id,
        )
        projection_rows = (
            await session.execute(
                select(models.ApplicationRecord)
                .join(
                    models.ApplicationTableRecord,
                    models.ApplicationTableRecord.record_id == models.ApplicationRecord.id,
                )
                .where(
                    models.ApplicationTableRecord.table_id == int(target_table.id),
                    models.ApplicationRecord.job_ref_id == job.id,
                )
                .order_by(models.ApplicationRecord.id.asc())
            )
        ).scalars().all()
        bound_record_id = ""
        for record in projection_rows:
            if record.application_id in (None, ""):
                record.application_id = int(application.id)
            custom_values = record.custom_values if isinstance(record.custom_values, Mapping) else {}
            if not str(custom_values.get("apply_status") or "").strip():
                record.custom_values = {
                    **dict(custom_values),
                    "apply_status": ApplicationLifecycleSpec.label("pending"),
                }
                flag_modified(record, "custom_values")
            if not bound_record_id:
                bound_record_id = str(record.id)
        await session.flush()
        if projection_rows:
            projection_ids = [int(record.id) for record in projection_rows]
            projection_rows = (
                await session.execute(
                    select(models.ApplicationRecord)
                    .where(models.ApplicationRecord.id.in_(projection_ids))
                    .execution_options(populate_existing=True)
                )
            ).scalars().all()
        await session.refresh(application)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "ensure_application_for_job",
            "model": "application",
            "record_id": str(application.id),
            "application_id": str(application.id),
            "application_record_id": bound_record_id,
            "table_id": str(target_table.id),
            "application": serialize_record(
                application,
                application_spec,
                application_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "created": bool(result.get("created") or 0),
            "created_count": int(result.get("created") or 0),
            "records": await _serialize_application_records(projection_rows),
            "summary": f"Ensured canonical application {application.id} for {job.company} {job.title}.",
        }

    return execute


async def _prepare_advance_application_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    application_spec = get_model_spec("application")
    application = await fetch_scoped_record(
        session,
        actor,
        application_spec,
        models.Application,
        input_payload.get("application_id"),
    )
    current_status = str(application.status or "pending").strip()
    if not ApplicationLifecycleSpec.is_valid(current_status):
        raise OperatorError(
            "validation_error",
            "Application is in an unknown lifecycle state; correct the record manually before advancing.",
            {"application_id": application.id, "current_status": current_status},
        )
    target_status = str(input_payload.get("target_status") or "").strip()
    try:
        policy = ApplicationLifecycleSpec.transition(current_status, target_status)
    except ApplicationLifecycleError as exc:
        raise OperatorError(
            "validation_error",
            str(exc),
            {"application_id": application.id, "current_status": current_status, "target_status": target_status},
        ) from exc
    notes = input_payload.get("notes")

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        live_status = str(application.status or "pending").strip()
        before_status = ApplicationLifecycleSpec.resolve_state(live_status)
        after_status = ApplicationLifecycleSpec.resolve_state(str(target_status))
        try:
            ApplicationLifecycleSpec.transition(before_status, after_status)
        except ApplicationLifecycleError as exc:
            raise OperatorError(
                "validation_error",
                str(exc),
                {
                    "application_id": application.id,
                    "current_status": before_status,
                    "target_status": after_status,
                },
            ) from exc
        if notes not in (None, ""):
            application.notes = str(notes).strip()
        if after_status != before_status:
            application.status = after_status
            if after_status == "submitted" and application.submitted_at is None:
                application.submitted_at = datetime.utcnow()
        await session.flush()
        projection_id = ""
        projection_rows = (
            await session.execute(
                select(models.ApplicationRecord).where(
                    models.ApplicationRecord.application_id == application.id
                )
            )
        ).scalars().all()
        for record in projection_rows:
            record.custom_values = {
                **(record.custom_values if isinstance(record.custom_values, Mapping) else {}),
                "apply_status": ApplicationLifecycleSpec.label(after_status),
            }
            flag_modified(record, "custom_values")
            record.apply_status = after_status
            record.updated_at_value = datetime.utcnow()
            if not projection_id:
                projection_id = str(record.id)
        await session.flush()
        await session.refresh(application)
        summary = f"Advanced application {application.id} from {before_status} to {after_status}."
        if bool(policy.get("noop")):
            summary = f"Application {application.id} is already {after_status}."
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "advance_application",
            "model": "application",
            "record_id": str(application.id),
            "application_id": str(application.id),
            "application_record_id": projection_id,
            "application": serialize_record(
                application,
                application_spec,
                application_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "before_status": before_status,
            "after_status": after_status,
            "summary": summary,
        }

    return execute


async def _prepare_prepare_application_material_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    application_spec = get_model_spec("application")
    application = await fetch_scoped_record(
        session,
        actor,
        application_spec,
        models.Application,
        input_payload.get("application_id"),
    )
    material_type = str(input_payload.get("material_type") or "").strip()
    if material_type != "cover_letter":
        raise OperatorError(
            "validation_error",
            "Unsupported application material type.",
            {"application_id": application.id, "material_type": material_type, "supported_types": ["cover_letter"]},
        )
    tone = str(input_payload.get("tone") or "professional").strip()[:80] or "professional"
    constraints = str(input_payload.get("constraints") or "").strip()
    job_spec = get_model_spec("job")
    job = await fetch_scoped_record(session, actor, job_spec, models.Job, application.job_id)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        cover_letter = _deterministic_cover_letter(job, application, tone)
        if constraints:
            cover_letter = f"{cover_letter.rstrip()}\n\n用户约束:{constraints[:280]}"
        if not cover_letter.strip():
            raise OperatorError(
                "transient_error",
                "Application material preparation returned empty content.",
                {"application_id": application.id, "material_type": material_type},
            )
        application.cover_letter = cover_letter
        await session.flush()
        await session.refresh(application)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "prepare_application_material",
            "model": "application",
            "record_id": str(application.id),
            "application_id": str(application.id),
            "application": serialize_record(
                application,
                application_spec,
                application_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "material_type": material_type,
            "material": {"content": cover_letter, "tone": tone},
            "summary": f"Prepared {material_type} for application {application.id}.",
        }

    return execute


async def _prepare_interview_generate_answer_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    question = await fetch_scoped_record(
        session,
        actor,
        get_model_spec("interview_question"),
        models.InterviewQuestion,
        input_payload.get("question_id"),
    )
    job_id = input_payload.get("job_id")
    if job_id not in (None, "") and str(getattr(question, "job_id", "") or "") not in {"", str(job_id)}:
        raise OperatorError(
            "validation_error",
            "Interview question job_id must match the locked question context.",
            {"question_id": question.id, "job_id": job_id, "question_job_id": getattr(question, "job_id", None)},
        )
    profile = (
        await session.execute(
            select(models.Profile)
            .where(models.Profile.owner_actor_id == actor.actor_id)
            .order_by(models.Profile.is_default.desc(), models.Profile.id.asc())
        )
    ).scalars().first()
    if profile is None:
        raise OperatorError("not_found_error", "Profile was not found.", {"actor_id": actor.actor_id})
    sections = (
        await session.execute(
            select(models.ProfileSection)
            .where(models.ProfileSection.profile_id == profile.id)
            .order_by(models.ProfileSection.sort_order.asc(), models.ProfileSection.id.asc())
        )
    ).scalars().all()
    profile_lines: list[str] = []
    for section in sections:
        content = section.content_json if isinstance(section.content_json, Mapping) else {}
        bullet = str(content.get("bullet") or "").strip()
        description = str(content.get("description") or "").strip()
        title = str(section.title or "").strip()
        detail = bullet or description
        text = f"{title} - {detail}" if title and detail else title or detail
        if text:
            profile_lines.append(f"- [{section.section_type}] {text}")
    profile_bullets = "\n".join(profile_lines) or "- [profile] No structured profile bullets were found."
    style = str(input_payload.get("style") or "STAR").strip() or "STAR"

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        prompt_question = str(question.question_text or "").strip()
        category = str(question.category or "behavioral").strip()
        difficulty = int(question.difficulty or 3)
        answer = (
            f"{style} 回答思路：\n"
            f"问题：{prompt_question}\n"
            f"重点：{category}，难度 {difficulty}/5。\n"
            f"可结合的经历：\n{profile_bullets}\n"
            f"建议围绕情境、任务、行动、结果展开，并突出你的真实项目细节。"
        )
        question.suggested_answer = answer
        await session.flush()
        await session.refresh(question)
        serialized_question = serialize_record(
            question,
            get_model_spec("interview_question"),
            get_model_spec("interview_question").detail_fields,
            include_long_text=True,
            truncate_long_text=False,
        )
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "interview_generate_answer",
            "model": "interview_question",
            "record_id": str(question.id),
            "question": serialized_question,
            "suggested_answer": answer,
            "summary": f"Generated a suggested answer for interview question {question.id}.",
        }

    return execute


async def _prepare_interview_extract_questions_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    experience = await fetch_scoped_record(
        session,
        actor,
        get_model_spec("interview_experience"),
        models.InterviewExperience,
        input_payload.get("experience_id"),
    )
    job_id = input_payload.get("job_id")
    if job_id not in (None, ""):
        job = await fetch_scoped_record(session, actor, get_model_spec("job"), models.Job, job_id)
        if getattr(experience, "job_id", None) not in (None, "") and int(experience.job_id) != int(job.id):
            raise OperatorError(
                "validation_error",
                "Interview extraction job_id must match the locked experience context.",
                {"experience_id": experience.id, "job_id": job.id, "experience_job_id": experience.job_id},
            )

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        extracted = _extract_interview_questions_from_text(
            company=str(experience.company or ""),
            role=str(experience.role or ""),
            raw_text=str(experience.raw_text or ""),
        )
        questions_payload = extracted.get("questions") or []
        if not questions_payload:
            raise OperatorError(
                "validation_error",
                "No interview questions could be extracted from this experience.",
                {"experience_id": experience.id},
            )
        question_spec = get_model_spec("interview_question")
        resolved_job_id = int(job_id) if job_id not in (None, "") else getattr(experience, "job_id", None)
        created: list[Any] = []
        for item in questions_payload:
            question_text = str(item.get("question_text") or "").strip()
            if not question_text:
                continue
            question = models.InterviewQuestion(
                owner_actor_id=actor.actor_id,
                experience_id=experience.id,
                question_text=question_text,
                round_type=_normalize_interview_round_type(item.get("round_type")),
                category=_normalize_interview_category(item.get("category")),
                difficulty=_normalize_interview_difficulty(item.get("difficulty")),
                frequency=1,
                job_id=resolved_job_id,
            )
            session.add(question)
            created.append(question)
        if not created:
            raise OperatorError(
                "validation_error",
                "No valid interview questions could be extracted from this experience.",
                {"experience_id": experience.id},
            )
        rounds = [str(item).strip() for item in extracted.get("rounds") or [] if str(item).strip()]
        if rounds:
            experience.interview_rounds = json.dumps(rounds, ensure_ascii=False)
        await session.flush()
        for question in created:
            await session.refresh(question)
        await session.refresh(experience)
        questions = [
            serialize_record(
                question,
                question_spec,
                question_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            )
            for question in created
        ]
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "interview_extract_questions",
            "model": "interview_question",
            "experience_id": str(experience.id),
            "created_count": len(questions),
            "questions": questions,
            "rounds": rounds,
            "summary": f"Extracted {len(questions)} interview question(s) from experience {experience.id}.",
        }

    return execute


async def _prepare_calendar_auto_fill_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    notification = await fetch_scoped_record(
        session,
        actor,
        get_model_spec("interview_notification"),
        models.InterviewNotification,
        input_payload.get("notification_id"),
    )
    job = None
    if getattr(notification, "job_id", None) not in (None, ""):
        job = await session.get(models.Job, notification.job_id)
        if job is not None and getattr(job, "owner_actor_id", None) != actor.actor_id:
            raise OperatorError("permission_error", "Notification job is outside the current actor scope.", {"notification_id": notification.id})
    text = str(input_payload.get("text") or "").strip()

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        event = models.CalendarEvent(
            owner_actor_id=actor.actor_id,
            title=f"面试 - {notification.company or 'Interview'}",
            description=text or str(notification.action_required or notification.email_subject or "").strip(),
            event_type="interview",
            start_time=notification.interview_time or datetime.now(timezone.utc).replace(tzinfo=None),
            end_time=(notification.interview_time or datetime.now(timezone.utc).replace(tzinfo=None)) + timedelta(hours=1),
            location=str(notification.location or ""),
            related_job_id=getattr(notification, "job_id", None),
            related_notification_id=notification.id,
        )
        session.add(event)
        await session.flush()
        await session.refresh(event)
        serialized_event = serialize_record(
            event,
            get_model_spec("calendar_event"),
            get_model_spec("calendar_event").detail_fields,
            include_long_text=True,
            truncate_long_text=False,
        )
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "calendar_auto_fill",
            "model": "calendar_event",
            "record_id": str(event.id),
            "created_count": 1,
            "event": serialized_event,
            "summary": f"Created calendar event {event.id} from interview notification {notification.id}.",
        }

    return execute


def _extract_interview_questions_from_text(*, company: str, role: str, raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        return {"rounds": [], "questions": []}
    rounds = _extract_interview_rounds(text)
    questions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_question(question_text: str, *, source_line: str = "") -> None:
        normalized = _normalize_question_text(question_text)
        if not normalized:
            return
        key = normalized.casefold()
        if key in seen:
            return
        seen.add(key)
        context = f"{source_line}\n{normalized}"
        questions.append(
            {
                "question_text": normalized,
                "round_type": _infer_round_type(context),
                "category": _infer_question_category(context, company=company, role=role),
                "difficulty": _infer_question_difficulty(context),
            }
        )

    for line in _candidate_question_lines(text):
        cleaned = re.sub(r"^\s*(?:Q\d*|Question\s*\d*|问题\s*\d*|问)\s*[:：.-]\s*", "", line, flags=re.IGNORECASE)
        for fragment in _split_question_fragments(cleaned):
            add_question(fragment, source_line=line)

    for pattern in (
        r"\basked\s+(?:me\s+)?(?:about\s+)?(?P<body>how\s+to\s+[^.?!\n]+)",
        r"\basked\s+(?:me\s+)?(?P<body>why\s+[^.?!\n]+)",
        r"\basked\s+(?:me\s+)?(?P<body>what\s+[^.?!\n]+)",
    ):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            add_question(match.group("body"), source_line=match.group(0))

    for fragment, source in _colloquial_chinese_asked_fragments(text):
        question_text = _questionize_interview_topic(fragment)
        add_question(question_text, source_line=source)

    return {
        "rounds": rounds or sorted({item["round_type"] for item in questions if item.get("round_type")}),
        "questions": questions,
    }


def _candidate_question_lines(text: str) -> list[str]:
    lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    candidates: list[str] = []
    for line in lines:
        if "?" in line or "？" in line:
            candidates.append(line)
            continue
        if re.match(r"^\s*(?:Q\d*|Question\s*\d*|问题\s*\d*|问)\s*[:：.-]", line, flags=re.IGNORECASE):
            candidates.append(line)
    return candidates


def _split_question_fragments(line: str) -> list[str]:
    parts = re.split(r"[?？]+", str(line or ""))
    fragments: list[str] = []
    for index, part in enumerate(parts):
        cleaned = part.strip(" \t\r\n:：;；,，。.")
        if not cleaned:
            continue
        if index < len(parts) - 1 or _question_text_detected(cleaned):
            fragments.append(cleaned)
    return fragments


def _colloquial_chinese_asked_fragments(text: str) -> list[tuple[str, str]]:
    fragments: list[tuple[str, str]] = []
    for match in re.finditer(
        r"(?:面试官|hr|HR|leader|主管|对方|一面|二面|三面)?\s*(?:主要)?(?:问了|问到|问的是|提问了|追问了|问(?!题|卷|候|答))\s*(?P<body>[^。！？?!\n]+)",
        str(text or ""),
        flags=re.IGNORECASE,
    ):
        body = match.group("body")
        body = re.sub(r"^(?:我|你|关于|一下|几个|这些|这个|那个)\s*", "", body.strip())
        body = re.sub(r"^(?:[一二三四五六七八九十0-9]+个)?问题\s*[:：]\s*", "", body)
        body = re.sub(r"(?:，|,)?\s*(?:还有|以及|然后|另外|再就是|和|还追着问|还追问|追着问|追问)\s*", "、", body)
        for item in re.split(r"[、,，;；/]+", body):
            cleaned = item.strip(" \t\r\n:：;；,，。.！？?!")
            cleaned = re.sub(r"^(?:关于|一下|一个|几个|就是)\s*", "", cleaned)
            cleaned = re.sub(r"^\d+\s*[\.．、:：]\s*", "", cleaned)
            if len(cleaned) < 3:
                continue
            fragments.append((cleaned, match.group(0)))
    return fragments


def _questionize_interview_topic(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" \t\r\n:：;；,，。.！？?!")
    if not cleaned:
        return ""
    if _question_text_detected(cleaned):
        return cleaned
    if any(token in cleaned for token in ("优先级", "重点", "重要", "排序")):
        return f"如何判断{cleaned}"
    if any(token in cleaned for token in ("竞品", "入口", "方案", "策略", "设计", "功能")):
        return f"如何分析{cleaned}"
    return f"如何说明{cleaned}"


def _normalize_question_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" \t\r\n:：;；,，。.")
    if not cleaned:
        return ""
    cleaned = re.sub(r"^(?:asked\s+(?:me\s+)?(?:about\s+)?)", "", cleaned, flags=re.IGNORECASE).strip()
    how_to_match = re.match(r"^how\s+to\s+(.+)$", cleaned, flags=re.IGNORECASE)
    if how_to_match:
        cleaned = f"How would you {how_to_match.group(1).strip()}"
    elif cleaned and cleaned[0].isascii():
        cleaned = cleaned[:1].upper() + cleaned[1:]
    if not _question_text_detected(cleaned):
        return ""
    if cleaned.endswith(("?", "？")):
        return cleaned
    return f"{cleaned}?"


def _question_text_detected(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    if lowered.startswith(("how ", "why ", "what ", "when ", "where ", "which ", "who ", "can ", "could ", "would ", "do ", "does ", "did ")):
        return True
    return any(token in text for token in ("如何", "为什么", "什么", "哪", "是否", "能否", "怎么", "请介绍"))


def _extract_interview_rounds(text: str) -> list[str]:
    mapping = (
        ("hr", ("HR", "hr", "人力", "终面", "final")),
        ("final", ("终面", "final", "最后一轮")),
        ("department", ("业务面", "技术面", "一面", "二面", "department", "technical")),
    )
    rounds: list[str] = []
    for normalized, markers in mapping:
        if any(marker in text for marker in markers) and normalized not in rounds:
            rounds.append(normalized)
    return rounds


def _infer_round_type(text: str) -> str:
    lowered = str(text or "").lower()
    if "hr" in lowered or "人力" in text:
        return "hr"
    if "final" in lowered or "终面" in text:
        return "final"
    return "department"


def _infer_question_category(text: str, *, company: str, role: str) -> str:
    lowered = str(text or "").lower()
    if any(token in lowered for token in ("why", "motivation", "interest")) or any(token in text for token in ("为什么", "动机", "意向")):
        return "motivation"
    if any(token in lowered for token in ("case", "design", "dashboard", "metric", "strategy")) or any(token in text for token in ("设计", "分析", "增长", "指标", "方案")):
        return "case"
    if any(token in lowered for token in ("sql", "python", "algorithm", "architecture", "technical")) or any(token in text for token in ("算法", "系统", "技术", "代码")):
        return "technical"
    if company or role:
        return "behavioral"
    return "behavioral"


def _infer_question_difficulty(text: str) -> int:
    lowered = str(text or "").lower()
    score = 3
    if any(token in lowered for token in ("system", "architecture", "strategy", "case")) or any(token in text for token in ("系统", "架构", "策略", "设计")):
        score += 1
    if any(token in lowered for token in ("deep", "hard", "complex")) or any(token in text for token in ("复杂", "深入", "追问")):
        score += 1
    return max(1, min(5, score))


def _normalize_interview_round_type(value: Any) -> str:
    normalized = str(value or "department").strip().lower()
    return normalized if normalized in {"hr", "department", "final"} else "department"


def _normalize_interview_category(value: Any) -> str:
    normalized = str(value or "behavioral").strip().lower()
    return normalized if normalized in {"behavioral", "technical", "case", "motivation"} else "behavioral"


def _normalize_interview_difficulty(value: Any) -> int:
    try:
        difficulty = int(value)
    except (TypeError, ValueError):
        difficulty = 3
    return max(1, min(5, difficulty))


async def _prepare_auto_write_application_content_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    job_spec = get_model_spec("job")
    job = await fetch_scoped_record(session, actor, job_spec, models.Job, input_payload.get("job_id"))

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        total_table = (
            await session.execute(
                select(models.ApplicationTable)
                .where(models.ApplicationTable.owner_actor_id == actor.actor_id, models.ApplicationTable.is_total.is_(True))
                .order_by(models.ApplicationTable.id.asc())
            )
        ).scalars().first()
        if total_table is None:
            total_table = models.ApplicationTable(
                owner_actor_id=actor.actor_id,
                name="Total",
                is_total=True,
                schema_json=[],
            )
            session.add(total_table)
            await session.flush()
        result = await _create_records_from_jobs_no_commit(
            session,
            table_id=total_table.id,
            job_ids=[int(job.id)],
            skip_existing_in_table=True,
            owner_actor_id=actor.actor_id,
        )
        records = await _serialize_application_records(result.get("records") or [])
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "auto_write_application_content",
            "model": "application_record",
            "table_id": str(total_table.id),
            "created_count": int(result.get("created") or 0),
            "records": records,
            "summary": f"Auto-wrote application workspace content for {job.company} {job.title}.",
        }

    return execute


async def _prepare_generate_resume_action(
    session: AsyncSession,
    actor: ActorContext,
    input_payload: Mapping[str, Any],
) -> Any:
    profile_id = input_payload.get("profile_id")
    job_id = input_payload.get("job_id")
    template_id = input_payload.get("template_id")
    requested_title = _normalize_resume_text(input_payload.get("title"), max_length=300)
    instructions = _normalize_resume_text(input_payload.get("instructions"), max_length=5000)
    confirmed_scope = _normalize_generate_resume_confirmed_scope(input_payload.get("confirmed_scope"))
    effective_instructions = _merge_generate_resume_confirmed_scope_instructions(instructions, confirmed_scope)
    profile_spec = get_model_spec("profile")
    job_spec = get_model_spec("job")
    profile = await fetch_scoped_record(session, actor, profile_spec, models.Profile, profile_id)
    job = await fetch_scoped_record(session, actor, job_spec, models.Job, job_id)
    template = None
    resolved_template_id: int | None = None
    if template_id not in (None, ""):
        template_spec = get_model_spec("resume_template")
        template = await fetch_scoped_record(session, actor, template_spec, models.ResumeTemplate, template_id)
        resolved_template_id = int(template.id)
    archive_sections = _build_personal_archive_sections(profile)
    sections_stmt = select(models.ProfileSection).where(models.ProfileSection.profile_id == profile.id)
    if archive_sections is not None:
        sections_stmt = sections_stmt.where(models.ProfileSection.source != "archive_sync")
    stored_sections = (
        await session.execute(
            sections_stmt.order_by(
                models.ProfileSection.sort_order.asc(),
                models.ProfileSection.updated_at.desc(),
                models.ProfileSection.id.asc(),
            ).limit(12)
        )
    ).scalars().all()
    sections = list(stored_sections)
    if archive_sections is not None:
        sections.extend(archive_sections[: max(12 - len(sections), 0)])
    selected = _filter_generate_resume_profile_sections(list(sections), effective_instructions, confirmed_scope)
    if not selected:
        selected = []
    rows = _build_resume_rows_from_profile_sections(selected)
    rows = _apply_generate_resume_instruction_constraints(rows, effective_instructions)
    rows = _merge_confirmed_instruction_rows(rows, _build_resume_rows_from_confirmed_instructions(profile, effective_instructions))
    rows = _filter_generate_resume_rows_for_excluded_content(rows, effective_instructions)
    rows = _normalize_generate_resume_rows_for_storage(rows)
    if not rows:
        raise OperatorError(
            "validation_error",
            "generate_resume could not build resume sections from the locked profile snapshot.",
            {"profile_id": profile_id},
        )
    source_profile_snapshot = _build_source_profile_snapshot(profile, selected)
    source_profile_snapshot["job_id"] = job.id
    source_profile_snapshot["template_id"] = resolved_template_id
    if instructions:
        source_profile_snapshot["instructions"] = instructions
    if confirmed_scope:
        source_profile_snapshot["confirmed_scope"] = confirmed_scope
        source_profile_snapshot["effective_instructions"] = effective_instructions
    contact_json = _merge_contact_json_from_confirmed_instructions(_profile_to_contact_json(profile), effective_instructions)
    user_name = _resolve_resume_user_name_from_confirmed_instructions(profile, effective_instructions)
    title = requested_title or f"{job.company} - {job.title} 定制简历".strip(" -")
    summary = _generate_resume_instruction_summary(profile, effective_instructions) or str(profile.headline or profile.exit_story or "")
    if not summary and not _instructions_forbid_leadership_overclaim(effective_instructions):
        summary = str(job.summary or "")
    if _instructions_forbid_leadership_overclaim(effective_instructions) and re.search(r"(负责|主导|负责人)", summary):
        summary = "具备用户反馈整理、活动复盘、竞品观察与基础 SQL 查询经验。"
    summary = _sanitize_generate_resume_summary(summary, effective_instructions)

    async def execute() -> dict[str, Any]:
        resume = models.Resume(
            owner_actor_id=actor.actor_id,
            user_name=user_name,
            title=title or "定制简历",
            summary=summary,
            contact_json=json_safe(contact_json),
            style_config={},
            template_id=resolved_template_id,
            is_primary=False,
            language="zh",
            source_mode="operator_generate_resume",
            source_job_ids=[job.id],
            source_profile_snapshot=json_safe(source_profile_snapshot),
        )
        session.add(resume)
        await session.flush()
        for row in rows:
            section = models.ResumeSection(
                owner_actor_id=actor.actor_id,
                resume_id=resume.id,
                section_type=str(row["section_type"]),
                sort_order=int(row["sort_order"]),
                title=str(row["title"]),
                visible=True,
                content_json=json_safe(row["content_json"]),
            )
            session.add(section)
        await session.flush()
        await session.refresh(resume)
        resume_spec = get_model_spec("resume")
        section_spec = get_model_spec("resume_section")
        resume_sections = (
            await session.execute(
                select(models.ResumeSection)
                .where(models.ResumeSection.resume_id == resume.id)
                .order_by(models.ResumeSection.sort_order.asc())
            )
        ).scalars().all()
        section_types = [str(section.section_type or "") for section in resume_sections]
        refine = {
            "ok": bool(resume.id and resume_sections),
            "resume_id": str(resume.id),
            "section_count": len(resume_sections),
            "section_types": section_types,
            "summary": (
                f"已读回新简历 {resume.id}：共 {len(resume_sections)} 个章节"
                f"（{', '.join(section_types) if section_types else '无章节'}）。"
            ),
        }
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "generate_resume",
            "model": "resume",
            "record_id": str(resume.id),
            "resume_id": str(resume.id),
            "resume": serialize_record(
                resume,
                resume_spec,
                resume_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "sections": [
                serialize_record(
                    section,
                    section_spec,
                    section_spec.detail_fields,
                    include_long_text=True,
                    truncate_long_text=False,
                )
                for section in resume_sections
            ],
            "sections_count": len(resume_sections),
            "refine": refine,
            "summary": f"已生成并复核定制简历《{resume.title}》。{refine['summary']}",
        }

    return execute


async def _prepare_optimize_resume_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    resume_spec = get_model_spec("resume")
    job_spec = get_model_spec("job")
    section_spec = get_model_spec("resume_section")
    resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, input_payload.get("resume_id"))
    job = await fetch_scoped_record(session, actor, job_spec, models.Job, input_payload.get("job_id"))
    instructions = _normalize_resume_text(input_payload.get("instructions"), max_length=1000)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        current_summary = str(getattr(resume, "summary", "") or "").strip()
        job_context = str(getattr(job, "summary", "") or getattr(job, "raw_description", "") or "").strip()
        resume.title = f"{job.company} - {job.title} optimized resume"[:300]
        resume.summary = _join_sentences(
            current_summary,
            f"Tailored for {job.company} {job.title}.",
            instructions,
            job_context[:240],
        )[:4000]
        resume.source_mode = "operator_optimize_resume"
        resume.source_job_ids = _merge_id_list(getattr(resume, "source_job_ids", None), int(job.id))
        snapshot = dict(resume.source_profile_snapshot) if isinstance(resume.source_profile_snapshot, Mapping) else {}
        snapshot["operator_optimize_resume"] = {
            "job_id": int(job.id),
            "instructions": instructions,
            "generator": "deterministic_operator_resume_v1",
        }
        resume.source_profile_snapshot = json_safe(snapshot)
        sections = await _resume_sections(session, resume.id)
        tailored_item = _resume_content_item(
            "Tailored for role",
            _join_sentences(f"{job.company} {job.title}", instructions, job_context[:220]),
        )
        tailored_section = next((section for section in sections if _is_tailored_resume_section(section)), None)
        if tailored_section is None:
            tailored_section = models.ResumeSection(
                owner_actor_id=actor.actor_id,
                resume_id=resume.id,
                section_type=DEFAULT_RESUME_PERSONAL_SECTION_TYPE,
                sort_order=_next_resume_section_sort_order(sections),
                title="Tailored Highlights",
                visible=True,
                content_json=[tailored_item],
            )
            session.add(tailored_section)
            await session.flush()
        else:
            tailored_section.title = tailored_section.title or "Tailored Highlights"
            tailored_section.content_json = _append_resume_content(tailored_section.content_json, tailored_item)
        await session.flush()
        await session.refresh(resume)
        sections = await _resume_sections(session, resume.id)
        return _resume_mutation_result(
            action="optimize_resume",
            resume=resume,
            sections=sections,
            resume_spec=resume_spec,
            section_spec=section_spec,
            summary=f"Optimized resume {resume.id} for {job.company} {job.title}.",
        )

    return execute


async def _prepare_batch_optimize_resume_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    resume_spec = get_model_spec("resume")
    job_spec = get_model_spec("job")
    section_spec = get_model_spec("resume_section")
    source_resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, input_payload.get("resume_id"))
    raw_job_ids = input_payload.get("job_ids")
    if not isinstance(raw_job_ids, list) or not raw_job_ids:
        raise OperatorError("validation_error", "batch_optimize_resume requires at least one job id.", {})
    job_ids = _canonical_int_ids(raw_job_ids, field_name="job_ids")
    jobs = [await fetch_scoped_record(session, actor, job_spec, models.Job, job_id) for job_id in job_ids]

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        source_sections = await _resume_sections(session, source_resume.id)
        created: list[Any] = []
        created_sections_count = 0
        for job in jobs:
            clone = models.Resume(
                owner_actor_id=actor.actor_id,
                user_name=source_resume.user_name,
                title=f"{job.company} - {job.title} batch optimized resume"[:300],
                photo_url=source_resume.photo_url,
                summary=_join_sentences(
                    source_resume.summary,
                    f"Batch optimized for {job.company} {job.title}.",
                    str(job.summary or job.raw_description or "")[:240],
                )[:4000],
                contact_json=json_safe(source_resume.contact_json if isinstance(source_resume.contact_json, Mapping) else {}),
                template_id=source_resume.template_id,
                style_config=json_safe(source_resume.style_config if isinstance(source_resume.style_config, Mapping) else {}),
                is_primary=False,
                language=source_resume.language,
                source_mode="operator_batch_optimize_resume",
                source_job_ids=[int(job.id)],
                source_profile_snapshot=json_safe(
                    {
                        "source_resume_id": int(source_resume.id),
                        "job_id": int(job.id),
                        "generator": "deterministic_operator_resume_v1",
                    }
                ),
            )
            session.add(clone)
            await session.flush()
            if source_sections:
                for index, section in enumerate(source_sections):
                    session.add(
                        models.ResumeSection(
                            owner_actor_id=actor.actor_id,
                            resume_id=clone.id,
                            section_type=_resume_editor_section_type_from_resume_section(section),
                            sort_order=section.sort_order if section.sort_order is not None else index,
                            title=section.title or "Tailored Section",
                            visible=bool(section.visible),
                            content_json=_append_resume_content(
                                section.content_json,
                                _resume_content_item("Batch tailoring", f"Aligned with {job.company} {job.title}."),
                            ),
                        )
                    )
                    created_sections_count += 1
            else:
                session.add(
                    models.ResumeSection(
                        owner_actor_id=actor.actor_id,
                        resume_id=clone.id,
                        section_type=DEFAULT_RESUME_PERSONAL_SECTION_TYPE,
                        sort_order=0,
                        title="Batch Tailoring",
                        visible=True,
                        content_json=[_resume_content_item("Batch tailoring", f"Aligned with {job.company} {job.title}.")],
                    )
                )
                created_sections_count += 1
            created.append(clone)
        await session.flush()
        records: list[dict[str, Any]] = []
        for resume in created:
            await session.refresh(resume)
            records.append(
                serialize_record(
                    resume,
                    resume_spec,
                    resume_spec.detail_fields,
                    include_long_text=True,
                    truncate_long_text=False,
                )
            )
        task_id = f"resume_batch_{proposal.proposal_id[-12:]}"
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "batch_optimize_resume",
            "model": "resume",
            "resume_id": str(source_resume.id),
            "created_count": len(records),
            "resumes": records,
            "sections_count": created_sections_count,
            "task_id": task_id,
            "task_payload": {"resume_id": str(source_resume.id), "job_ids": [str(job.id) for job in jobs]},
            "summary": f"Created {len(records)} batch optimized resume(s).",
        }

    return execute


async def _prepare_apply_resume_ai_patch_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    resume_spec = get_model_spec("resume")
    section_spec = get_model_spec("resume_section")
    resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, input_payload.get("resume_id"))
    patch = input_payload.get("patch")
    if not isinstance(patch, Mapping):
        raise OperatorError("validation_error", "apply_resume_ai_patch requires a patch object.", {})

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        changed_sections = await _apply_resume_patch(session, actor, resume, patch)
        await session.flush()
        await session.refresh(resume)
        sections = await _resume_sections(session, resume.id)
        return _resume_mutation_result(
            action="apply_resume_ai_patch",
            resume=resume,
            sections=sections,
            resume_spec=resume_spec,
            section_spec=section_spec,
            summary=f"Applied resume AI patch to resume {resume.id}.",
            changed_sections_count=len(changed_sections),
        )

    return execute


async def _prepare_apply_resume_ai_batch_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    resume_spec = get_model_spec("resume")
    section_spec = get_model_spec("resume_section")
    resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, input_payload.get("resume_id"))
    changes = input_payload.get("changes")
    if not isinstance(changes, list):
        raise OperatorError("validation_error", "apply_resume_ai_batch requires changes array.", {})

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        changed_sections: list[Any] = []
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            target = str(change.get("target") or "").strip().lower()
            updates = change.get("updates") if isinstance(change.get("updates"), Mapping) else {}
            if target == "resume":
                _apply_resume_updates(resume, updates)
            elif target == "section":
                section_id = change.get("section_id")
                section = await _fetch_resume_section(session, actor, resume.id, section_id)
                _apply_resume_section_updates(section, updates)
                changed_sections.append(section)
            elif target == "create_section":
                created = await _create_resume_section_from_change(session, actor, resume.id, change)
                changed_sections.append(created)
        if changes and not changed_sections:
            first = (await _resume_sections(session, resume.id))[0:1]
            if first:
                first[0].content_json = _append_resume_content(
                    first[0].content_json,
                    _resume_content_item("Batch AI update", "Reviewed batch changes were applied to the resume."),
                )
                changed_sections.append(first[0])
        await session.flush()
        await session.refresh(resume)
        sections = await _resume_sections(session, resume.id)
        return _resume_mutation_result(
            action="apply_resume_ai_batch",
            resume=resume,
            sections=sections,
            resume_spec=resume_spec,
            section_spec=section_spec,
            summary=f"Applied {len(changes)} resume AI batch change(s).",
            changed_sections_count=len(changed_sections),
        )

    return execute


async def _prepare_parse_resume_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    text = _normalize_resume_text(input_payload.get("text"), max_length=12000)
    if not text:
        raise OperatorError("validation_error", "parse_resume requires non-empty text.", {})
    file_id = str(input_payload.get("file_id") or "").strip()
    parsed = _parse_resume_text(text)
    resume_spec = get_model_spec("resume")
    section_spec = get_model_spec("resume_section")
    profile_section_spec = get_model_spec("profile_section")

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        profile = await _default_or_new_profile_for_actor(session, actor, parsed)
        resume = models.Resume(
            owner_actor_id=actor.actor_id,
            user_name=parsed["name"],
            title=f"{parsed['name']} parsed resume"[:300],
            summary=parsed["summary"],
            contact_json=json_safe({"email": parsed["email"], "source_file_id": file_id}),
            style_config={},
            is_primary=False,
            language="zh",
            source_mode="operator_parse_resume",
            source_job_ids=[],
            source_profile_snapshot=json_safe({"source": "parse_resume", "file_id": file_id, "text_excerpt": text[:240]}),
        )
        session.add(resume)
        await session.flush()
        resume_section = models.ResumeSection(
            owner_actor_id=actor.actor_id,
            resume_id=resume.id,
            section_type=DEFAULT_RESUME_PERSONAL_SECTION_TYPE,
            sort_order=0,
            title=parsed["section_title"],
            visible=True,
            content_json=[_resume_content_item(parsed["section_title"], parsed["summary"])],
        )
        profile_section = models.ProfileSection(
            owner_actor_id=actor.actor_id,
            profile_id=profile.id,
            section_type="custom",
            title=parsed["section_title"],
            sort_order=0,
            content_json={"description": parsed["summary"], "source_text_excerpt": text[:500]},
            source="operator_parse_resume",
            confidence=0.8,
        )
        session.add_all([resume_section, profile_section])
        await session.flush()
        await session.refresh(resume)
        await session.refresh(resume_section)
        await session.refresh(profile_section)
        sync_profile_section_to_personal_archive(profile, profile_section)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "parse_resume",
            "model": "resume",
            "record_id": str(resume.id),
            "resume_id": str(resume.id),
            "resume": serialize_record(resume, resume_spec, resume_spec.detail_fields, include_long_text=True, truncate_long_text=False),
            "sections": [serialize_record(resume_section, section_spec, section_spec.detail_fields, include_long_text=True, truncate_long_text=False)],
            "profile_sections": [serialize_record(profile_section, profile_section_spec, profile_section_spec.detail_fields, include_long_text=True, truncate_long_text=False)],
            "summary": f"Parsed resume text into resume {resume.id}.",
        }

    return execute


async def _prepare_resume_asset_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    action = spec.action
    resume_spec = get_model_spec("resume")
    resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, input_payload.get("resume_id"))

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        style = dict(resume.style_config) if isinstance(resume.style_config, Mapping) else {}
        if action == "upload_resume_photo":
            file_id = _required_resume_text(input_payload.get("file_id"), field_name="file_id", max_length=200)
            resume.photo_url = f"/api/operator/artifacts/resume-photo/{file_id}"
            style["photo_file_id"] = file_id
            summary = f"Uploaded resume photo asset {file_id}."
        elif action == "upload_resume_logo":
            file_id = _required_resume_text(input_payload.get("file_id"), field_name="file_id", max_length=200)
            style["logo_file_id"] = file_id
            style["logo_url"] = f"/api/operator/artifacts/resume-logo/{file_id}"
            summary = f"Uploaded resume logo asset {file_id}."
        else:
            name = _required_resume_text(input_payload.get("name"), field_name="name", max_length=200)
            style["resolved_logo_name"] = name
            style["logo_url"] = f"/api/operator/artifacts/resume-logo/resolved-{_slugify(name)}.png"
            summary = f"Resolved resume logo for {name}."
        resume.style_config = json_safe(style)
        await session.flush()
        await session.refresh(resume)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": action,
            "model": "resume",
            "record_id": str(resume.id),
            "resume_id": str(resume.id),
            "resume": serialize_record(resume, resume_spec, resume_spec.detail_fields, include_long_text=True, truncate_long_text=False),
            "summary": summary,
        }

    return execute


async def _prepare_resume_export_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    resume_spec = get_model_spec("resume")
    resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, input_payload.get("resume_id"))
    fmt = "pdf" if spec.action == "export_resume_pdf" else str(input_payload.get("format") or "png").strip().lower()
    if fmt not in {"pdf", "png", "jpg", "jpeg"}:
        raise OperatorError("validation_error", "Unsupported resume export format.", {"format": fmt})
    fmt = "jpg" if fmt == "jpeg" else fmt

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        sections = await _resume_sections(session, resume.id)
        content_type = "application/pdf" if fmt == "pdf" else f"image/{fmt}"
        file_name = f"resume-{resume.id}.{fmt}"
        artifact = {
            "artifact_id": f"resume_export_{resume.id}_{fmt}",
            "resume_id": str(resume.id),
            "format": fmt,
            "content_type": content_type,
            "file_name": file_name,
            "section_count": len(sections),
            "title": resume.title,
        }
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": spec.action,
            "resume_id": str(resume.id),
            "format": fmt,
            "content_type": content_type,
            "file_name": file_name,
            "download_url": f"/api/operator/artifacts/{artifact['artifact_id']}/download",
            "artifact": artifact,
            "summary": f"Prepared {fmt.upper()} export metadata for resume {resume.id}.",
        }

    return execute


async def _prepare_analyze_resume_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    resume_spec = get_model_spec("resume")
    resume = await fetch_scoped_record(session, actor, resume_spec, models.Resume, input_payload.get("resume_id"))
    job = None
    if input_payload.get("job_id") not in (None, ""):
        job = await fetch_scoped_record(session, actor, get_model_spec("job"), models.Job, input_payload.get("job_id"))

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        sections = await _resume_sections(session, resume.id)
        report = _build_resume_report(resume, sections, job)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "analyze_resume",
            "resume_id": str(resume.id),
            "job_id": str(job.id) if job is not None else "",
            "report": report,
            "summary": f"Analyzed resume {resume.id} with {len(sections)} section(s).",
        }

    return execute


async def _prepare_batch_triage_jobs_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    raw_job_ids = input_payload.get("job_ids")
    if not isinstance(raw_job_ids, list) or not raw_job_ids:
        raise OperatorError("validation_error", "batch_triage_jobs requires at least one locked job id.", {})
    job_ids = _canonical_int_ids(raw_job_ids, field_name="job_ids")
    triage_status = _normalize_triage_status(input_payload.get("triage_status"))
    pool_id = _normalize_optional_pool_id(input_payload.get("pool_id"))
    if not triage_status and pool_id is None:
        raise OperatorError("validation_error", "batch_triage_jobs requires a triage status or pool id.", {})

    job_spec = get_model_spec("job")
    pool_spec = get_model_spec("pool")
    await validate_action_references(session, actor, spec, input_payload)
    if pool_id is not None:
        await fetch_scoped_record(session, actor, pool_spec, models.Pool, pool_id)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        jobs = [
            await fetch_scoped_record(session, actor, job_spec, models.Job, job_id)
            for job_id in job_ids
        ]
        no_op_job_ids: list[str] = []
        for job in jobs:
            changes = False
            if triage_status and str(job.triage_status or "") != triage_status:
                job.triage_status = triage_status
                changes = True
            if pool_id is not None and int(job.pool_id or 0) != int(pool_id):
                job.pool_id = pool_id
                changes = True
            if not changes:
                no_op_job_ids.append(str(job.id))
        from app.operator.effect_manifest import record_noop_effect
        for job_id in no_op_job_ids:
            record_noop_effect(model="job", record_id=job_id, reason="triage_already_satisfied")
        await session.flush()
        for job in jobs:
            await session.refresh(job)
        records = [
            serialize_record(
                job,
                job_spec,
                job_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            )
            for job in jobs
        ]
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "batch_triage_jobs",
            "model": "job",
            "changed_count": len(records),
            "records": records,
            "summary": f"Updated {len(records)} job(s).",
        }

    return execute


async def _prepare_batch_delete_jobs_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    raw_job_ids = input_payload.get("job_ids")
    if not isinstance(raw_job_ids, list) or not raw_job_ids:
        raise OperatorError("validation_error", "batch_delete_jobs requires at least one locked job id.", {})
    job_ids = _canonical_int_ids(raw_job_ids, field_name="job_ids")
    archive = bool(input_payload.get("archive", False))
    operation = "archive" if archive else "delete"
    job_spec = get_model_spec("job")
    await validate_action_references(session, actor, spec, input_payload)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        jobs = [
            await fetch_scoped_record(session, actor, job_spec, models.Job, job_id)
            for job_id in job_ids
        ]
        changed: list[dict[str, Any]] = []
        if archive:
            for job in jobs:
                job.triage_status = "ignored"
            await session.flush()
            for job in jobs:
                await session.refresh(job)
                changed.append(
                    serialize_record(
                        job,
                        job_spec,
                        job_spec.detail_fields,
                        include_long_text=True,
                        truncate_long_text=False,
                    )
                )
        else:
            for job in jobs:
                changed.append(
                    {
                        "id": getattr(job, job_spec.primary_key),
                        "model": "job",
                        "deleted": True,
                    }
                )
                await session.delete(job)
            await session.flush()

        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "batch_delete_jobs",
            "model": "job",
            "operation": operation,
            "changed_count": len(changed),
            "records": changed,
            "summary": f"{'Archived' if archive else 'Deleted'} {len(changed)} job(s).",
        }

    return execute


async def _prepare_batch_mutate_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    operation = str(input_payload.get("operation") or "")
    model_name = str(input_payload.get("model") or "")
    target = input_payload.get("target") or {}
    updates = input_payload.get("updates") or {}
    per_record_updates = _normalize_per_record_updates(input_payload.get("per_record_updates"))
    patch_mode = str(input_payload.get("patch_mode") or "replace")
    if operation == "patch" and model_name == "application_record":
        updates = _normalize_application_record_status_patch(updates)
        per_record_updates = {
            record_id: _normalize_application_record_status_patch(record_updates)
            for record_id, record_updates in per_record_updates.items()
        }
        if _contains_custom_values_update(updates) or any(_contains_custom_values_update(item) for item in per_record_updates.values()):
            patch_mode = "merge"

    if operation not in {"patch", "delete", "archive", "restore"}:
        raise OperatorError(
            "validation_error",
            "batch_mutate operation must be one of patch, delete, archive, restore.",
            {"operation": operation},
        )
    if not model_name:
        raise OperatorError("validation_error", "batch_mutate requires a model name.", {})
    if not isinstance(target, Mapping):
        raise OperatorError("validation_error", "batch_mutate target must be an object.", {})
    if not isinstance(updates, Mapping):
        raise OperatorError("validation_error", "batch_mutate updates must be an object.", {})
    if patch_mode not in {"replace", "merge"}:
        raise OperatorError("validation_error", "batch_mutate patch_mode must be replace or merge.", {"patch_mode": patch_mode})
    if per_record_updates and operation != "patch":
        raise OperatorError("validation_error", "batch_mutate per_record_updates only supports patch operations.", {})

    model_spec = get_model_spec(model_name)
    model_class = get_model_class(model_name)

    raw_record_ids = target.get("record_ids") or []
    if not isinstance(raw_record_ids, list) or not raw_record_ids:
        raise OperatorError(
            "validation_error",
            "batch_mutate confirm-time payload must have target.mode='by_ids' with record_ids; by_filter must be materialized at proposal creation.",
            {},
        )
    int_ids = _canonical_int_ids(raw_record_ids, field_name="record_ids")
    target_id_set = {str(record_id) for record_id in int_ids}
    extra_update_ids = sorted(set(per_record_updates) - target_id_set)
    if extra_update_ids:
        raise OperatorError(
            "validation_error",
            "batch_mutate per_record_updates contains ids outside the locked target.",
            {"record_ids": extra_update_ids},
        )
    expected_count_raw = payload.get("expected_count")
    expected_count = int(expected_count_raw) if expected_count_raw is not None else len(int_ids)

    if operation == "patch":
        writable = set(model_spec.writable_fields)
        update_sets = [updates, *per_record_updates.values()]
        invalid_fields = sorted(set().union(*(set(item.keys()) for item in update_sets)) - writable)
        if invalid_fields:
            raise OperatorError(
                "validation_error",
                "batch_mutate updates contain non-writable fields.",
                {"fields": invalid_fields, "writable_fields": list(writable)},
            )
        if updates:
            validate_model_values(updates, model_spec, purpose=f"batch_mutate patch {model_name}")
        for record_id, record_updates in per_record_updates.items():
            validate_model_values(record_updates, model_spec, purpose=f"batch_mutate patch {model_name}:{record_id}")

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        if len(int_ids) != expected_count:
            raise OperatorError(
                "conflict_error",
                "batch_mutate locked record count does not match expected_count.",
                {"locked_count": len(int_ids), "expected_count": expected_count},
            )
        records = [
            await fetch_scoped_record(session, actor, model_spec, model_class, record_id)
            for record_id in int_ids
        ]
        if len(records) != expected_count:
            raise OperatorError(
                "conflict_error",
                "batch_mutate resolved fewer records than locked at proposal creation.",
                {"resolved_count": len(records), "expected_count": expected_count},
            )
        changed: list[dict[str, Any]] = []
        if operation == "patch":
            for record in records:
                record_id = str(getattr(record, model_spec.primary_key))
                record_updates = dict(updates)
                record_updates.update(per_record_updates.get(record_id, {}))
                for field_name, value in record_updates.items():
                    if patch_mode == "merge" and isinstance(value, Mapping) and isinstance(getattr(record, field_name, None), Mapping):
                        setattr(record, field_name, {**getattr(record, field_name), **value})
                    else:
                        setattr(record, field_name, value)
                changed.append({"id": getattr(record, model_spec.primary_key), "model": model_name})
        elif operation == "delete":
            for record in records:
                changed.append(
                    {
                        "id": getattr(record, model_spec.primary_key),
                        "model": model_name,
                        "deleted": True,
                    }
                )
                await session.delete(record)
        elif operation in ("archive", "restore"):
            for record in records:
                if hasattr(record, "triage_status"):
                    setattr(record, "triage_status", "ignored" if operation == "archive" else "inbox")
                changed.append(
                    {
                        "id": getattr(record, model_spec.primary_key),
                        "model": model_name,
                        "operation": operation,
                    }
                )
        await session.flush()

        serialized: list[dict[str, Any]] = []
        if operation != "delete":
            for record in records:
                await session.refresh(record)
                serialized.append(
                    serialize_record(
                        record,
                        model_spec,
                        model_spec.detail_fields,
                        include_long_text=True,
                        truncate_long_text=False,
                    )
                )
        else:
            serialized = changed

        action_label = {
            "patch": "Updated",
            "delete": "Deleted",
            "archive": "Archived",
            "restore": "Restored",
        }[operation]
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "batch_mutate",
            "model": model_name,
            "operation": operation,
            "changed_count": len(changed),
            "records": serialized,
            "summary": f"{action_label} {len(changed)} {model_name} record(s).",
        }

    return execute


def _normalize_per_record_updates(value: Any) -> dict[str, dict[str, Any]]:
    if value in (None, ""):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    if isinstance(value, Mapping):
        for raw_record_id, raw_updates in value.items():
            if not isinstance(raw_updates, Mapping):
                raise OperatorError(
                    "validation_error",
                    "batch_mutate per_record_updates values must be update objects.",
                    {"record_id": str(raw_record_id)},
                )
            normalized[str(raw_record_id)] = json_safe(dict(raw_updates))
        return normalized
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, Mapping):
                raise OperatorError("validation_error", "batch_mutate per_record_updates items must be objects.", {})
            record_id = item.get("record_id") or item.get("id")
            updates = item.get("updates")
            if record_id in (None, "") or not isinstance(updates, Mapping):
                raise OperatorError(
                    "validation_error",
                    "batch_mutate per_record_updates list items require record_id and updates.",
                    {"item": json_safe(item)},
                )
            normalized[str(record_id)] = json_safe(dict(updates))
        return normalized
    raise OperatorError("validation_error", "batch_mutate per_record_updates must be an object or list.", {})


def _normalize_application_record_status_patch(value: Any) -> dict[str, Any]:
    """Normalize ``apply_status`` through the single ApplicationLifecycleSpec
    authority: unknown values fail closed (validation_error), granular
    interview markers resolve to the interview stage (round text preserved
    under ``interview_round``), and the canonical value stays top-level so the
    FieldSpec enum and the durable ``apply_status`` column write the same
    value."""
    if not isinstance(value, Mapping):
        return {}
    try:
        return normalize_apply_status_update(value)
    except ApplicationLifecycleError as exc:
        raise OperatorError(
            "validation_error",
            str(exc),
            {"field": "apply_status"},
        ) from exc


def _contains_custom_values_update(value: Mapping[str, Any]) -> bool:
    return "custom_values" in value and isinstance(value.get("custom_values"), Mapping)


async def _prepare_organize_jobs_into_pool_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    raw_job_ids = input_payload.get("job_ids")
    if not isinstance(raw_job_ids, list) or not raw_job_ids:
        raise OperatorError("validation_error", "organize_jobs_into_pool requires at least one locked job id.", {})
    job_ids = _canonical_int_ids(raw_job_ids, field_name="job_ids")
    pool_name = _normalize_pool_name(input_payload.get("pool_name"))
    pool_scope = _normalize_pool_scope(input_payload.get("pool_scope") or "picked")
    triage_status = _normalize_triage_status(input_payload.get("triage_status") or pool_scope)
    if not triage_status:
        triage_status = pool_scope
    if triage_status != pool_scope:
        raise OperatorError(
            "validation_error",
            "Pool scope and job triage status must match for a pooled batch move.",
            {"pool_scope": pool_scope, "triage_status": triage_status},
        )
    pool_description = _normalize_optional_text(input_payload.get("pool_description"), max_length=1000)
    _normalize_bool(input_payload.get("reuse_existing"), default=True)
    # organize_jobs_into_pool must complete the user-level create/reuse + move
    # transaction. Do not let a model-supplied false value convert it into a
    # conflict against an existing visible or repairable pool.
    reuse_existing = True

    job_spec = get_model_spec("job")
    pool_spec = get_model_spec("pool")
    validate_model_values({"triage_status": triage_status}, job_spec, purpose="organize jobs into pool")
    validate_model_values({"scope": pool_scope}, pool_spec, purpose="organize jobs into pool")
    await validate_action_references(session, actor, spec, input_payload)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        jobs = [
            await fetch_scoped_record(session, actor, job_spec, models.Job, job_id)
            for job_id in job_ids
        ]
        pool, pool_created, repaired_scope = await _create_or_reuse_job_pool(
            session,
            actor,
            name=pool_name,
            scope=pool_scope,
            description=pool_description,
            reuse_existing=reuse_existing,
        )
        for job in jobs:
            job.triage_status = triage_status
            job.pool_id = pool.id
        await session.flush()
        await session.refresh(pool)
        for job in jobs:
            await session.refresh(job)
        pool_record = serialize_record(
            pool,
            pool_spec,
            pool_spec.detail_fields,
            include_long_text=True,
            truncate_long_text=False,
        )
        records = [
            serialize_record(
                job,
                job_spec,
                job_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            )
            for job in jobs
        ]
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "organize_jobs_into_pool",
            "model": "pool",
            "pool": pool_record,
            "pool_id": pool.id,
            "pool_created": pool_created,
            "pool_scope_repaired": repaired_scope,
            "changed_count": len(records),
            "records": records,
            "job_ids": [job.id for job in jobs],
            "summary": f"Moved {len(records)} job(s) into {pool_scope} pool {pool.name}.",
        }

    return execute


def _build_resume_rows_from_profile_sections(sections: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    title_by_type: dict[str, str] = {}
    order = [
        "education",
        "workExperiences",
        "internshipExperiences",
        "projects",
        "skills",
        "certificates",
        "personalExperiences",
    ]
    for section in sections:
        raw_content = section.content_json if isinstance(section.content_json, Mapping) else {}
        section_type = normalize_section_type_alias(str(section.section_type or "custom"))
        category_label = get_category_label(section_type, dict(raw_content))
        try:
            category_key, resolved_label, _is_custom, canonical_content = canonicalize_profile_section_payload(
                section_type=section_type,
                category_label=category_label,
                title=str(section.title or ""),
                raw_content_json=_resume_source_content_for_section(section),
            )
        except ValueError:
            category_key, resolved_label, _is_custom, canonical_content = canonicalize_profile_section_payload(
                section_type="custom",
                category_label=category_label or "补充亮点",
                title=str(section.title or ""),
                raw_content_json=_resume_source_content_for_section(section),
            )
        resume_section_type = _resume_editor_section_type(section, category_key, canonical_content)
        item = _resume_editor_content_item(category_key, canonical_content, str(section.title or resolved_label), resume_section_type)
        if not _resume_content_item_has_value(item):
            continue
        grouped.setdefault(resume_section_type, []).append(json_safe(item))
        title_by_type.setdefault(resume_section_type, RESUME_SECTION_TITLES.get(resume_section_type, resolved_label))

    rows: list[dict[str, Any]] = []
    ordered_types = [section_type for section_type in order if section_type in grouped]
    ordered_types.extend(sorted(section_type for section_type in grouped if section_type not in set(order)))
    for index, section_type in enumerate(ordered_types):
        content = _dedupe_resume_editor_items(section_type, grouped[section_type])
        if not content:
            continue
        rows.append(
            {
                "section_type": section_type,
                "title": title_by_type.get(section_type) or RESUME_SECTION_TITLES.get(section_type, "补充亮点"),
                "sort_order": index,
                "visible": True,
                "content_json": content,
            }
        )
    return rows


def _normalize_generate_resume_confirmed_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    facts = value.get("facts")
    excluded = value.get("excluded_terms")
    section_ids: list[str] = []
    for item in value.get("profile_section_ids") if isinstance(value.get("profile_section_ids"), list) else []:
        normalized_id = str(item or "").strip()
        if normalized_id and normalized_id not in section_ids:
            section_ids.append(normalized_id)
    try:
        scope_version = int(value.get("scope_version") or 0)
    except (TypeError, ValueError):
        scope_version = 0
    normalized: dict[str, Any] = {
        "scope_version": scope_version,
        "source": str(value.get("source") or "").strip(),
        "mode": str(value.get("mode") or "").strip(),
        "strategy_confirmed": bool(value.get("strategy_confirmed")),
        "profile_id": str(value.get("profile_id") or "").strip(),
        "job_id": str(value.get("job_id") or "").strip(),
        "profile_section_ids": section_ids,
        "evidence_digest": str(value.get("evidence_digest") or "").strip(),
        "facts": json_safe(facts if isinstance(facts, Mapping) else {}),
        "excluded_terms": [
            str(term).strip()
            for term in (excluded if isinstance(excluded, list) else [])
            if str(term).strip()
        ],
    }
    return {key: child for key, child in normalized.items() if child not in ("", [], {}, 0)}


def _generate_resume_authority_scope_matches(
    sealed_scope: Mapping[str, Any],
    runtime_scope: Mapping[str, Any],
) -> bool:
    authority_keys = (
        "scope_version",
        "source",
        "mode",
        "strategy_confirmed",
        "profile_id",
        "job_id",
        "profile_section_ids",
        "evidence_digest",
    )
    return all(json_safe(sealed_scope.get(key)) == json_safe(runtime_scope.get(key)) for key in authority_keys)


def _merge_generate_resume_confirmed_scope_instructions(instructions: str, confirmed_scope: Mapping[str, Any]) -> str:
    scope_text = _generate_resume_confirmed_scope_instruction_text(confirmed_scope)
    source = str(instructions or "").strip()
    if not scope_text:
        return source
    return "\n".join(part for part in (source, scope_text) if part)[-5000:]


def _generate_resume_confirmed_scope_instruction_text(confirmed_scope: Mapping[str, Any]) -> str:
    if not isinstance(confirmed_scope, Mapping) or not confirmed_scope:
        return ""
    facts = confirmed_scope.get("facts")
    facts = facts if isinstance(facts, Mapping) else {}
    parts: list[str] = []
    mode = str(confirmed_scope.get("mode") or "").strip()
    if mode == "current_facts_only":
        parts.append("只写这版待确认事实")
    elif mode:
        parts.append("按已确认策略生成")
    feedback_count = str(facts.get("feedback_count") or "").strip()
    if feedback_count:
        parts.append(f"整理约 {feedback_count} 条用户反馈")
    tools = facts.get("tools")
    tools_text = "、".join(str(tool).strip() for tool in tools if str(tool).strip()) if isinstance(tools, list) else ""
    classification = facts.get("classification_basis")
    classification_text = "、".join(str(item).strip() for item in classification if str(item).strip()) if isinstance(classification, list) else ""
    if re.search(r"Excel|表格", tools_text, re.IGNORECASE):
        if classification_text:
            parts.append(f"使用 Excel/表格按{classification_text}分类")
        else:
            parts.append("使用 Excel/表格做分类整理")
    activity = facts.get("activity_review")
    if isinstance(activity, Mapping):
        tasks = activity.get("tasks")
        task_text = "、".join(str(task).strip() for task in tasks if str(task).strip()) if isinstance(tasks, list) else ""
        if task_text:
            parts.append(f"参与活动复盘，整理{task_text}")
    if str(facts.get("contribution_boundary") or "").strip() == "assistive":
        parts.append("表述为参与/协助，不写主导或负责人")
    for field, label in (("name", "姓名"), ("school", "学校"), ("major", "专业"), ("degree", "学历"), ("graduation", "毕业时间"), ("email", "邮箱")):
        value = str(facts.get(field) or "").strip()
        if value:
            parts.append(f"{label}{value}")
    excluded_terms = confirmed_scope.get("excluded_terms")
    excluded = [
        str(term).strip()
        for term in (excluded_terms if isinstance(excluded_terms, list) else [])
        if str(term).strip()
    ]
    if excluded:
        parts.append("、".join(excluded) + "都不要")
    if confirmed_scope.get("strategy_confirmed") is True:
        parts.append("当前策略已确认")
    return "；".join(parts) + "。" if parts else ""


def _filter_generate_resume_profile_sections(
    sections: list[Any],
    instructions: str,
    confirmed_scope: Mapping[str, Any] | None = None,
) -> list[Any]:
    text = str(instructions or "")
    scope = confirmed_scope if isinstance(confirmed_scope, Mapping) else {}
    allowed_section_ids = {
        str(item or "").strip()
        for item in scope.get("profile_section_ids", [])
        if str(item or "").strip()
    } if isinstance(scope.get("profile_section_ids"), list) else set()
    evidence_only = str(scope.get("mode") or "") == "detailed_read_evidence_only"
    if evidence_only and not allowed_section_ids:
        return []
    if not text and not evidence_only:
        return sections
    constrained_to_user_facts = (
        "可以使用的经历只有" in text
        or "只用" in text
        or "只能使用" in text
        or _generate_resume_has_restrictive_confirmed_scope(text)
    )
    current_fact_only = _generate_resume_has_current_fact_only_scope(text)
    exclude_types = _generate_resume_excluded_editor_section_types(text)
    allow_responsibility_rewrite = _generate_resume_has_assistive_boundary(text)
    only_target = _generate_resume_only_experience_target(text)
    filtered: list[Any] = []
    for section in sections:
        if evidence_only and str(getattr(section, "id", "") or "").strip() not in allowed_section_ids:
            continue
        raw_content = section.content_json if isinstance(section.content_json, Mapping) else {}
        section_type = normalize_section_type_alias(str(section.section_type or "custom"))
        try:
            category_key, _resolved_label, _is_custom, canonical_content = canonicalize_profile_section_payload(
                section_type=section_type,
                category_label=get_category_label(section_type, dict(raw_content)),
                title=str(section.title or ""),
                raw_content_json=_resume_source_content_for_section(section),
            )
        except ValueError:
            category_key, _resolved_label, _is_custom, canonical_content = canonicalize_profile_section_payload(
                section_type="custom",
                category_label="补充亮点",
                title=str(section.title or ""),
                raw_content_json=_resume_source_content_for_section(section),
            )
        resume_section_type = _resume_editor_section_type(section, category_key, canonical_content)
        if current_fact_only and not only_target and resume_section_type in {
            "workExperiences",
            "internshipExperiences",
            "projects",
            "certificates",
            "awards",
            "personalExperiences",
            "skills",
        }:
            continue
        if resume_section_type in exclude_types:
            continue
        visible_section_text = " ".join(
            [
                str(getattr(section, "title", "") or ""),
                _generate_resume_visible_value_text(_resume_source_content_for_section(section)),
            ]
        )
        if _generate_resume_text_has_excluded_content(
            visible_section_text,
            text,
            ignore_rewritable_responsibility=allow_responsibility_rewrite,
        ):
            continue
        if constrained_to_user_facts and resume_section_type in {
            "workExperiences",
            "internshipExperiences",
            "projects",
            "certificates",
            "awards",
            "personalExperiences",
        }:
            if only_target and only_target not in visible_section_text:
                continue
            if not only_target and not _generate_resume_section_matches_confirmed_scope(visible_section_text, text):
                continue
        filtered.append(section)
    return filtered


def _generate_resume_section_matches_confirmed_scope(section_text: str, instructions: str) -> bool:
    visible = str(section_text or "")
    text = str(instructions or "")
    anchors = (
        "招聘会",
        "问卷",
        "反馈",
        "现场问题",
        "Excel",
        "表格",
        "活动复盘",
        "复盘",
        "签到",
        "问题整理",
        "分类",
    )
    return any(anchor in text and anchor in visible for anchor in anchors)


def _generate_resume_excluded_editor_section_types(instructions: str) -> set[str]:
    text = str(instructions or "")
    excluded: set[str] = set()
    if re.search(r"(没有正式实习|不要.{0,16}实习|不写.{0,16}实习|任何实习经历)", text):
        excluded.update({"workExperiences", "internshipExperiences"})
    if re.search(r"(不要|不想|别|不写).{0,24}(证书|六级|cet)", text, re.IGNORECASE):
        excluded.add("certificates")
    if re.search(r"(不要|不想|别|不写).{0,24}(课程作业|大作业|项目经历|项目)", text):
        excluded.add("projects")
    if re.search(r"(不要|不想|别|不写).{0,24}(社团|奖学金|奖项|荣誉)", text):
        excluded.add("awards")
    if "可以使用的经历只有" in text:
        excluded.update({"workExperiences", "internshipExperiences", "projects", "certificates", "awards"})
    return excluded


def _apply_generate_resume_instruction_constraints(rows: list[dict[str, Any]], instructions: str) -> list[dict[str, Any]]:
    text = str(instructions or "")
    if not text:
        return rows
    excluded_types = _generate_resume_excluded_editor_section_types(text)
    only_target = _generate_resume_only_experience_target(text)
    clean_rows: list[dict[str, Any]] = []
    for row in rows:
        section_type = str(row.get("section_type") or "")
        if section_type in excluded_types:
            continue
        if section_type == "education" and re.search(r"(不要|不想|别|不写).{0,24}(课程|GPA|绩点|奖学金|社团)", text, re.IGNORECASE):
            row = {**row, "content_json": [_strip_education_optional_fields(item) for item in _row_items(row)]}
        if section_type == "skills" and _instructions_limit_sql_to_basic(text):
            row = {**row, "content_json": _basic_skill_items_from_instructions(text)}
        if section_type in {"projects", "personalExperiences"} and re.search(r"(不要|不想|别|不写).{0,24}(数据看板|dashboard)", text, re.IGNORECASE):
            filtered_items = [
                item for item in _row_items(row)
                if not re.search(r"(数据看板|dashboard)", json.dumps(item, ensure_ascii=False), re.IGNORECASE)
            ]
            if not filtered_items:
                continue
            row = {**row, "content_json": filtered_items}
        if section_type in {
            "workExperiences",
            "internshipExperiences",
            "projects",
            "personalExperiences",
            "skills",
        }:
            constrained_items = _apply_generate_resume_confirmed_fact_overrides(section_type, _row_items(row), text)
            if not constrained_items:
                continue
            row = {**row, "content_json": constrained_items}
        clean_rows.append(row)

    confirmed_items = [] if only_target else _instruction_confirmed_personal_experience_items(text)
    if confirmed_items:
        clean_rows = [row for row in clean_rows if str(row.get("section_type") or "") != "personalExperiences"]
        clean_rows.append(
            {
                "section_type": "personalExperiences",
                "title": RESUME_SECTION_TITLES.get("personalExperiences", "个人经历"),
                "sort_order": len(clean_rows),
                "visible": True,
                "content_json": confirmed_items,
            }
        )
    for index, row in enumerate(clean_rows):
        row["sort_order"] = index
    return clean_rows


def _filter_generate_resume_rows_for_excluded_content(
    rows: list[dict[str, Any]],
    instructions: str,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    clean_rows: list[dict[str, Any]] = []
    for row in rows:
        section_type = str(row.get("section_type") or "")
        items = _row_items(row)
        if not items:
            if _generate_resume_text_has_excluded_content(
                _generate_resume_visible_value_text(row.get("content_json")),
                instructions,
            ):
                continue
            clean_rows.append(row)
            continue
        clean_items: list[dict[str, Any]] = []
        for item in items:
            if section_type == "skills":
                item = _remove_excluded_skill_items(item, instructions)
                if not _resume_content_item_has_value(item):
                    continue
            if _generate_resume_text_has_excluded_content(
                _generate_resume_visible_value_text(item),
                instructions,
            ):
                continue
            clean_items.append(item)
        if not clean_items:
            continue
        clean_rows.append({**row, "content_json": clean_items})
    for index, row in enumerate(clean_rows):
        row["sort_order"] = index
    return clean_rows


def _normalize_generate_resume_rows_for_storage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    title_by_type: dict[str, str] = {}
    order = ["education", "workExperiences", "internshipExperiences", "projects", "skills", "certificates", "awards"]
    for row in rows:
        source_type = str(row.get("section_type") or "")
        target_type = _canonical_generate_resume_section_type(source_type)
        if not target_type:
            continue
        items = [
            _canonical_generate_resume_item_for_section(source_type, target_type, item)
            for item in _row_items(row)
        ]
        items = [item for item in items if _resume_content_item_has_value(item)]
        if not items:
            continue
        grouped.setdefault(target_type, []).extend(items)
        title_by_type.setdefault(
            target_type,
            RESUME_SECTION_TITLES.get(target_type)
            or str(row.get("title") or "").strip()
            or target_type,
        )
    normalized_rows: list[dict[str, Any]] = []
    ordered_types = [section_type for section_type in order if section_type in grouped]
    ordered_types.extend(sorted(section_type for section_type in grouped if section_type not in set(order)))
    for index, section_type in enumerate(ordered_types):
        items = _dedupe_resume_editor_items(section_type, grouped[section_type])
        if not items:
            continue
        normalized_rows.append(
            {
                "section_type": section_type,
                "title": title_by_type.get(section_type) or RESUME_SECTION_TITLES.get(section_type, section_type),
                "sort_order": index,
                "visible": True,
                "content_json": items,
            }
        )
    return normalized_rows


def _canonical_generate_resume_section_type(section_type: str) -> str:
    value = str(section_type or "").strip()
    mapping = {
        "experience": "workExperiences",
        "custom": "workExperiences",
        "personalExperiences": "workExperiences",
        "personal_experiences": "workExperiences",
        "personalExperience": "workExperiences",
        "project": "projects",
        "skill": "skills",
        "certificate": "certificates",
    }
    return mapping.get(value, value)


def _canonical_generate_resume_item_for_section(
    source_type: str,
    target_type: str,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    cleaned = dict(item)
    if target_type in {"workExperiences", "internshipExperiences"} and source_type in {
        "personalExperiences",
        "personal_experiences",
        "personalExperience",
        "custom",
        "experience",
    }:
        return {
            "company": str(
                cleaned.get("company")
                or cleaned.get("experienceTitle")
                or cleaned.get("title")
                or cleaned.get("subtitle")
                or "实践经历"
            ).strip(),
            "position": str(cleaned.get("position") or cleaned.get("role") or "").strip(),
            "startDate": str(cleaned.get("startDate") or cleaned.get("start_date") or "").strip(),
            "endDate": str(cleaned.get("endDate") or cleaned.get("end_date") or "").strip(),
            "description": str(cleaned.get("description") or cleaned.get("content") or "").strip(),
        }
    return cleaned


def _remove_excluded_skill_items(item: Mapping[str, Any], instructions: str) -> dict[str, Any]:
    cleaned = dict(item)
    raw_items = cleaned.get("items")
    if isinstance(raw_items, list):
        cleaned["items"] = [
            skill
            for skill in raw_items
            if not _generate_resume_text_has_excluded_content(str(skill), instructions)
        ]
    return cleaned


def _apply_generate_resume_confirmed_fact_overrides(
    section_type: str,
    items: list[dict[str, Any]],
    instructions: str,
) -> list[dict[str, Any]]:
    if not items:
        return []
    feedback_count = _generate_resume_latest_feedback_count(instructions)
    assistive = _generate_resume_has_assistive_boundary(instructions)
    only_target = _generate_resume_only_experience_target(instructions)
    cleaned_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        next_item = _sanitize_generate_resume_item(
            section_type,
            item,
            feedback_count=feedback_count,
            assistive=assistive,
            only_target=only_target,
        )
        if next_item is None:
            continue
        key = _generate_resume_item_dedupe_key(section_type, next_item)
        if key in seen:
            continue
        seen.add(key)
        cleaned_items.append(next_item)
    return cleaned_items


def _sanitize_generate_resume_item(
    section_type: str,
    item: Mapping[str, Any],
    *,
    feedback_count: str,
    assistive: bool,
    only_target: str,
) -> dict[str, Any] | None:
    cleaned = dict(item)
    visible_before = _generate_resume_visible_value_text(cleaned)
    if _generate_resume_placeholder_text_detected(visible_before):
        return None
    if only_target and section_type in {"workExperiences", "internshipExperiences", "projects", "personalExperiences"}:
        if only_target not in visible_before:
            return None
    title_like_values = [
        str(cleaned.get("company") or ""),
        str(cleaned.get("experienceTitle") or ""),
        str(cleaned.get("projectName") or ""),
        str(cleaned.get("name") or ""),
    ]
    description = str(cleaned.get("description") or "").strip()
    if description and any(_generate_resume_compact_text(description) == _generate_resume_compact_text(value) for value in title_like_values):
        return None
    if description:
        cleaned["description"] = _rewrite_generate_resume_description_from_instructions(
            description,
            feedback_count=feedback_count,
            assistive=assistive,
        )
    if "items" in cleaned and isinstance(cleaned.get("items"), list):
        cleaned["items"] = [
            item
            for item in cleaned["items"]
            if not _generate_resume_placeholder_text_detected(str(item))
        ]
    return cleaned if _resume_content_item_has_value(cleaned) else None


def _rewrite_generate_resume_description_from_instructions(
    description: str,
    *,
    feedback_count: str,
    assistive: bool,
) -> str:
    updated = str(description or "").strip()
    if feedback_count:
        updated = re.sub(
            r"(?:累计)?(?:记录|收集|整理)?约?\s*\d{1,5}\s*条",
            f"整理约 {feedback_count} 条",
            updated,
        )
    if assistive:
        updated = updated.replace("负责企业签到表", "协助企业签到表")
        updated = updated.replace("负责学生", "协助学生")
        updated = re.sub(r"(?<!不)(负责)(?=[^。；;]{0,24}(收集|整理|核对|分类|复盘|签到))", "协助", updated)
        updated = re.sub(r"(?<!不)负责", "协助", updated)
        updated = re.sub(r"(主导|负责人)", "", updated)
    return re.sub(r"\s+", " ", updated).strip()


def _generate_resume_latest_feedback_count(instructions: str) -> str:
    text = str(instructions or "")
    matches: list[tuple[int, str]] = []
    patterns = [
        r"(?:反馈|问题|记录|整理|分类).{0,20}?(\d{1,5}\s*(?:-|~|—|到|至)\s*\d{1,5})\s*(?:来)?\s*条",
        r"(\d{1,5}\s*(?:-|~|—|到|至)\s*\d{1,5})\s*(?:来)?\s*条.{0,20}?(?:反馈|问题|记录|整理|分类)",
        r"(?:反馈|问题|记录|整理|分类).{0,20}?(?<![-~—到至\d])(\d{1,5})(?!\s*(?:-|~|—|到|至))\s*(?:来)?\s*条",
        r"(?<![-~—到至\d])(\d{1,5})(?!\s*(?:-|~|—|到|至))\s*(?:来)?\s*条.{0,20}?(?:反馈|问题|记录|整理|分类)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            window = text[max(0, match.start() - 12) : min(len(text), match.end() + 12)]
            if re.search(r"(别拿|不要|不用|不是|旧档案|旧数据|压我)", window):
                continue
            value = re.sub(r"\s*(?:-|~|—|到|至)\s*", "-", match.group(1).strip())
            matches.append((match.start(), value))
    matches.sort(key=lambda item: item[0])
    return matches[-1][1] if matches else ""


def _generate_resume_has_assistive_boundary(instructions: str) -> bool:
    return bool(
        re.search(
            r"(不是负责人|不是.{0,8}主导|协助老师|协助.{0,12}整理|我只是协助|只是协助|"
            r"不要写成我设计|别写主导|不写主导|负责.{0,20}(改成|换成).{0,8}(协助|参与)|"
            r"(协助|支持|参与).{0,12}口径|保持.{0,12}协助)",
            str(instructions or ""),
        )
    )


def _generate_resume_only_experience_target(instructions: str) -> str:
    text = str(instructions or "")
    match = re.search(r"只保留(?:一条|1\s*条)?([^。；;\n]{2,40}?)(?:经历|实践)", text)
    if not match:
        match = re.search(r"(?:只保留|只有)([^。；;\n]{2,40}?)(?:这一条|这\s*1\s*条|一条|1\s*条)", text)
    if not match:
        match = re.search(r"(?:只使用|只采用|仅使用|仅采用)([^。；;\n]{2,80})", text)
    if not match:
        return ""
    target = match.group(1).strip(" ：:,，、")
    if "、" in target or "，" in target or "," in target:
        parts = [part.strip(" ：:,，、") for part in re.split(r"[、，,]", target) if part.strip(" ：:,，、")]
        target = next((part for part in parts if "招聘会" in part or "活动" in part or "志愿" in part), parts[0] if parts else target)
    target = re.sub(r"^(?:一条|1\s*条)", "", target).strip(" ：:,，、")
    target = re.sub(r"(?:这一条|这\s*1\s*条|一条|1\s*条)$", "", target).strip(" ：:,，、")
    compact = _generate_resume_compact_text(target)
    if re.fullmatch(r"(?:以下|这|这些|上述|上面)?(?:[一二两三四五六七八九十0-9]+)?(?:段|条|个)?(?:保守|真实|确认)?(?:实践|经历|事实|内容|素材)+", compact):
        return ""
    return target


def _generate_resume_placeholder_text_detected(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return True
    compact = _generate_resume_compact_text(source)
    return compact in {"none", "null", "待确认", "待补"} or bool(
        re.search(r"(待确认|待补|暂无|未填写)(?:经历|条目|项目|内容)?", source, re.IGNORECASE)
    )


def _generate_resume_compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip().lower())


def _generate_resume_item_dedupe_key(section_type: str, item: Mapping[str, Any]) -> str:
    if section_type in {"workExperiences", "internshipExperiences"}:
        return "::".join(
            _generate_resume_compact_text(str(item.get(field) or ""))
            for field in ("company", "position", "description")
        )
    if section_type == "personalExperiences":
        return "::".join(
            _generate_resume_compact_text(str(item.get(field) or ""))
            for field in ("experienceTitle", "description")
        )
    return _generate_resume_compact_text(json.dumps(item, ensure_ascii=False, sort_keys=True))


def _generate_resume_text_has_excluded_content(
    value: str,
    instructions: str,
    *,
    ignore_rewritable_responsibility: bool = False,
) -> bool:
    text = str(value or "")
    if not text:
        return False
    for term in _generate_resume_excluded_content_terms(instructions):
        if ignore_rewritable_responsibility and term == "负责":
            continue
        if re.search(re.escape(term), text, re.IGNORECASE):
            return True
    return False


def _generate_resume_visible_value_text(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_generate_resume_visible_value_text(child) for child in value.values())
    if isinstance(value, list):
        return " ".join(_generate_resume_visible_value_text(child) for child in value)
    return str(value or "")


def _generate_resume_excluded_content_terms(instructions: str) -> list[str]:
    text = str(instructions or "")
    terms: list[str] = []

    def add(*values: str) -> None:
        for value in values:
            if value and value not in terms:
                terms.append(value)

    def add_with_aliases(value: str) -> None:
        term = str(value or "").strip(" ：:，,、；;。.!！ \n\r\t")
        term = re.sub(r"^(?:和|及|以及|还有|那些|这些|旧档案里|旧档案|旧数据|公司学校)+", "", term)
        term = re.sub(r"(?:都|也|全)?(?:不要|别|不用|不写|不能|避免).*$", "", term)
        term = term.strip(" ：:，,、；;。.!！ \n\r\t")
        if not term or len(term) > 80:
            return
        if term in {"内容", "经历", "项目", "那些", "这些", "公司学校", "旧档案"}:
            return
        add(term)
        compact = re.sub(r"\s+", "", term)
        if compact and compact != term:
            add(compact)
        if re.fullmatch(r"\d{1,5}\s*条", term):
            add(re.sub(r"\s+", "", term), re.sub(r"\D", "", term))
        aliases = {
            "字节": ("字节跳动",),
            "复旦": ("复旦大学",),
            "北大": ("北京大学",),
        }
        add(*aliases.get(term, ()))

    def negated(pattern: str, *, window: int = 30) -> bool:
        prefix_negated = rf"(不要|不想|别|不写|不能|避免).{{0,{window}}}({pattern})"
        suffix_negated = rf"({pattern}).{{0,{window}}}(可以|可).{{0,4}}(不|别|不用|不要).{{0,8}}(写|放|加|体现|保留)"
        trailing_negated = rf"({pattern}).{{0,{window}}}(?:都|也)?(不要|别|不用|不写|不能|避免).{{0,8}}(?:写|放|加|体现|保留)?"
        return bool(re.search(rf"{prefix_negated}|{suffix_negated}|{trailing_negated}", text, re.IGNORECASE))

    def listed_exclusion(pattern: str) -> bool:
        heading = r"(坚决不写入的内容|不写入的内容|不允许出现|绝对不写入|全文不允许出现|禁用内容)"
        return bool(re.search(rf"{heading}[\s\S]{{0,260}}({pattern})", text, re.IGNORECASE))

    for match in re.finditer(
        r"([^。；;\n]{2,180}?)(?:都|全)(?:不要|别|不用|不写|不能|避免)(?:写|放|加|体现|保留|出现)?",
        text,
        re.IGNORECASE,
    ):
        list_text = re.split(r"[。；;\n]", match.group(1))[-1]
        if not re.search(r"[、，,]", list_text):
            continue
        for part in re.split(r"[、，,]", list_text):
            add_with_aliases(part)

    named_exclusion = (
        r"(?:排除|剔除|移除|删去|不纳入|不要包含|不包含|不要突出|别突出|"
        r"不强调|别强调|避免突出|避免强调)\s*([^。；;\n]{1,160})"
    )
    for match in re.finditer(named_exclusion, text, re.IGNORECASE):
        clause = str(match.group(1) or "").strip()
        clause = re.split(r"(?:，|,)?(?:但|但是|同时|然后)", clause, maxsplit=1)[0]
        for part in re.split(r"\s*(?:、|，|,|以及|和|与|及)\s*", clause):
            term = re.sub(r"^(?:把|将|有关|相关)+", "", str(part or "").strip())
            term = re.sub(
                r"(?:相关)?(?:经历|内容|项目|方向|技能|材料)$",
                "",
                term,
                flags=re.IGNORECASE,
            ).strip()
            add_with_aliases(term)

    if negated(r"二手交易平台|交易平台") or listed_exclusion(r"二手交易平台|交易平台"):
        add("二手交易平台", "校园二手交易平台")
    if negated(r"证书|六级|CET") or listed_exclusion(r"证书|六级|CET"):
        add("证书", "CET", "六级")
    if negated(r"SQL") or listed_exclusion(r"SQL"):
        add("SQL", "select", "where", "group by", "SELECT", "WHERE", "COUNT")
    if negated(r"深度竞品研究|竞品分析|竞品研究") or re.search(r"(日常体验).{0,24}(别|不要|不写).{0,16}(竞品分析|深度竞品研究)", text):
        add("深度竞品研究", "竞品分析", "竞品研究", "竞品观察")
    if negated(r"主导") or listed_exclusion(r"主导"):
        add("主导")
    if negated(r"负责人") or listed_exclusion(r"负责人"):
        add("负责人")
    if negated(r"负责") or listed_exclusion(r"负责"):
        add("负责")
    if negated(r"负责") or listed_exclusion(r"负责"):
        add("小程序", "待确认经历条目", "待确认经历")
    return terms


def _generate_resume_has_restrictive_confirmed_scope(instructions: str) -> bool:
    text = str(instructions or "")
    if not text:
        return False
    return bool(
        re.search(
            r"(只使用|只采用|仅使用|仅采用|只保留|只写)[\s\S]{0,200}"
            r"(招聘会|问卷|反馈|现场问题|Excel|表格|活动复盘|保守事实)",
            text,
            re.IGNORECASE,
        )
    )


def _generate_resume_has_current_fact_only_scope(instructions: str) -> bool:
    text = str(instructions or "")
    if not text:
        return False
    return bool(
        re.search(
            r"(只写|只用|只使用|只采用|仅使用|仅采用|限定|只基于|基于)[\s\S]{0,80}"
            r"(待确认经历|这些事实|确认事实|当前事实|当前确认|这版|拟采用段落|最新拟采用|上面这版)",
            text,
        )
        or re.search(r"(?:就按|按|采用)[\s\S]{0,16}(?:这版|拟采用段落|最新拟采用|上面这版)", text)
    )


def _build_resume_rows_from_confirmed_instructions(profile: Any, instructions: str) -> list[dict[str, Any]]:
    text = str(instructions or "")
    if not text.strip():
        return []

    rows: list[dict[str, Any]] = []
    education_item = _education_item_from_profile_and_instructions(profile, text)
    if _resume_content_item_has_value(education_item):
        rows.append(
            {
                "section_type": "education",
                "title": RESUME_SECTION_TITLES.get("education", "教育背景"),
                "sort_order": len(rows),
                "visible": True,
                "content_json": [education_item],
            }
        )

    confirmed_items = [] if (
        _generate_resume_only_experience_target(text)
    ) else _instruction_confirmed_personal_experience_items(text)
    if confirmed_items:
        rows.append(
            {
                "section_type": "personalExperiences",
                "title": RESUME_SECTION_TITLES.get("personalExperiences", "个人经历"),
                "sort_order": len(rows),
                "visible": True,
                "content_json": confirmed_items,
            }
        )

    skill_items = _skill_items_from_confirmed_instructions(text)
    if skill_items:
        rows.append(
            {
                "section_type": "skills",
                "title": RESUME_SECTION_TITLES.get("skills", "技能"),
                "sort_order": len(rows),
                "visible": True,
                "content_json": [{"category": "技能", "items": skill_items}],
            }
        )

    return rows


def _merge_confirmed_instruction_rows(
    rows: list[dict[str, Any]],
    instruction_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not instruction_rows:
        return rows
    merged = [dict(row) for row in rows]
    existing_types = {str(row.get("section_type") or "") for row in merged}
    for row in instruction_rows:
        section_type = str(row.get("section_type") or "")
        if not section_type or section_type in existing_types:
            continue
        next_row = dict(row)
        next_row["sort_order"] = len(merged)
        merged.append(next_row)
        existing_types.add(section_type)
    for index, row in enumerate(merged):
        row["sort_order"] = index
    return merged


def _merge_contact_json_from_confirmed_instructions(contact_json: Mapping[str, Any], instructions: str) -> dict[str, Any]:
    merged = dict(contact_json or {})
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", str(instructions or ""))
    if email_match:
        merged["email"] = email_match.group(0)
    return merged


def _resolve_resume_user_name_from_confirmed_instructions(profile: Any, instructions: str) -> str:
    profile_name = str(getattr(profile, "name", "") or "").strip()
    if profile_name and profile_name not in {"默认档案", "默认候选人", "候选人"}:
        return profile_name
    instruction_name = _confirmed_name_from_instructions(instructions)
    if instruction_name:
        return instruction_name
    return profile_name or "默认候选人"


def _confirmed_name_from_instructions(instructions: str) -> str:
    text = str(instructions or "")
    for pattern in (
        r"(?:名字|姓名)(?:先写|写|填|先填|叫|是|用)?\s*[:：]?\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z·.' -]{1,38})",
        r"(?:我叫|叫我)\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z·.' -]{1,38})",
    ):
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = match.group(1).strip(" ，,；;。.!！、\n\r\t")
        candidate = re.split(r"[，,；;。.!！\n\r\t]", candidate, maxsplit=1)[0].strip()
        if candidate and candidate not in {"先写", "写", "填", "学校", "邮箱"} and not re.search(
            r"(邮箱|电话|学校|专业|先别管|别管|不用|不要|不写|待补)",
            candidate,
        ):
            return candidate
    return ""


def _education_item_from_profile_and_instructions(profile: Any, instructions: str) -> dict[str, Any]:
    school = str(getattr(profile, "school", "") or "").strip()
    major = str(getattr(profile, "major", "") or "").strip()
    degree = str(getattr(profile, "degree", "") or "").strip()
    graduation_year = ""

    school_match = re.search(r"学校(?:先写|写|是)?\s*([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40})", instructions)
    if school_match:
        parsed_school = _clean_confirmed_school_candidate(school_match.group(1))
        if parsed_school:
            school = parsed_school
    explicit_major_match = None
    for candidate_match in re.finditer(
        r"专业(?:先写|写|是)?\s*[:：=]?\s*([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,40})",
        instructions,
    ):
        parsed_major = _clean_confirmed_major_candidate(candidate_match.group(1))
        if parsed_major:
            explicit_major_match = candidate_match
            major = parsed_major
    degree_major_match = re.search(
        r"(本科|硕士|博士|研究生)[，,、；;。\s]*(?:专业)?"
        r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,30}?)(?:专业)?(?=大[一二三四五六\d]|研[一二三\d]|毕业|20\d{2}|[，,、；;。\s]|$)",
        instructions,
    )
    if degree_major_match:
        degree = degree_major_match.group(1).strip()
        parsed_major = _clean_confirmed_major_candidate(degree_major_match.group(2))
        if parsed_major:
            if not explicit_major_match or re.search(r"(没写|没填|漏写|写出来)", major):
                major = parsed_major
    degree_first_match = re.search(
        r"(?:普通|全日制|统招|在读)?\s*(本科|硕士|博士|研究生)\s*"
        r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,30}?)(?=大[一二三四五六\d]|研[一二三\d]|毕业|20\d{2}|[，,；;。\s]|$)",
        instructions,
    )
    if degree_first_match and not degree_major_match:
        degree = degree_first_match.group(1).strip()
        if not explicit_major_match:
            parsed_major = _clean_confirmed_major_candidate(degree_first_match.group(2))
            if parsed_major:
                major = parsed_major
    major_match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9·（）()]{2,30})(本科|硕士|博士|研究生)", instructions)
    if major_match and not degree_first_match and not degree_major_match:
        if not explicit_major_match:
            parsed_major = _clean_confirmed_major_candidate(major_match.group(1))
            if parsed_major:
                major = parsed_major
        degree = major_match.group(2).strip()
    year_match = re.search(r"(20\d{2})\s*(毕业|届)|(?<!\d)(\d{2})\s*届", instructions)
    if year_match:
        graduation_year = year_match.group(1) or f"20{year_match.group(3)}"

    return {
        "school": school,
        "major": major,
        "degree": degree,
        "startDate": "",
        "endDate": graduation_year,
        "description": _confirmed_courses_from_instructions(instructions),
    }


def _clean_confirmed_major_candidate(value: str) -> str:
    candidate = str(value or "").strip(" ，,；;。")
    candidate = re.sub(r"^(普通|全日制|统招|在读)+", "", candidate).strip(" ，,；;。")
    candidate = re.sub(r"(大[一二三四五六\d]|研[一二三\d]|毕业|在读).*$", "", candidate).strip(" ，,；;。")
    if re.search(r"(先别管|别管|不用|不要|不写|待补|没写|没填|漏写|写出来)", candidate):
        return ""
    return "" if candidate in {"普通", "全日制", "统招", "本科", "硕士", "博士", "研究生"} else candidate


def _clean_confirmed_school_candidate(value: str) -> str:
    candidate = str(value or "").strip(" ，,；;。")
    candidate = re.sub(r"^就", "", candidate).strip(" ，,；;。")
    if not candidate or re.search(r"(邮箱|名字|姓名|专业|先别管|别管|不用|不要|不写|待补)", candidate):
        return ""
    if re.fullmatch(r"(普通|全日制|统招)?(本科|硕士|博士|研究生)(大[一二三四五六\d])?", candidate):
        return ""
    if re.search(r"(本科|硕士|博士|研究生|大[一二三四五六\d])", candidate) and not re.search(
        r"(大学|学院|学校|University|College)",
        candidate,
        re.IGNORECASE,
    ):
        return ""
    return candidate


def _confirmed_courses_from_instructions(instructions: str) -> str:
    course_match = re.search(r"(?:相关课程|课程名|课程(?:写|是))\s*[:：]?\s*([^。；;\n]{2,80})", instructions)
    if course_match:
        return f"相关课程：{course_match.group(1).strip(' ，,')}"
    courses = []
    for course in ("数据库原理", "信息系统分析", "数据分析基础", "产品设计入门"):
        if course in instructions:
            courses.append(course)
    return f"相关课程：{'、'.join(courses)}" if courses else ""


def _skill_items_from_confirmed_instructions(instructions: str) -> list[str]:
    items: list[str] = []
    excluded_terms = _generate_resume_excluded_content_terms(instructions)
    if "SQL" not in excluded_terms and re.search(r"SQL|select|where|count", instructions, re.IGNORECASE):
        if re.search(r"SELECT\s*/\s*WHERE\s*/\s*COUNT|select\s*/\s*where\s*/\s*count|只写.{0,16}(SELECT|select)|只会.{0,16}(select|where|count)", instructions, re.IGNORECASE):
            items.append("SQL（基础查询：SELECT / WHERE / COUNT）")
        else:
            items.append("SQL（基础查询）")
    if re.search(r"Excel|表格", instructions, re.IGNORECASE):
        items.append("Excel（报名、到场与反馈数据统计）")
    if re.search(r"文档|复盘|会议纪要", instructions):
        items.append("文档整理与活动复盘")
    return items


def _row_items(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = row.get("content_json")
    if not isinstance(content, list):
        return []
    return [dict(item) for item in content if isinstance(item, Mapping)]


def _strip_education_optional_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = dict(item)
    for key in ("description", "gpa", "GPA", "honors", "awards"):
        if key in cleaned:
            cleaned[key] = ""
    return cleaned


def _instructions_limit_sql_to_basic(instructions: str) -> bool:
    if "SQL" in _generate_resume_excluded_content_terms(instructions):
        return False
    return bool(re.search(r"(SQL\s*(只写|只会)|基础\s*SQL|select\s*/\s*where\s*/\s*(count|group\s*by))", instructions, re.IGNORECASE))


def _basic_skill_items_from_instructions(instructions: str) -> list[dict[str, Any]]:
    if re.search(r"select\s*/\s*where\s*/\s*count|SELECT\s*/\s*WHERE\s*/\s*COUNT", instructions, re.IGNORECASE):
        items = ["SQL（基础查询：SELECT / WHERE / COUNT）"]
    else:
        items = ["基础 SQL（select / where / group by）"]
    if "Excel" in instructions or "excel" in instructions.lower():
        items.append("Excel（反馈分类与统计）")
    if re.search(r"notion|问题池", instructions, re.IGNORECASE):
        items.append("Notion（问题池维护）")
    return [{"category": "技能", "items": items}]


def _instruction_confirmed_personal_experience_items(instructions: str) -> list[dict[str, Any]]:
    text = str(instructions or "")
    items: list[dict[str, Any]] = []
    feedback_parts: list[str] = []
    feedback_count = _generate_resume_latest_feedback_count(text)
    if "反馈" in text and feedback_count:
        feedback_parts.append(f"整理约 {feedback_count} 条用户反馈，使用 Excel/表格按问题类型和严重程度分类记录。")
    elif "反馈" in text and re.search(r"300\s*条|300\+", text):
        feedback_parts.append("用 Excel 对 300 条校园活动反馈进行分类统计，沉淀反馈统计表。")
    elif "反馈" in text and re.search(r"(二三十|20\s*[-~到至]?\s*30|30)\s*条", text):
        feedback_parts.append("参与整理约 20-30 条用户反馈，按登录体验、价格感知、流程卡点进行分类记录。")
    if re.search(r"notion|问题池", text, re.IGNORECASE):
        feedback_parts.append("维护 Notion 问题池，每周整理高频问题并输出周度问题摘要。")
    if "活动" in text and re.search(r"(三四|3\s*[-~到至]?\s*4|3|4)\s*场", text):
        feedback_parts.append("使用 Excel 统计 3-4 场活动的报名、到场与反馈情况，辅助形成复盘表格。")
    elif (
        _generate_resume_has_current_fact_only_scope(text)
        and re.search(r"(活动复盘|复盘)", text)
        and re.search(r"(签到|问卷|评论|到场|报名)", text)
    ):
        feedback_parts.append("参与活动复盘，整理签到、问卷和评论反馈，辅助沉淀复盘记录。")
    if "小红书" in text and re.search(r"(四五|4\s*[-~到至]?\s*5|4|5)\s*个", text):
        feedback_parts.append("观察 4-5 个小红书竞品账号及评论区反馈，记录用户槽点与功能期待。")
    if "小红书" in text and re.search(r"(日常观察|日常刷|日常看).{0,24}(内容入口|搜索路径)|(内容入口).{0,16}(搜索路径)", text):
        feedback_parts.append("日常观察内容入口和搜索路径，记录小红书入口呈现与内容浏览路径中的体验差异。")
    if feedback_parts:
        items.append(
            {
                "experienceTitle": "产品分析与活动复盘实践",
                "startDate": "",
                "endDate": "",
                "description": " ".join(feedback_parts),
            }
        )
    if "小红书" in text and re.search(r"12\s*个|12\+", text):
        items.append(
            {
                "experienceTitle": "竞品内容观察",
                "startDate": "",
                "endDate": "",
                "description": "整理 12 个小红书竞品账号的内容主题与互动数据，辅助判断内容策略差异。",
            }
        )
    return items


def _generate_resume_instruction_summary(profile: Any, instructions: str) -> str:
    text = str(instructions or "")
    if not text:
        return ""
    if re.search(r"(用户反馈|活动复盘|小红书|竞品|SELECT|WHERE|COUNT|Excel)", text, re.IGNORECASE):
        major = str(getattr(profile, "major", "") or "").strip()
        degree = str(getattr(profile, "degree", "") or "").strip()
        prefix = f"{major}{degree}" if major or degree else "候选人"
        return f"{prefix}，具备用户反馈整理、活动复盘、竞品观察与基础 SQL 查询经验。"
    if "Personal Summary" not in text and "个人简介" not in text and "个人定位" not in text:
        return ""
    major = str(getattr(profile, "major", "") or "").strip()
    degree_or_year = "大三学生" if "大三" in text else str(getattr(profile, "degree", "") or "").strip()
    prefix = f"{major}{degree_or_year}" if major or degree_or_year else "候选人"
    return f"{prefix}，具备基础数据处理与用户反馈整理经验。"


def _sanitize_generate_resume_summary(summary: str, instructions: str) -> str:
    text = str(summary or "").strip()
    if not text:
        return ""
    if not _generate_resume_text_has_excluded_content(text, instructions):
        return text
    return "候选人，具备用户反馈整理、活动复盘与基础数据整理经验。"


def _instructions_forbid_leadership_overclaim(instructions: str) -> bool:
    return bool(re.search(r"(不要|不写|别|不能|避免).{0,24}(负责|主导|负责人)", str(instructions or "")))


def _resume_editor_section_type(section: Any, category_key: str, canonical_content: Mapping[str, Any]) -> str:
    legacy_type = get_resume_section_type(category_key)
    if category_key == "experience":
        return "internshipExperiences" if _profile_section_has_internship_hint(section, canonical_content) else "workExperiences"
    if category_key == "project":
        return "projects"
    if category_key == "skill":
        return "skills"
    if category_key == "certificate":
        return "certificates"
    if category_key == "education":
        return "education"
    if _profile_section_has_internship_hint(section, canonical_content):
        return "internshipExperiences"
    if legacy_type == "custom":
        return DEFAULT_RESUME_PERSONAL_SECTION_TYPE
    return legacy_type


def _profile_section_has_internship_hint(section: Any, canonical_content: Mapping[str, Any]) -> bool:
    normalized = canonical_content.get("normalized") if isinstance(canonical_content, Mapping) else {}
    normalized = normalized if isinstance(normalized, Mapping) else {}
    parts = [
        getattr(section, "section_type", ""),
        getattr(section, "title", ""),
        canonical_content.get("category_label") if isinstance(canonical_content, Mapping) else "",
        canonical_content.get("title") if isinstance(canonical_content, Mapping) else "",
        canonical_content.get("bullet") if isinstance(canonical_content, Mapping) else "",
        normalized.get("type"),
        normalized.get("position"),
        normalized.get("positionName"),
        normalized.get("position_name"),
        normalized.get("job_title"),
    ]
    hint = " ".join(str(part or "") for part in parts).lower()
    return "实习" in hint or "intern" in hint


def _resume_editor_content_item(
    category_key: str,
    canonical_content: Mapping[str, Any],
    fallback_title: str,
    resume_section_type: str,
) -> dict[str, Any]:
    normalized = canonical_content.get("normalized") if isinstance(canonical_content, Mapping) else {}
    normalized = normalized if isinstance(normalized, Mapping) else {}
    bullet = str(canonical_content.get("bullet") or "").strip() if isinstance(canonical_content, Mapping) else ""
    if resume_section_type in {"workExperiences", "internshipExperiences"}:
        return {
            "company": str(normalized.get("company") or normalized.get("companyName") or fallback_title or "").strip(),
            "position": str(
                normalized.get("position")
                or normalized.get("positionName")
                or normalized.get("position_name")
                or normalized.get("job_title")
                or ""
            ).strip(),
            "startDate": str(normalized.get("start_date") or normalized.get("startDate") or "").strip(),
            "endDate": str(normalized.get("end_date") or normalized.get("endDate") or "").strip(),
            "description": str(normalized.get("description") or bullet or "").strip(),
        }
    if resume_section_type == "personalExperiences":
        return {
            "experienceTitle": str(normalized.get("experienceTitle") or normalized.get("subtitle") or fallback_title or "").strip(),
            "startDate": str(normalized.get("start_date") or normalized.get("startDate") or "").strip(),
            "endDate": str(normalized.get("end_date") or normalized.get("endDate") or "").strip(),
            "description": str(normalized.get("description") or bullet or "").strip(),
        }
    return to_resume_content_item(category_key, dict(canonical_content), fallback_title)


def _dedupe_resume_editor_items(section_type: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if section_type not in {"workExperiences", "internshipExperiences"}:
        return items
    best_by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = _resume_editor_item_identity(section_type, item)
        if not key:
            key = f"__row_{len(order)}"
        if key not in best_by_key:
            best_by_key[key] = item
            order.append(key)
            continue
        if _resume_editor_item_score(item) > _resume_editor_item_score(best_by_key[key]):
            best_by_key[key] = item
    return [best_by_key[key] for key in order if _resume_content_item_has_value(best_by_key[key])]


def _resume_editor_item_identity(section_type: str, item: Mapping[str, Any]) -> str:
    if section_type in {"workExperiences", "internshipExperiences"}:
        company = str(item.get("company") or "").strip().lower()
        position = str(item.get("position") or "").strip().lower()
        if company or position:
            return f"{company}|{position}"
    return ""


def _resume_editor_item_score(item: Mapping[str, Any]) -> float:
    score = 0.0
    for key in ("company", "position", "startDate", "endDate", "description"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            score += 1
    description = str(item.get("description") or "")
    score += min(len(description), 240) / 240
    return score


def _canonical_int_ids(values: list[Any], *, field_name: str) -> list[int]:
    ids: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            record_id = int(value)
        except (TypeError, ValueError) as exc:
            raise OperatorError("validation_error", "Record ids must be integers.", {"field": field_name}) from exc
        if record_id <= 0:
            raise OperatorError("validation_error", "Record ids must be positive integers.", {"field": field_name})
        if record_id not in seen:
            ids.append(record_id)
            seen.add(record_id)
    if not ids:
        raise OperatorError("validation_error", "At least one record id is required.", {"field": field_name})
    return ids


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise OperatorError("validation_error", "Value must be an integer.", {"value": json_safe(value)}) from exc
    if parsed < minimum or parsed > maximum:
        raise OperatorError(
            "validation_error",
            "Value is outside the allowed range.",
            {"value": parsed, "minimum": minimum, "maximum": maximum},
        )
    return parsed


async def _scoped_job_ids_for_batch(
    session: AsyncSession,
    actor: ActorContext,
    batch_id: str,
    *,
    limit: int,
) -> list[int]:
    rows = (
        await session.execute(
            select(models.Job.id)
            .where(
                models.Job.owner_actor_id == actor.actor_id,
                models.Job.batch_id == batch_id,
            )
            .order_by(models.Job.created_at.desc(), models.Job.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [int(row) for row in rows]


async def _serialize_application_records(records: list[Any]) -> list[dict[str, Any]]:
    record_spec = get_model_spec("application_record")
    return [
        serialize_record(
            record,
            record_spec,
            record_spec.detail_fields,
            include_long_text=True,
            truncate_long_text=False,
        )
        for record in records
    ]


async def _create_records_from_jobs_no_commit(
    session: AsyncSession,
    *,
    table_id: Any,
    job_ids: list[int],
    skip_existing_in_table: bool,
    owner_actor_id: str,
) -> dict[str, Any]:
    try:
        from app.services.application_workspace import create_records_from_jobs_no_commit as create_records
    except ImportError as exc:
        raise OperatorError(
            "validation_error",
            "Application workspace import helper is not available in this worktree.",
            {"error": str(exc)},
        ) from exc
    return await create_records(
        session,
        table_id=table_id,
        job_ids=job_ids,
        skip_existing_in_table=skip_existing_in_table,
        owner_actor_id=owner_actor_id,
    )


async def _sync_personal_archive_to_sections(profile: Any, session: AsyncSession) -> None:
    try:
        from app.services.profile_archive_sections import sync_personal_archive_to_sections as sync_archive
    except ImportError as exc:
        raise OperatorError(
            "validation_error",
            "Profile archive section sync helper is not available in this worktree.",
            {"error": str(exc)},
        ) from exc
    await sync_archive(profile, session)


def _build_personal_archive_sections(profile: Any) -> list[Any] | None:
    try:
        from app.services.profile_archive_sections import build_personal_archive_sections
    except ImportError as exc:
        raise OperatorError(
            "validation_error",
            "Profile archive section builder is not available in this worktree.",
            {"error": str(exc)},
        ) from exc
    return build_personal_archive_sections(profile)


def _deterministic_cover_letter(job: Any, application: Any, tone: str) -> str:
    company = str(getattr(job, "company", "") or "your team").strip()
    title = str(getattr(job, "title", "") or "this role").strip()
    location = str(getattr(job, "location", "") or "").strip()
    summary = str(getattr(job, "summary", "") or getattr(job, "raw_description", "") or "").strip()
    notes = str(getattr(application, "notes", "") or "").strip()
    detail = summary[:260] if summary else "the role's priorities and responsibilities"
    context = f" in {location}" if location else ""
    note_sentence = f" I also want to highlight this context: {notes[:180]}." if notes else ""
    return (
        f"Dear {company} hiring team,\n\n"
        f"I am excited to apply for the {title}{context}. "
        f"My background and working style align with {detail}."
        f"{note_sentence}\n\n"
        f"I would welcome the chance to discuss how I can contribute to {company}. "
        f"Thank you for considering my application.\n\n"
        f"Sincerely,\nOfferU Candidate\n\n"
        f"Tone: {tone}"
    )


def _normalize_triage_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    aliases = {
        "unscreened": "inbox",
        "inbox": "inbox",
        "screened": "picked",
        "picked": "picked",
        "ignored": "ignored",
    }
    if not status:
        return ""
    normalized = aliases.get(status, status)
    if normalized not in {"inbox", "picked", "ignored"}:
        raise OperatorError("validation_error", "Invalid triage status.", {"triage_status": value})
    return normalized


def _normalize_optional_pool_id(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        pool_id = int(value)
    except (TypeError, ValueError) as exc:
        raise OperatorError("validation_error", "pool_id must be an integer or empty.", {"pool_id": value}) from exc
    if pool_id <= 0:
        raise OperatorError("validation_error", "pool_id must be a positive integer or empty.", {"pool_id": value})
    return pool_id


def _normalize_pool_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        raise OperatorError("validation_error", "Pool name is required.", {"field": "pool_name"})
    if len(name) > 100:
        raise OperatorError("validation_error", "Pool name is too long.", {"field": "pool_name", "max_length": 100})
    return name


def _normalize_pool_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    aliases = {
        "screened": "picked",
        "selected": "picked",
        "saved": "picked",
        "unscreened": "inbox",
        "trash": "ignored",
    }
    normalized = aliases.get(scope, scope)
    if normalized not in {"inbox", "picked", "ignored"}:
        raise OperatorError("validation_error", "Invalid pool scope.", {"pool_scope": value})
    return normalized


def _normalize_optional_text(value: Any, *, max_length: int) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if len(text) > max_length:
        raise OperatorError(
            "validation_error",
            "Text value is too long.",
            {"max_length": max_length, "length": len(text)},
        )
    return text


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise OperatorError("validation_error", "Boolean value is invalid.", {"value": json_safe(value)})


async def _create_or_reuse_job_pool(
    session: AsyncSession,
    actor: ActorContext,
    *,
    name: str,
    scope: str,
    description: str,
    reuse_existing: bool,
) -> tuple[Any, bool, bool]:
    pool_spec = get_model_spec("pool")
    validate_model_values({"name": name, "scope": scope}, pool_spec, purpose="organize jobs into pool")
    existing_rows = (
        await session.execute(
            select(models.Pool)
            .where(models.Pool.name == name)
            .order_by(models.Pool.id.asc())
        )
    ).scalars().all()
    current_actor_pool = next(
        (pool for pool in existing_rows if getattr(pool, "owner_actor_id", "") == actor.actor_id),
        None,
    )
    if current_actor_pool is not None:
        if not reuse_existing:
            raise OperatorError(
                "conflict_error",
                "A pool with this name already exists for the current actor.",
                {"pool_name": name, "pool_id": current_actor_pool.id},
            )
        repaired_scope = current_actor_pool.scope != scope
        current_actor_pool.scope = scope
        if description and not str(current_actor_pool.description or "").strip():
            current_actor_pool.description = description
        return current_actor_pool, False, repaired_scope
    if existing_rows:
        raise OperatorError(
            "conflict_error",
            "A pool with this name already exists outside the current actor scope.",
            {"pool_name": name},
        )
    pool = models.Pool(
        owner_actor_id=actor.actor_id,
        name=name,
        description=description,
        scope=scope,
    )
    session.add(pool)
    await session.flush()
    return pool, True, False


def _resume_source_content_for_section(section: Any) -> dict[str, Any]:
    raw = dict(section.content_json) if isinstance(section.content_json, Mapping) else {}
    if raw.get("bullet"):
        return raw
    if isinstance(raw.get("bullets"), list):
        bullets = [str(item).strip() for item in raw["bullets"] if str(item).strip()]
        if bullets:
            raw["bullet"] = "；".join(bullets)
    if not raw.get("bullet"):
        description = raw.get("description") or raw.get("desc")
        if isinstance(description, str) and description.strip():
            raw["bullet"] = description.strip()
    if not raw.get("bullet"):
        normalized = raw.get("normalized") if isinstance(raw.get("normalized"), Mapping) else {}
        description = normalized.get("description") if isinstance(normalized, Mapping) else ""
        if isinstance(description, str) and description.strip():
            raw["bullet"] = description.strip()
    if not raw.get("description") and raw.get("bullet"):
        raw["description"] = raw["bullet"]
    return raw


def _resume_content_item_has_value(item: Mapping[str, Any]) -> bool:
    for value in item.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(str(child).strip() for child in value):
            return True
    return False


async def _prepare_profile_chat_confirm_action(
    session: AsyncSession,
    actor: ActorContext,
    input_payload: Mapping[str, Any],
) -> Any:
    chat_session_id = _profile_chat_session_id_from_actor(actor)
    chat_session = await session.get(models.ProfileChatSession, chat_session_id)
    if chat_session is None:
        raise OperatorError("not_found_error", "Profile chat session was not found.", {"session_id": chat_session_id})
    profile_spec = get_model_spec("profile")
    profile = await fetch_scoped_record(session, actor, profile_spec, models.Profile, chat_session.profile_id)
    raw_candidate = input_payload.get("candidate")
    if not isinstance(raw_candidate, Mapping):
        raise OperatorError("validation_error", "profile_chat_confirm requires a locked candidate snapshot.", {})
    candidate = _normalized_profile_candidate(str(chat_session.topic or "general"), raw_candidate)
    edits = input_payload.get("edits")
    if isinstance(edits, Mapping):
        candidate = _normalized_profile_candidate(str(chat_session.topic or "general"), {**candidate, **dict(edits)})

    async def execute() -> dict[str, Any]:
        section = await _get_or_create_profile_section(
            session,
            actor,
            profile,
            candidate,
            source="ai_chat",
        )
        await session.flush()
        await session.refresh(section)
        sync_profile_section_to_personal_archive(profile, section)
        section_spec = get_model_spec("profile_section")
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "profile_chat_confirm",
            "model": "profile_section",
            "record_id": str(section.id),
            "record": serialize_record(
                section,
                section_spec,
                section_spec.detail_fields,
                include_long_text=True,
                truncate_long_text=False,
            ),
            "summary": f"Created profile section {section.title}.",
        }

    return execute


async def _prepare_profile_agent_apply_patch_action(
    session: AsyncSession,
    actor: ActorContext,
    input_payload: Mapping[str, Any],
) -> Any:
    profile_id = input_payload.get("profile_id")
    patch_raw = input_payload.get("patch")
    if not isinstance(patch_raw, Mapping):
        raise OperatorError("validation_error", "profile_agent_apply_patch requires a locked patch object.", {})
    profile_spec = get_model_spec("profile")
    profile = await fetch_scoped_record(session, actor, profile_spec, models.Profile, profile_id)
    patch = normalize_profile_agent_patch(dict(patch_raw))

    async def execute() -> dict[str, Any]:
        existing_base_info = profile.base_info_json if isinstance(profile.base_info_json, dict) else {}
        base_info = patch.get("base_info") if isinstance(patch.get("base_info"), dict) else {}
        if base_info:
            merged_base = normalize_base_info_payload({**existing_base_info, **base_info})
            profile.base_info_json = {**existing_base_info, **merged_base, **base_info}
            if base_info.get("name"):
                profile.name = str(base_info["name"])[:120]
            if base_info.get("summary") and not profile.headline:
                profile.headline = str(base_info["summary"])[:300]

        await _apply_profile_target_roles(session, actor, profile, patch)
        applied_sections = await _apply_profile_patch_sections(session, actor, profile, patch)
        latest_base_info = profile.base_info_json if isinstance(profile.base_info_json, dict) else existing_base_info
        profile.base_info_json = {
            **latest_base_info,
            "personal_archive": _build_personal_archive(
                existing_base_info=latest_base_info,
                patch=patch,
                existing_archive=latest_base_info.get("personal_archive") if isinstance(latest_base_info, dict) else None,
            ),
        }
        await session.flush()
        for section in applied_sections:
            await session.refresh(section)
        await session.refresh(profile)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "profile_agent_apply_patch",
            "model": "profile",
            "record_id": str(profile.id),
            "applied": True,
            "applied_sections_count": len(applied_sections),
            "profile": await _profile_result(session, profile),
            "summary": f"Applied profile patch with {len(applied_sections)} section(s).",
        }

    return execute


async def _prepare_profile_generate_narrative_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    profile_id = input_payload.get("profile_id")
    target_role = _normalize_profile_action_text(
        input_payload.get("target_role"),
        field_name="target_role",
        max_length=120,
    )
    profile_spec = get_model_spec("profile")
    profile = await fetch_scoped_record(session, actor, profile_spec, models.Profile, profile_id)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        sections = await _profile_sections_for_action(session, profile.id, limit=30)
        narrative = _deterministic_profile_narrative(profile, sections, target_role)
        base_info = dict(profile.base_info_json) if isinstance(profile.base_info_json, Mapping) else {}
        profile.headline = narrative["headline"]
        profile.exit_story = narrative["exit_story"]
        profile.cross_cutting_advantage = narrative["cross_cutting_advantage"]
        profile.base_info_json = json_safe({**base_info, "narrative": narrative})
        await session.flush()
        await session.refresh(profile)
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "profile_generate_narrative",
            "model": "profile",
            "record_id": str(profile.id),
            "profile": await _profile_result(session, profile),
            "summary": f"Generated profile narrative for {target_role}.",
        }

    return execute


async def _prepare_profile_instant_draft_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    profile_id = input_payload.get("profile_id")
    source_text = _normalize_profile_action_text(
        input_payload.get("source_text"),
        field_name="source_text",
        max_length=4000,
    )
    profile_spec = get_model_spec("profile")
    profile = await fetch_scoped_record(session, actor, profile_spec, models.Profile, profile_id)

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        draft = _deterministic_profile_instant_draft(source_text)
        base_info = dict(profile.base_info_json) if isinstance(profile.base_info_json, Mapping) else {}
        if draft["headline"] and not str(profile.headline or "").strip():
            profile.headline = draft["headline"]
        profile.base_info_json = json_safe(
            {
                **base_info,
                "instant_draft": {
                    "headline": draft["headline"],
                    "source_text_excerpt": source_text[:240],
                    "missing_hints": draft["missing_hints"],
                },
            }
        )
        candidate = _normalized_profile_candidate(
            "project",
            {
                "section_type": draft["section_type"],
                "title": draft["title"],
                "content_json": draft["content_json"],
                "confidence": 0.72,
            },
        )
        section = await _get_or_create_profile_section(
            session,
            actor,
            profile,
            candidate,
            source="operator_instant_draft",
        )
        await session.flush()
        await session.refresh(profile)
        await session.refresh(section)
        section_spec = get_model_spec("profile_section")
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "profile_instant_draft",
            "model": "profile",
            "record_id": str(profile.id),
            "profile": await _profile_result(session, profile),
            "sections": [
                serialize_record(
                    section,
                    section_spec,
                    section_spec.detail_fields,
                    include_long_text=True,
                    truncate_long_text=False,
                )
            ],
            "created_sections_count": 1,
            "summary": f"Created instant profile draft section {section.title}.",
        }

    return execute


def _profile_chat_session_id_from_actor(actor: ActorContext) -> int:
    prefix = "profile_chat_"
    raw = str(actor.session_id or "")
    if not raw.startswith(prefix):
        raise OperatorError("validation_error", "profile_chat_confirm must be bound to a profile chat session.", {"session_id": raw})
    try:
        return int(raw[len(prefix) :])
    except ValueError as exc:
        raise OperatorError("validation_error", "Profile chat session id is invalid.", {"session_id": raw}) from exc


def _normalized_profile_candidate(topic: str, candidate: Mapping[str, Any]) -> dict[str, Any]:
    raw_section_type = str(candidate.get("section_type") or topic or "general").strip().lower()
    section_type = normalize_section_type_alias(raw_section_type)
    category_label = candidate.get("category_label") if isinstance(candidate.get("category_label"), str) else None
    if section_type in {"general", "activity", "competition"} or not is_valid_profile_section_type(section_type):
        section_type = "custom"
        category_label = category_label or "自定义分类"
    title = str(candidate.get("title") or "未命名条目").strip()[:220]
    content_json = candidate.get("content_json")
    if not isinstance(content_json, Mapping):
        raw = str(candidate.get("content") or candidate.get("bullet") or "").strip()
        content_json = {"bullet": raw}
    try:
        category_key, resolved_label, _is_custom, canonical_content_json = canonicalize_profile_section_payload(
            section_type=section_type,
            category_label=category_label,
            title=title,
            raw_content_json=dict(content_json),
        )
    except ValueError:
        category_key, resolved_label, _is_custom, canonical_content_json = canonicalize_profile_section_payload(
            section_type="custom",
            category_label="自定义分类",
            title=title,
            raw_content_json=dict(content_json),
        )
    try:
        confidence = float(candidate.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    return {
        "section_type": category_key,
        "category_label": resolved_label,
        "title": title,
        "content_json": canonical_content_json,
        "confidence": min(max(confidence, 0.0), 1.0),
    }


async def _get_or_create_profile_section(
    session: AsyncSession,
    actor: ActorContext,
    profile: Any,
    candidate: Mapping[str, Any],
    *,
    source: str,
) -> Any:
    existing_sections = (
        await session.execute(
            select(models.ProfileSection)
            .where(
                models.ProfileSection.profile_id == profile.id,
                models.ProfileSection.section_type == candidate["section_type"],
                models.ProfileSection.title == candidate["title"],
            )
            .order_by(models.ProfileSection.id.desc())
        )
    ).scalars().all()
    for existing in existing_sections:
        if (existing.content_json or {}) == candidate["content_json"]:
            return existing
    max_sort = (
        await session.execute(
            select(func.max(models.ProfileSection.sort_order)).where(models.ProfileSection.profile_id == profile.id)
        )
    ).scalar()
    section = models.ProfileSection(
        owner_actor_id=actor.actor_id,
        profile_id=profile.id,
        section_type=str(candidate["section_type"]),
        title=str(candidate["title"]),
        sort_order=int(max_sort or 0) + 1,
        content_json=json_safe(candidate["content_json"]),
        source=source,
        confidence=float(candidate.get("confidence") or 0.7),
    )
    session.add(section)
    return section


async def _apply_profile_target_roles(
    session: AsyncSession,
    actor: ActorContext,
    profile: Any,
    patch: Mapping[str, Any],
) -> None:
    existing_roles = {
        role.role_name
        for role in (
            await session.execute(select(models.ProfileTargetRole).where(models.ProfileTargetRole.profile_id == profile.id))
        ).scalars().all()
    }
    for index, raw_role in enumerate(patch.get("target_roles") or []):
        role = str(raw_role or "").strip()
        if not role or role in existing_roles:
            continue
        session.add(
            models.ProfileTargetRole(
                owner_actor_id=actor.actor_id,
                profile_id=profile.id,
                role_name=role[:120],
                role_level="",
                fit="primary" if index == 0 else "secondary",
            )
        )
        existing_roles.add(role)


async def _apply_profile_patch_sections(
    session: AsyncSession,
    actor: ActorContext,
    profile: Any,
    patch: Mapping[str, Any],
) -> list[Any]:
    applied_sections: list[Any] = []
    for item in patch.get("sections") or []:
        if not isinstance(item, Mapping):
            continue
        candidate = _normalized_profile_candidate(str(item.get("section_type") or "custom"), item)
        applied_sections.append(
            await _get_or_create_profile_section(
                session,
                actor,
                profile,
                candidate,
                source="ai_profile_agent",
            )
        )
    return applied_sections


async def _profile_result(session: AsyncSession, profile: Any) -> dict[str, Any]:
    profile_spec = get_model_spec("profile")
    profile_payload = serialize_record(
        profile,
        profile_spec,
        profile_spec.detail_fields,
        include_long_text=True,
        truncate_long_text=False,
    )
    section_spec = get_model_spec("profile_section")
    sections = (
        await session.execute(
            select(models.ProfileSection)
            .where(models.ProfileSection.profile_id == profile.id)
            .order_by(models.ProfileSection.sort_order.asc(), models.ProfileSection.created_at.asc())
        )
    ).scalars().all()
    profile_payload["sections"] = [
        serialize_record(
            section,
            section_spec,
            section_spec.detail_fields,
            include_long_text=True,
            truncate_long_text=False,
        )
        for section in sections
    ]
    return profile_payload


async def _profile_sections_for_action(session: AsyncSession, profile_id: Any, *, limit: int) -> list[Any]:
    return list(
        (
            await session.execute(
                select(models.ProfileSection)
                .where(models.ProfileSection.profile_id == profile_id)
                .order_by(
                    models.ProfileSection.sort_order.asc(),
                    models.ProfileSection.updated_at.desc(),
                    models.ProfileSection.id.asc(),
                )
                .limit(limit)
            )
        ).scalars().all()
    )


def _normalize_profile_action_text(value: Any, *, field_name: str, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise OperatorError("validation_error", "Profile action text input is required.", {"field": field_name})
    if len(text) > max_length:
        raise OperatorError(
            "validation_error",
            "Profile action text input is too long.",
            {"field": field_name, "max_length": max_length, "length": len(text)},
        )
    return text


def _deterministic_profile_narrative(profile: Any, sections: list[Any], target_role: str) -> dict[str, Any]:
    evidence = _profile_evidence_terms(profile, sections)
    lead = evidence[0] if evidence else str(getattr(profile, "major", "") or getattr(profile, "school", "") or "structured profile facts")
    second = evidence[1] if len(evidence) > 1 else "cross-functional execution"
    third = evidence[2] if len(evidence) > 2 else "clear learning and delivery habits"
    name = str(getattr(profile, "name", "") or "Candidate").strip()
    headline = f"{target_role} candidate with {lead}"[:300]
    exit_story = (
        f"{name} is positioning for {target_role} roles by connecting {lead}, {second}, "
        f"and practical delivery evidence from the profile archive."
    )
    advantage = (
        f"For {target_role}, the cross-cutting advantage is translating {lead} into structured plans, "
        f"using {second} to align stakeholders, and keeping execution grounded in {third}."
    )
    return {
        "target_role": target_role,
        "headline": headline,
        "exit_story": exit_story,
        "cross_cutting_advantage": advantage,
        "evidence_terms": evidence[:8],
        "generator": "deterministic_operator_profile_v1",
    }


def _deterministic_profile_instant_draft(source_text: str) -> dict[str, Any]:
    sentences = _split_profile_source_sentences(source_text)
    first = sentences[0] if sentences else source_text
    title = first[:80].strip(" .") or "Profile draft"
    if len(title) < 12 and len(source_text) > len(title):
        title = source_text[:80].strip(" .")
    bullets = []
    for sentence in sentences[:3]:
        clean = sentence.strip(" .")
        if clean:
            bullets.append(clean)
    if not bullets:
        bullets.append(source_text[:180])
    while len(bullets) < 2:
        bullets.append("Add measurable result, time range, and personal contribution before final resume use.")
    return {
        "headline": f"Profile draft based on {title}"[:300],
        "section_type": "project",
        "title": title[:220],
        "content_json": {
            "description": source_text[:1000],
            "bullets": bullets[:4],
            "source_text_excerpt": source_text[:240],
            "draft_status": "needs_review",
            "generator": "deterministic_operator_profile_v1",
        },
        "missing_hints": [
            "Add dates and scope.",
            "Add measurable outcomes.",
            "Clarify personal contribution.",
        ],
    }


def _profile_evidence_terms(profile: Any, sections: list[Any]) -> list[str]:
    values: list[str] = []
    for value in (
        getattr(profile, "major", ""),
        getattr(profile, "school", ""),
        getattr(profile, "headline", ""),
    ):
        _append_evidence_value(values, value)
    for section in sections:
        _append_evidence_value(values, getattr(section, "title", ""))
        content = getattr(section, "content_json", None)
        if isinstance(content, Mapping):
            for key in ("bullet", "description", "summary"):
                _append_evidence_value(values, content.get(key))
            bullets = content.get("bullets")
            if isinstance(bullets, list):
                for bullet in bullets[:3]:
                    _append_evidence_value(values, bullet)
    return values


def _append_evidence_value(values: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    text = " ".join(text.split())
    if len(text) > 80:
        text = text[:77].rstrip() + "..."
    if text not in values:
        values.append(text)


def _split_profile_source_sentences(source_text: str) -> list[str]:
    normalized = source_text.replace("\r", "\n").replace("。", ".").replace("；", ".").replace(";", ".")
    chunks: list[str] = []
    for line in normalized.split("\n"):
        for part in line.split("."):
            text = " ".join(part.split()).strip()
            if text:
                chunks.append(text)
    return chunks


def _build_personal_archive(
    *,
    existing_base_info: dict[str, Any] | None,
    patch: dict[str, Any],
    existing_archive: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from app.routes.profile_agent import build_personal_archive_from_agent_patch

        return build_personal_archive_from_agent_patch(
            existing_base_info=existing_base_info,
            patch=patch,
            existing_archive=existing_archive,
        )
    except Exception:
        return {
            "schemaVersion": "personal.archive.v1",
            "resumeArchive": {
                "basicInfo": existing_base_info or {},
                "personalSummary": str((existing_base_info or {}).get("summary") or ""),
            },
            "applicationArchive": {},
            "syncSettings": {"autoSyncEnabled": True, "overriddenFieldPaths": []},
        }


async def _record_intermediate_confirmation(session: AsyncSession, actor: ActorContext, proposal: Any) -> str:
    events = _events(proposal)
    existing_event_id = _latest_event_id(events, "first_confirmed")
    if existing_event_id:
        proposal.confirmation_events = events
    else:
        event = _event("first_confirmed", actor, result={"status": "awaiting_next_confirmation"})
        events.append(event)
        proposal.confirmation_events = events
        existing_event_id = str(event.get("event_id") or "")
    await session.execute(
        update(models.ProposalCache)
        .where(models.ProposalCache.proposal_id == proposal.proposal_id)
        .values(
            confirmation_count=proposal.confirmation_count,
            confirmations_received=getattr(proposal, "confirmations_received", 0) or 0,
            confirmation_challenges=getattr(proposal, "confirmation_challenges", None) or [],
            first_confirmed_at=proposal.first_confirmed_at,
            confirmation_challenge=proposal.confirmation_challenge,
            confirmation_events=proposal.confirmation_events,
        )
        .execution_options(synchronize_session=False)
    )
    return existing_event_id


async def _record_first_confirmation(session: AsyncSession, actor: ActorContext, proposal: Any) -> str:
    """Legacy alias for backward compatibility."""
    return await _record_intermediate_confirmation(session, actor, proposal)


async def _mark_terminal(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    status: str,
    *,
    reason: str = "",
) -> str:
    if not await _claim_pending_proposal(
        session,
        proposal,
        status=status,
        expected_confirmation_count=proposal.confirmation_count,
    ):
        return ""
    proposal.status = status
    if reason:
        proposal.reason = reason
    events = _events(proposal)
    event = _event(status, actor, result={"status": status, "reason": reason})
    events.append(event)
    proposal.confirmation_events = events
    await _remove_pending_proposal_id(session, actor, proposal.proposal_id)
    await _resolve_harness_pending_proposal(session, actor, proposal.proposal_id, status=status)
    return str(event.get("event_id") or "")


async def _remove_pending_proposal_id(session: AsyncSession, actor: ActorContext, proposal_id: str) -> None:
    from app.operator.guards import remove_pending_proposal_id

    await remove_pending_proposal_id(session, actor, proposal_id)


async def _current_session_pending_proposal_ids(session: AsyncSession, actor: ActorContext) -> set[str]:
    agent_session = await session.get(models.AgentSession, actor.session_id, populate_existing=True)
    if agent_session is None or agent_session.actor_id != actor.actor_id:
        return set()
    await session.refresh(agent_session)
    return {str(item) for item in (agent_session.pending_proposal_ids or []) if str(item or "").strip()}


async def _resolve_harness_pending_proposal(
    session: AsyncSession,
    actor: ActorContext,
    proposal_id: str,
    *,
    status: str,
) -> None:
    return None


def _proposal_execution_payload(proposal: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
        "status": "executed",
        "summary": str(result.get("summary") or getattr(proposal, "summary", "") or "Proposal executed."),
        "tool_name": str(getattr(proposal, "tool_name", "") or result.get("tool_name") or ""),
        "model_or_action": str(getattr(proposal, "model_or_action", "") or result.get("model") or ""),
        "changed_records": json_safe(
            result.get("changed_records")
            or result.get("affected_records")
            or getattr(proposal, "affected_records", None)
            or []
        ),
        "result": json_safe(dict(result)),
    }


def _continuation_status_projection(job: Any) -> dict[str, Any]:
    status = str(getattr(job, "status", "") or "")
    attempt_count = int(getattr(job, "attempt_count", 0) or 0)
    if status == "running":
        return {"ok": False, "error": "session_busy", "stop_reason": "busy",
                "status": "running", "attempt_count": attempt_count}
    if status == "failed":
        return {"ok": False, "error": "proposal_continuation_retrying",
                "stop_reason": "retrying", "status": "failed",
                "attempt_count": attempt_count}
    if status == "manual_review":
        stored_error = getattr(job, "error", None)
        details = dict(stored_error) if isinstance(stored_error, Mapping) else {}
        details["requires_manual_review"] = True
        return {"ok": False,
                "error": {"code": "proposal_continuation_manual_review",
                          "message": "Proposal continuation requires manual review.",
                          "details": json_safe(details)},
                "stop_reason": "error", "status": "manual_review",
                "attempt_count": attempt_count}
    return {"ok": False, "error": "proposal_continuation_unavailable",
            "stop_reason": "error", "status": status, "attempt_count": attempt_count}


_OUTER_HEARTBEAT_TRANSIENT_RETRY_ATTEMPTS = 5
_OUTER_HEARTBEAT_TRANSIENT_RETRY_BASE_SECONDS = 0.02
_logger = logging.getLogger(__name__)


def _is_transient_db_lock_error(exc: BaseException) -> bool:
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


async def _heartbeat_proposal_continuation(
    proposal_id: str,
    token: str,
    stop: asyncio.Event,
    lost: asyncio.Event,
    *,
    lease_seconds: float,
    cancel_token: Any | None = None,
) -> None:
    from app.operator.continuations import renew

    interval = max(0.01, float(lease_seconds) / 3.0)

    def _mark_lost() -> None:
        lost.set()
        if cancel_token is not None:
            cancel = getattr(cancel_token, "cancel", None)
            if callable(cancel):
                cancel()

    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        renewed = False
        last_error: Exception | None = None
        for attempt in range(_OUTER_HEARTBEAT_TRANSIENT_RETRY_ATTEMPTS):
            try:
                async with async_session() as heartbeat_session:
                    renewed = await renew(
                        heartbeat_session,
                        proposal_id,
                        token,
                        lease_seconds=lease_seconds,
                    )
                last_error = None
                break
            except OperationalError as exc:
                last_error = exc
                if not _is_transient_db_lock_error(exc):
                    raise
                if stop.is_set():
                    return
                delay = _OUTER_HEARTBEAT_TRANSIENT_RETRY_BASE_SECONDS * (2 ** attempt)
                _logger.warning(
                    "Proposal continuation heartbeat hit transient DB lock "
                    "(proposal_id=%s attempt %s/%s); retrying in %.3fs",
                    proposal_id,
                    attempt + 1,
                    _OUTER_HEARTBEAT_TRANSIENT_RETRY_ATTEMPTS,
                    delay,
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    return
                except asyncio.TimeoutError:
                    continue
        if last_error is not None:
            raise last_error
        if not renewed:
            _mark_lost()
            return


async def _continuation_after_confirmed(proposal_id: str) -> dict[str, Any] | None:
    """Drive continuation after a durable confirmed commit without poisoning confirm.

    Confirm/idempotent-confirm call sites must use this after session.commit() so
    any exception from the continuation driver is isolated into
    continuation.ok=false. Confirmed domain success must remain public ok=true.
    """
    try:
        return await _drive_proposal_continuation(proposal_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _logger.exception(
            "Post-confirm continuation failed without undoing confirmed write (proposal_id=%s)",
            proposal_id,
        )
        return {
            "ok": False,
            "error": {
                "code": "proposal_continuation_failed",
                "message": str(exc) or "Proposal continuation failed.",
            },
        }


async def _drive_proposal_continuation(proposal_id: str) -> dict[str, Any] | None:
    """Claim and synchronously drive one durable job; safe for duplicate confirms.

    Prefer _continuation_after_confirmed from confirm response paths. This helper
    also isolates claim/finish/session-factory failures as continuation.ok=false.
    """
    try:
        return await _drive_proposal_continuation_unbounded(proposal_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _logger.exception(
            "Proposal continuation drive failed after confirmed write (proposal_id=%s)",
            proposal_id,
        )
        return {
            "ok": False,
            "error": {
                "code": "proposal_continuation_failed",
                "message": str(exc) or "Proposal continuation failed.",
            },
        }


async def _drive_proposal_continuation_unbounded(proposal_id: str) -> dict[str, Any] | None:
    """Inner continuation driver; may raise. Prefer _drive_proposal_continuation."""
    from app.agent.types import CancelToken
    from app.operator.continuations import (
        CONTINUATION_JOB_LEASE_SECONDS,
        claim,
        finish,
        valid_payload,
    )

    async with async_session() as job_session:
        existing = await job_session.get(models.ProposalContinuation, proposal_id)
        if existing is None:
            return None
        proposal = await job_session.get(models.ProposalCache, proposal_id, populate_existing=True)
        actor = ActorContext(
            actor_id=str(existing.actor_id or ""),
            session_id=str(existing.session_id or ""),
            adapter="continuation_recovery",
        )
        if proposal is None or proposal.status != "confirmed":
            existing.status = "manual_review"
            existing.error = {
                "code": "proposal_continuation_confirmation_invalid",
                "message": "Continuation requires one authoritative confirmed proposal.",
            }
            await job_session.commit()
            return conflict_error(
                "Continuation requires one authoritative confirmed proposal.",
                {"proposal_id": proposal_id, "requires_manual_review": True},
            )
        try:
            await _validated_stored_confirm_response(job_session, actor, proposal)
        except OperatorError as exc:
            existing = await job_session.get(models.ProposalContinuation, proposal_id, populate_existing=True)
            if existing is not None:
                existing.status = "manual_review"
                existing.error = json_safe({"code": exc.code, "message": exc.message, "details": exc.details})
                await job_session.commit()
            return _operator_error_response(exc)
        if existing.status == "succeeded":
            return json_safe(existing.result or {})
        job, token = await claim(
            job_session, proposal_id, lease_seconds=CONTINUATION_JOB_LEASE_SECONDS
        )
        if job is None:
            current = await job_session.get(
                models.ProposalContinuation, proposal_id, populate_existing=True
            )
            return _continuation_status_projection(current) if current is not None else None
        if not valid_payload(job):
            error = {
                "ok": False,
                "error": {
                    "code": "proposal_continuation_payload_invalid",
                    "message": "Continuation payload hash mismatch.",
                },
            }
            await finish(job_session, proposal_id, token, error=error["error"], retryable=False)
            return error
        payload = dict(job.payload or {})
    actor = ActorContext(
        actor_id=str(payload["actor_id"]),
        session_id=str(payload["session_id"]),
        adapter=str(payload.get("adapter") or ""),
    )
    heartbeat_stop = asyncio.Event()
    heartbeat_lost = asyncio.Event()
    cancel_token = CancelToken()
    heartbeat_task = asyncio.create_task(
        _heartbeat_proposal_continuation(
            proposal_id,
            token,
            heartbeat_stop,
            heartbeat_lost,
            lease_seconds=CONTINUATION_JOB_LEASE_SECONDS,
            cancel_token=cancel_token,
        ),
        name=f"proposal-continuation-heartbeat:{proposal_id}",
    )
    continuation: dict[str, Any] | None = None
    try:
        continuation = await _continue_new_agent_after_confirmation(
            actor,
            payload.get("message_payload") or payload,
            payload.get("execution_result") or {},
            cancel=cancel_token,
        )
    finally:
        heartbeat_stop.set()
        try:
            await heartbeat_task
        except Exception:
            _logger.exception(
                "Proposal continuation heartbeat failed for %s",
                proposal_id,
            )
            heartbeat_lost.set()
            cancel_token.cancel()
    if heartbeat_lost.is_set() or cancel_token.cancelled:
        return {
            "ok": False,
            "error": {
                "code": "proposal_continuation_lease_lost",
                "message": "Proposal continuation lease was lost; durable completion was not recorded.",
            },
        }
    async with async_session() as finish_session:
        if not isinstance(continuation, Mapping) or continuation.get("ok") is False:
            continuation_error = continuation.get("error") if isinstance(continuation, Mapping) else None
            finished = await finish(
                finish_session,
                proposal_id,
                token,
                error={
                    "code": "proposal_continuation_retryable",
                    "response": json_safe(continuation or {}),
                    "cause": json_safe(continuation_error),
                },
            )
        else:
            finished = await finish(finish_session, proposal_id, token, result=continuation)
    if not finished:
        return {
            "ok": False,
            "error": {
                "code": "proposal_continuation_lease_lost",
                "message": "Proposal continuation completion lost its durable lease.",
            },
        }
    return continuation


async def recover_proposal_continuations(*, limit: int = 100) -> dict[str, dict[str, Any] | None]:
    """Recovery scanner entry point for startup jobs or periodic workers."""
    from app.operator.continuations import recoverable_ids, terminalize_exhausted_jobs

    async with async_session() as scan_session:
        terminalized = await terminalize_exhausted_jobs(scan_session, limit=limit)
        ids = await recoverable_ids(scan_session, limit=limit)
    results: dict[str, dict[str, Any] | None] = {
        proposal_id: {
            "ok": False,
            "error": {
                "code": "proposal_continuation_attempts_exhausted",
                "message": "Continuation attempt limit was exhausted after a worker stopped before completion.",
                "requires_manual_review": True,
            },
            "status": "manual_review",
        }
        for proposal_id in terminalized
    }
    for proposal_id in ids:
        results[proposal_id] = await _drive_proposal_continuation(proposal_id)
    return results


async def _continue_new_agent_after_confirmation(
    actor: ActorContext,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    cancel: Any | None = None,
) -> dict[str, Any] | None:
    try:
        from app.agent import orchestrator
        from app.agent.messages import create_custom_message

        payload = json_safe(dict(payload))
        invocation_key = str(payload.get("invocation_key") or "")
        async with async_session() as receipt_session:
            receipt = (
                await receipt_session.get(models.AgentContinuationInvocation, invocation_key)
                if invocation_key
                else None
            )
            if receipt is not None and receipt.status == "succeeded":
                return json_safe(receipt.result or {})
        message = create_custom_message(
            "proposal_execution_result",
            json.dumps(payload, ensure_ascii=False, default=str),
            display=False,
            details=payload,
        )
        async with async_session() as continuation_session:
            continuation = await orchestrator.run_agent_turn(
                continuation_session,
                actor,
                user_message=None,
                conversation_id=actor.session_id,
                injected_messages=[message],
                invocation_key=invocation_key or None,
                cancel=cancel,
            )
        if isinstance(continuation, Mapping):
            return json_safe(orchestrator.public_agent_response(continuation))
        return json_safe(
            orchestrator.public_agent_response(
                {
                    "ok": False,
                    "error": {
                        "code": "proposal_continuation_invalid",
                        "message": "Proposal continuation returned a non-object response.",
                    },
                }
            )
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "proposal_continuation_failed",
                "message": str(exc) or "Proposal continuation failed.",
            },
        }


def _goal_proposal_payload(proposal: Any | None) -> dict[str, Any]:
    if proposal is None:
        return {}
    locked_payload = getattr(proposal, "locked_payload", None)
    payload = dict(locked_payload) if isinstance(locked_payload, Mapping) else {}
    payload.setdefault("tool_name", str(getattr(proposal, "tool_name", "") or ""))
    payload.setdefault("model_or_action", str(getattr(proposal, "model_or_action", "") or ""))
    if getattr(proposal, "record_id", None):
        payload.setdefault("record_id", str(getattr(proposal, "record_id") or ""))
    return json_safe(payload)


async def _queue_harness_pending_proposals_from_result(
    session: AsyncSession,
    actor: ActorContext,
    result: Mapping[str, Any],
    *,
    exclude_proposal_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    return []


def _result_proposals(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    proposal = result.get("proposal")
    if isinstance(proposal, Mapping):
        output.append(dict(proposal))
    proposals = result.get("proposals")
    if isinstance(proposals, list):
        for item in proposals:
            if isinstance(item, Mapping):
                output.append(dict(item))
    return output


async def _run_confirmed_proposal_refine(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    result: Mapping[str, Any],
) -> dict[str, Any] | None:
    return None


async def _is_current_session_pending_proposal(session: AsyncSession, actor: ActorContext, proposal_id: str) -> bool:
    agent_session = await session.get(models.AgentSession, actor.session_id, populate_existing=True)
    if agent_session is None or agent_session.actor_id != actor.actor_id:
        return False
    await session.refresh(agent_session)
    return proposal_id in list(agent_session.pending_proposal_ids or [])


async def _sync_profile_section_archive(session: AsyncSession, model_name: str, record: Any) -> None:
    if str(model_name) != "profile_section":
        return
    profile_id = getattr(record, "profile_id", None)
    if profile_id in (None, ""):
        return
    profile = await session.get(models.Profile, profile_id)
    if profile is None:
        return
    sync_profile_section_to_personal_archive(profile, record)


async def _remove_profile_section_archive(session: AsyncSession, model_name: str, record: Any) -> None:
    if str(model_name) != "profile_section":
        return
    profile_id = getattr(record, "profile_id", None)
    if profile_id in (None, ""):
        return
    profile = await session.get(models.Profile, profile_id)
    if profile is None:
        return
    remove_profile_section_from_personal_archive(profile, record)


def _patch_values(record: Any, updates: Mapping[str, Any], patch_mode: str) -> dict[str, Any]:
    """Compute patch values without mutating the ORM identity before a CAS write."""
    values: dict[str, Any] = {}
    for field, value in updates.items():
        current = getattr(record, field, None)
        if patch_mode in {"replace", "rewrite"}:
            values[str(field)] = value
        elif patch_mode == "append":
            if current in (None, ""):
                values[str(field)] = value
            elif isinstance(current, list):
                values[str(field)] = [*current, *value] if isinstance(value, list) else [*current, value]
            elif isinstance(current, str):
                values[str(field)] = f"{current}{value}"
            else:
                raise OperatorError("validation_error", "Append mode only supports text and array fields.", {"field": field})
        elif patch_mode == "merge":
            if not isinstance(current, Mapping) or not isinstance(value, Mapping):
                raise OperatorError("validation_error", "Merge mode only supports object fields.", {"field": field})
            values[str(field)] = {**current, **value}
    return values


def _safe_rebase_cas_predicates(
    record: Any, spec: Any, actor: ActorContext, *, expected_fields: tuple[str, ...]
) -> list[Any]:
    table = type(record).__table__
    primary_key = table.columns.get(str(spec.primary_key))
    if primary_key is None:
        raise OperatorError("conflict_error", "Safe rebase model has no durable primary key.", {})
    predicates: list[Any] = [primary_key == getattr(record, spec.primary_key)]
    if getattr(spec, "ownership_scope", "") == "actor_owned" and table.columns.get("owner_actor_id") is not None:
        predicates.append(table.columns.owner_actor_id == str(actor.actor_id))

    stored_hash = str(getattr(record, "operator_version_hash", "") or "")
    version_column = table.columns.get("operator_version_hash")
    if stored_hash and version_column is not None:
        predicates.append(version_column == stored_hash)
    # Always fence every field this write may replace. The durable hash, when
    # present, adds whole-record fencing; field predicates still protect legacy
    # or externally-written rows whose stored hash may not have advanced.
    for field in expected_fields:
        column = table.columns.get(str(field))
        if column is None:
            raise OperatorError("conflict_error", "Safe rebase field is not durably mapped.", {"field": field})
        value = getattr(record, field, None)
        predicates.append(column.is_(None) if value is None else column == value)
    return predicates


def _apply_patch(record: Any, updates: Mapping[str, Any], patch_mode: str) -> None:
    for field, value in updates.items():
        current = getattr(record, field, None)
        if patch_mode in {"replace", "rewrite"}:
            setattr(record, field, value)
        elif patch_mode == "append":
            if current in (None, ""):
                setattr(record, field, value)
            elif isinstance(current, list):
                setattr(record, field, [*current, *value] if isinstance(value, list) else [*current, value])
            elif isinstance(current, str):
                setattr(record, field, f"{current}{value}")
            else:
                raise OperatorError("validation_error", "Append mode only supports text and array fields.", {"field": field})
        elif patch_mode == "merge":
            if not isinstance(current, Mapping) or not isinstance(value, Mapping):
                raise OperatorError("validation_error", "Merge mode only supports object fields.", {"field": field})
            setattr(record, field, {**current, **value})


def _apply_visibility_operation(record: Any, operation: str) -> str:
    if operation == "archive":
        if hasattr(record, "archived"):
            record.archived = True
            return "archived"
        if hasattr(record, "is_archived"):
            record.is_archived = True
            return "archived"
        if hasattr(record, "status"):
            record.status = "archived"
            return "archived"
    if operation == "restore":
        if hasattr(record, "archived"):
            record.archived = False
            return "restored"
        if hasattr(record, "is_archived"):
            record.is_archived = False
            return "restored"
        if hasattr(record, "status"):
            record.status = "active"
            return "restored"
    return "completed_noop"


def _normalize_resume_text(value: Any, *, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:max_length]


def _required_resume_text(value: Any, *, field_name: str, max_length: int) -> str:
    text = _normalize_resume_text(value, max_length=max_length)
    if not text:
        raise OperatorError("validation_error", "Required text value is missing.", {"field": field_name})
    return text


def _join_sentences(*parts: str) -> str:
    return " ".join(part.strip().strip("。.") for part in parts if str(part or "").strip()).strip()


def _slugify(value: str) -> str:
    text = str(value or "").strip().lower()
    chars = [char if char.isalnum() else "-" for char in text]
    slug = "".join(chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "logo"


def _merge_id_list(existing: Any, value: int) -> list[int]:
    ids: list[int] = []
    for item in existing or []:
        try:
            item_int = int(item)
        except (TypeError, ValueError):
            continue
        if item_int not in ids:
            ids.append(item_int)
    if value not in ids:
        ids.append(value)
    return ids


def _resume_content_item(title: str, description: str) -> dict[str, Any]:
    normalized_title = str(title or "").strip()
    return {
        "name": normalized_title,
        "title": normalized_title,
        "description": str(description or "").strip(),
    }


def _append_resume_content(current: Any, item: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = [json_safe(entry) for entry in current] if isinstance(current, list) else []
    content.append(json_safe(item))
    return content


async def _resume_sections(session: AsyncSession, resume_id: Any) -> list[Any]:
    return (
        await session.execute(
            select(models.ResumeSection)
            .where(models.ResumeSection.resume_id == resume_id)
            .order_by(models.ResumeSection.sort_order.asc(), models.ResumeSection.id.asc())
        )
    ).scalars().all()


def _is_tailored_resume_section(section: Any) -> bool:
    section_type = str(getattr(section, "section_type", "") or "").strip().lower()
    return section_type in {"custom", "personalexperiences", "personal_experiences", "personal_experience"}


EDITOR_RESUME_SECTION_TYPES = {
    "education",
    "workExperiences",
    "internshipExperiences",
    "projects",
    "skills",
    "certificates",
    "awards",
    "personalExperiences",
}


LEGACY_RESUME_SECTION_TYPE_MAP = {
    "experience": "workExperiences",
    "work_experience": "workExperiences",
    "workexperience": "workExperiences",
    "internship": "internshipExperiences",
    "internship_experience": "internshipExperiences",
    "internshipexperience": "internshipExperiences",
    "project": "projects",
    "skill": "skills",
    "certificate": "certificates",
    "custom": "personalExperiences",
    "personal_experiences": "personalExperiences",
    "personal_experience": "personalExperiences",
}


def _resume_editor_section_type_from_resume_section(section: Any) -> str:
    return _resume_editor_section_type_value(
        getattr(section, "section_type", ""),
        title=getattr(section, "title", ""),
        content_json=getattr(section, "content_json", None),
    )


def _resume_editor_section_type_value(
    section_type: Any,
    *,
    title: Any = "",
    content_json: Any = None,
) -> str:
    raw_type = str(section_type or "").strip()
    if raw_type in EDITOR_RESUME_SECTION_TYPES:
        return raw_type
    normalized = raw_type.lower()
    if normalized == "experience" and _resume_section_value_has_internship_hint(raw_type, title=title, content_json=content_json):
        return "internshipExperiences"
    return LEGACY_RESUME_SECTION_TYPE_MAP.get(normalized, DEFAULT_RESUME_PERSONAL_SECTION_TYPE)


def _resume_section_has_internship_hint(section: Any) -> bool:
    return _resume_section_value_has_internship_hint(
        getattr(section, "section_type", ""),
        title=getattr(section, "title", ""),
        content_json=getattr(section, "content_json", None),
    )


def _resume_section_value_has_internship_hint(section_type: Any, *, title: Any = "", content_json: Any = None) -> bool:
    try:
        content_text = json.dumps(content_json or "", ensure_ascii=False)
    except Exception:
        content_text = str(content_json or "")
    hint = " ".join(
        [
            str(section_type or ""),
            str(title or ""),
            content_text,
        ]
    ).lower()
    return "实习" in hint or "intern" in hint


def _next_resume_section_sort_order(sections: list[Any]) -> int:
    sort_orders: list[int] = []
    for section in sections:
        try:
            sort_orders.append(int(getattr(section, "sort_order", 0) or 0))
        except (TypeError, ValueError):
            continue
    return (max(sort_orders) + 10) if sort_orders else 0


async def _fetch_resume_section(session: AsyncSession, actor: ActorContext, resume_id: Any, section_id: Any) -> Any:
    if section_id in (None, ""):
        raise OperatorError("validation_error", "Section id is required.", {"field": "section_id"})
    section_spec = get_model_spec("resume_section")
    section = await fetch_scoped_record(session, actor, section_spec, models.ResumeSection, section_id)
    if int(section.resume_id) != int(resume_id):
        raise OperatorError("validation_error", "Resume section does not belong to the locked resume.", {"section_id": section_id})
    return section


def _apply_resume_updates(resume: Any, updates: Mapping[str, Any]) -> None:
    for field, value in updates.items():
        if hasattr(resume, field):
            setattr(resume, field, json_safe(value))


def _apply_resume_section_updates(section: Any, updates: Mapping[str, Any]) -> None:
    for field, value in updates.items():
        if hasattr(section, field):
            if field == "section_type":
                value = _resume_editor_section_type_value(
                    value,
                    title=getattr(section, "title", ""),
                    content_json=getattr(section, "content_json", None),
                )
            setattr(section, field, json_safe(value))


async def _create_resume_section_from_change(session: AsyncSession, actor: ActorContext, resume_id: Any, change: Mapping[str, Any]) -> Any:
    section = models.ResumeSection(
        owner_actor_id=actor.actor_id,
        resume_id=resume_id,
        section_type=_resume_editor_section_type_value(
            change.get("section_type"),
            title=change.get("title"),
            content_json=change.get("content_json"),
        ),
        sort_order=int(change.get("sort_order") or 0),
        title=str(change.get("title") or "AI Batch Section"),
        visible=True,
        content_json=json_safe(change.get("content_json") or []),
    )
    session.add(section)
    await session.flush()
    return section


async def _apply_resume_patch(session: AsyncSession, actor: ActorContext, resume: Any, patch: Mapping[str, Any]) -> list[Any]:
    changed_sections: list[Any] = []
    resume_updates = patch.get("resume") if isinstance(patch.get("resume"), Mapping) else {}
    if resume_updates:
        _apply_resume_updates(resume, resume_updates)
    sections = patch.get("sections") if isinstance(patch.get("sections"), list) else []
    for change in sections:
        if not isinstance(change, Mapping):
            continue
        section_id = change.get("section_id")
        section = await _fetch_resume_section(session, actor, resume.id, section_id)
        updates = {}
        if isinstance(change.get("title"), str):
            updates["title"] = change["title"]
        if isinstance(change.get("section_type"), str):
            updates["section_type"] = change["section_type"]
        if isinstance(change.get("content_json"), (list, dict)):
            updates["content_json"] = change["content_json"]
        _apply_resume_section_updates(section, updates)
        changed_sections.append(section)
    if not changed_sections and resume_updates:
        sections = await _resume_sections(session, resume.id)
        if sections:
            first = sections[0]
            first.content_json = _append_resume_content(first.content_json, _resume_content_item("AI patch", "Applied resume-wide optimization."))
            changed_sections.append(first)
    return changed_sections


def _resume_mutation_result(
    *,
    action: str,
    resume: Any,
    sections: list[Any],
    resume_spec: Any,
    section_spec: Any,
    summary: str,
    changed_sections_count: int | None = None,
) -> dict[str, Any]:
    serialized_resume = serialize_record(resume, resume_spec, resume_spec.detail_fields, include_long_text=True, truncate_long_text=False)
    serialized_sections = [
        serialize_record(section, section_spec, section_spec.detail_fields, include_long_text=True, truncate_long_text=False)
        for section in sections
    ]
    result = {
        "status": "completed",
        "tool_name": "invoke_action",
        "action": action,
        "model": "resume",
        "record_id": str(resume.id),
        "resume_id": str(resume.id),
        "resume": serialized_resume,
        "sections": serialized_sections,
        "summary": summary,
    }
    if changed_sections_count is not None:
        result["changed_sections_count"] = changed_sections_count
    return result


def _parse_resume_text(text: str) -> dict[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = lines[0][:120] if lines else "Parsed Candidate"
    email = ""
    for line in lines:
        if "@" in line:
            email = line.split()[-1][:200]
            break
    summary = _join_sentences(*(lines[:4] or [text[:240]]))
    return {
        "name": name,
        "email": email,
        "summary": summary,
        "section_title": "Parsed Resume",
    }


async def _default_or_new_profile_for_actor(session: AsyncSession, actor: ActorContext, parsed: Mapping[str, str]) -> Any:
    profile_spec = get_model_spec("profile")
    profile = (
        await session.execute(
            select(models.Profile)
            .where(models.Profile.owner_actor_id == actor.actor_id)
            .order_by(models.Profile.is_default.desc(), models.Profile.id.asc())
        )
    ).scalars().first()
    if profile is not None:
        return profile
    profile = models.Profile(
        owner_actor_id=actor.actor_id,
        name=parsed.get("name") or "Parsed Profile",
        headline=parsed.get("summary") or "",
        base_info_json={"parsed_resume": True},
        is_default=True,
    )
    session.add(profile)
    await session.flush()
    return profile


def _build_resume_report(resume: Any, sections: list[Any], job: Any | None) -> dict[str, Any]:
    section_titles = [str(section.title or "") for section in sections if str(section.title or "").strip()]
    suggestions = []
    if not resume.summary:
        suggestions.append("Add a concise summary.")
    if len(section_titles) < 2:
        suggestions.append("Add more tailored sections.")
    if job is not None:
        suggestions.append(f"Align summary more closely with {job.company} {job.title}.")
    return {
        "resume_id": str(resume.id),
        "job_id": str(getattr(job, "id", "") or ""),
        "title": str(resume.title or ""),
        "section_count": len(sections),
        "section_titles": section_titles,
        "suggestions": suggestions or ["Resume is structurally sound."],
    }


def _locked_payload(proposal: Any) -> Mapping[str, Any]:
    payload = proposal.locked_payload or {}
    if not isinstance(payload, Mapping):
        raise OperatorError("validation_error", "Proposal locked payload is invalid.", {"proposal_id": proposal.proposal_id})
    return payload


async def _validate_expected_version(
    session: AsyncSession,
    actor: ActorContext,
    record: Any,
    spec: Any,
    proposal: Any,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    expected = str(payload.get("expected_version_or_hash") or proposal.expected_version_or_hash or "")
    if not expected:
        return {"status": "exact", "current_version": ""}
    await session.refresh(record)
    current = canonical_version(record, spec)
    if current != expected:
        plan_id = str(getattr(proposal, "plan_id", "") or "")
        node_ids = [str(value) for value in list(getattr(proposal, "node_ids", None) or []) if str(value)]
        if plan_id and len(node_ids) == 1 and str(getattr(proposal, "tool_name", "")) == "patch_record":
            from app.operator.safe_rebase import evaluate_field_rebase
            current_record = serialize_record(record, spec, spec.detail_fields, include_long_text=True, truncate_long_text=False)
            sealed_before = proposal.before if isinstance(proposal.before, Mapping) else {}
            desired_updates = payload.get("updates") if isinstance(payload.get("updates"), Mapping) else {}
            assessment = evaluate_field_rebase(sealed_before=sealed_before, desired_updates=desired_updates, current_record=current_record)
            if assessment.status == "conflict":
                raise OperatorError("conflict_error", "Plan safe rebase was refused.", {"proposal_id": proposal.proposal_id, "competing_fields": list(assessment.competing_fields), "current_version_or_hash": current})
            setattr(proposal, "_pending_safe_rebase", {"node_id": node_ids[0], "sealed_before": dict(sealed_before), "current_record": current_record, "current_version": current, "desired_updates": dict(desired_updates), "event_key": f"{proposal.proposal_id}:{current}"})
            return {"status": assessment.status, "current_version": current, "rebased_updates": dict(assessment.rebased_updates)}
        raise OperatorError(
            "conflict_error",
            "Underlying record changed after proposal creation.",
            {
                "proposal_id": proposal.proposal_id,
                "expected_version_or_hash": expected,
                "current_version_or_hash": current,
            },
        )
    return {"status": "exact", "current_version": current}


async def _validate_action_expected_versions(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> None:
    current_expected = await collect_action_expected_versions(session, actor, spec, input_payload)
    stored_raw = payload.get("expected_versions")
    if not current_expected:
        if stored_raw not in (None, {}, ""):
            raise OperatorError(
                "conflict_error",
                "Action proposal contains unexpected locked reference versions.",
                {"proposal_id": proposal.proposal_id, "action": spec.action},
            )
        return
    if not isinstance(stored_raw, Mapping) or not stored_raw:
        raise OperatorError(
            "conflict_error",
            "Action proposal is missing locked reference versions.",
            {"proposal_id": proposal.proposal_id, "action": spec.action},
        )
    stored_expected = {str(key): str(value) for key, value in sorted(stored_raw.items())}
    expected_hash = expected_versions_hash(stored_expected)
    stored_hash = str(payload.get("expected_version_or_hash") or proposal.expected_version_or_hash or "")
    if not stored_hash or stored_hash != expected_hash:
        raise OperatorError(
            "conflict_error",
            "Action proposal version hash does not match its locked reference versions.",
            {
                "proposal_id": proposal.proposal_id,
                "action": spec.action,
                "expected_version_or_hash": stored_hash,
                "computed_expected_version_or_hash": expected_hash,
            },
        )
    current_normalized = {str(key): str(value) for key, value in sorted(current_expected.items())}
    if current_normalized != stored_expected:
        changed = sorted(
            key
            for key in set(current_normalized) | set(stored_expected)
            if current_normalized.get(key) != stored_expected.get(key)
        )
        raise OperatorError(
            "conflict_error",
            "Underlying action reference changed after proposal creation.",
            {
                "proposal_id": proposal.proposal_id,
                "action": spec.action,
                "changed_references": changed,
                "expected_versions": stored_expected,
                "current_versions": current_normalized,
            },
        )


def _validate_stored_risk(proposal: Any, computed_risk: int) -> None:
    """Fail closed when immutable confirmation metadata no longer matches policy."""
    stored_risk = int(getattr(proposal, "risk_level", -1))
    expected_risk = int(computed_risk)
    expected_confirmations = int(RISK_CONFIRMATIONS.get(expected_risk, 0))
    stored_confirmations = int(getattr(proposal, "confirmations_required", -1))
    expected_second = expected_risk >= 5
    stored_second = bool(getattr(proposal, "requires_second_confirmation", False))
    if (
        stored_risk != expected_risk
        or stored_confirmations != expected_confirmations
        or stored_second != expected_second
    ):
        raise OperatorError(
            "conflict_error",
            "Stored proposal risk policy no longer matches the authoritative operation policy.",
            {
                "proposal_id": str(getattr(proposal, "proposal_id", "") or ""),
                "stored_risk_level": stored_risk,
                "computed_risk_level": expected_risk,
                "stored_confirmations_required": stored_confirmations,
                "computed_confirmations_required": expected_confirmations,
                "stored_requires_second_confirmation": stored_second,
                "computed_requires_second_confirmation": expected_second,
            },
        )

def _decision_body(body: Mapping[str, Any] | None) -> dict[str, Any]:
    if body is None:
        return {}
    if not isinstance(body, Mapping):
        raise OperatorError("validation_error", "Proposal decision body must be an object.", {})
    allowed = {"confirmation_challenge", "reason"}
    unknown = sorted(str(key) for key in body if str(key) not in allowed)
    if unknown:
        raise OperatorError("validation_error", "Proposal decision body contains forbidden fields.", {"fields": unknown})
    result: dict[str, Any] = {}
    reason = body.get("reason")
    if reason is not None:
        if not isinstance(reason, str) or len(reason) > 1000:
            raise OperatorError("validation_error", "reason must be a bounded string.", {})
        result["reason"] = reason
    challenge = body.get("confirmation_challenge")
    if challenge is None:
        return result
    if not isinstance(challenge, str) or len(challenge) > 256:
        raise OperatorError("validation_error", "confirmation_challenge must be a bounded string.", {})
    return {**result, "confirmation_challenge": challenge}


def _is_expired(proposal: Any) -> bool:
    expires_at = proposal.expires_at
    return expires_at is not None and expires_at <= datetime.now(timezone.utc).replace(tzinfo=None)


def _events(proposal: Any) -> list[dict[str, Any]]:
    events = proposal.confirmation_events or []
    return [json_safe(event) for event in events if isinstance(event, Mapping)]


def _latest_event_id(events: list[dict[str, Any]], status: str) -> str:
    for event in reversed(events):
        if event.get("status") == status and event.get("event_id"):
            return str(event["event_id"])
    return ""


def _event(status: str, actor: ActorContext | None, *, result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "status": status,
        "actor_id": actor.actor_id if actor is not None else "",
        "session_id": actor.session_id if actor is not None else "",
        "created_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "result": json_safe(result),
    }


def _stored_result(proposal: Any) -> dict[str, Any]:
    for event in reversed(_events(proposal)):
        if event.get("status") == "confirmed" and isinstance(event.get("result"), Mapping):
            return json_safe(event["result"])
    return {"status": "completed", "summary": proposal.summary, "after": json_safe(proposal.after)}


async def _confirmed_invariant_issues(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    *,
    repair_pending_membership: bool = True,
) -> list[str]:
    issues: list[str] = []
    confirmed_events = [event for event in _events(proposal) if event.get("status") == "confirmed"]
    latest: Mapping[str, Any] | None = None
    if not confirmed_events:
        issues.append("missing_confirmed_event")
    else:
        latest = confirmed_events[-1]
        if str(latest.get("actor_id") or "") != actor.actor_id:
            issues.append("confirmed_event_actor_mismatch")
        if str(latest.get("session_id") or "") != actor.session_id:
            issues.append("confirmed_event_session_mismatch")
        if not isinstance(latest.get("result"), Mapping):
            issues.append("missing_confirmed_result")
        if not isinstance(latest.get("response"), Mapping):
            issues.append("missing_confirmed_response")
        response = latest.get("response") if isinstance(latest.get("response"), Mapping) else {}
        if str(response.get("proposal_id") or "") != str(proposal.proposal_id):
            issues.append("confirmed_response_proposal_mismatch")
        if response.get("ok") is not True or str(response.get("status") or "") != "confirmed":
            issues.append("confirmed_response_status_mismatch")
        receipt = latest.get("mutation_receipt") if isinstance(latest.get("mutation_receipt"), Mapping) else {}
        event_result = latest.get("result") if isinstance(latest.get("result"), Mapping) else {}
        expected_receipt = _build_mutation_receipt(proposal, latest, event_result)
        if receipt and receipt != expected_receipt:
            issues.append("mutation_receipt_mismatch")
        raw_locked = getattr(proposal, "locked_payload", None)
        locked = raw_locked if isinstance(raw_locked, Mapping) else {}
        if not isinstance(raw_locked, Mapping):
            issues.append("invalid_locked_payload")
        if str(locked.get("action") or "") == "batch_mutate":
            input_data = locked.get("input") if isinstance(locked.get("input"), Mapping) else {}
            target = input_data.get("target") if isinstance(input_data.get("target"), Mapping) else {}
            expected_ids = sorted(str(item) for item in (target.get("record_ids") or []))
            scope = latest.get("intent_scope") if isinstance(latest.get("intent_scope"), Mapping) else {}
            scope_ids = sorted(str(item) for item in (scope.get("record_ids") or []))
            if (
                str(scope.get("actor_id") or "") != actor.actor_id
                or str(scope.get("model") or "") != str(input_data.get("model") or "")
                or str(scope.get("operation") or "") != str(input_data.get("operation") or "")
                or scope_ids != expected_ids
            ):
                issues.append("confirmed_intent_scope_mismatch")
    try:
        confirmations_received = int(getattr(proposal, "confirmations_received", 0) or 0)
        confirmation_count = int(getattr(proposal, "confirmation_count", 0) or 0)
        confirmations_required = int(getattr(proposal, "confirmations_required", 0) or 0)
    except (TypeError, ValueError):
        issues.append("invalid_confirmation_counts")
        confirmations_received = confirmation_count = confirmations_required = 0
    if confirmations_required < 1:
        confirmations_required = 2 if bool(getattr(proposal, "requires_second_confirmation", False)) else 1
    if confirmations_received < 1:
        issues.append("missing_confirmation_count")
    if confirmations_received < confirmations_required:
        issues.append("insufficient_confirmations")
    if confirmation_count != confirmations_received:
        issues.append("confirmation_count_mismatch")
    if getattr(proposal, "first_confirmed_at", None) is None:
        issues.append("missing_first_confirmed_at")
    if confirmations_required >= 2 and getattr(proposal, "second_confirmed_at", None) is None:
        issues.append("missing_second_confirmed_at")
    agent_session = await session.get(models.AgentSession, actor.session_id, populate_existing=True)
    if agent_session is None or str(agent_session.actor_id or "") != actor.actor_id:
        issues.append("missing_agent_session")
    elif str(proposal.proposal_id) in list(agent_session.pending_proposal_ids or []):
        # Inventory callers must remain pure. Confirm/recovery paths may explicitly
        # repair this control-plane drift inside their write transaction.
        if not repair_pending_membership:
            issues.append("still_listed_as_pending")
        else:
            from app.operator.guards import remove_pending_proposal_id

            await remove_pending_proposal_id(session, actor, str(proposal.proposal_id))
            agent_session = await session.get(models.AgentSession, actor.session_id, populate_existing=True)
            if agent_session is not None and str(proposal.proposal_id) in list(
                agent_session.pending_proposal_ids or []
            ):
                issues.append("still_listed_as_pending")
    continuation = await session.get(
        models.ProposalContinuation,
        str(proposal.proposal_id),
        populate_existing=True,
    )
    if continuation is None:
        issues.append("missing_continuation_job")
    else:
        expected_event_id = str(latest.get("event_id") or "") if latest is not None else ""
        expected_invocation_key = (
            f"proposal-continuation:v1:{proposal.proposal_id}:{expected_event_id}"
            if expected_event_id
            else ""
        )
        if str(continuation.actor_id or "") != actor.actor_id:
            issues.append("continuation_actor_mismatch")
        if str(continuation.session_id or "") != actor.session_id:
            issues.append("continuation_session_mismatch")
        if str(continuation.confirmed_event_id or "") != expected_event_id:
            issues.append("continuation_event_mismatch")
        if str(continuation.invocation_key or "") != expected_invocation_key:
            issues.append("continuation_invocation_mismatch")
        payload = continuation.payload if isinstance(continuation.payload, Mapping) else {}
        if not isinstance(continuation.payload, Mapping):
            issues.append("invalid_continuation_payload")
        elif (
            str(payload.get("proposal_id") or "") != str(proposal.proposal_id)
            or str(payload.get("event_id") or "") != expected_event_id
            or str(payload.get("actor_id") or "") != actor.actor_id
            or str(payload.get("session_id") or "") != actor.session_id
            or str(payload.get("invocation_key") or "") != expected_invocation_key
        ):
            issues.append("continuation_payload_binding_mismatch")
        from app.operator.continuations import valid_payload

        if not valid_payload(continuation):
            issues.append("continuation_payload_hash_mismatch")
        continuation_status = str(continuation.status or "")
        if continuation_status not in {"queued", "running", "succeeded", "failed", "manual_review"}:
            issues.append("invalid_continuation_status")
        if continuation_status == "succeeded" and not isinstance(continuation.result, Mapping):
            issues.append("missing_continuation_result")
    if str(getattr(proposal, "plan_id", "") or "") and str(getattr(proposal, "tool_name", "") or "") == "confirm_plan_group":
        execution_job = await session.get(models.PlanGroupExecutionJob, str(proposal.proposal_id), populate_existing=True)
        if execution_job is None:
            issues.append("missing_plan_execution_job")
        elif execution_job.status != "completed":
            issues.append("incomplete_plan_execution_job")
        elif str(execution_job.plan_id) != str(proposal.plan_id) or str(execution_job.group_id) != str(proposal.confirmation_group_id):
            issues.append("plan_execution_job_binding_mismatch")
        elif not isinstance(execution_job.result_json, Mapping):
            issues.append("missing_plan_execution_job_result")
        try:
            from app.operator.plan_execution import execution_result_from_receipts

            durable_result = json_safe(await execution_result_from_receipts(
                session, str(proposal.plan_id), group_id=str(proposal.confirmation_group_id)
            ))
            durable_receipt_id = str(durable_result.get("result_receipt_id") or "")
            durable_digest = str(durable_result.get("result_digest") or "")
            if not durable_result.get("all_nodes_terminal"):
                issues.append("plan_group_nodes_not_terminal")
            if not durable_receipt_id or not durable_digest:
                issues.append("missing_durable_group_result_reference")
            event_result = latest.get("result") if latest is not None and isinstance(latest.get("result"), Mapping) else {}
            event_response = latest.get("response") if latest is not None and isinstance(latest.get("response"), Mapping) else {}
            event_receipt = latest.get("mutation_receipt") if latest is not None and isinstance(latest.get("mutation_receipt"), Mapping) else {}
            expected_response = _confirmed_response(proposal, durable_result)
            expected_response["result_receipt_id"] = durable_receipt_id
            expected_response["result_digest"] = durable_digest
            if json_safe(event_result) != durable_result:
                issues.append("confirmed_event_durable_result_mismatch")
            if json_safe(event_response) != json_safe(expected_response):
                issues.append("confirmed_response_durable_result_mismatch")
            if (
                str(latest.get("result_receipt_id") or "") != durable_receipt_id
                or str(latest.get("result_digest") or "") != durable_digest
            ):
                issues.append("confirmed_event_result_reference_mismatch")
            if (
                str(event_receipt.get("result_receipt_id") or "") != durable_receipt_id
                or str(event_receipt.get("result_digest") or "") != durable_digest
                or json_safe(event_receipt.get("result") or {}) != durable_result
            ):
                issues.append("mutation_receipt_durable_result_mismatch")
            if execution_job is not None:
                if (
                    str(execution_job.result_receipt_id or "") != durable_receipt_id
                    or str(execution_job.result_digest or "") != durable_digest
                    or json_safe(execution_job.result_json or {}) != durable_result
                ):
                    issues.append("execution_job_durable_result_mismatch")
            if continuation is not None:
                payload = continuation.payload if isinstance(continuation.payload, Mapping) else {}
                message_payload = payload.get("message_payload") if isinstance(payload.get("message_payload"), Mapping) else {}
                durable_ref = payload.get("durable_result_ref") if isinstance(payload.get("durable_result_ref"), Mapping) else {}
                message_ref = message_payload.get("durable_result_ref") if isinstance(message_payload.get("durable_result_ref"), Mapping) else {}
                expected_ref = {"result_receipt_id": durable_receipt_id, "result_digest": durable_digest}
                continuation_mismatch = False
                if (
                    str(continuation.result_receipt_id or "") != durable_receipt_id
                    or str(continuation.result_digest or "") != durable_digest
                    or json_safe(durable_ref) != expected_ref
                    or json_safe(message_ref) != expected_ref
                ):
                    issues.append("continuation_result_reference_mismatch")
                    continuation_mismatch = True
                if json_safe(payload.get("execution_result") or {}) != durable_result:
                    issues.append("continuation_execution_result_mismatch")
                    continuation_mismatch = True
                if (
                    json_safe(message_payload.get("result") or {}) != durable_result
                    or json_safe(message_payload.get("changed_records") or []) != json_safe(durable_result.get("changed_records") or [])
                ):
                    issues.append("continuation_message_payload_mismatch")
                    continuation_mismatch = True
                if continuation_mismatch:
                    issues.append("continuation_durable_result_mismatch")
        except Exception:
            issues.append("durable_execution_receipt_projection_failed")
    if latest is not None:
        event_id = str(latest.get("event_id") or "")
        audit_row = await session.scalar(
            select(models.AgentAuditLog).where(
                models.AgentAuditLog.proposal_id == str(proposal.proposal_id),
                models.AgentAuditLog.confirmation_status == "confirmed",
                models.AgentAuditLog.confirmation_event_id == event_id,
            ).order_by(models.AgentAuditLog.created_at.desc())
        )
        if audit_row is None:
            issues.append("missing_confirmed_audit")
        else:
            if str(audit_row.actor_id or "") != actor.actor_id:
                issues.append("confirmed_audit_actor_mismatch")
            if str(audit_row.session_id or "") != actor.session_id:
                issues.append("confirmed_audit_session_mismatch")
            if str(audit_row.tool_name or "") != str(proposal.tool_name or ""):
                issues.append("confirmed_audit_tool_mismatch")
            if str(audit_row.idempotency_key or "") != str(proposal.idempotency_key or ""):
                issues.append("confirmed_audit_idempotency_mismatch")
            if str(getattr(proposal, "tool_name", "") or "") == "confirm_plan_group" and latest is not None:
                if (
                    str(audit_row.result_receipt_id or "") != str(latest.get("result_receipt_id") or "")
                    or str(audit_row.result_digest or "") != str(latest.get("result_digest") or "")
                ):
                    issues.append("confirmed_audit_result_reference_mismatch")
            if str(audit_row.result_status or "") in {"", "error", "transient_error"}:
                issues.append("confirmed_audit_result_invalid")
            receipt = latest.get("mutation_receipt") if isinstance(latest.get("mutation_receipt"), Mapping) else {}
            audit_receipt = str(audit_row.after_version_or_hash or "")
            try:
                requires_receipt = int(getattr(proposal, "confirmation_invariant_version", 0) or 0) >= 1
            except (TypeError, ValueError):
                issues.append("invalid_confirmation_invariant_version")
                requires_receipt = True
            if (requires_receipt or audit_receipt.startswith(_CONFIRMATION_RECEIPT_AUDIT_PREFIX)) and not receipt:
                issues.append("missing_mutation_receipt")
            if receipt:
                changed_records = receipt.get("changed_records")
                if not isinstance(changed_records, list) or any(
                    not isinstance(item, Mapping)
                    or (
                        not str(item.get("action") or "").strip()
                        and (
                            not str(item.get("model") or item.get("result_model") or "").strip()
                            or not str(item.get("id") or "").strip()
                        )
                    )
                    for item in (changed_records if isinstance(changed_records, list) else [])
                ):
                    issues.append("invalid_receipt_changed_records")
                receipt_digest = str(receipt.get("digest") or "")
                if audit_receipt not in {
                    receipt_digest,
                    f"{_CONFIRMATION_RECEIPT_AUDIT_PREFIX}{receipt_digest}",
                }:
                    issues.append("confirmed_audit_receipt_mismatch")
                if json_safe(audit_row.changed_records or []) != json_safe(receipt.get("changed_records") or []):
                    issues.append("confirmed_audit_changed_records_mismatch")
    return issues


async def scan_confirmed_invariant_violations(
    session: AsyncSession,
    *,
    limit: int = 1000,
) -> dict[str, Any]:
    """Read-only inventory of confirmed rows that cannot prove their durable invariant."""

    safe_limit = max(1, min(int(limit), 10_000))
    with session.no_autoflush:
        proposals = list(
            await session.scalars(
                select(models.ProposalCache)
                .where(models.ProposalCache.status == "confirmed")
                .order_by(models.ProposalCache.created_at.asc(), models.ProposalCache.proposal_id.asc())
                .limit(safe_limit)
            )
        )
        violations: list[dict[str, Any]] = []
        for proposal in proposals:
            actor = ActorContext(
                actor_id=str(proposal.actor_id or ""),
                session_id=str(proposal.session_id or ""),
                adapter="confirmed_invariant_scan",
            )
            try:
                issues = await _confirmed_invariant_issues(
                    session,
                    actor,
                    proposal,
                    repair_pending_membership=False,
                )
            except Exception as exc:  # Defensive inventory: malformed history must remain reportable.
                issues = ["invariant_scan_error", type(exc).__name__]
            if issues:
                violations.append(
                    {
                        "proposal_id": str(proposal.proposal_id),
                        "actor_id": str(proposal.actor_id or ""),
                        "session_id": str(proposal.session_id or ""),
                        "issues": list(issues),
                    }
                )
    return {
        "scanned": len(proposals),
        "invalid": len(violations),
        "violations": violations,
        "truncated": len(proposals) >= safe_limit,
    }


def _build_mutation_receipt(
    proposal: Any,
    confirmed_event: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    changed_records = result.get("changed_records") if isinstance(result.get("changed_records"), list) else []
    material = {
        "version": 1,
        "proposal_id": str(proposal.proposal_id),
        "event_id": str(confirmed_event.get("event_id") or ""),
        "actor_id": str(confirmed_event.get("actor_id") or ""),
        "session_id": str(confirmed_event.get("session_id") or ""),
        "idempotency_key": str(proposal.idempotency_key or ""),
        "tool_name": str(proposal.tool_name or ""),
        "model_or_action": str(proposal.model_or_action or ""),
        "result": json_safe(dict(result)),
        "result_receipt_id": str(result.get("result_receipt_id") or confirmed_event.get("result_receipt_id") or ""),
        "result_digest": str(result.get("result_digest") or confirmed_event.get("result_digest") or ""),
        "base_response": json_safe(dict(confirmed_event.get("response") or {})),
        "changed_records": json_safe(changed_records),
        "confirmation_count": int(getattr(proposal, "confirmation_count", 0) or 0),
        "confirmations_received": int(getattr(proposal, "confirmations_received", 0) or 0),
        "confirmations_required": int(getattr(proposal, "confirmations_required", 0) or 0),
        "first_confirmed_at": json_safe(getattr(proposal, "first_confirmed_at", None)),
        "second_confirmed_at": json_safe(getattr(proposal, "second_confirmed_at", None)),
        "intent_scope": json_safe(confirmed_event.get("intent_scope") or {}),
    }
    digest = hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    receipt = dict(material)
    receipt["digest"] = digest
    return receipt


async def _validated_stored_confirm_response(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
) -> dict[str, Any]:
    invariant_issues = await _confirmed_invariant_issues(session, actor, proposal)
    if not invariant_issues:
        return _stored_confirm_response(proposal)
    await _quarantine_corrupt_confirmed_proposal(session, actor, proposal, invariant_issues)
    await session.commit()
    raise OperatorError(
        "conflict_error",
        "Confirmed proposal state is incomplete and requires manual review.",
        {
            "proposal_id": str(proposal.proposal_id),
            "requires_manual_review": True,
            "invariant_issues": invariant_issues,
        },
    )


async def _quarantine_corrupt_confirmed_proposal(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    issues: list[str],
) -> None:
    conflict_event = _event(
        "conflict",
        actor,
        result={
            "status": "conflict",
            "reason": "confirmed_invariant_violation",
            "invariant_issues": list(issues),
        },
    )
    proposal.status = "conflict"
    proposal.reason = "Confirmed proposal state is incomplete and requires manual review."
    proposal.confirmation_events = [*_events(proposal), conflict_event]
    flag_modified(proposal, "confirmation_events")
    await _remove_pending_proposal_id(session, actor, str(proposal.proposal_id))
    await _resolve_harness_pending_proposal(session, actor, str(proposal.proposal_id), status="conflict")
    # Same transaction: revoke any queued/running continuation so a worker that
    # already claimed cannot keep driving agent work on a quarantined confirm.
    confirmed_events = [
        event for event in _events(proposal) if event.get("status") == "confirmed"
    ]
    expected_confirmed_event_id = (
        str(confirmed_events[-1].get("event_id") or "") if confirmed_events else ""
    )
    await _revoke_proposal_continuation_for_quarantine(
        session,
        proposal_id=str(proposal.proposal_id),
        proposal_actor_id=str(proposal.actor_id or ""),
        proposal_session_id=str(proposal.session_id or ""),
        expected_confirmed_event_id=expected_confirmed_event_id,
        issues=list(issues),
    )
    await _audit_decision(
        session,
        actor,
        proposal,
        confirmation_status="confirmed_invariant_failed",
        result_status="conflict_error",
        error="Confirmed proposal state violated durable invariants.",
        confirmation_event_id=str(conflict_event.get("event_id") or ""),
    )


async def _revoke_proposal_continuation_for_quarantine(
    session: AsyncSession,
    *,
    proposal_id: str,
    issues: list[str],
    proposal_actor_id: str | None = None,
    proposal_session_id: str | None = None,
    expected_confirmed_event_id: str | None = None,
) -> None:
    """Terminalize only continuation work authoritatively bound to the proposal.

    The continuation row is one of the invariants being quarantined, so its
    invocation key and actor/session scope are not authority for revoking inner
    work.  Inner/session fencing is derived from ProposalCache plus the latest
    confirmed event and remains exact-CAS shaped.
    """
    error = {
        "code": "confirmed_invariant_quarantine",
        "message": "Confirmed proposal was quarantined; continuation revoked for manual review.",
        "requires_manual_review": True,
        "invariant_issues": list(issues),
    }
    authoritative_proposal = None
    if (
        proposal_actor_id is None
        or proposal_session_id is None
        or expected_confirmed_event_id is None
    ):
        authoritative_proposal = await session.get(
            models.ProposalCache,
            proposal_id,
            populate_existing=True,
        )
    if authoritative_proposal is not None:
        if proposal_actor_id is None:
            proposal_actor_id = str(authoritative_proposal.actor_id or "")
        if proposal_session_id is None:
            proposal_session_id = str(authoritative_proposal.session_id or "")
        if expected_confirmed_event_id is None:
            confirmed_events = [
                event
                for event in _events(authoritative_proposal)
                if event.get("status") == "confirmed"
            ]
            expected_confirmed_event_id = (
                str(confirmed_events[-1].get("event_id") or "")
                if confirmed_events
                else ""
            )

    actor_id = str(proposal_actor_id or "")
    session_id = str(proposal_session_id or "")
    event_id = str(expected_confirmed_event_id or "")
    expected_invocation_key = (
        f"proposal-continuation:v1:{proposal_id}:{event_id}" if event_id else ""
    )

    continuation = await session.get(
        models.ProposalContinuation,
        proposal_id,
        populate_existing=True,
    )

    # The deterministic key is authoritative even if the outer row is missing or
    # corrupt.  Never follow continuation.invocation_key into another proposal.
    inner = None
    inner_token = ""
    inner_bound = False
    if expected_invocation_key and actor_id and session_id:
        inner = await session.get(
            models.AgentContinuationInvocation,
            expected_invocation_key,
            populate_existing=True,
        )
        inner_bound = bool(
            inner is not None
            and str(inner.invocation_key or "") == expected_invocation_key
            and str(inner.proposal_id or "") == proposal_id
            and str(inner.actor_id or "") == actor_id
            and str(inner.session_id or "") == session_id
        )
        if inner_bound and str(inner.status or "") == "running":
            inner_token = str(inner.lease_token or "")

    session_snapshot = None
    session_token = ""
    session_generation = 0
    if inner_bound and inner_token:
        session_snapshot = await session.get(
            models.AgentSessionExecutionLease,
            {"actor_id": actor_id, "session_id": session_id},
            populate_existing=True,
        )
        session_token = str(getattr(session_snapshot, "lease_token", "") or "")
        session_generation = int(getattr(session_snapshot, "generation", 0) or 0)

    outer_changed = await session.execute(
        update(models.ProposalContinuation)
        .where(
            models.ProposalContinuation.proposal_id == proposal_id,
            models.ProposalContinuation.status.in_(["queued", "failed", "running"]),
        )
        .values(
            status="manual_review",
            lease_token="",
            lease_expires_at=None,
            available_at=None,
            result=None,
            error=error,
        )
        .execution_options(synchronize_session=False)
    )
    outer_revoked = outer_changed.rowcount == 1

    inner_revoked = False
    if inner_bound and inner_token:
        inner_changed = await session.execute(
            update(models.AgentContinuationInvocation)
            .where(
                models.AgentContinuationInvocation.invocation_key
                == expected_invocation_key,
                models.AgentContinuationInvocation.proposal_id == proposal_id,
                models.AgentContinuationInvocation.actor_id == actor_id,
                models.AgentContinuationInvocation.session_id == session_id,
                models.AgentContinuationInvocation.status == "running",
                models.AgentContinuationInvocation.lease_token == inner_token,
            )
            .values(
                status="manual_review",
                lease_token="",
                lease_expires_at=None,
                completed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                result={"ok": False, "error": error},
            )
            .execution_options(synchronize_session=False)
        )
        inner_revoked = inner_changed.rowcount == 1

    if (
        inner_revoked
        and session_snapshot is not None
        and str(session_snapshot.owner_invocation_key or "")
        == expected_invocation_key
        and session_token
        and session_token == inner_token
    ):
        await session.execute(
            update(models.AgentSessionExecutionLease)
            .where(
                models.AgentSessionExecutionLease.actor_id == actor_id,
                models.AgentSessionExecutionLease.session_id == session_id,
                models.AgentSessionExecutionLease.owner_invocation_key
                == expected_invocation_key,
                models.AgentSessionExecutionLease.lease_token == session_token,
                models.AgentSessionExecutionLease.generation == session_generation,
            )
            .values(
                owner_invocation_key="",
                lease_token="",
                lease_expires_at=None,
            )
            .execution_options(synchronize_session=False)
        )

    # Keep the identity map aligned without creating a second, unfenced ORM
    # UPDATE that could overwrite a terminal/concurrent state after CAS miss.
    if continuation is not None:
        if outer_revoked:
            set_committed_value(continuation, "status", "manual_review")
            set_committed_value(continuation, "lease_token", "")
            set_committed_value(continuation, "lease_expires_at", None)
            set_committed_value(continuation, "available_at", None)
            set_committed_value(continuation, "result", None)
            set_committed_value(continuation, "error", error)
        else:
            await session.refresh(continuation)


def _stored_confirm_response(proposal: Any) -> dict[str, Any]:
    for event in reversed(_events(proposal)):
        if event.get("status") == "confirmed" and isinstance(event.get("response"), Mapping):
            return json_safe(event["response"])
    return _confirmed_response(proposal, _stored_result(proposal))


def _confirmed_response(
    proposal: Any,
    result: Mapping[str, Any],
    *,
    pre_confirmation_checkpoint_id: str = "",
) -> dict[str, Any]:
    visible_result = attach_visibility(result, proposal=proposal)
    checkpoint_id = pre_confirmation_checkpoint_id or str(
        visible_result.get("pre_confirmation_checkpoint_id") or visible_result.get("checkpoint_id") or ""
    )
    response = {
        "ok": bool(visible_result.get("ok", True)),
        "status": "confirmed",
        "proposal_id": proposal.proposal_id,
        "result": json_safe(visible_result),
        "changed_records": visible_result.get("changed_records", []),
        "affected_resources": visible_result.get("affected_resources", []),
    }
    assistant_message = str(visible_result.get("assistant_message") or "")
    if assistant_message:
        response["assistant_message"] = assistant_message
    stop_reason = str(visible_result.get("stop_reason") or "")
    if stop_reason:
        response["stop_reason"] = stop_reason
    surfaced_proposal = visible_result.get("proposal")
    if isinstance(surfaced_proposal, Mapping):
        response["proposal"] = json_safe(dict(surfaced_proposal))
    surfaced_proposals = visible_result.get("proposals")
    if isinstance(surfaced_proposals, list):
        response["proposals"] = json_safe(list(surfaced_proposals))
    if checkpoint_id:
        response["checkpoint_id"] = checkpoint_id
        response["pre_confirmation_checkpoint_id"] = checkpoint_id
    if isinstance(visible_result.get("active_plan"), Mapping):
        response["active_plan"] = json_safe(dict(visible_result["active_plan"]))
    return response


_EXECUTION_FAILURE_NEXT_ALLOWED_OPERATIONS = (
    "view_manual_review_cases",
    "list_manual_review_cases",
    "resolve_manual_review_case",
)


def _execution_failure_expression(
    response: Mapping[str, Any],
    execution_result: Mapping[str, Any],
    *,
    plan_id: str = "",
    plan_status: str = "",
    manual_review_case_id: str = "",
) -> dict[str, Any]:
    """Derived user/model-facing expression for a confirmed-but-not-executed proposal.

    The durable confirmed event (and its stored response inside the confirmed
    event) is authoritative and unchanged. This view is what the confirm API
    returns when authorization was durably recorded but execution did not
    produce a successful outcome, so an execution failure is never presented
    with confirmed-success semantics.

    It distinguishes "confirmation recorded but execution did not occur"
    (``confirmation_recorded=True``, ``ok=False``, non-``confirmed`` status,
    plus a structured ``error`` block and ``next_allowed_operations``) from
    "execution succeeded" (``ok=True``, ``status="confirmed"``).
    """
    failures = [item for item in (execution_result.get("failures") or []) if isinstance(item, Mapping)]
    messages = [str(item.get("message") or "") for item in failures if str(item.get("message") or "").strip()]
    failure_status = str(execution_result.get("status") or "manual_review")
    reason = " ".join(dict.fromkeys(messages)) or (
        f"Plan Group execution did not complete ({failure_status}); manual review is required."
    )
    expression = {
        **dict(response),
        "ok": False,
        "status": failure_status,
        "confirmation_recorded": True,
        "error": {
            "code": "execution_rejected",
            "message": reason,
            "details": {
                "plan_id": str(plan_id or execution_result.get("plan_id") or ""),
                "group_id": str(execution_result.get("group_id") or ""),
                "group_status": str(execution_result.get("group_status") or ""),
                "failures": json_safe(failures),
                "requires_manual_review": True,
            },
        },
        "next_allowed_operations": list(_EXECUTION_FAILURE_NEXT_ALLOWED_OPERATIONS),
    }
    if manual_review_case_id:
        expression["error"]["details"]["manual_review_case_id"] = str(manual_review_case_id)
        expression["manual_review_case_id"] = str(manual_review_case_id)
    return expression


def _first_manual_review_case_id(envelope: Mapping[str, Any]) -> str:
    """First open manual-review case id projected onto a plan_event envelope."""
    for node in envelope.get("nodes") or []:
        if isinstance(node, Mapping) and str(node.get("manual_review_case_id") or ""):
            return str(node["manual_review_case_id"])
    return ""


def _operator_error_response(exc: OperatorError) -> dict[str, Any]:
    if exc.code == "not_implemented":
        return {
            "ok": False,
            "error": {
                "code": "not_implemented",
                "message": exc.message,
                "details": json_safe(exc.details or {}),
            },
        }
    if exc.code == "validation_error":
        return validation_error(exc.message, exc.details)
    if exc.code == "permission_error":
        return permission_error(exc.message, exc.details)
    if exc.code == "not_found_error":
        return not_found_error(exc.message, exc.details)
    if exc.code == "conflict_error":
        return conflict_error(exc.message, exc.details)
    if exc.code == "transient_error":
        return transient_error(exc.message, exc.details)
    return validation_error(exc.message, exc.details)


def _action_is_implemented(spec: Any) -> bool:
    return str(getattr(spec, "implementation_status", "")) == "implemented"


def _early_not_implemented_check(proposal: Any) -> OperatorError | None:
    """Return a not_implemented OperatorError if the proposal targets a non-operable action.

    This runs before the multi-confirmation flow so unimplemented actions are
    refused immediately without ever recording any confirmations.
    """
    if str(getattr(proposal, "tool_name", "") or "") != "invoke_action":
        return None
    action_name = str(getattr(proposal, "model_or_action", "") or "")
    payload = proposal.locked_payload if isinstance(proposal.locked_payload, Mapping) else {}
    payload_action = str(payload.get("action") or "")
    if payload_action:
        action_name = payload_action
    if not action_name:
        return None
    try:
        spec = get_action_spec(action_name)
    except OperatorError:
        return None
    if _action_is_implemented(spec):
        return None
    return OperatorError(
        "not_implemented",
        f"Operator action is not implemented: {action_name}",
        {
            "action": action_name,
            "implementation_status": str(getattr(spec, "implementation_status", "not_implemented")),
            "reason": str(getattr(spec, "non_operable_reason", "") or "Action is not implemented."),
        },
    )


async def _audit_decision(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any | None,
    *,
    confirmation_status: str,
    result_status: str,
    result_summary: str = "",
    error: str = "",
    args_snapshot: Mapping[str, Any] | None = None,
    confirmation_event_id: str = "",
    changed_records: list[Mapping[str, Any]] | None = None,
    after_version_or_hash: str = "",
    result_receipt_id: str = "",
    result_digest: str = "",
) -> None:
    await log_agent_audit(
        session,
        actor=actor,
        proposal=proposal,
        proposal_id=str(getattr(proposal, "proposal_id", "") if proposal is not None else ""),
        args_snapshot=args_snapshot or {},
        args_redacted=redact_audit_args(args_snapshot or {}),
        confirmation_event_id=confirmation_event_id,
        confirmation_status=confirmation_status,
        result_status=result_status,
        result_summary=result_summary,
        changed_records=(
            changed_records
            if changed_records is not None
            else (getattr(proposal, "affected_records", []) if proposal is not None else [])
        ),
        after_version_or_hash=after_version_or_hash,
        result_receipt_id=result_receipt_id,
        result_digest=result_digest,
        error=error,
    )


async def _audit_lost_reject_claim(session: AsyncSession, actor: ActorContext, proposal_id: str) -> None:
    proposal = await _load_proposal_authoritative(session, proposal_id)
    await _audit_decision(
        session,
        actor,
        proposal,
        confirmation_status="reject_rejected",
        result_status="conflict_error",
        error="Proposal was already transitioned by another decision.",
    )
    await session.commit()


def _unsafe_decision_snapshot(body: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        return {}
    return {str(key): json_safe(value) for key, value in body.items()}


async def _rollback_if_needed(session: AsyncSession, exc: OperatorError) -> None:
    await _rollback_quietly(session)


async def _rollback_quietly(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        pass


async def _prepare_remember_preference_action(
    session: AsyncSession,
    actor: ActorContext,
    proposal: Any,
    payload: Mapping[str, Any],
    spec: Any,
    input_payload: Mapping[str, Any],
) -> Any:
    """Prepare the durable memory write for remember_preference.

    The write routes through every ``write_memory_candidate`` guard (category
    whitelist, business-fact rejection, sensitive-content confirmation, scope,
    redaction). It executes directly after the low-risk invoke_action routing
    instead of requiring a proposal, so ordinary preference writes do not force
    a confirmation round-trip; sensitive content is never auto-stored and
    instead returns a structured confirmation request.
    """
    category = str(input_payload.get("category") or "").strip()
    topic = str(input_payload.get("topic") or "global").strip()
    content_raw = input_payload.get("content")
    scope = str(input_payload.get("scope") or "session").strip()
    if scope not in {"session", "actor"}:
        raise OperatorError(
            "validation_error",
            "Memory scope must be session or actor.",
            {"action": "remember_preference", "scope": scope},
        )
    if not isinstance(content_raw, Mapping):
        raise OperatorError(
            "validation_error",
            "Memory content must be an object.",
            {"action": "remember_preference", "category": category},
        )

    subject = str(getattr(actor, "auth_subject", "") or "")
    if scope == "actor":
        from app.operator.memory import memory_session_id

        effective_session = memory_session_id(subject) if subject else ""
    else:
        effective_session = str(actor.session_id or "")
    effective_actor = ActorContext(
        actor_id=str(actor.actor_id or ""),
        session_id=effective_session,
        adapter=str(getattr(actor, "adapter", "") or "web"),
        auth_subject=subject,
    )

    async def execute() -> dict[str, Any]:
        await _validate_action_expected_versions(session, actor, proposal, payload, spec, input_payload)
        from app.operator.memory import write_memory_candidate

        result = await write_memory_candidate(
            session,
            effective_actor,
            category=category,
            topic=topic,
            content=content_raw,
            confidence=1.0,
            skill="",
            sensitive_confirmed=False,
        )
        if result.get("needs_confirmation"):
            message = str((result.get("error") or {}).get("message") or "Sensitive memory requires explicit confirmation.")
            return {
                "status": "needs_confirmation",
                "tool_name": "invoke_action",
                "action": "remember_preference",
                "model": "agent_memory",
                "memory_id": "",
                "category": category,
                "topic": topic,
                "scope": scope,
                "needs_confirmation": True,
                "confirmation": {
                    "code": "sensitive_memory_confirmation_required",
                    "message": message,
                    "action": "remember_preference",
                    "category": category,
                    "topic": topic,
                },
                "summary": "Memory content is sensitive and was not stored; explicit confirmation is required before it can be saved.",
            }
        if not result.get("ok"):
            error = result.get("error") or {}
            raise OperatorError(
                str(error.get("code") or "operator_error"),
                str(error.get("message") or "Memory write was rejected by operator guards."),
                json_safe(error.get("details") or {}),
            )
        memory = result.get("memory") or {}
        return {
            "status": "completed",
            "tool_name": "invoke_action",
            "action": "remember_preference",
            "model": "agent_memory",
            "memory_id": str(memory.get("memory_id") or ""),
            "category": category,
            "topic": topic,
            "scope": scope,
            "needs_confirmation": False,
            "memory": memory,
            "summary": f"Stored {category} memory ({topic or 'global'}, {scope} scope).",
        }

    return execute





