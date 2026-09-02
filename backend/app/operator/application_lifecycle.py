"""ApplicationLifecycleSpec — single authority for the Application lifecycle.

The lifecycle vocabulary is intentionally centralized here so that the ORM,
service validation, FieldSpec registry, universal-tool normalization, canonical
actions, workspace options, seeds, and the grader cannot drift:

    states      draft, pending, submitted, interview, rejected, offer
    labels      草稿 / 待投递 / 已投递 / 面试中 / 已拒绝 / 已录用

The label vocabulary matches the existing product language:
- ORM comment on ``applications.status``: pending / submitted / rejected /
  interview / offer;
- application workspace template options: 待投递 / 已投递 / 面试中 / 已拒绝 / 已录用;
- workspace ``_build_fixed_values_from_job`` default: 待投递 (= pending).

Interview round information is a separate dimension (``interview_round``) and
never pollutes the status value.

Unknown values fail closed: ``resolve_state`` / ``transition`` raise
``ApplicationLifecycleError`` instead of guessing.
"""
from __future__ import annotations

from typing import Any, Mapping


class ApplicationLifecycleError(ValueError):
    """Raised for unknown lifecycle states or invalid transitions."""


class ApplicationLifecycleSpec:
    """Declarative lifecycle registry consumed by validation, actions, and the
    grader. One authority for state vocabulary and transition policy."""

    # Ordered lifecycle stages. The order is the canonical presentation order.
    states: tuple[str, ...] = (
        "draft",
        "pending",
        "submitted",
        "interview",
        "rejected",
        "offer",
    )

    # Localized labels (existing production vocabulary).
    _labels: Mapping[str, str] = {
        "draft": "草稿",
        "pending": "待投递",
        "submitted": "已投递",
        "interview": "面试中",
        "rejected": "已拒绝",
        "offer": "已录用",
    }

    # Code/UI synonyms that deterministically resolve to a canonical state.
    _aliases: Mapping[str, str] = {
        "草稿": "draft",
        "起草": "draft",
        "preparing": "draft",
        "待投递": "pending",
        "准备投递": "pending",
        "not_submitted": "pending",
        "已投递": "submitted",
        "已提交": "submitted",
        "applied": "submitted",
        "面试中": "interview",
        "进面": "interview",
        "约面": "interview",
        "interviewing": "interview",
        "已拒绝": "rejected",
        "拒绝": "rejected",
        "未通过": "rejected",
        "declined": "rejected",
        "已录用": "offer",
        "拿到offer": "offer",
        "录取": "offer",
        "accepted": "offer",
    }

    # Interview sub-round markers stored by the legacy workspace normalization.
    # They are not status values; they all belong to the interview stage and
    # the precise round is tracked separately (interview_round).
    _interview_round_markers: tuple[str, ...] = (
        "一面", "二面", "三面", "终面", "笔试", "测评", "终面复盘", "hr面", "HR面",
    )

    # Interview round is a separate field, never a status value.
    interview_round_field_name: str = "interview_round"

    # Legal transitions. Terminal states have no outgoing edges.
    _transitions: Mapping[str, tuple[str, ...]] = {
        "draft": ("pending", "rejected"),
        "pending": ("submitted", "rejected"),
        "submitted": ("interview", "rejected", "offer"),
        "interview": ("offer", "rejected"),
        "rejected": (),
        "offer": (),
    }

    _terminal_states: frozenset[str] = frozenset({"rejected", "offer"})

    # Per-transition risk/confirmation policy. The ActionSpec surfaces a single
    # risk level (advance_application risk 4); the lifecycle policy documents
    # the per-transition severity and whether the transition has an external
    # (irreversible) side effect such as submitting to an employer.
    _transition_policy: Mapping[tuple[str, str], Mapping[str, Any]] = {
        ("draft", "pending"): {"risk": 2, "confirmation_required": False, "external": False},
        ("draft", "rejected"): {"risk": 3, "confirmation_required": True, "external": False},
        ("pending", "submitted"): {"risk": 4, "confirmation_required": True, "external": True},
        ("pending", "rejected"): {"risk": 3, "confirmation_required": True, "external": False},
        ("submitted", "interview"): {"risk": 3, "confirmation_required": True, "external": False},
        ("submitted", "offer"): {"risk": 4, "confirmation_required": True, "external": False},
        ("submitted", "rejected"): {"risk": 3, "confirmation_required": True, "external": False},
        ("interview", "offer"): {"risk": 4, "confirmation_required": True, "external": False},
        ("interview", "rejected"): {"risk": 3, "confirmation_required": True, "external": False},
    }

    # Same-state re-assertion is an idempotent no-op.
    _noop_policy: Mapping[str, Any] = {
        "risk": 1, "confirmation_required": False, "external": False, "noop": True,
    }

    @classmethod
    def state(cls, value: Any) -> Mapping[str, Any]:
        """Return the full contract entry for one canonical state.

        Raises ``ApplicationLifecycleError`` for unknown values (fail closed).
        """
        state_name = cls.resolve_state(value)
        return {
            "state": state_name,
            "label": cls.label(state_name),
            "aliases": cls.aliases_for(state_name),
            "transitions": tuple(cls._transitions[state_name]),
            "terminal": state_name in cls._terminal_states,
            "risk_level": cls.risk_level(state_name),
            "confirmation_required": cls.confirmation_required(state_name),
        }

    @classmethod
    def states_tuple(cls) -> tuple[str, ...]:
        return cls.states

    @classmethod
    def is_valid(cls, value: Any) -> bool:
        """Whether the value is a canonical lifecycle state or a resolvable alias."""
        return _normalized_value(value) in cls.states

    @classmethod
    def is_terminal(cls, value: Any) -> bool:
        if not cls.is_valid(value):
            raise ApplicationLifecycleError(
                f"Unknown Application lifecycle state: {value!r}"
            )
        return cls.resolve_state(value) in cls._terminal_states

    @classmethod
    def label(cls, value: Any) -> str:
        state_name = cls.resolve_state(value)
        return cls._labels[state_name]

    @classmethod
    def aliases_for(cls, value: Any) -> tuple[str, ...]:
        state_name = cls.resolve_state(value)
        return tuple(
            sorted(
                name
                for name, target in cls._aliases.items()
                if target == state_name
            )
        )

    @classmethod
    def risk_level(cls, value: Any) -> int:
        state_name = cls.resolve_state(value)
        return max(
            (int(policy.get("risk") or 1) for (source, _target), policy in cls._transition_policy.items()
             if source == state_name),
            default=1,
        )

    @classmethod
    def confirmation_required(cls, value: Any) -> bool:
        state_name = cls.resolve_state(value)
        return any(
            bool(policy.get("confirmation_required"))
            for (source, _target), policy in cls._transition_policy.items()
            if source == state_name
        )

    @classmethod
    def resolve_state(cls, value: Any) -> str:
        """Resolve a raw value (canonical state, localized label, or alias) to
        a canonical state; unknown values fail closed."""
        normalized = _normalized_value(value)
        if normalized in cls.states:
            return normalized
        resolved = cls._aliases.get(normalized)
        if resolved is not None:
            return resolved
        if normalized in cls._interview_round_markers:
            return "interview"
        raise ApplicationLifecycleError(
            f"Unknown Application lifecycle value: {value!r}. "
            f"Allowed states: {list(cls.states)}"
        )

    @classmethod
    def resolve_label(cls, value: Any) -> str:
        return cls.label(cls.resolve_state(value))

    @classmethod
    def normalize_apply_status(cls, value: Any) -> dict[str, Any]:
        """Resolve one raw apply_status input to the canonical lifecycle
        contract.  Unknown values raise ``ApplicationLifecycleError`` (fail
        closed) — no second vocabulary exists anywhere else.

        Returns ``{"state": canonical state, "label": localized display label,
        "round_marker": raw interview-round marker text or None}``.  Granular
        interview-round markers (一面/二面/...) resolve to the ``interview``
        stage; the exact marker text is returned separately so callers can
        track the round under ``interview_round`` instead of storing it as a
        status value.
        """
        raw = value if isinstance(value, str) else str(value or "")
        state = cls.resolve_state(raw)
        round_marker = None
        if _normalized_value(raw) in {
            _normalized_value(marker) for marker in cls._interview_round_markers
        }:
            round_marker = raw.strip()
        return {
            "state": state,
            "label": cls.label(state),
            "round_marker": round_marker,
        }

    @classmethod
    def transition(cls, from_state: Any, to_state: Any) -> Mapping[str, Any]:
        """Return the policy for one state transition.

        - unknown source/target states raise (fail closed);
        - same-state re-assertion returns the documented no-op policy;
        - an undeclared edge raises (no guessing).
        """
        source = cls.resolve_state(from_state)
        target = cls.resolve_state(to_state)
        if source == target:
            return {**cls._noop_policy, "from_state": source, "to_state": target}
        if target not in cls._transitions[source]:
            raise ApplicationLifecycleError(
                f"Application lifecycle transition {source!r} -> {target!r} is not allowed. "
                f"Legal transitions from {source!r}: {list(cls._transitions[source])}"
            )
        policy = cls._transition_policy.get((source, target)) or {
            "risk": 3, "confirmation_required": True, "external": False,
        }
        return {
            **dict(policy),
            "from_state": source,
            "to_state": target,
        }


def _normalized_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip().lower()
    return str(value).strip().lower()


# --- Module-level convenience API (public, importable by tools/grader) ------

def is_valid(value: Any) -> bool:
    return ApplicationLifecycleSpec.is_valid(value)


def is_terminal(value: Any) -> bool:
    return ApplicationLifecycleSpec.is_terminal(value)


def state(value: Any) -> Mapping[str, Any]:
    return ApplicationLifecycleSpec.state(value)


def state_label(value: Any) -> str:
    return ApplicationLifecycleSpec.label(value)


def transition(from_state: Any, to_state: Any) -> Mapping[str, Any]:
    return ApplicationLifecycleSpec.transition(from_state, to_state)


def resolve_state(value: Any) -> str:
    return ApplicationLifecycleSpec.resolve_state(value)


def normalize_apply_status_update(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Rewrite an ``application_record`` write mapping so apply_status is owned
    entirely by ``ApplicationLifecycleSpec``.

    - Top-level ``apply_status`` (or ``custom_values.apply_status`` when the
      top-level key is absent) must resolve through the lifecycle authority;
      unknown values raise ``ApplicationLifecycleError`` (fail closed).
    - The canonical state is kept at the top level so the registered FieldSpec
      enum and the durable ``apply_status`` column write path validate the same
      single value.
    - ``custom_values.apply_status`` carries the localized display label (the
      workspace vocabulary) so existing read/render paths keep working.
    - When the raw input is a granular interview-round marker, the exact marker
      text is preserved under ``interview_round`` (never as an apply_status
      value).
    """
    normalized = {str(key): value for key, value in updates.items()}
    custom_values = normalized.get("custom_values")
    if "apply_status" not in normalized:
        if isinstance(custom_values, Mapping) and "apply_status" in custom_values:
            resolved = ApplicationLifecycleSpec.normalize_apply_status(
                custom_values.get("apply_status")
            )
            merged_custom = {str(key): item for key, item in custom_values.items()}
            merged_custom["apply_status"] = resolved["label"]
            if resolved["round_marker"] is not None and ApplicationLifecycleSpec.interview_round_field_name not in merged_custom:
                merged_custom[ApplicationLifecycleSpec.interview_round_field_name] = resolved["round_marker"]
            normalized["custom_values"] = merged_custom
            normalized["apply_status"] = resolved["state"]
        return normalized
    resolved = ApplicationLifecycleSpec.normalize_apply_status(
        normalized.pop("apply_status")
    )
    if not isinstance(custom_values, Mapping):
        custom_values = {}
    merged_custom = {str(key): item for key, item in custom_values.items()}
    merged_custom["apply_status"] = resolved["label"]
    if resolved["round_marker"] is not None and ApplicationLifecycleSpec.interview_round_field_name not in merged_custom:
        merged_custom[ApplicationLifecycleSpec.interview_round_field_name] = resolved["round_marker"]
    normalized["custom_values"] = merged_custom
    normalized["apply_status"] = resolved["state"]
    return normalized