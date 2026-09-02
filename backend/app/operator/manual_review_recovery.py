from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from app.models import models


async def load_plan_recovery_overlay(db: Any, plan_id: str) -> dict[str, Any]:
    cases = list(
        (
            await db.scalars(
                select(models.ManualReviewCase)
                .where(
                    models.ManualReviewCase.plan_id == str(plan_id),
                    models.ManualReviewCase.status == "resolved",
                )
                .order_by(models.ManualReviewCase.created_at, models.ManualReviewCase.case_id)
            )
        ).all()
    )
    if not cases:
        return {}
    case_ids = [str(case.case_id) for case in cases]
    rows = list(
        (
            await db.scalars(
                select(models.ManualReviewResolution)
                .where(models.ManualReviewResolution.case_id.in_(case_ids))
                .order_by(
                    models.ManualReviewResolution.created_at,
                    models.ManualReviewResolution.case_id,
                    models.ManualReviewResolution.sequence,
                )
            )
        ).all()
    )
    latest: dict[str, models.ManualReviewResolution] = {}
    for row in rows:
        latest[str(row.case_id)] = row
    if not latest:
        return {}
    recoveries: list[dict[str, Any]] = []
    for case in cases:
        row = latest.get(str(case.case_id))
        if row is None:
            continue
        stored = row.result_json if isinstance(row.result_json, Mapping) else {}
        recovery = stored.get("recovery") if isinstance(stored.get("recovery"), Mapping) else {}
        item = {
            "case_id": str(case.case_id),
            "resolution_id": str(row.resolution_id),
            "resolution": str(row.resolution or ""),
            "plan_id": str(recovery.get("plan_id") or row.retry_plan_id or ""),
            "effective_status": str(recovery.get("effective_status") or ""),
        }
        recoveries.append(item)
    if not recoveries:
        return {}

    # An active recovery plan is the plan's real forward state; sibling cases aborted
    # only to unblock a joint retry must not mask it. Pure aborts stay authoritative.
    if all(item["resolution"] == "abort_plan" for item in recoveries):
        effective_status = "aborted"
    elif any(item["plan_id"] for item in recoveries):
        recovery_plans = [
            plan
            for plan in (
                [await db.get(models.ProposalPlan, item["plan_id"], populate_existing=True) for item in recoveries if item["plan_id"]]
            )
            if plan is not None
        ]
        statuses = {str(plan.status or "") for plan in recovery_plans}
        if statuses and statuses <= {"completed"}:
            effective_status = "recovered_completed"
        elif statuses & {"failed", "manual_review", "partially_completed", "compensated"}:
            effective_status = "recovery_failed"
        elif statuses & {"expired"}:
            effective_status = "recovery_expired"
        else:
            effective_status = "recovery_pending_authorization"
    elif any(item["resolution"] == "compensation_completed" for item in recoveries):
        effective_status = "compensated"
    elif all(item["resolution"] == "effect_present_accept" for item in recoveries):
        effective_status = "accepted_effect"
    else:
        effective_status = "resolved_manual_review"

    primary = recoveries[-1]
    return {
        "effective_status": effective_status,
        "resolution": str(primary["resolution"]),
        "resolution_id": str(primary["resolution_id"]),
        "case_id": str(primary["case_id"]),
        "plan_id": str(primary["plan_id"]),
        "cases": recoveries,
    }


async def apply_plan_recovery_overlay(db: Any, plan_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    overlay = await load_plan_recovery_overlay(db, str(plan_id))
    if overlay:
        result["effective_status"] = str(overlay["effective_status"])
        result["recovery"] = overlay
    return result
