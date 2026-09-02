from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm.attributes import flag_modified

from app.models import models
from app.operator.guards import ActorContext, get_or_create_agent_session, json_safe
from app.operator.readiness import (
    job_read_evidence_ready,
    profile_read_evidence_ready,
    readiness_recovery_payload,
    resolve_readiness_missing_requirements,
)
from app.operator.registry import get_skill_spec


RESUME_GENERATE_SKILL_NAME = "resume-optimizer"
RESUME_SKILL_NAMES = {RESUME_GENERATE_SKILL_NAME, "resume-experience-mining"}
RESUME_READY_STEP_MARKERS = {
    "strategy_confirmed",
    "readiness_confirmed",
    "ready_to_generate_resume",
    "generate_resume_ready",
}
RESUME_GENERATE_READY_STATUS = "active"


@dataclass(frozen=True)
class SkillInstanceState:
    skill_name: str = ""
    current_step: str = ""
    status: str = "inactive"
    readiness_gates: dict[str, bool] = field(default_factory=dict)
    source: str = "harness_skill_runtime"
    metadata: dict[str, Any] = field(default_factory=dict)
    skill_instance_id: str = ""
    parent_task_id: str = ""
    parent_step_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_instance_id": self.skill_instance_id,
            "skill_name": self.skill_name,
            "current_step": self.current_step,
            "status": self.status,
            "readiness_gates": dict(self.readiness_gates),
            "source": self.source,
            "metadata": json_safe(dict(self.metadata)),
            "parent_task_id": self.parent_task_id,
            "parent_step_id": self.parent_step_id,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "SkillInstanceState":
        if not isinstance(value, Mapping):
            return cls()
        gates_raw = value.get("readiness_gates")
        gates = {
            str(key): bool(item)
            for key, item in (gates_raw.items() if isinstance(gates_raw, Mapping) else [])
        }
        metadata = value.get("metadata")
        return cls(
            skill_instance_id=str(value.get("skill_instance_id") or ""),
            skill_name=str(value.get("skill_name") or ""),
            current_step=str(value.get("current_step") or ""),
            status=str(value.get("status") or "inactive"),
            readiness_gates=gates,
            source=str(value.get("source") or "harness_skill_runtime"),
            metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            parent_task_id=str(value.get("parent_task_id") or ""),
            parent_step_id=str(value.get("parent_step_id") or ""),
        )


async def load_skill_state(session: Any, actor: ActorContext | None) -> SkillInstanceState:
    if session is None or actor is None:
        return SkillInstanceState(source="missing_runtime_context")

    existing = await _load_harness_skill_mapping(session, actor)
    if existing is not None:
        return SkillInstanceState.from_mapping(existing)

    readback = await _readback_agent_session_marker(session, actor)
    if readback is not None:
        await _persist_skill_state(session, actor, readback)
        return readback

    return SkillInstanceState()


async def load_harness_skill_state(session: Any, actor: ActorContext | None) -> SkillInstanceState:
    if session is None or actor is None:
        return SkillInstanceState(source="missing_runtime_context")

    existing = await _load_harness_skill_mapping(session, actor)
    if existing is not None:
        return SkillInstanceState.from_mapping(existing)
    return SkillInstanceState()


async def set_active_skill_state(
    session: Any,
    actor: ActorContext,
    *,
    skill_name: str,
    skill_step: str = "",
    status: str = "active",
    readiness_gates: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    source: str = "harness_skill_runtime",
    parent_task_id: str = "",
    parent_step_id: str = "",
    sync_agent_session: bool = True,
) -> SkillInstanceState:
    normalized_skill = str(skill_name or "").strip()
    normalized_step = str(skill_step or "").strip()
    if normalized_skill:
        get_skill_spec(normalized_skill)

    existing = await _load_harness_skill_mapping(session, actor)
    existing_state = SkillInstanceState.from_mapping(existing)
    instance_id = existing_state.skill_instance_id
    if not instance_id or existing_state.skill_name != normalized_skill:
        instance_id = f"skill_{uuid.uuid4().hex}"

    gates = _normalize_readiness_gates(readiness_gates)
    if not gates:
        gates = _inferred_readiness_gates(normalized_skill, normalized_step)

    state = SkillInstanceState(
        skill_instance_id=instance_id,
        skill_name=normalized_skill,
        current_step=normalized_step,
        status=str(status or ("active" if normalized_skill else "inactive")),
        readiness_gates=gates,
        source=str(source or "harness_skill_runtime"),
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
        parent_task_id=str(parent_task_id or ""),
        parent_step_id=str(parent_step_id or ""),
    )
    # No-change guard: only explicit transitions may advance the durable skill
    # authority. Re-persisting an identical state (e.g. turn-end persist after
    # a readback that already matched) is a write storm, and a persist call
    # whose source of truth was lost must never clear durable state.
    if _state_mapping_equivalent(existing, state):
        return existing_state
    await _persist_skill_state(session, actor, state)
    if sync_agent_session:
        await _sync_agent_session_marker(session, actor, state)
    return state


def _state_mapping_equivalent(existing: Mapping[str, Any] | None, state: SkillInstanceState) -> bool:
    if existing is None:
        return False
    current = SkillInstanceState.from_mapping(existing)
    return current.to_dict() == state.to_dict()


async def verify_resume_generate_readiness(
    session: Any,
    actor: ActorContext | None,
    *,
    allow_transitional_readback: bool = False,
) -> dict[str, Any]:
    if session is None or actor is None:
        return {
            "ok": False,
            "message": (
                "generate_resume 需要 Harness Skill Runtime readiness；"
                "没有数据库 session/actor，不能读取 AgentSession.state_json。"
            ),
            "recovery": _readiness_recovery_for(None, None, False),
        }

    state = (
        await load_skill_state(session, actor)
        if allow_transitional_readback
        else await load_harness_skill_state(session, actor)
    )
    recovery = _readiness_recovery_for_state(state)
    if not state.skill_name:
        return {
            "ok": False,
            "message": (
                "未找到 Harness Skill Runtime readiness，"
                "不能校验 active resume Skill。"
            ),
            "state": state.to_dict(),
            "recovery": recovery,
        }
    if state.source == "agent_session_readback" and not allow_transitional_readback:
        return {
            "ok": False,
            "message": (
                "generate_resume readiness 来自 transitional AgentSession readback；"
                "主路径必须使用 Harness Skill Runtime 写入的 readiness。"
            ),
            "state": state.to_dict(),
            "active_skill": state.skill_name,
            "current_step": state.current_step,
            "recovery": recovery,
        }
    if state.skill_name != RESUME_GENERATE_SKILL_NAME:
        return {
            "ok": False,
            "message": (
                "generate_resume readiness 要求 active resume-optimizer Skill，"
                f"但当前 skill_name={state.skill_name or '<empty>'}。"
            ),
            "state": state.to_dict(),
            "active_skill": state.skill_name,
            "current_step": state.current_step,
            "recovery": recovery,
        }
    if str(state.status or "").strip().lower() != RESUME_GENERATE_READY_STATUS:
        return {
            "ok": False,
            "message": (
                "generate_resume readiness 尚未通过 Harness Skill Runtime 复核："
                f"skill_name={state.skill_name}, current_step={state.current_step or '<empty>'}, "
                f"status={state.status or '<empty>'}，需要 status=active 且 strategy_confirmed。"
            ),
            "state": state.to_dict(),
            "active_skill": state.skill_name,
            "current_step": state.current_step,
            "recovery": recovery,
        }
    if state.readiness_gates.get("strategy_confirmed") is not True:
        return {
            "ok": False,
            "message": (
                "generate_resume readiness 尚未通过 Harness Skill Runtime 复核："
                f"skill_name={state.skill_name}, current_step={state.current_step or '<empty>'}, "
                "readiness_gates.strategy_confirmed 必须显式为 true。"
            ),
            "state": state.to_dict(),
            "active_skill": state.skill_name,
            "current_step": state.current_step,
            "recovery": recovery,
        }
    if not profile_read_evidence_ready(state.metadata.get("profile_read_evidence")):
        return {
            "ok": False,
            "message": (
                "generate_resume readiness 缺少可信 profile_read_evidence；"
                "profile_facts_loaded 不能由 readiness_gates 自证，必须记录已通过 "
                "Operator 工具轨迹读取 profile/profile_section。"
            ),
            "state": state.to_dict(),
            "active_skill": state.skill_name,
            "current_step": state.current_step,
            "recovery": recovery,
        }
    if not job_read_evidence_ready(state.metadata.get("job_read_evidence")):
        return {
            "ok": False,
            "message": (
                "generate_resume readiness 缺少可信 job_read_evidence；"
                "job_context_loaded 不能由 readiness_gates 自证，必须记录已通过 "
                "Operator 工具轨迹读取目标岗位/JD。"
            ),
            "state": state.to_dict(),
            "active_skill": state.skill_name,
            "current_step": state.current_step,
            "recovery": recovery,
        }
    return {
        "ok": True,
        "message": (
            "已复核 Harness Skill Runtime readiness："
            f"skill_name={state.skill_name}, current_step={state.current_step}。"
        ),
        "state": state.to_dict(),
        "active_skill": state.skill_name,
        "current_step": state.current_step,
        "source": state.source,
    }


def _readiness_recovery_for_state(state: SkillInstanceState) -> dict[str, Any]:
    return _readiness_recovery_for(
        state.metadata.get("profile_read_evidence"),
        state.metadata.get("job_read_evidence"),
        state.readiness_gates.get("strategy_confirmed") is True,
    )


def _readiness_recovery_for(profile_evidence: Any, job_evidence: Any, strategy_confirmed: bool) -> dict[str, Any]:
    return readiness_recovery_payload(
        resolve_readiness_missing_requirements(
            profile_evidence=profile_evidence,
            job_evidence=job_evidence,
            strategy_confirmed=strategy_confirmed,
        )
    )


async def is_resume_skill_ready_for_generate(session: Any, actor: ActorContext | None) -> bool:
    readiness = await verify_resume_generate_readiness(session, actor)
    return bool(readiness.get("ok"))


async def _load_harness_skill_mapping(session: Any, actor: ActorContext) -> Mapping[str, Any] | None:
    agent_session = await session.get(models.AgentSession, actor.session_id)
    if agent_session is None or str(getattr(agent_session, "actor_id", "") or "") != actor.actor_id:
        return None
    return _active_skill_from_harness_state(getattr(agent_session, "state_json", None))


async def _persist_skill_state(session: Any, actor: ActorContext, state: SkillInstanceState) -> None:
    agent_session = await get_or_create_agent_session(session, actor)
    root = dict(getattr(agent_session, "state_json", None) or {})
    runtime = dict(root.get("skill_runtime") or {})
    instances = dict(runtime.get("instances") or {})
    state_dict = state.to_dict()
    if state.skill_instance_id:
        instances[state.skill_instance_id] = state_dict
    runtime["active_skill"] = state_dict
    runtime["instances"] = instances
    root["skill_runtime"] = runtime
    agent_session.state_json = json_safe(root)
    flag_modified(agent_session, "state_json")
    await session.flush()


async def _sync_agent_session_marker(session: Any, actor: ActorContext, state: SkillInstanceState) -> None:
    agent_session = await get_or_create_agent_session(session, actor)
    agent_session.active_skill = state.skill_name
    agent_session.current_step = state.current_step
    agent_session.actor_id = actor.actor_id
    agent_session.adapter = actor.adapter
    await session.flush()


async def _readback_agent_session_marker(session: Any, actor: ActorContext) -> SkillInstanceState | None:
    agent_session = await session.get(models.AgentSession, actor.session_id)
    if agent_session is None or str(getattr(agent_session, "actor_id", "") or "") != actor.actor_id:
        return None
    skill_name = str(getattr(agent_session, "active_skill", "") or "").strip()
    current_step = str(getattr(agent_session, "current_step", "") or "").strip()
    if not skill_name and not current_step:
        return None
    return SkillInstanceState(
        skill_instance_id=f"skill_{uuid.uuid4().hex}",
        skill_name=skill_name,
        current_step=current_step,
        status="active" if skill_name else "inactive",
        readiness_gates=_inferred_readiness_gates(skill_name, current_step),
        source="agent_session_readback",
    )


def _active_skill_from_harness_state(state_json: Any) -> Mapping[str, Any] | None:
    if not isinstance(state_json, Mapping):
        return None
    runtime = state_json.get("skill_runtime")
    if not isinstance(runtime, Mapping):
        return None
    active = runtime.get("active_skill")
    return active if isinstance(active, Mapping) else None


def _normalize_readiness_gates(readiness_gates: Mapping[str, Any] | None) -> dict[str, bool]:
    if not isinstance(readiness_gates, Mapping):
        return {}
    return {str(key): bool(value) for key, value in readiness_gates.items()}


def _inferred_readiness_gates(skill_name: str, current_step: str) -> dict[str, bool]:
    """Readiness gates inferred from free text.

    Trusted readiness must never be derived from a free-form ``current_step``
    string: text is not a security proof (SPEC §5.4). AHA-style markers such as
    ``strategy_confirmed`` describe a step name, not a durable user decision.
    Inference therefore contributes no trusted gates; every trusted gate must
    come from an explicit durable transition (backend tool traces for read
    evidence, authenticated confirmation for the strategy decision).
    """
    return {}


def _resume_skill_step_is_ready(current_step: str) -> bool:
    # Kept for diagnostic/display classification only; never a trust proof.
    normalized = str(current_step or "").strip().lower()
    return normalized in RESUME_READY_STEP_MARKERS
