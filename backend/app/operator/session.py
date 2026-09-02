from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import models
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
    SESSION_OPERATIONS,
    SESSION_UPDATE_FIELDS,
    canonical_version,
    fetch_scoped_record,
    get_model_class,
    get_model_spec,
    get_or_create_agent_session,
    json_safe,
    reject_trusted_args,
    session_snapshot,
    validate_session_context_scope,
)
from app.operator.registry import MODEL_REGISTRY, get_skill_spec


RESTORABLE_OPERATIONS = {"activate_skill", "deactivate_skill", "set_context", "clear_context", "set_skill_step"}
CONTEXT_FIELDS = (
    "current_job_id",
    "current_resume_id",
    "current_profile_section_id",
    "current_application_id",
)
FIELD_TO_MODEL = {
    "current_job_id": "job",
    "current_resume_id": "resume",
    "current_profile_section_id": "profile_section",
    "current_application_id": "application",
}
MODEL_TO_CONTEXT_FIELD = {
    "job": "current_job_id",
    "resume": "current_resume_id",
    "profile_section": "current_profile_section_id",
    "application": "current_application_id",
}
REFERENCE_FIELD_TO_CONTEXT_FIELD = {
    "application_id": "current_application_id",
    "application_ids": "current_application_id",
    "current_application_id": "current_application_id",
    "current_job_id": "current_job_id",
    "current_profile_section_id": "current_profile_section_id",
    "current_resume_id": "current_resume_id",
    "job_id": "current_job_id",
    "job_ids": "current_job_id",
    "job_ref_id": "current_job_id",
    "profile_section_id": "current_profile_section_id",
    "profile_section_ids": "current_profile_section_id",
    "related_job_id": "current_job_id",
    "resume_id": "current_resume_id",
    "resume_ids": "current_resume_id",
    "section_id": "current_profile_section_id",
    "section_ids": "current_profile_section_id",
}
CHECKPOINT_TTL_MINUTES = 30
PUBLIC_SESSION_FIELDS = (
    "active_skill",
    "current_step",
    "current_job_id",
    "current_resume_id",
    "current_profile_section_id",
    "current_application_id",
    "pending_proposal_ids",
    "checkpoint_id",
)


async def load_session_snapshot(session: AsyncSession, actor: ActorContext) -> dict[str, Any]:
    agent_session = await get_or_create_agent_session(session, actor)
    return _clean_snapshot(session_snapshot(agent_session))


async def update_session_state(
    session: AsyncSession,
    actor: ActorContext,
    operation: str,
    updates: Mapping[str, Any],
    reason: str = "",
    _defer_commit: bool = False,
) -> dict[str, Any]:
    try:
        if not isinstance(updates, Mapping):
            raise OperatorError("validation_error", "Session updates must be an object.", {"operation": operation})
        agent_session = await get_or_create_agent_session(session, actor)
        cleaned = _validate_updates(operation, updates)

        checkpoint_id = ""
        checkpoint_details: dict[str, Any] = {}
        if operation in RESTORABLE_OPERATIONS:
            checkpoint_id, checkpoint_details = await _create_checkpoint(
                session,
                actor,
                agent_session,
                reason=reason or operation,
            )

        if operation == "restore_checkpoint":
            await _restore_checkpoint(session, actor, agent_session, str(cleaned["checkpoint_id"]))
            if _defer_commit:
                await session.flush()
            else:
                await session.commit()
            await session.refresh(agent_session)
            return {
                "ok": True,
                "status": "completed",
                "operation": operation,
                "checkpoint_id": agent_session.checkpoint_id or "",
                "session_snapshot": _clean_snapshot(session_snapshot(agent_session)),
            }

        await validate_session_context_scope(session, actor, cleaned)
        _apply_operation(agent_session, operation, cleaned)
        if checkpoint_id:
            agent_session.checkpoint_id = checkpoint_id
        await _expire_incompatible_pending_proposals(session, actor, agent_session)
        agent_session.actor_id = actor.actor_id
        agent_session.adapter = actor.adapter
        if _defer_commit:
            await session.flush()
        else:
            await session.commit()
        await session.refresh(agent_session)
        return {
            "ok": True,
            "status": "completed",
            "operation": operation,
            "checkpoint_id": checkpoint_id,
            "checkpoint": checkpoint_details,
            "session_snapshot": _clean_snapshot(session_snapshot(agent_session)),
        }
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive adapter boundary.
        await _rollback_quietly(session)
        return transient_error("Operator session update failed transiently.", {"error": str(exc)})


async def create_pre_confirmation_checkpoint(
    session: AsyncSession,
    actor: ActorContext,
    *,
    proposal_id: str,
    reason: str = "proposal confirmation",
) -> str:
    agent_session = await get_or_create_agent_session(session, actor)
    checkpoint_id, _details = await _create_checkpoint(
        session,
        actor,
        agent_session,
        reason=reason,
        state_extra={"proposal_id": proposal_id, "kind": "pre_confirmation_checkpoint"},
    )
    agent_session.checkpoint_id = checkpoint_id
    agent_session.actor_id = actor.actor_id
    agent_session.adapter = actor.adapter
    await session.flush()
    return checkpoint_id


#: Fields each session operation may actually process. A field listed for one
#: operation but ignored by another is a contract violation: runtime must
#: reject it, and the exported operation schema must match this allowlist.
OPERATION_UPDATE_FIELDS = {
    "activate_skill": frozenset({"active_skill", "skill", "name", "current_step"}),
    "deactivate_skill": frozenset(),
    "set_context": frozenset(CONTEXT_FIELDS),
    "clear_context": frozenset(),
    "set_skill_step": frozenset({"current_step"}),
    "restore_checkpoint": frozenset({"checkpoint_id"}),
}
BACKEND_OWNED_SESSION_FIELDS = frozenset({"pending_proposal_ids", "checkpoint_id"})


def _validate_updates(operation: str, updates: Mapping[str, Any]) -> dict[str, Any]:
    if operation not in SESSION_OPERATIONS:
        raise OperatorError("validation_error", "Unsupported session operation.", {"operation": operation})
    allow_top_level_names = {"checkpoint_id"} if operation == "restore_checkpoint" else set()
    reject_trusted_args(
        updates,
        location=f"manage_session:{operation}.updates",
        allow_top_level_names=allow_top_level_names,
    )
    normalized_updates = _normalize_activate_skill_aliases(operation, updates)
    rejected = sorted(set(normalized_updates) - SESSION_UPDATE_FIELDS)
    if rejected:
        raise OperatorError("validation_error", "Session update fields are not allowed.", {"fields": rejected})
    allowed_operation_fields = OPERATION_UPDATE_FIELDS.get(operation)
    if allowed_operation_fields is not None:
        outside_operation = sorted(set(normalized_updates) - allowed_operation_fields)
        if outside_operation:
            raise OperatorError(
                "validation_error",
                "Session update fields are not handled by this operation.",
                {"operation": operation, "fields": outside_operation},
            )
    backend_owned = BACKEND_OWNED_SESSION_FIELDS - {"checkpoint_id"} if operation == "restore_checkpoint" else BACKEND_OWNED_SESSION_FIELDS
    forbidden = sorted(backend_owned & set(normalized_updates))
    if forbidden:
        raise OperatorError(
            "validation_error",
            "Session proposal and checkpoint fields are backend-owned for this operation.",
            {"operation": operation, "fields": forbidden},
        )
    cleaned = {str(key): json_safe(value) for key, value in normalized_updates.items()}
    # clear_context/deactivate_skill accept an empty payload; other operations
    # must not silently accept nothing.
    if operation == "activate_skill" and not cleaned.get("active_skill"):
        raise OperatorError("validation_error", "activate_skill requires active_skill.", {})
    if operation == "activate_skill":
        try:
            get_skill_spec(str(cleaned.get("active_skill") or ""))
        except ValueError as exc:
            raise OperatorError(
                "validation_error",
                "activate_skill requires a registered operator Skill.",
                {"active_skill": cleaned.get("active_skill")},
            ) from exc
    if operation == "set_context" and not cleaned:
        raise OperatorError("validation_error", "set_context requires at least one context coordinate.", {})
    if operation == "set_skill_step" and "current_step" not in cleaned:
        raise OperatorError("validation_error", "set_skill_step requires current_step.", {})
    if operation == "restore_checkpoint" and not cleaned.get("checkpoint_id"):
        raise OperatorError("validation_error", "restore_checkpoint requires checkpoint_id.", {})
    return cleaned


def _normalize_activate_skill_aliases(operation: str, updates: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(updates)
    if operation != "activate_skill":
        return normalized
    active_value = normalized.get("active_skill")
    for alias in ("skill", "name"):
        if alias not in normalized:
            continue
        alias_value = normalized.pop(alias)
        if active_value not in (None, "") and alias_value not in (None, "") and str(alias_value) != str(active_value):
            raise OperatorError(
                "validation_error",
                "activate_skill received conflicting skill aliases.",
                {"active_skill": active_value, alias: alias_value},
            )
        if active_value in (None, ""):
            active_value = alias_value
    if active_value not in (None, ""):
        normalized["active_skill"] = active_value
    return normalized


async def _create_checkpoint(
    session: AsyncSession,
    actor: ActorContext,
    agent_session: Any,
    *,
    reason: str,
    state_extra: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    checkpoint_id = f"ckpt_{uuid.uuid4().hex}"
    coordinates, reference_versions, omitted_references = await _checkpoint_restorable_state(session, actor, agent_session)
    checkpoint = models.AgentCheckpoint(
        checkpoint_id=checkpoint_id,
        session_id=actor.session_id,
        actor_id=actor.actor_id,
        active_skill=str(coordinates.get("active_skill") or ""),
        current_step=str(coordinates.get("current_step") or ""),
        current_job_id=coordinates.get("current_job_id"),
        current_resume_id=coordinates.get("current_resume_id"),
        current_profile_section_id=coordinates.get("current_profile_section_id"),
        current_application_id=coordinates.get("current_application_id"),
        pending_proposal_ids=list(agent_session.pending_proposal_ids or []),
        state_blob={
            "kind": "session_checkpoint",
            "coordinates": coordinates,
            "reference_versions": reference_versions,
            "omitted_references": omitted_references,
            **json_safe(state_extra or {}),
        },
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=CHECKPOINT_TTL_MINUTES),
        reason=reason,
    )
    session.add(checkpoint)
    await session.flush()
    return checkpoint_id, {
        "checkpoint_id": checkpoint_id,
        "status": "created",
        "omitted_references": omitted_references,
    }


def _checkpoint_coordinates(agent_session: Any) -> dict[str, Any]:
    return {
        "active_skill": agent_session.active_skill or "",
        "current_step": agent_session.current_step or "",
        "current_job_id": agent_session.current_job_id,
        "current_resume_id": agent_session.current_resume_id,
        "current_profile_section_id": agent_session.current_profile_section_id,
        "current_application_id": agent_session.current_application_id,
    }


def _apply_operation(agent_session: Any, operation: str, updates: Mapping[str, Any]) -> None:
    if operation == "activate_skill":
        agent_session.active_skill = str(updates.get("active_skill") or "")
        if "current_step" in updates:
            agent_session.current_step = str(updates.get("current_step") or "")
    elif operation == "deactivate_skill":
        agent_session.active_skill = ""
        agent_session.current_step = ""
    elif operation == "clear_context":
        for field in CONTEXT_FIELDS:
            setattr(agent_session, field, None)
    elif operation == "set_context":
        for field in CONTEXT_FIELDS:
            if field in updates:
                setattr(agent_session, field, _none_if_blank(updates[field]))
    elif operation == "set_skill_step":
        agent_session.current_step = str(updates.get("current_step") or "")
    else:
        for field, value in updates.items():
            if field in SESSION_UPDATE_FIELDS and field not in {"pending_proposal_ids", "checkpoint_id"}:
                setattr(agent_session, field, _none_if_blank(value))


async def _restore_checkpoint(
    session: AsyncSession,
    actor: ActorContext,
    agent_session: Any,
    checkpoint_id: str,
) -> None:
    checkpoint = await session.get(models.AgentCheckpoint, checkpoint_id)
    if checkpoint is None:
        raise OperatorError("not_found_error", "Checkpoint was not found.", {"checkpoint_id": checkpoint_id})
    if checkpoint.actor_id != actor.actor_id or checkpoint.session_id != actor.session_id:
        raise OperatorError("permission_error", "Checkpoint is outside the current actor/session scope.", {})
    if checkpoint.expires_at is not None and checkpoint.expires_at <= datetime.utcnow():
        raise OperatorError("conflict_error", "Checkpoint has expired.", {"checkpoint_id": checkpoint_id})
    await _validate_checkpoint_references(session, actor, checkpoint)
    await _expire_actor_session_pending_proposals(
        session,
        actor,
        reason="Expired because checkpoint restore clears pending proposals.",
    )
    agent_session.active_skill = checkpoint.active_skill or ""
    agent_session.current_step = checkpoint.current_step or ""
    agent_session.current_job_id = checkpoint.current_job_id
    agent_session.current_resume_id = checkpoint.current_resume_id
    agent_session.current_profile_section_id = checkpoint.current_profile_section_id
    agent_session.current_application_id = checkpoint.current_application_id
    from app.operator.guards import replace_pending_proposal_ids

    await replace_pending_proposal_ids(session, actor, [])
    agent_session.checkpoint_id = checkpoint.checkpoint_id
    agent_session.actor_id = actor.actor_id
    agent_session.adapter = actor.adapter


_CONFIRMABLE_PENDING_STATUSES = frozenset({"pending", "awaiting_next_confirmation"})


async def _expire_actor_session_pending_proposals(
    session: AsyncSession,
    actor: ActorContext,
    *,
    reason: str,
) -> None:
    """Expire every confirmable non-terminal proposal for this session (clear-all paths).

    Includes awaiting_next_confirmation so multi-step confirms are not left stranded
    after checkpoint restore empties the sole-authority pending list.
    """
    rows = await session.execute(
        select(models.ProposalCache).where(
            models.ProposalCache.actor_id == actor.actor_id,
            models.ProposalCache.session_id == actor.session_id,
            models.ProposalCache.status.in_(sorted(_CONFIRMABLE_PENDING_STATUSES)),
        )
    )
    for proposal in rows.scalars():
        await try_expire_confirmable_proposal(session, proposal, actor, reason=reason)


def _proposal_event(status: str, actor: ActorContext, *, reason: str) -> dict[str, Any]:
    return {
        "event_id": f"evt_{uuid.uuid4().hex}",
        "status": status,
        "actor_id": actor.actor_id,
        "session_id": actor.session_id,
        "created_at": datetime.utcnow().isoformat(),
        "result": {"status": status, "reason": reason},
    }


async def _validate_checkpoint_references(session: AsyncSession, actor: ActorContext, checkpoint: Any) -> None:
    expected_versions = {}
    state_blob = checkpoint.state_blob or {}
    if isinstance(state_blob, Mapping) and isinstance(state_blob.get("reference_versions"), Mapping):
        expected_versions = {str(key): str(value) for key, value in state_blob["reference_versions"].items()}
    for field, model_name in FIELD_TO_MODEL.items():
        record_id = getattr(checkpoint, field, None)
        if record_id in (None, ""):
            continue
        spec = get_model_spec(model_name)
        model_cls = get_model_class(model_name)
        try:
            record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
        except OperatorError as exc:
            if exc.code != "not_found_error":
                raise
            raise OperatorError(
                "conflict_error",
                "Checkpoint referenced records no longer exist.",
                {"checkpoint_id": checkpoint.checkpoint_id, "model": model_name, "record_id": record_id},
            ) from exc
        await session.refresh(record)
        key = _reference_key(model_name, record_id)
        expected = expected_versions.get(key)
        if expected and canonical_version(record, spec) != expected:
            raise OperatorError(
                "conflict_error",
                "Checkpoint referenced records changed after checkpoint creation.",
                {"checkpoint_id": checkpoint.checkpoint_id, "model": model_name, "record_id": record_id},
            )


async def _checkpoint_reference_versions(
    session: AsyncSession,
    actor: ActorContext,
    coordinates: Mapping[str, Any],
) -> dict[str, str]:
    versions: dict[str, str] = {}
    for field, model_name in FIELD_TO_MODEL.items():
        record_id = coordinates.get(field)
        if record_id in (None, ""):
            continue
        spec = get_model_spec(model_name)
        model_cls = get_model_class(model_name)
        record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
        await session.refresh(record)
        versions[_reference_key(model_name, record_id)] = canonical_version(record, spec)
    return versions


async def _checkpoint_restorable_state(
    session: AsyncSession,
    actor: ActorContext,
    agent_session: Any,
) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]]]:
    coordinates = _checkpoint_coordinates(agent_session)
    versions: dict[str, str] = {}
    omitted: list[dict[str, Any]] = []
    for field, model_name in FIELD_TO_MODEL.items():
        record_id = coordinates.get(field)
        if record_id in (None, ""):
            continue
        spec = get_model_spec(model_name)
        model_cls = get_model_class(model_name)
        try:
            record = await fetch_scoped_record(session, actor, spec, model_cls, record_id)
            await session.refresh(record)
        except OperatorError as exc:
            coordinates[field] = None
            omitted.append({"field": field, "model": model_name, "record_id": record_id, "reason": exc.code})
            continue
        versions[_reference_key(model_name, record_id)] = canonical_version(record, spec)
    return coordinates, versions, omitted


def _reference_key(model_name: str, record_id: Any) -> str:
    return f"{model_name}:{record_id}"


async def _expire_incompatible_pending_proposals(
    session: AsyncSession,
    actor: ActorContext,
    agent_session: Any,
) -> None:
    """Remove only TTL-elapsed, hard-conflicting, or non-confirmable list members.

    Confirmable statuses are pending and awaiting_next_confirmation (multi-step
    confirm mid-state). Compatible confirmables stay on the sole-authority list.

    Uses relative CAS removals (not absolute replace of a scanned kept list) so a
    concurrent shape_proposal add cannot be wiped by a stale expire scan.

    Terminal expire writes use conditional UPDATE (status still confirmable) so a
    concurrent confirm that already transitioned the row cannot be overwritten by
    a stale ORM snapshot flush.
    """
    remove_ids: list[str] = []
    for proposal_id in list(agent_session.pending_proposal_ids or []):
        proposal = await session.get(models.ProposalCache, proposal_id)
        if proposal is None or proposal.actor_id != actor.actor_id or proposal.session_id != actor.session_id:
            remove_ids.append(str(proposal_id))
            continue
        if str(proposal.status or "") not in _CONFIRMABLE_PENDING_STATUSES:
            # Terminal / non-confirmable membership is cleaned without re-expiring.
            remove_ids.append(str(proposal_id))
            continue
        if _proposal_expired(proposal):
            expired = await try_expire_confirmable_proposal(
                session,
                proposal,
                actor,
                reason="Expired because proposal TTL elapsed.",
            )
            if expired or str(getattr(proposal, "status", "") or "") not in _CONFIRMABLE_PENDING_STATUSES:
                remove_ids.append(str(proposal_id))
            continue
        if not _proposal_compatible_with_session(proposal, agent_session):
            expired = await try_expire_confirmable_proposal(
                session,
                proposal,
                actor,
                reason="Expired because session context changed.",
            )
            if expired or str(getattr(proposal, "status", "") or "") not in _CONFIRMABLE_PENDING_STATUSES:
                remove_ids.append(str(proposal_id))
            continue
        # keep confirmable compatible proposal (pending or awaiting_next_confirmation)
    if not remove_ids:
        return
    from app.operator.guards import remove_pending_proposal_ids

    await remove_pending_proposal_ids(session, actor, remove_ids)


async def try_expire_confirmable_proposal(
    session: AsyncSession,
    proposal: Any,
    actor: ActorContext,
    *,
    reason: str,
) -> bool:
    """CAS-expire a confirmable proposal. Returns True only if this writer won.

    Never assigns status/events on a loaded ORM object and flushes unconditionally:
    that races with confirm's conditional claim and can overwrite confirmed rows.
    """
    proposal_id = str(getattr(proposal, "proposal_id", "") or "")
    if not proposal_id:
        return False
    current_status = str(getattr(proposal, "status", "") or "")
    if current_status not in _CONFIRMABLE_PENDING_STATUSES:
        return False
    expire_event = _proposal_event("expired", actor, reason=reason)
    prior_events = [
        json_safe(event)
        for event in (getattr(proposal, "confirmation_events", None) or [])
        if isinstance(event, Mapping)
    ]
    next_events = [*prior_events, expire_event]
    expected_count = int(getattr(proposal, "confirmation_count", 0) or 0)
    result = await session.execute(
        update(models.ProposalCache)
        .where(
            models.ProposalCache.proposal_id == proposal_id,
            models.ProposalCache.actor_id == actor.actor_id,
            models.ProposalCache.session_id == actor.session_id,
            models.ProposalCache.status.in_(sorted(_CONFIRMABLE_PENDING_STATUSES)),
            models.ProposalCache.confirmation_count == expected_count,
        )
        .values(
            status="expired",
            reason=reason or getattr(proposal, "reason", None) or "Proposal expired.",
            confirmation_events=next_events,
        )
        .execution_options(synchronize_session=False)
    )
    if int(result.rowcount or 0) != 1:
        await session.refresh(proposal)
        return False
    proposal.status = "expired"
    proposal.reason = reason or proposal.reason or "Proposal expired."
    proposal.confirmation_events = next_events
    flag_modified(proposal, "confirmation_events")
    return True


def _mark_proposal_expired(proposal: Any, actor: ActorContext, *, reason: str) -> None:
    """Legacy in-memory marker for callers without a bound session.

    Production expire paths must use :func:`try_expire_confirmable_proposal`.
    """
    proposal.status = "expired"
    # Prefer the concrete expire cause over the original creation reason so
    # confirm/list UIs can explain why a previously pending card became expired.
    proposal.reason = reason or proposal.reason or "Proposal expired."
    events = [json_safe(event) for event in (proposal.confirmation_events or []) if isinstance(event, Mapping)]
    events.append(_proposal_event("expired", actor, reason=reason))
    proposal.confirmation_events = events


def _proposal_expired(proposal: Any) -> bool:
    return proposal.expires_at is not None and proposal.expires_at <= datetime.utcnow()


def _proposal_compatible_with_session(proposal: Any, agent_session: Any) -> bool:
    """Return False only on hard session-focus conflicts or missing binding.

    Pending proposals must remain confirmable across same-turn manage_session
    skill/context updates unless the focused session coordinate now points at a
    different record than the proposal is bound to.

    Unfocused context (None/"") is *not* a conflict: create_record proposals and
    patches emitted before the session focuses a record are still valid until TTL
    elapses or an explicit reject/expire/confirm happens. Live eval observed
    create_resume cards still pending in the final response while immediate
    confirm saw status=expired after activate_skill — caused by treating
    "no targets / empty focus" as incompatible.
    """
    context_refs = _proposal_context_refs(proposal)
    if context_refs:
        return all(
            _session_context_compatible(agent_session, context_field, expected_id)
            for context_field, expected_id in context_refs
        )

    # No durable context binding (typical create_record / unbound write): keep.
    return True


def _session_context_compatible(agent_session: Any, context_field: str, expected_id: Any) -> bool:
    """Compatible unless the session actively focuses a conflicting id."""
    current_id = getattr(agent_session, context_field, None)
    if current_id in (None, ""):
        return True
    return _session_context_matches(agent_session, context_field, expected_id)


def _session_context_matches(agent_session: Any, context_field: str, expected_id: Any) -> bool:
    current_id = getattr(agent_session, context_field, None)
    if current_id in (None, ""):
        return False
    if isinstance(expected_id, Sequence) and not isinstance(expected_id, (str, bytes, bytearray)):
        expected_values = [item for item in expected_id if item not in (None, "")]
        return bool(expected_values) and any(str(current_id) == str(item) for item in expected_values)
    return expected_id not in (None, "") and str(current_id) == str(expected_id)


def _proposal_context_refs(proposal: Any) -> list[tuple[str, Any]]:
    refs: list[tuple[str, Any]] = []
    payload = proposal.locked_payload if isinstance(proposal.locked_payload, Mapping) else {}
    model_name = str(payload.get("model") or proposal.model_or_action or "")

    for item in proposal.affected_records or []:
        if not isinstance(item, Mapping):
            continue
        affected_model = str(item.get("model") or "")
        record_id = item.get("id")
        if affected_model and record_id not in (None, ""):
            context_field = MODEL_TO_CONTEXT_FIELD.get(affected_model)
            if context_field:
                refs.append((context_field, record_id))

    record_id = payload.get("record_id") if payload.get("record_id") not in (None, "") else proposal.record_id
    if model_name and record_id not in (None, ""):
        context_field = MODEL_TO_CONTEXT_FIELD.get(model_name)
        if context_field:
            refs.append((context_field, record_id))

    for value in (payload, proposal.before, proposal.after):
        refs.extend(_extract_context_refs(value))

    if model_name in MODEL_REGISTRY:
        spec = MODEL_REGISTRY[model_name]
        data = payload.get("data")
        if isinstance(data, Mapping):
            for field_name, related_model in spec.relations.items():
                context_field = MODEL_TO_CONTEXT_FIELD.get(str(related_model))
                related_id = data.get(field_name)
                if context_field and related_id not in (None, ""):
                    refs.append((context_field, related_id))

    return _dedupe_context_refs(refs)


def _extract_context_refs(value: Any) -> list[tuple[str, Any]]:
    refs: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            context_field = REFERENCE_FIELD_TO_CONTEXT_FIELD.get(_normalize_reference_key(str(key)))
            if context_field and child not in (None, ""):
                refs.append((context_field, child))
            refs.extend(_extract_context_refs(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            refs.extend(_extract_context_refs(child))
    return refs


def _dedupe_context_refs(refs: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    deduped: list[tuple[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for context_field, expected_id in refs:
        marker = (context_field, str(json_safe(expected_id)))
        if expected_id in (None, "") or marker in seen:
            continue
        seen.add(marker)
        deduped.append((context_field, expected_id))
    return deduped


def _normalize_reference_key(key: str) -> str:
    chars: list[str] = []
    previous = ""
    for char in key:
        if char.isupper() and previous and (previous.islower() or previous.isdigit()):
            chars.append("_")
        chars.append(char.lower() if char.isalnum() else "_")
        previous = char
    return "_".join(part for part in "".join(chars).split("_") if part)


def _clean_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = {field: json_safe(snapshot.get(field)) for field in PUBLIC_SESSION_FIELDS}
    for field in CONTEXT_FIELDS:
        if cleaned.get(field) == "":
            cleaned[field] = None
    cleaned["active_skill"] = cleaned.get("active_skill") or None
    cleaned["current_step"] = cleaned.get("current_step") or None
    cleaned["checkpoint_id"] = cleaned.get("checkpoint_id") or None
    cleaned["pending_proposal_ids"] = list(cleaned.get("pending_proposal_ids") or [])
    return cleaned


def _none_if_blank(value: Any) -> Any:
    return None if value == "" else value


def _operator_error_response(exc: OperatorError) -> dict[str, Any]:
    if exc.code == "validation_error":
        return validation_error(exc.message, exc.details)
    if exc.code == "permission_error":
        return permission_error(exc.message, exc.details)
    if exc.code == "not_found_error":
        return not_found_error(exc.message, exc.details)
    if exc.code == "conflict_error":
        return conflict_error(exc.message, exc.details)
    return validation_error(exc.message, exc.details)


async def _rollback_quietly(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        pass
