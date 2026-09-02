from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.operator.guards import TRUSTED_ARG_NAMES as GUARD_TRUSTED_ARG_NAMES
from app.operator.guards import TRUSTED_CONTROL_ARG_NAMES as GUARD_TRUSTED_CONTROL_ARG_NAMES
from app.operator.registry import (
    ACTION_REGISTRY,
    SKILL_EXECUTION_CHANNEL_ACTIONS,
    SKILL_REGISTRY,
    TOOL_ARGUMENT_ALIASES,
    UNIVERSAL_TOOL_NAMES,
)
from app.operator.readiness import (
    evidence_target_mismatches,
    job_read_evidence_ready,
    profile_read_evidence_ready,
    readiness_recovery_payload,
    resolve_readiness_missing_requirements,
)


TRUSTED_ARG_NAMES = set(GUARD_TRUSTED_ARG_NAMES) | set(GUARD_TRUSTED_CONTROL_ARG_NAMES) | {
    "actor",
    "proposal_id",
}

READINESS_REQUIRED_ACTIONS = {"generate_resume"}
READINESS_KEYS = {
    "job_context_loaded",
    "profile_facts_loaded",
    "strategy_confirmed",
    "resume_readiness_evidence",
}


@dataclass(frozen=True)
class SecurityDecision:
    blocked: bool
    reason: str = ""
    normalized_args: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None


def normalize_tool_args(tool_name: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
    safe_args = dict(args or {})
    if tool_name not in UNIVERSAL_TOOL_NAMES and tool_name in ACTION_REGISTRY:
        return {"action": tool_name, "input": dict(safe_args)}

    aliases = TOOL_ARGUMENT_ALIASES.get(tool_name, {})
    normalized = dict(safe_args)
    for old_name, canonical_name in aliases.items():
        if old_name in normalized and canonical_name not in normalized:
            normalized[canonical_name] = normalized.pop(old_name)
    if tool_name == "manage_session":
        normalized = _normalize_manage_session_args(normalized)
    return normalized


def _normalize_manage_session_args(args: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(args)
    if str(normalized.get("operation") or "") != "activate_skill":
        return normalized
    updates = normalized.get("updates")
    if not isinstance(updates, Mapping):
        return normalized
    canonical_updates = dict(updates)
    if "active_skill" not in canonical_updates:
        for alias in ("skill", "name"):
            if alias in canonical_updates:
                canonical_updates["active_skill"] = canonical_updates.pop(alias)
                break
    for alias in ("skill", "name"):
        if alias in canonical_updates and canonical_updates.get(alias) == canonical_updates.get("active_skill"):
            canonical_updates.pop(alias)
    normalized["updates"] = canonical_updates
    return normalized


def validate_trusted_args(tool_name: str, args: Mapping[str, Any] | None, actor: Any) -> SecurityDecision:
    violations = _trusted_violations(args or {})
    if not violations:
        return SecurityDecision(False, normalized_args=dict(args or {}))
    reason = (
        "安全策略阻止：工具参数包含后端可信字段 %s。"
        "这些字段只能由服务器提供，请不要重试相同参数；改用普通业务字段重新表达请求。"
    ) % ", ".join(sorted(violations))
    return SecurityDecision(True, reason=reason, normalized_args=dict(args or {}))


def _required_skill_for_action(action_name: str) -> str | None:
    """Registry-derived Skill requirement for a write action.

    ``SKILL_EXECUTION_CHANNEL_ACTIONS`` actions are exclusively reachable under
    their owning Skill; all other action-to-Skill bindings come from
    ``SkillSpec.allowed_write_actions``. No hard-coded action sets are kept, so
    registry changes propagate to the gate automatically.
    """
    channel_skill = SKILL_EXECUTION_CHANNEL_ACTIONS.get(action_name)
    if channel_skill:
        return channel_skill
    for skill_name, skill_spec in SKILL_REGISTRY.items():
        if action_name in skill_spec.allowed_write_actions:
            return skill_name
    return None


def _skill_gate_recovery(action: str, required_skill: str) -> dict[str, Any]:
    return {
        "code": "skill_gate_blocked",
        "required_skill": required_skill,
        "missing_requirements": [
            {
                "name": "active_skill",
                "status": "missing",
                "satisfy_with": [
                    {
                        "tool": "describe_capability",
                        "kind": "skill",
                        "name": required_skill,
                        "operation": "activate",
                    },
                    {
                        "tool": "manage_session",
                        "operation": "activate_skill",
                        "updates": {"active_skill": required_skill},
                    },
                ],
            }
        ],
        "next_allowed_operations": [
            {"tool": "describe_capability", "kind": "skill", "name": required_skill, "operation": "activate"},
            {"tool": "manage_session", "operation": "activate_skill", "updates": {"active_skill": required_skill}},
        ],
        "retry_same_action_after": [],
    }


def check_skill_tool_gate(tool_name: str, args: Mapping[str, Any] | None, active_skill: str | None) -> SecurityDecision:
    action = _action_name(tool_name, args or {})
    required_skill = _required_skill_for_action(action)
    if required_skill and active_skill != required_skill:
        return SecurityDecision(
            True,
            reason=(
                "Skill-only tool call blocked: action %s requires active Skill %s. "
                "Do not retry the same parameters; ask to activate the Skill or choose a read-only step."
            )
            % (action, required_skill),
            normalized_args=dict(args or {}),
            recovery=_skill_gate_recovery(action, required_skill),
        )
    return SecurityDecision(False, normalized_args=dict(args or {}))


def verify_resume_generate_readiness(args: Mapping[str, Any] | None, session_state: Mapping[str, Any] | None) -> SecurityDecision:
    safe_args = dict(args or {})
    action = _action_name("invoke_action", safe_args)
    if action not in READINESS_REQUIRED_ACTIONS:
        return SecurityDecision(False, normalized_args=safe_args)
    state = dict(session_state or {})
    evidence = state.get("resume_readiness_evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    missing: list[str] = []

    profile_evidence = evidence.get("profile_read_evidence")
    if not profile_read_evidence_ready(profile_evidence):
        missing.append("profile_read_evidence")
    job_evidence = evidence.get("job_read_evidence")
    if not job_read_evidence_ready(job_evidence):
        missing.append("job_read_evidence")
    if not bool(state.get("strategy_confirmed")):
        missing.append("strategy_confirmed")
    for field_name in evidence_target_mismatches(safe_args, state):
        missing.append(f"{field_name}_read_evidence_binding")

    if not missing:
        return SecurityDecision(False, normalized_args=safe_args)
    missing_requirements = resolve_readiness_missing_requirements(
        profile_evidence=profile_evidence,
        job_evidence=job_evidence,
        strategy_confirmed=bool(state.get("strategy_confirmed")),
    )
    for binding_name in missing:
        if binding_name.endswith("_read_evidence_binding") and binding_name not in {
            item["name"] for item in missing_requirements
        }:
            missing_requirements.append(
                {
                    "name": binding_name,
                    "status": "missing",
                    "satisfy_with": [],
                }
            )
    return SecurityDecision(
        True,
        reason=(
            "generate_resume blocked: resume readiness evidence is insufficient; missing %s. "
            "请先读取职位/JD、档案事实并确认策略；安全策略阻止，请不要重试相同参数。"
        )
        % ", ".join(missing),
        normalized_args=safe_args,
        recovery=readiness_recovery_payload(missing_requirements),
    )



def _action_name(tool_name: str, args: Mapping[str, Any]) -> str:
    if tool_name == "invoke_action":
        return str(args.get("action") or "")
    if tool_name not in UNIVERSAL_TOOL_NAMES and tool_name in ACTION_REGISTRY:
        return tool_name
    return ""


def _trusted_violations(value: Any, *, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_str = str(key)
            path = "%s.%s" % (prefix, key_str) if prefix else key_str
            if key_str in TRUSTED_ARG_NAMES:
                found.add(path)
            found.update(_trusted_violations(child, prefix=path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.update(_trusted_violations(child, prefix="%s[%s]" % (prefix, index)))
    return found
