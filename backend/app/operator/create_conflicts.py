from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import models
from app.operator.errors import OperatorError
from app.operator.guards import ActorContext, json_safe


async def reject_duplicate_job_create_conflict(
    session: AsyncSession,
    actor: ActorContext,
    data: Mapping[str, Any],
) -> None:
    """Reject duplicate official job creation before SQLite raises a raw error."""
    hash_key = str(data.get("hash_key") or "").strip()
    existing = None
    conflict_type = ""
    duplicate_fields = ["title", "company", "location", "url", "apply_url", "source"]
    if hash_key:
        existing = (
            await session.execute(
                select(models.Job)
                .where(models.Job.hash_key == hash_key)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            conflict_type = "hash_duplicate"
    if existing is None:
        title = _normalized_text(data.get("title"))
        company = _normalized_text(data.get("company"))
        location = _normalized_text(data.get("location"))
        if title and company and location:
            existing = (
                await session.execute(
                    select(models.Job)
                    .where(models.Job.owner_actor_id == actor.actor_id)
                    .where(models.Job.title == title)
                    .where(models.Job.company == company)
                    .where(models.Job.location == location)
                    .order_by(models.Job.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                conflict_type = "semantic_duplicate"
                duplicate_fields = ["title", "company", "location"]
    if existing is None:
        return

    details: dict[str, Any] = {
        "hash_key": hash_key,
        "conflict_type": conflict_type or "duplicate",
        "duplicate_fields": duplicate_fields,
    }
    if str(getattr(existing, "owner_actor_id", "") or "") == str(actor.actor_id or ""):
        details["existing_record"] = {
            "id": int(existing.id),
            "title": str(existing.title or ""),
            "company": str(existing.company or ""),
            "location": str(existing.location or ""),
            "source": str(existing.source or ""),
            "triage_status": str(existing.triage_status or ""),
            "pool_id": json_safe(existing.pool_id),
        }
    raise OperatorError(
        "conflict_error",
        "已存在同一岗位身份的记录。请复用已有岗位，或先调整岗位标题、公司、地点、链接、来源等会影响去重身份的字段后再创建。",
        details,
    )


def _normalized_text(value: Any) -> str:
    return str(value or "").strip()
