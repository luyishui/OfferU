from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import models
from app.operator.audit import redact_audit_args
from app.operator.errors import (
    OperatorError,
    permission_error,
    transient_error,
    validation_error,
)


def _json_safe(value: Any) -> Any:
    """Lazy passthrough to the canonical JSON-safe coercion in operator.guards.

    Imported lazily so this module never participates in the
    guards -> registry import cycle at module load time; the coercion logic
    itself stays single-sourced in guards.json_safe.
    """
    from app.operator.guards import json_safe

    return json_safe(value)


MEMORY_CATEGORIES = {
    "facts",
    "preferences",
    "goals",
    "constraints",
    "style_preferences",
    "interaction_preferences",
    "workflow_preferences",
}
PREFERENCE_CATEGORIES = {
    "preferences",
    "goals",
    "constraints",
    "style_preferences",
    "interaction_preferences",
    "workflow_preferences",
}
MIN_STORE_CONFIDENCE = 0.75
BUSINESS_MEMORY_CATEGORIES = PREFERENCE_CATEGORIES - {"facts"}
BUSINESS_MEMORY_TEXT_KEYS = {
    "avoid",
    "constraint",
    "constraints",
    "goal",
    "goals",
    "interaction_preference",
    "interaction_preferences",
    "language",
    "next_step",
    "note_style",
    "preference",
    "preferences",
    "process",
    "reminder",
    "style_preference",
    "style_preferences",
    "tone",
    "workflow",
    "workflow_preferences",
}
BUSINESS_DATA_KEYS = {
    "application_id",
    "application_ids",
    "official_profile_fact",
    "profile_id",
    "profile_ids",
    "resume_id",
    "resume_ids",
    "section_id",
    "section_ids",
}
OFFICIAL_FACT_KEYS = {
    "application_status",
    "apply_url",
    "applied",
    "candidate",
    "company",
    "company_name",
    "compensation_band",
    "current_employer",
    "degree",
    "education",
    "employer",
    "employer_name",
    "experience",
    "expected_salary",
    "gpa",
    "graduation_date",
    "graduation_year",
    "industry",
    "job_level",
    "job_title",
    "major",
    "position",
    "profile_fact",
    "role",
    "salary",
    "salary_range",
    "school",
    "skills",
    "status",
    "submitted_at",
    "title",
    "university",
    "work_history",
    "work_authorization",
}
OFFICIAL_FACT_KEY_SUFFIXES = {
    "field",
    "key",
    "label",
    "name",
    "text",
    "value",
}
BUSINESS_TOPIC_ALIASES = {
    "app": "application",
    "application": "application",
    "application_note": "application",
    "application_notes": "application",
    "applications": "application",
    "career": "profile",
    "careers": "profile",
    "career_profile": "profile",
    "candidate": "profile",
    "candidates": "profile",
    "education": "profile",
    "education_history": "profile",
    "educations": "profile",
    "job": "job",
    "jobs": "job",
    "position": "job",
    "positions": "job",
    "posting": "job",
    "postings": "job",
    "profile": "profile",
    "profiles": "profile",
    "profile_section": "section",
    "resume": "resume",
    "resumes": "resume",
    "section": "section",
    "sections": "section",
    "work": "profile",
    "work_history": "profile",
}
BUSINESS_TOPICS = {"application", "job", "profile", "resume", "section"}
OFFICIAL_FACT_TEXT_KEY_MARKERS = {
    "application_fact",
    "application_status",
    "candidate_fact",
    "company_name",
    "compensation_band",
    "current_employer",
    "expected_salary",
    "graduation_date",
    "graduation_year",
    "official_application",
    "official_company",
    "official_compensation",
    "official_degree",
    "official_employer",
    "official_graduation",
    "official_major",
    "official_profile",
    "official_resume",
    "official_salary",
    "official_school",
    "official_status",
    "official_university",
    "profile_fact",
    "resume_fact",
    "salary_range",
    "school_name",
    "university_name",
    "work_history",
}
OFFICIAL_FACT_TEXT_ASSERTIONS = {
    "application status is",
    "applied to",
    "applied to acme",
    "company name is",
    "compensation band",
    "current employer is",
    "degree is",
    "employer is",
    "graduation date",
    "graduation year",
    "i applied to",
    "major is",
    "my alma mater is",
    "my company is",
    "my degree is",
    "my employer is",
    "my major is",
    "my salary is",
    "my school is",
    "my status is",
    "my university is",
    "salary is",
    "salary range",
    "school is",
    "status is submitted",
    "university is",
}
_FACT_ENTITY = r"[A-Z][A-Za-z0-9&.'-]*(?:\s+(?:of|and|the|&|[A-Z][A-Za-z0-9&.'-]*)){0,6}"
_FIRST_PERSON_PROFILE_SUBJECT = r"(?:i|i['’]m|i\s+am|i['’]ve|i\s+have|we|we['’]re|we\s+are|we['’]ve|we\s+have)"
_FIRST_PERSON_POSSESSIVE = r"(?:my|our)"
_ROLE_TITLE_WORDS = (
    "business analyst",
    "consultant",
    "data analyst",
    "data scientist",
    "designer",
    "engineer",
    "intern",
    "marketing manager",
    "operations manager",
    "product manager",
    "product owner",
    "program manager",
    "project manager",
    "recruiter",
    "researcher",
    "software engineer",
)
_APPLICATION_STATUS_WORDS = (
    "accepted",
    "assessment",
    "interview",
    "interviewing",
    "offer",
    "offered",
    "rejected",
    "screen",
    "screening",
    "submitted",
    "withdrawn",
)
_ROLE_TITLE_PATTERN = "|".join(re.escape(title) for title in _ROLE_TITLE_WORDS)
_APPLICATION_STATUS_PATTERN = "|".join(_APPLICATION_STATUS_WORDS)
OFFICIAL_EDUCATION_FACT_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"(?i:\b{_FIRST_PERSON_PROFILE_SUBJECT}\s+(?:am\s+|are\s+|was\s+|were\s+)?(?:study|studying|studied)\s+(?:at|in)\s+){_FACT_ENTITY}\b",
        rf"(?i:\b{_FIRST_PERSON_PROFILE_SUBJECT}\s+(?:am\s+|are\s+|was\s+|were\s+)?(?:attend|attending|attended)\s+(?:(?:at|in)\s+)?){_FACT_ENTITY}\b",
        rf"(?i:\b{_FIRST_PERSON_PROFILE_SUBJECT}\s+graduated\s+from\s+){_FACT_ENTITY}\b",
        rf"(?i:\b(?:i|we)\s+(?:majored|minor(?:ed)?)\s+in\s+)[A-Za-z][A-Za-z0-9&.'+\-\s]{{1,80}}(?i:\s+at\s+){_FACT_ENTITY}\b",
        rf"(?i:\b{_FIRST_PERSON_POSSESSIVE}\s+(?:alma\s+mater|school|university|college)\s+(?:is|was)\s+){_FACT_ENTITY}\b",
        rf"(?i:\b{_FIRST_PERSON_POSSESSIVE}\s+degree\s+(?:is|was|came)\s+from\s+){_FACT_ENTITY}\b",
    )
)
OFFICIAL_EMPLOYMENT_FACT_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"(?i:\b{_FIRST_PERSON_PROFILE_SUBJECT}\s+(?:currently\s+)?(?:work|working|worked|intern|interning|interned)\s+(?:at|for|with)\s+){_FACT_ENTITY}\b",
        rf"(?i:\b[A-Za-z][A-Za-z'-]*\s+(?:currently\s+)?works\s+(?:at|for|with)\s+){_FACT_ENTITY}\b",
        rf"(?i:\b(?:i|i'm|i am|we|we're|we are)\s+(?:currently\s+)?employed\s+(?:at|by)\s+){_FACT_ENTITY}\b",
        rf"(?i:\b[A-Za-z][A-Za-z'-]*\s+(?:is|was|has\s+been)\s+(?:currently\s+)?employed\s+(?:at|by)\s+){_FACT_ENTITY}\b",
        rf"(?i:\b(?:i\s+am|i'm|my\s+role\s+is|my\s+job\s+title\s+is|i\s+work\s+as)\s+(?:an?\s+)?(?:junior\s+|senior\s+|associate\s+|lead\s+)?(?:{_ROLE_TITLE_PATTERN})\b)",
    )
)
OFFICIAL_APPLICATION_FACT_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        rf"(?i:\b(?:my|the)\s+)(?:{_FACT_ENTITY}\s+)?(?i:application\s+(?:moved|advanced|changed|went|transitioned)\s+(?:to|into)\s+(?:the\s+)?(?:{_APPLICATION_STATUS_PATTERN})(?:\s+(?:round|stage|phase|status))?\b)",
        rf"(?:\b{_FACT_ENTITY}\s+)(?i:application\s+(?:moved|advanced|changed|went|transitioned)\s+(?:to|into)\s+(?:the\s+)?(?:{_APPLICATION_STATUS_PATTERN})(?:\s+(?:round|stage|phase|status))?\b)",
        rf"(?i:\b(?:my|the)\s+)(?:{_FACT_ENTITY}\s+)?(?i:application\s+(?:is|was|has\s+been|got)\s+(?:in\s+|at\s+|to\s+)?(?:the\s+)?(?:{_APPLICATION_STATUS_PATTERN})(?:\s+(?:round|stage|phase|status))?\b)",
        rf"(?:\b{_FACT_ENTITY}\s+)(?i:application\s+(?:is|was|has\s+been|got)\s+(?:in\s+|at\s+|to\s+)?(?:the\s+)?(?:{_APPLICATION_STATUS_PATTERN})(?:\s+(?:round|stage|phase|status))?\b)",
        rf"(?i:\b(?:i|we)\s+(?:submitted|withdrew)\s+(?:my|our|the)?\s*)?(?:{_FACT_ENTITY}\s+)?(?i:application\s+(?:for\s+)?(?:{_FACT_ENTITY}\s+)?(?:is|was|has\s+been)?\s*(?:submitted|rejected|accepted|offered|withdrawn)\b)",
    )
)
OFFICIAL_FACT_TEXT_PATTERN_GROUPS = (
    OFFICIAL_EDUCATION_FACT_PATTERNS,
    OFFICIAL_EMPLOYMENT_FACT_PATTERNS,
    OFFICIAL_APPLICATION_FACT_PATTERNS,
)
OFFICIAL_FACT_TEXT_CJK_MARKERS = {
    "公司名称",
    "公司名",
    "官方公司",
    "官方学校",
    "官方学位",
    "官方专业",
    "官方档案",
    "官方状态",
    "官方申请",
    "官方简历",
    "官方薪资",
    "官方薪酬",
    "官方雇主",
    "学历是",
    "母校是",
    "学校是",
    "学位是",
    "申请状态",
    "公司是",
    "简历事实",
    "简历中写了",
    "简历上写了",
    "简历里写了",
    "薪水是",
    "薪资是",
    "薪酬是",
    "专业是",
    "雇主是",
    "档案事实",
    "毕业学校",
    "毕业时间",
    "毕业年份",
    "毕业院校",
}
OFFICIAL_FACT_TEXT_CJK_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"我(?:曾经)?(?:在|于).{1,40}(?:学习|读书|就读)",
        r"我(?:毕业于|就读于).{1,40}",
        r"我(?:目前|现在)?(?:在|为).{1,40}(?:工作|任职)",
        r"我(?:曾经|目前|现在|之前|以前)?(?:在|于)[^，。；,!?、]{1,40}?(?:做|从事|负责)[^，。；,!?、]{0,24}?(?:后端|前端|客户端|全栈|产品|运营|设计|开发|研发|工程|测试|数据|算法|架构|平台|系统|业务|市场|销售|客服|财务|人力|内容|增长|方向|相关|工作|经历|工程师|经理|分析师|设计师|实习生|顾问|产品经理|项目经理)",
        r"我(?:是|担任).{0,20}(?:产品经理|项目经理|工程师|分析师|设计师|实习生|顾问)",
        r"(?:我的)?.{0,20}申请(?:进入|到了|转到|变成|已|被).{0,20}(?:面试|提交|录用|拒绝|offer|Offer)",
    )
)
SENSITIVE_KEYS = {
    "contact_json",
    "email",
    "email_body",
    "phone",
    "raw_text",
    "secret",
    "token",
    "wechat",
}


MEMORY_SESSION_PREFIX = "memory:"


def memory_session_id(auth_subject: str) -> str:
    """Deterministic per-principal memory namespace.

    Each authenticated browser principal owns a dedicated memory session that
    cannot be claimed by another principal (``bind_session_authority`` fences
    sessions by ``auth_subject``). Memory import/export binds this namespace so
    exported memories never cross browser-principal boundaries.
    """
    return f"{MEMORY_SESSION_PREFIX}{auth_subject}"


def _memory_decision(*, ok: bool, stored: bool, **payload: Any) -> dict[str, Any]:
    return {
        "ok": ok,
        "stored": stored,
        "needs_confirmation": False,
        **payload,
    }


def write_memory_candidate(
    session: AsyncSession | None = None,
    actor: ActorContext | None = None,
    *,
    category: str,
    topic: str,
    content: Mapping[str, Any],
    confidence: float = 1.0,
    skill: str = "",
    sensitive_confirmed: bool = False,
    actor_id: str = "",
    session_id: str = "",
) -> Any:
    """Route one memory candidate through every memory guard.

    Two call modes share one guard implementation:

    - Guard-only evaluation: call without ``session``/``actor`` (optionally
      with ``actor_id``/``session_id`` for identification in error details).
      Returns the decision dict synchronously, never touching storage — the
      durable write path is reserved for modes with a database session.

    - Durable write: call with ``session`` and ``actor`` and await the result.
      Returns a coroutine that re-evaluates the guards and, when the candidate
      passes, persists one ``AgentMemory`` row and commits.

    Decisions always carry ``ok`` and ``stored`` flags; sensitive candidates
    that were not explicitly confirmed additionally carry
    ``needs_confirmation=True`` so callers can issue a structured confirmation
    request instead of silently dropping or auto-storing user content.
    """
    if session is not None and actor is not None:

        async def guard_and_write() -> dict[str, Any]:
            guarded = _evaluate_memory_candidate(
                category=category,
                topic=topic,
                content=content,
                confidence=confidence,
                sensitive_confirmed=sensitive_confirmed,
            )
            if guarded is not None:
                return guarded
            return await _persist_memory_candidate(
                session,
                actor,
                category=category,
                topic=topic,
                content=content,
                confidence=confidence,
                skill=skill,
            )

        return guard_and_write()

    # Guard-only evaluation mode: no durable session is available, so the
    # decision is returned synchronously (rejections, confirmation requests,
    # skips) or as an explicit error when storage would be required.
    decision = _evaluate_memory_candidate(
        category=category,
        topic=topic,
        content=content,
        confidence=confidence,
        sensitive_confirmed=sensitive_confirmed,
    )
    if decision is not None:
        return decision
    identity = {"actor_id": str(actor_id or getattr(actor, "actor_id", "") or ""), "session_id": str(session_id or getattr(actor, "session_id", "") or "")}
    return _memory_decision(
        ok=False,
        stored=False,
        error={
            "code": "operator_error",
            "message": "Memory write requires a durable session and actor scope.",
            "details": {"category": category, "topic": topic, **identity},
        },
    )


def _evaluate_memory_candidate(
    *,
    category: str,
    topic: str,
    content: Any,
    confidence: float = 1.0,
    sensitive_confirmed: bool = False,
) -> dict[str, Any] | None:
    """Run the memory guards; return a decision dict or None when storable.

    The returned decisions reuse the structured error envelope consumed by the
    import route and the remember_preference action, so no caller can bypass
    the whitelist, business-fact rejection, or sensitive-content confirmation.
    """
    if category not in MEMORY_CATEGORIES:
        return _memory_decision(
            ok=False,
            stored=False,
            error={
                "code": "validation_error",
                "message": "Memory category is not allowed.",
                "details": {"category": category},
            },
        )
    if not isinstance(content, Mapping):
        return _memory_decision(
            ok=False,
            stored=False,
            error={
                "code": "validation_error",
                "message": "Memory content must be an object.",
                "details": {"category": category},
            },
        )
    safe_content = _json_safe(content)
    if _contains_disallowed_business_memory(category, topic, safe_content):
        return _memory_decision(
            ok=False,
            stored=False,
            error={
                "code": "validation_error",
                "message": "Official business data must be stored through business records, not long-term memory.",
                "details": {"category": category, "topic": topic},
            },
        )
    if _contains_sensitive_content(safe_content) and not sensitive_confirmed:
        return _memory_decision(
            ok=False,
            stored=False,
            needs_confirmation=True,
            error={
                "code": "validation_error",
                "message": "Sensitive memory requires explicit confirmation.",
                "details": {"category": category, "topic": topic},
            },
        )
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        return _memory_decision(
            ok=False,
            stored=False,
            error={
                "code": "validation_error",
                "message": "Memory confidence must be numeric.",
                "details": {"category": category, "topic": topic},
            },
        )
    if confidence < MIN_STORE_CONFIDENCE or _looks_one_off(safe_content):
        return _memory_decision(
            ok=True,
            stored=False,
            status="skipped",
            reason="Memory candidate is not stable or high-confidence enough to store.",
        )
    return None


async def _persist_memory_candidate(
    session: AsyncSession,
    actor: ActorContext,
    *,
    category: str,
    topic: str,
    content: Mapping[str, Any],
    confidence: float,
    skill: str,
) -> dict[str, Any]:
    try:
        safe_content = _json_safe(content)
        memory_id = f"mem_{uuid.uuid4().hex}"
        row = models.AgentMemory(
            memory_id=memory_id,
            actor_id=actor.actor_id,
            session_id=actor.session_id,
            category=category,
            topic=topic or "",
            skill=skill or "",
            content_json=safe_content,
            confidence=confidence,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _memory_decision(
            ok=True,
            stored=True,
            status="stored",
            memory=_serialize_memory(row),
        )
    except OperatorError as exc:
        await _rollback_quietly(session)
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive adapter boundary.
        await _rollback_quietly(session)
        return transient_error("Operator memory write failed transiently.", {"error": str(exc)})


async def retrieve_memories(
    session: AsyncSession,
    actor: ActorContext,
    *,
    topic: str = "",
    skill: str = "",
    categories: Sequence[str] = (),
    session_id: str = "",
) -> dict[str, Any]:
    """Read durable memories for the actor's scope.

    The actor filter remains authoritative (unchanged existing semantics).
    ``session_id`` is an opt-in exact-session cut for principal-bound surfaces
    such as memory import/export; when empty the call keeps its historical
    actor-wide behavior. Failures are explicit: a structured error envelope is
    returned (never silence), so callers can fail closed instead of treating a
    retrieval failure as "no memory".
    """
    try:
        clauses = [models.AgentMemory.actor_id == actor.actor_id]
        if session_id:
            clauses.append(models.AgentMemory.session_id == session_id)
        if topic:
            clauses.append(models.AgentMemory.topic == topic)
        if skill:
            clauses.append((models.AgentMemory.skill == skill) | (models.AgentMemory.skill == ""))
        if categories:
            invalid = sorted(set(categories) - MEMORY_CATEGORIES)
            if invalid:
                raise OperatorError("validation_error", "Memory categories are not allowed.", {"categories": invalid})
            clauses.append(models.AgentMemory.category.in_(list(categories)))
        rows = (
            await session.execute(select(models.AgentMemory).where(and_(*clauses)).order_by(models.AgentMemory.updated_at.desc()))
        ).scalars().all()
        return {
            "ok": True,
            "status": "success",
            "memories": [_serialize_memory(row) for row in rows],
        }
    except OperatorError as exc:
        return _operator_error_response(exc)
    except Exception as exc:  # pragma: no cover - defensive adapter boundary.
        # Explicit, recognizable failure envelope: an upstream consumer (the
        # system prompt builder, import/export routes) must be able to
        # distinguish "memory unavailable" from "no memory". Callers are
        # expected to fail closed instead of treating this as an empty result.
        return {
            "ok": False,
            "status": "error",
            "error": {
                "code": "memory_unavailable",
                "message": "Operator memory retrieval failed transiently.",
                "details": {"error": str(exc)},
            },
        }


def _serialize_memory(row: Any) -> dict[str, Any]:
    return {
        "memory_id": row.memory_id,
        "actor_id": row.actor_id,
        "session_id": row.session_id,
        "category": row.category,
        "topic": row.topic,
        "skill": row.skill,
        "content": _json_safe(row.content_json or {}),
        "confidence": row.confidence,
    }


def _contains_disallowed_business_memory(category: str, topic: str, value: Any) -> bool:
    if _contains_business_record_reference(value):
        return True
    if _contains_official_fact_content(value):
        return True
    if not _is_business_topic(topic):
        return False
    return not _is_allowed_business_topic_memory(category, value)


def _is_allowed_business_topic_memory(category: str, value: Any) -> bool:
    """Business-topic preference memory must be a policy/preference object.

    The key vocabulary is deliberately not the acceptance gate: workflow
    constraints arrive under free-form keys (``default_behavior``,
    ``highlight``, ``style``, ...) whose values are still preference policy
    text. A content object is allowed when every entry is policy memory;
    business-record references and official-fact keys are rejected by the
    earlier predicates and remain rejected here as defense in depth.
    """
    if category not in BUSINESS_MEMORY_CATEGORIES or not isinstance(value, Mapping) or not value:
        return False
    return _is_policy_object(value)


def _is_policy_object(value: Mapping[str, Any]) -> bool:
    """One policy/preference object.

    When at least one key is a recognized policy key the object is anchored as
    policy and every entry is judged by the same key framing (``avoid: [...]
    `` means "do not ..."). Without an anchor, every value must carry explicit
    policy wording, so free-form objects such as ``{"note": "Software
    Engineer"}`` keep being rejected.
    """
    anchored = any(_normalize_key(str(key)) in BUSINESS_MEMORY_TEXT_KEYS for key in value)
    for key, child in value.items():
        normalized = _normalize_key(str(key))
        if normalized in BUSINESS_DATA_KEYS or _is_official_fact_key(str(key)):
            return False
        entry_ok = _is_policy_framed_value(child) if anchored else _is_policy_memory_value(child)
        if not entry_ok:
            return False
    return True


def _is_policy_framed_value(value: Any) -> bool:
    """Value under a recognized policy key.

    Any non-empty textual content that does not itself assert an official
    business fact is acceptable: the key already frames the entry as policy
    (``avoid: ["突出区块链经历"]`` means "do not highlight blockchain
    experience"). Nested objects recurse with the same key framing, which lets
    structured style objects (``style: {tone/evidence/fabrication}``) store as
    long as every leaf stays non-factual.
    """
    if isinstance(value, str):
        return bool(value.strip()) and not _contains_factual_business_assertion(value)
    if isinstance(value, Mapping):
        return bool(value) and _is_policy_object(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value) and all(
            isinstance(item, str)
            and bool(item.strip())
            and not _contains_factual_business_assertion(item)
            for item in value
        )
    return False


def _is_policy_memory_value(value: Any) -> bool:
    if isinstance(value, str):
        return _is_preference_policy_text(value)
    if isinstance(value, Mapping):
        return bool(value) and _is_policy_object(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value) and all(isinstance(item, str) and _is_preference_policy_text(item) for item in value)
    return False


def _is_textual_memory_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return bool(value) and all(isinstance(item, str) and bool(item.strip()) for item in value)
    return False


def _contains_business_record_reference(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalize_key(str(key))
            if normalized in BUSINESS_DATA_KEYS:
                return True
            if normalized.endswith("_id") and normalized.split("_id", 1)[0] in {"profile", "resume", "application", "section"}:
                return True
            if _contains_business_record_reference(child):
                return True
    elif isinstance(value, list):
        return any(_contains_business_record_reference(child) for child in value)
    return False


def _contains_official_fact_content(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _is_official_fact_key(str(key)):
                return True
            if _contains_official_fact_content(child):
                return True
    elif isinstance(value, str):
        return _contains_factual_business_assertion(value)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_official_fact_content(child) for child in value)
    return False


def _is_official_fact_key(key: str) -> bool:
    words = _identifier_words(key)
    if not words:
        return False

    variants = {_normalize_words(words)}
    variants.update(_normalized_word_windows(words))

    trimmed_words = _without_trailing_suffixes(words, OFFICIAL_FACT_KEY_SUFFIXES)
    if trimmed_words != words:
        variants.add(_normalize_words(trimmed_words))
        variants.update(_normalized_word_windows(trimmed_words))

    return any(variant in OFFICIAL_FACT_KEYS for variant in variants if variant)


def _normalized_word_windows(words: Sequence[str]) -> set[str]:
    variants: set[str] = set()
    for start in range(len(words)):
        for end in range(start + 1, len(words) + 1):
            variants.add(_normalize_words(words[start:end]))
    return variants


def _without_trailing_suffixes(words: Sequence[str], suffixes: set[str]) -> list[str]:
    trimmed = list(words)
    while len(trimmed) > 1 and trimmed[-1] in suffixes:
        trimmed.pop()
    return trimmed


def _contains_official_fact_text(text: str) -> bool:
    if not text.strip():
        return False
    normalized_key_text = _normalize_key(text)
    if any(marker in normalized_key_text for marker in OFFICIAL_FACT_TEXT_KEY_MARKERS):
        return True

    normalized_words = _normalize_text_words(text)
    if any(assertion in normalized_words for assertion in OFFICIAL_FACT_TEXT_ASSERTIONS):
        return True
    if _matches_any_official_fact_text_pattern(text):
        return True
    return any(marker in text for marker in OFFICIAL_FACT_TEXT_CJK_MARKERS)


def _is_preference_policy_text(text: str) -> bool:
    if not text.strip() or _contains_factual_business_assertion(text):
        return False
    normalized = _normalize_text_words(text)
    cjk_policy_markers = (
        "不要",
        "别",
        "避免",
        "偏好",
        "喜欢",
        "希望",
        "请",
        "语气",
        "口吻",
        "风格",
        "简洁",
        "夸张",
        "正式",
        "中文",
        "英文",
        "每次",
        "以后",
        "都",
        "先",
        "确认",
    )
    if any(marker in text for marker in cjk_policy_markers):
        return True

    policy_patterns = (
        r"\b(?:i|we)\s+(?:prefer|would prefer|like|would like|want|need)\b",
        r"\b(?:please|always|never)\b",
        r"\b(?:do not|don't|dont|avoid|skip|exclude|include|use|write|make|keep|ask|confirm|remind)\b",
        r"\b(?:tone|style|voice|language|wording|format|formatting|concise|brief|formal|casual|process|workflow|constraint|preference|policy)\b",
    )
    return any(re.search(pattern, normalized) for pattern in policy_patterns)


def _contains_factual_business_assertion(text: str) -> bool:
    if _contains_official_fact_text(text):
        return True
    normalized = _normalize_text_words(text)
    if not normalized:
        return False

    fact_patterns = (
        r"\b(?:i|we)\s+(?:go|went)\s+to\s+[a-z0-9]",
        r"\b(?:i|we)\s+(?:am|are|was|were|m|re)?\s*(?:an?\s+)?[a-z0-9&.' -]{1,60}\s+student\b",
        r"\b(?:my|our)\s+(?:school|university|college|major|degree|education)\s+(?:is|was|are|were)\b",
        r"\b(?:my|our)\s+(?:salary|compensation|pay|expected salary|salary range|compensation band)\s+(?:is|was|are|were)\s+(?:[a-z]{2,4}\s+)?[0-9]",
        r"\b(?:i|we)\s+got\s+(?:an?\s+)?offer\s+from\s+[a-z0-9]",
        r"\b(?:i|we)\s+(?:received|accepted|declined)\s+(?:an?\s+)?offer\s+(?:from|at)\s+[a-z0-9]",
        r"\b(?:my|our)\s+(?:resume|cv)\s+(?:headline|title|summary)\s+(?:is|was|says|reads)\b",
        r"\b(?:my|our)\s+(?:headline|title|summary)\s+(?:is|was|says|reads)\b",
        r"\b(?:i|we)\s+(?:am|are|was|were|m|re)\s+(?:an?\s+)?(?:intern|employee|engineer|manager|analyst|designer|consultant|recruiter|researcher)\b",
        r"\b(?:my|our)\s+(?:role|job|job title|employer|company)\s+(?:is|was)\b",
        r"\bgpa\s*(?:is|was)?\s*[0-9]",
        r"\b绩点\s*[0-9]",
        r"(?:工资|薪水|薪资|薪酬|年薪|月薪)\s*[0-9]",
    )
    if any(re.search(pattern, normalized) for pattern in fact_patterns):
        return True

    business_nouns = (
        "application",
        "resume",
        "cv",
        "profile",
        "school",
        "university",
        "college",
        "student",
        "degree",
        "major",
        "employer",
        "company",
        "role",
        "job title",
        "headline",
        "summary",
        "salary",
        "compensation",
        "pay",
        "offer",
        "interview",
        "status",
    )
    assertion_verbs = (
        " is ",
        " was ",
        " are ",
        " were ",
        " got ",
        " has ",
        " have ",
        " moved ",
        " changed ",
        " advanced ",
        " submitted ",
        " accepted ",
        " rejected ",
        " offered ",
    )
    has_first_person_anchor = re.search(r"\b(?:i|we|my|our)\b", normalized) is not None
    has_business_noun = any(noun in normalized for noun in business_nouns)
    has_assertion_verb = any(verb in f" {normalized} " for verb in assertion_verbs)
    has_named_entity = re.search(r"\b[A-Z][A-Za-z0-9&.'-]*(?:\s+[A-Z][A-Za-z0-9&.'-]*)*\b", text) is not None
    return has_first_person_anchor and has_business_noun and has_assertion_verb and has_named_entity


def _matches_any_official_fact_text_pattern(text: str) -> bool:
    for group in OFFICIAL_FACT_TEXT_PATTERN_GROUPS:
        if any(pattern.search(text) for pattern in group):
            return True
    return any(pattern.search(text) for pattern in OFFICIAL_FACT_TEXT_CJK_PATTERNS)


def _contains_sensitive_content(value: Any) -> bool:
    redacted = redact_audit_args(value)
    return redacted != _json_safe(value)


def _looks_one_off(value: Any) -> bool:
    encoded = str(value)
    one_off_markers = ("今天", "刚才", "现在", "this time", "today")
    return any(marker in encoded for marker in one_off_markers)


def _normalize_key(key: str) -> str:
    return "_".join(_identifier_words(key))


def _normalize_words(words: Sequence[str]) -> str:
    return "_".join(words)


def _is_business_topic(topic: str) -> bool:
    normalized = _normalize_key(topic)
    candidates = {normalized, *_identifier_words(topic)}
    for candidate in list(candidates):
        singular = _singularize(candidate)
        if singular:
            candidates.add(singular)
    return any(BUSINESS_TOPIC_ALIASES.get(candidate, candidate) in BUSINESS_TOPICS for candidate in candidates if candidate)


def _identifier_words(value: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return re.findall(r"[A-Za-z0-9]+", spaced.lower())


def _singularize(value: str) -> str:
    if len(value) > 3 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 2 and value.endswith("s"):
        return value[:-1]
    return value


def _normalize_text_words(text: str) -> str:
    return " ".join("".join(char.lower() if char.isalnum() else " " for char in text).split())


def _operator_error_response(exc: OperatorError) -> dict[str, Any]:
    if exc.code == "permission_error":
        return permission_error(exc.message, exc.details)
    return validation_error(exc.message, exc.details)


async def _rollback_quietly(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        pass
