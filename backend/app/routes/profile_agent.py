# =============================================
# Profile Agent 路由 — 档案构建对话（已收敛到统一 orchestrator）
# =============================================
# 第二套独立 chat loop（直接 chat_completion + ProfileChatSession 自管会话）已拆除。
# 会话统一走 app.agent.orchestrator.run_agent_turn，写入统一走 operator proposal
# 门（profile_agent_apply_patch / profile_section / profile_target_role 等）。
# 本文件只保留：
#   - /start     解析简历/目标 → 以 profile-cleanup 技能启动一个 orchestrator 会话
#   - /message   向该会话追加用户消息并跑一轮 orchestrator
#   - /sessions* 会话列表/详情（由通用 harness 会话支撑）
#   - build_personal_archive_from_agent_patch 等纯函数，仍被 proposals.py 复用
# =============================================

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import orchestrator
from app.agent.messages import create_custom_message
from app.agent.types import AgentMessage
from app.database import async_session, get_db
from app.models import models
from app.operator.guards import ActorContext, json_safe
from app.operator.public_redaction import redact_public_payload
from app.operator.session_authority import (
    AUTHORITY_STATE_KEY,
    BROWSER_PRINCIPAL_COOKIE_PATH,
    SessionAuthorityError,
    bind_session_authority,
    issue_principal_token,
    verify_principal_token,
)
from app.routes._agent_sse import agent_sse_response
from app.routes.profile import (
    _extract_resume_base_info,
    _extract_resume_candidates,
)
from app.services.profile_builder_agent import (
    build_initial_agent_state,
    normalize_profile_agent_patch,
)
from app.services.resume_parser import parse_resume_file
from app.services.harness_history import get_conversation, list_conversations

router = APIRouter()

MAX_AGENT_RESUME_FILE_SIZE = 10 * 1024 * 1024
PROFILE_AGENT_TOPIC = "profile_builder"
PERSONAL_ARCHIVE_SCHEMA_VERSION = "personal.archive.v1"
PROFILE_AGENT_SKILL = "profile-cleanup"
_PROFILE_SESSION_PREFIX = "profile_agent_"

_BROWSER_PRINCIPAL_COOKIE = "offeru_browser_principal"
_BROWSER_PRINCIPAL_MAX_AGE = 60 * 60 * 24 * 365


class ProfileAgentMessageRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)
    message: str = Field(..., min_length=1, max_length=8000)




def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_as_str(item) for item in value if _as_str(item)]
    text = _as_str(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,，、；;\n|]+", text) if item.strip()]


_DESCRIPTION_BULLET_RE = re.compile(r"^\s*(?:[•·●▪◦*+-]|\d+[.)、]|[（(]?\d+[）)])\s*")


def _description_items(value: Any) -> list[str]:
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_description_items(item))
        return items

    text = _as_str(value)
    if not text:
        return []

    raw_lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    if not raw_lines:
        return []

    has_explicit_bullets = any(_DESCRIPTION_BULLET_RE.match(line) for line in raw_lines)
    if not has_explicit_bullets:
        return [re.sub(r"\s+", " ", " ".join(raw_lines)).strip()]

    items: list[str] = []
    current = ""
    for line in raw_lines:
        if _DESCRIPTION_BULLET_RE.match(line):
            if current:
                items.append(current.strip())
            current = _DESCRIPTION_BULLET_RE.sub("", line).strip()
        elif current:
            current = f"{current} {line}".strip()
        else:
            current = line

    if current:
        items.append(current.strip())
    return [item for item in items if item]


def _archive_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _descriptions(value: Any, fallback: str = "") -> list[str]:
    lines = _description_items(value)
    if not lines and fallback:
        lines = _description_items(fallback)
    return lines or [""]


def _default_resume_archive() -> dict[str, Any]:
    return {
        "basicInfo": {
            "name": "",
            "phone": "",
            "email": "",
            "currentCity": "",
            "jobIntention": "",
            "website": "",
            "github": "",
        },
        "personalSummary": "",
        "education": [],
        "workExperiences": [],
        "internshipExperiences": [],
        "projects": [],
        "skills": [],
        "certificates": [],
        "awards": [],
        "personalExperiences": [],
    }


def _default_application_archive(resume_archive: dict[str, Any]) -> dict[str, Any]:
    return {
        "shared": _copy_json(resume_archive),
        "identityContact": {
            "chineseName": _as_str(resume_archive.get("basicInfo", {}).get("name")),
            "englishOrPinyinName": "",
            "phone": _as_str(resume_archive.get("basicInfo", {}).get("phone")),
            "email": _as_str(resume_archive.get("basicInfo", {}).get("email")),
            "gender": "",
            "birthDate": "",
            "nationalityOrRegion": "",
            "idType": "",
            "idNumber": "",
            "currentCity": _as_str(resume_archive.get("basicInfo", {}).get("currentCity")),
            "currentAddress": "",
            "nativePlace": "",
            "householdRegistration": "",
            "ethnicity": "",
            "politicalStatus": "",
            "maritalStatus": "",
        },
        "jobPreference": {
            "expectedPosition": _as_str(resume_archive.get("basicInfo", {}).get("jobIntention")),
            "expectedPositionCategory": "",
            "expectedCities": [
                _as_str(resume_archive.get("basicInfo", {}).get("currentCity"))
            ]
            if _as_str(resume_archive.get("basicInfo", {}).get("currentCity"))
            else [],
            "expectedSalary": "",
            "employmentType": "",
            "availableStartDate": "",
            "currentJobSearchStatus": "",
            "acceptAdjustment": "",
            "acceptBusinessTravel": "",
            "acceptAssignment": "",
            "acceptShiftWork": "",
        },
        "campusFields": {
            "isFreshGraduate": "",
            "graduationDate": "",
            "studentOrigin": "",
            "studentStatus": "",
            "studentId": "",
            "gpa": "",
            "majorRank": "",
            "transcriptRef": None,
            "thesis": "",
            "patent": "",
            "researchExperiences": [],
            "internshipCertificateRef": None,
        },
        "relationshipCompliance": {
            "familyMembers": [],
            "hasRelativeInTargetCompany": "",
            "relativeName": "",
            "relativeRelation": "",
            "relativeDepartment": "",
            "emergencyContactName": "",
            "emergencyContactRelation": "",
            "emergencyContactPhone": "",
            "backgroundCheckAuthorization": "",
            "hasNonCompete": "",
            "healthDeclaration": "",
        },
        "sourceReferral": {
            "sourceChannel": "",
            "referralCode": "",
            "referralName": "",
            "referralEmployeeId": "",
            "referralContact": "",
            "recommenderInfo": "",
            "notes": "",
        },
        "attachments": {
            "resumeZh": None,
            "resumeEn": None,
            "idPhoto": None,
            "lifePhoto": None,
            "transcript": None,
            "graduationCertificate": None,
            "degreeCertificate": None,
            "chsiMaterials": None,
            "internshipCertificate": None,
            "professionalCertificates": None,
            "otherAttachments": [],
        },
    }


def _default_personal_archive() -> dict[str, Any]:
    resume_archive = _default_resume_archive()
    return {
        "schemaVersion": PERSONAL_ARCHIVE_SCHEMA_VERSION,
        "updatedAt": _now_iso(),
        "resumeArchive": resume_archive,
        "applicationArchive": _default_application_archive(resume_archive),
        "syncSettings": {
            "autoSyncEnabled": True,
            "overriddenFieldPaths": [],
        },
    }


def _valid_personal_archive(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schemaVersion") != PERSONAL_ARCHIVE_SCHEMA_VERSION:
        return None
    return _copy_json(value)


def _section_normalized(item: dict[str, Any]) -> dict[str, Any]:
    content = item.get("content_json") if isinstance(item.get("content_json"), dict) else {}
    normalized = content.get("normalized") if isinstance(content.get("normalized"), dict) else None
    return normalized if isinstance(normalized, dict) else content


def _append_unique(target: list[dict[str, Any]], entry: dict[str, Any], identity_keys: tuple[str, ...]) -> None:
    identity = tuple(_as_str(entry.get(key)) for key in identity_keys)
    if any(tuple(_as_str(existing.get(key)) for key in identity_keys) == identity for existing in target):
        return
    target.append(entry)


def _merge_archive_section(resume_archive: dict[str, Any], section: dict[str, Any]) -> None:
    if not isinstance(section, dict):
        return
    section_type = _as_str(section.get("section_type")).lower()
    title = _as_str(section.get("title"))
    content = section.get("content_json") if isinstance(section.get("content_json"), dict) else {}
    normalized = _section_normalized(section)
    category_label = _as_str(section.get("category_label") or content.get("category_label"))
    hint = f"{section_type} {title} {category_label}".lower()
    bullet = _as_str(content.get("bullet"))

    if section_type == "education":
        entry = {
            "id": _archive_id("edu", title + json.dumps(normalized, ensure_ascii=False)),
            "schoolName": _as_str(normalized.get("school") or normalized.get("school_name") or title),
            "educationLevel": _as_str(normalized.get("degree")),
            "degree": _as_str(normalized.get("degree")),
            "major": _as_str(normalized.get("major")),
            "startDate": _as_str(normalized.get("start_date")),
            "endDate": _as_str(normalized.get("end_date")),
            "gpa": _as_str(normalized.get("gpa")),
            "relatedCourses": _as_str_list(normalized.get("related_courses")),
            "descriptions": _descriptions(normalized.get("description"), bullet),
        }
        _append_unique(resume_archive["education"], entry, ("schoolName", "degree", "major"))
        return

    if section_type == "experience":
        entry = {
            "id": _archive_id("intern" if "实习" in hint else "work", title + json.dumps(normalized, ensure_ascii=False)),
            "companyName": _as_str(normalized.get("company") or title),
            "positionName": _as_str(normalized.get("position")),
            "startDate": _as_str(normalized.get("start_date")),
            "endDate": _as_str(normalized.get("end_date")),
            "descriptions": _descriptions(normalized.get("description"), bullet),
        }
        if "实习" in hint or "intern" in hint:
            _append_unique(resume_archive["internshipExperiences"], entry, ("companyName", "positionName"))
        else:
            entry["department"] = _as_str(normalized.get("department"))
            _append_unique(resume_archive["workExperiences"], entry, ("companyName", "positionName"))
        return

    if section_type == "project":
        entry = {
            "id": _archive_id("proj", title + json.dumps(normalized, ensure_ascii=False)),
            "projectName": _as_str(normalized.get("name") or title),
            "projectRole": _as_str(normalized.get("role")),
            "startDate": _as_str(normalized.get("start_date")),
            "endDate": _as_str(normalized.get("end_date")),
            "projectLink": _as_str(normalized.get("url")),
            "descriptions": _descriptions(normalized.get("description"), bullet),
        }
        _append_unique(resume_archive["projects"], entry, ("projectName", "projectRole"))
        return

    if section_type == "skill":
        skills = _as_str_list(normalized.get("items")) or _as_str_list(bullet) or [_as_str(normalized.get("category") or title)]
        for skill_name in skills:
            entry = {
                "id": _archive_id("skill", skill_name),
                "skillName": skill_name,
                "proficiency": "",
                "remark": "",
            }
            _append_unique(resume_archive["skills"], entry, ("skillName",))
        return

    if section_type == "certificate":
        entry = {
            "id": _archive_id("cert", title + json.dumps(normalized, ensure_ascii=False)),
            "certificateName": _as_str(normalized.get("name") or title),
            "scoreOrLevel": _as_str(normalized.get("score")),
            "acquiredAt": _as_str(normalized.get("date")),
            "issuer": _as_str(normalized.get("issuer")),
        }
        _append_unique(resume_archive["certificates"], entry, ("certificateName", "issuer"))
        return

    if "award" in hint or "奖" in hint:
        entry = {
            "id": _archive_id("award", title + bullet),
            "awardName": title or "获奖经历",
            "issuer": _as_str(normalized.get("issuer")),
            "awardedAt": _as_str(normalized.get("date")),
            "descriptions": _descriptions(normalized.get("description"), bullet),
        }
        _append_unique(resume_archive["awards"], entry, ("awardName", "issuer"))
        return

    entry = {
        "id": _archive_id("personal", title + bullet),
        "experienceTitle": title or "个人经历",
        "startDate": _as_str(normalized.get("start_date")),
        "endDate": _as_str(normalized.get("end_date")),
        "descriptions": _descriptions(normalized.get("description"), bullet),
    }
    _append_unique(resume_archive["personalExperiences"], entry, ("experienceTitle",))


def build_personal_archive_from_agent_patch(
    *,
    existing_base_info: dict[str, Any] | None,
    patch: dict[str, Any],
    existing_archive: dict[str, Any] | None = None,
) -> dict[str, Any]:
    archive = _valid_personal_archive(existing_archive) or _default_personal_archive()
    resume_archive = archive.get("resumeArchive") if isinstance(archive.get("resumeArchive"), dict) else {}
    if not resume_archive:
        resume_archive = _default_resume_archive()
        archive["resumeArchive"] = resume_archive

    base = existing_base_info if isinstance(existing_base_info, dict) else {}
    patch_base = patch.get("base_info") if isinstance(patch.get("base_info"), dict) else {}
    merged_base = {**base, **patch_base}
    basic = resume_archive.setdefault("basicInfo", _default_resume_archive()["basicInfo"])
    basic["name"] = _as_str(merged_base.get("name") or basic.get("name"))
    basic["phone"] = _as_str(merged_base.get("phone") or basic.get("phone"))
    basic["email"] = _as_str(merged_base.get("email") or basic.get("email"))
    basic["currentCity"] = _as_str(merged_base.get("current_city") or merged_base.get("currentCity") or basic.get("currentCity"))
    basic["jobIntention"] = _as_str(
        merged_base.get("job_intention")
        or merged_base.get("jobIntention")
        or (patch.get("target_roles") or [""])[0]
        or basic.get("jobIntention")
    )
    basic["website"] = _as_str(merged_base.get("website") or basic.get("website"))
    basic["github"] = _as_str(merged_base.get("github") or basic.get("github"))
    resume_archive["personalSummary"] = _as_str(
        merged_base.get("summary")
        or merged_base.get("personal_summary")
        or resume_archive.get("personalSummary")
    )

    for key in (
        "education",
        "workExperiences",
        "internshipExperiences",
        "projects",
        "skills",
        "certificates",
        "awards",
        "personalExperiences",
    ):
        if not isinstance(resume_archive.get(key), list):
            resume_archive[key] = []

    for section in patch.get("sections") or []:
        _merge_archive_section(resume_archive, section)

    archive["schemaVersion"] = PERSONAL_ARCHIVE_SCHEMA_VERSION
    archive["updatedAt"] = _now_iso()
    archive["resumeArchive"] = resume_archive
    archive["applicationArchive"] = _default_application_archive(resume_archive)
    sync_settings = archive.get("syncSettings") if isinstance(archive.get("syncSettings"), dict) else {}
    archive["syncSettings"] = {
        "autoSyncEnabled": bool(sync_settings.get("autoSyncEnabled", True)),
        "overriddenFieldPaths": sync_settings.get("overriddenFieldPaths")
        if isinstance(sync_settings.get("overriddenFieldPaths"), list)
        else [],
    }
    return archive


# ---------------------------------------------------------------------------
# Orchestrator-bound profile-agent routes
# ---------------------------------------------------------------------------


def _set_browser_principal_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _BROWSER_PRINCIPAL_COOKIE,
        token,
        max_age=_BROWSER_PRINCIPAL_MAX_AGE,
        httponly=True,
        secure=False,
        samesite="lax",
        path=BROWSER_PRINCIPAL_COOKIE_PATH,
    )


def _authenticated_subject(request: Request) -> str:
    try:
        return verify_principal_token(request.cookies.get(_BROWSER_PRINCIPAL_COOKIE))
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def _profile_agent_actor_for_request(
    db: AsyncSession,
    request: Request,
    session_id: str,
    *,
    allow_create: bool,
) -> tuple[ActorContext, str | None]:
    token = request.cookies.get(_BROWSER_PRINCIPAL_COOKIE)
    issued_token: str | None = None
    if token:
        try:
            subject = verify_principal_token(token)
        except SessionAuthorityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    elif allow_create:
        issued_token, subject = issue_principal_token()
    else:
        raise HTTPException(status_code=401, detail="authenticated browser principal is required")
    try:
        actor = await bind_session_authority(
            db, session_id=session_id, auth_subject=subject, allow_create=allow_create
        )
    except SessionAuthorityError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return actor, issued_token


def _session_authority_subject(row: Any) -> str:
    """Read the auth_subject recorded by bind_session_authority on an AgentSession row."""
    state = dict(getattr(row, "state_json", None) or {})
    authority = state.get(AUTHORITY_STATE_KEY)
    if not isinstance(authority, dict):
        return ""
    return str(authority.get("auth_subject") or "")


def _profile_conversation_id(value: str | None = None) -> str:
    clean = str(value or "").strip()
    if clean:
        return clean
    return f"{_PROFILE_SESSION_PREFIX}{uuid.uuid4().hex[:12]}"


def _profile_start_context_message(
    *,
    filename: str,
    source_text: str,
    target_role: str,
    target_city: str,
    job_goal: str,
    base_info: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> AgentMessage:
    state = build_initial_agent_state(
        resume_text=source_text,
        target_role=target_role,
        target_city=target_city,
        job_goal=job_goal,
        extracted_base_info=base_info,
        resume_candidates=candidates,
    )
    seed_patch = normalize_profile_agent_patch(
        {
            "action": "propose_patch" if (base_info or candidates or target_role) else "ask_user",
            "assistant_message": "",
            "base_info": base_info,
            "target_roles": [target_role] if target_role else [],
            "sections": candidates,
            "next_question": state.get("next_question") or "",
            "confidence": 0.75,
        }
    )
    context = {
        "kind": "profile_agent_start",
        "agent": PROFILE_AGENT_TOPIC,
        "filename": filename,
        "resume_text_length": len(source_text),
        "target_role": target_role,
        "target_city": target_city,
        "job_goal": job_goal,
        "state": state,
        "seed_patch": seed_patch,
    }
    instruction = (
        "你是 OfferU 的 AI 建档助手。用户刚提交了简历/目标岗位，系统已解析出初步候选。\n"
        "请基于下面的结构化上下文，用中文回复用户：\n"
        "1. 简要说明你已整理出一版档案草稿；\n"
        "2. 如果 seed_patch 里有可入库的 base_info/target_roles/sections，调用 invoke_action "
        "profile_agent_apply_patch 把它们作为 proposal 提出（需要用户确认后才会写入）；\n"
        "3. 结尾只问一个最关键的追问（优先围绕 state.missing_fields / next_question）。\n"
        "不要编造事实；所有数字必须来自用户提供的材料。\n\n"
        f"上下文:\n{json.dumps(context, ensure_ascii=False)}"
    )
    return create_custom_message(
        "profile_agent_start_context",
        instruction,
        display=False,
        details={"source": "profile_agent_start"},
    )


def _public_turn_response(result: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    public = orchestrator.public_agent_response(result)
    payload = json_safe(redact_public_payload(public))
    payload["session_id"] = conversation_id
    return payload


async def _parse_uploaded_resume(file: UploadFile | None) -> tuple[str, str]:
    if file is None or not file.filename:
        return "", ""

    filename = file.filename.strip()
    lower = filename.lower()
    if not (lower.endswith(".pdf") or lower.endswith(".docx") or lower.endswith(".txt")):
        raise HTTPException(status_code=400, detail="unsupported file type, only .pdf/.docx/.txt")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")
    if len(file_bytes) > MAX_AGENT_RESUME_FILE_SIZE:
        raise HTTPException(status_code=400, detail="file too large (max 10MB)")

    if lower.endswith(".txt"):
        return filename, file_bytes.decode("utf-8", errors="ignore")

    parsed_text = await parse_resume_file(filename, file_bytes)
    if not parsed_text or not parsed_text.strip():
        raise HTTPException(status_code=400, detail="resume text is empty")
    return filename, parsed_text


@router.post("/start")
async def start_profile_agent(
    request: Request,
    response: Response,
    target_role: str = Form(default=""),
    target_city: str = Form(default=""),
    job_goal: str = Form(default=""),
    resume_text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    db: AsyncSession = Depends(get_db),
):
    filename, parsed_text = await _parse_uploaded_resume(file)
    source_text = (parsed_text or resume_text or "").strip()

    base_info = _extract_resume_base_info(source_text) if source_text else {}
    candidates = await _extract_resume_candidates(source_text) if source_text else []

    conversation_id = _profile_conversation_id()
    actor, issued_token = await _profile_agent_actor_for_request(
        db, request, conversation_id, allow_create=True
    )
    await db.commit()
    if issued_token:
        _set_browser_principal_cookie(response, issued_token)

    context_message = _profile_start_context_message(
        filename=filename,
        source_text=source_text,
        target_role=target_role.strip(),
        target_city=target_city.strip(),
        job_goal=job_goal.strip(),
        base_info=base_info,
        candidates=candidates,
    )
    result = await orchestrator.run_agent_turn(
        db,
        actor,
        None,
        conversation_id,
        injected_messages=[context_message],
        preactivated_skill=PROFILE_AGENT_SKILL,
    )
    return _public_turn_response(result, conversation_id)


@router.post("/message")
async def continue_profile_agent(
    data: ProfileAgentMessageRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    conversation_id = _profile_conversation_id(data.session_id)
    actor, _ = await _profile_agent_actor_for_request(
        db, request, conversation_id, allow_create=False
    )
    user_message = data.message.strip()
    result = await orchestrator.run_agent_turn(
        db,
        actor,
        user_message,
        conversation_id,
        preactivated_skill=PROFILE_AGENT_SKILL,
    )
    return _public_turn_response(result, conversation_id)


@router.post("/message/stream")
async def continue_profile_agent_stream(
    data: ProfileAgentMessageRequest,
    request: Request,
):
    conversation_id = _profile_conversation_id(data.session_id)
    browser_token = request.cookies.get(_BROWSER_PRINCIPAL_COOKIE)
    if browser_token:
        try:
            auth_subject = verify_principal_token(browser_token)
        except SessionAuthorityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
    else:
        raise HTTPException(status_code=401, detail="authenticated browser principal is required")

    if orchestrator.is_session_busy(
        conversation_id,
        actor_id=str(models.LOCAL_DEFAULT_ACTOR_ID),
    ):
        raise HTTPException(
            status_code=409,
            detail=json_safe(orchestrator.session_busy_response(conversation_id)),
        )
    user_message = data.message.strip()

    async def run(event_sink):
        async with async_session() as stream_db:
            try:
                bound_actor = await bind_session_authority(
                    stream_db,
                    session_id=conversation_id,
                    auth_subject=auth_subject,
                    allow_create=False,
                )
            except SessionAuthorityError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            await stream_db.commit()
            return await orchestrator.run_agent_turn(
                stream_db,
                bound_actor,
                user_message,
                conversation_id,
                event_sink=event_sink,
                preactivated_skill=PROFILE_AGENT_SKILL,
            )

    return agent_sse_response(run)


@router.get("/sessions")
async def list_profile_agent_sessions(
    request: Request,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    auth_subject = _authenticated_subject(request)
    rows = list((await db.execute(select(models.AgentSession))).scalars().all())
    owned = {
        str(row.session_id)
        for row in rows
        if _session_authority_subject(row) == auth_subject
        and str(row.session_id or "").startswith(_PROFILE_SESSION_PREFIX)
    }
    conversations = [
        conversation
        for conversation in list_conversations()
        if str(conversation.get("id") or "") in owned
    ]
    return {"sessions": conversations[: max(1, min(int(limit or 20), 100))]}


@router.get("/sessions/{session_id}")
async def get_profile_agent_session(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    auth_subject = _authenticated_subject(request)
    row = await db.get(models.AgentSession, str(session_id))
    if row is None or _session_authority_subject(row) != auth_subject:
        raise HTTPException(status_code=404, detail="profile agent session not found")
    if not str(session_id).startswith(_PROFILE_SESSION_PREFIX):
        raise HTTPException(status_code=404, detail="profile agent session not found")
    conversation = get_conversation(str(session_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail="profile agent session not found")
    return {
        "id": conversation.get("id"),
        "status": "active",
        "title": conversation.get("title"),
        "messages_json": conversation.get("messages") or [],
    }


__all__ = ["router", "build_personal_archive_from_agent_patch"]
