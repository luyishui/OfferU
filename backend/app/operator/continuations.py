from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import models
from app.operator.guards import json_safe


MAX_CONTINUATION_ATTEMPTS = 3
CONTINUATION_JOB_LEASE_SECONDS = 60.0


def build_continuation(proposal: Any, actor: Any, event_id: str, execution_result: Mapping[str, Any]) -> Any:
    invocation_key = f"proposal-continuation:v1:{proposal.proposal_id}:{event_id}"
    message_payload = {"proposal_id": str(proposal.proposal_id), "status": "executed",
                       "summary": str(execution_result.get("summary") or proposal.summary or "Proposal executed."),
                       "tool_name": str(proposal.tool_name or ""), "model_or_action": str(proposal.model_or_action or ""),
                       "changed_records": execution_result.get("changed_records") or execution_result.get("affected_records") or [],
                       "result": dict(execution_result), "invocation_key": invocation_key}
    result_receipt_id = str(execution_result.get("result_receipt_id") or "")
    result_digest = str(execution_result.get("result_digest") or "")
    durable_result_ref = {"result_receipt_id": result_receipt_id, "result_digest": result_digest}
    message_payload["durable_result_ref"] = durable_result_ref
    payload = json_safe({"version": 2, "proposal_id": str(proposal.proposal_id), "event_id": event_id,
                         "actor_id": actor.actor_id, "session_id": actor.session_id,
                         "adapter": actor.adapter, "execution_result": dict(execution_result),
                         "message_payload": message_payload,
                         "durable_result_ref": durable_result_ref,
                         "invocation_key": invocation_key})
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return models.ProposalContinuation(proposal_id=str(proposal.proposal_id), actor_id=actor.actor_id,
        session_id=actor.session_id, confirmed_event_id=event_id, invocation_key=invocation_key,
        status="queued", payload=payload, payload_hash=digest, result_receipt_id=result_receipt_id,
        result_digest=result_digest, available_at=datetime.now(timezone.utc).replace(tzinfo=None))


def valid_payload(job: Any) -> bool:
    digest = hashlib.sha256(json.dumps(job.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return digest == job.payload_hash


async def claim(session: AsyncSession, proposal_id: str, *, lease_seconds: float = CONTINUATION_JOB_LEASE_SECONDS) -> tuple[Any | None, str]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    recoverable = _recoverable_predicate(now)
    terminalized = await session.execute(
        update(models.ProposalContinuation).where(
            models.ProposalContinuation.proposal_id == proposal_id,
            models.ProposalContinuation.attempt_count >= MAX_CONTINUATION_ATTEMPTS,
            recoverable,
        ).values(
            status="manual_review", lease_token="", lease_expires_at=None, available_at=None,
            error={"code": "proposal_continuation_attempts_exhausted",
                   "message": "Continuation attempt limit was exhausted after a worker stopped before completion.",
                   "requires_manual_review": True,
                   "retry_policy": {"attempt_count": MAX_CONTINUATION_ATTEMPTS,
                                    "max_attempts": MAX_CONTINUATION_ATTEMPTS, "exhausted": True}},
        ).execution_options(synchronize_session=False)
    )
    if terminalized.rowcount == 1:
        await session.commit()
        return None, ""
    token = uuid.uuid4().hex
    changed = await session.execute(
        update(models.ProposalContinuation).where(
            models.ProposalContinuation.proposal_id == proposal_id,
            models.ProposalContinuation.attempt_count < MAX_CONTINUATION_ATTEMPTS,
            recoverable,
        ).values(status="running", lease_token=token,
                 lease_expires_at=now + timedelta(seconds=float(lease_seconds)),
                 attempt_count=models.ProposalContinuation.attempt_count + 1)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        return None, ""
    await session.commit()
    return await session.get(models.ProposalContinuation, proposal_id, populate_existing=True), token


async def renew(session: AsyncSession, proposal_id: str, token: str, *, lease_seconds: float = CONTINUATION_JOB_LEASE_SECONDS) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    changed = await session.execute(
        update(models.ProposalContinuation).where(
            models.ProposalContinuation.proposal_id == proposal_id,
            models.ProposalContinuation.status == "running",
            models.ProposalContinuation.lease_token == token,
            models.ProposalContinuation.lease_expires_at >= now,
        ).values(lease_expires_at=now + timedelta(seconds=float(lease_seconds)))
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount == 1:
        await session.commit()
        return True
    await session.rollback()
    return False

async def finish(
    session: AsyncSession,
    proposal_id: str,
    token: str,
    *,
    result: Mapping[str, Any] | None = None,
    error: Mapping[str, Any] | None = None,
    retryable: bool = True,
) -> bool:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job = await session.get(models.ProposalContinuation, proposal_id, populate_existing=True)
    if (
        job is None
        or job.status != "running"
        or job.lease_token != token
        or job.lease_expires_at is None
        or job.lease_expires_at < now
    ):
        return False

    attempt_count = int(job.attempt_count or 0)
    retry_exhausted = bool(error and retryable and attempt_count >= MAX_CONTINUATION_ATTEMPTS)
    requires_manual_review = bool(error and (not retryable or retry_exhausted))
    stored_error = json_safe(dict(error)) if error else None
    if stored_error is not None and requires_manual_review:
        stored_error = {
            **stored_error,
            "requires_manual_review": True,
            "retry_policy": {
                "attempt_count": attempt_count,
                "max_attempts": MAX_CONTINUATION_ATTEMPTS,
                "exhausted": retry_exhausted,
            },
        }

    values = {
        "status": (
            "succeeded"
            if error is None
            else ("manual_review" if requires_manual_review else "failed")
        ),
        "lease_token": "",
        "lease_expires_at": None,
        "result": json_safe(dict(result or {})) if error is None else None,
        "error": stored_error,
        "available_at": (
            datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1)
            if error and not requires_manual_review
            else None
        ),
    }
    changed = await session.execute(
        update(models.ProposalContinuation)
        .where(
            models.ProposalContinuation.proposal_id == proposal_id,
            models.ProposalContinuation.status == "running",
            models.ProposalContinuation.lease_token == token,
            models.ProposalContinuation.lease_expires_at >= now,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return changed.rowcount == 1

def _recoverable_predicate(now: datetime):
    return or_(
        models.ProposalContinuation.status == "queued",
        (models.ProposalContinuation.status == "failed") & (models.ProposalContinuation.available_at <= now),
        (models.ProposalContinuation.status == "running") & (models.ProposalContinuation.lease_expires_at < now),
    )


async def terminalize_exhausted_jobs(session: AsyncSession, *, limit: int = 100) -> list[str]:
    """Move exhausted, recoverable jobs to manual_review without requiring claim-by-id.

    claim() can terminalize a single id, but the recovery scanner only drives
    recoverable_ids(). Jobs that already consumed MAX attempts and then crashed
    while running must still become observable terminal state.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    candidates = list(
        await session.scalars(
            select(models.ProposalContinuation.proposal_id)
            .where(
                _recoverable_predicate(now),
                models.ProposalContinuation.attempt_count >= MAX_CONTINUATION_ATTEMPTS,
                models.ProposalContinuation.status != "manual_review",
            )
            .limit(limit)
        )
    )
    if not candidates:
        return []
    changed = await session.execute(
        update(models.ProposalContinuation)
        .where(
            models.ProposalContinuation.proposal_id.in_(candidates),
            _recoverable_predicate(now),
            models.ProposalContinuation.attempt_count >= MAX_CONTINUATION_ATTEMPTS,
            models.ProposalContinuation.status != "manual_review",
        )
        .values(
            status="manual_review",
            lease_token="",
            lease_expires_at=None,
            available_at=None,
            error={
                "code": "proposal_continuation_attempts_exhausted",
                "message": "Continuation attempt limit was exhausted after a worker stopped before completion.",
                "requires_manual_review": True,
                "retry_policy": {
                    "attempt_count": MAX_CONTINUATION_ATTEMPTS,
                    "max_attempts": MAX_CONTINUATION_ATTEMPTS,
                    "exhausted": True,
                },
            },
        )
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount:
        await session.commit()
    else:
        await session.rollback()
    # Re-read which of the candidates are now terminal (idempotent under races).
    terminal = list(
        await session.scalars(
            select(models.ProposalContinuation.proposal_id).where(
                models.ProposalContinuation.proposal_id.in_(candidates),
                models.ProposalContinuation.status == "manual_review",
            )
        )
    )
    return terminal


async def recoverable_ids(session: AsyncSession, *, limit: int = 100) -> list[str]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = await session.scalars(
        select(models.ProposalContinuation.proposal_id)
        .where(
            _recoverable_predicate(now),
            models.ProposalContinuation.attempt_count < MAX_CONTINUATION_ATTEMPTS,
            models.ProposalContinuation.status != "manual_review",
        )
        .limit(limit)
    )
    return list(rows)
