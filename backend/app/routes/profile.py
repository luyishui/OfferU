# =============================================
# Profile 路由 — 个人档案与对话引导 API
# =============================================
# GET    /api/profile/                     获取默认档案
# PUT    /api/profile/                     更新档案主信息
# GET    /api/profile/target-roles         获取目标岗位
# POST   /api/profile/target-roles         创建目标岗位
# DELETE /api/profile/target-roles/{id}    删除目标岗位
# POST   /api/profile/sections             手动新增条目
# PUT    /api/profile/sections/{id}        更新条目
# DELETE /api/profile/sections/{id}        删除条目
# POST   /api/profile/chat                 SSE 对话引导并产出 bullet 候选
# GET    /api/profile/chat/sessions        会话列表
# GET    /api/profile/chat/sessions/{id}   会话详情
# POST   /api/profile/chat/confirm         确认候选并入库
# POST   /api/profile/import-resume        上传简历并提取候选条目
# POST   /api/profile/generate-narrative   生成叙事字段
# =============================================

from __future__ import annotations

import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import chat_completion, extract_json
from app.database import get_db
from app.models.models import (
    Profile,
    ProfileChatSession,
    ProfileSection,
    ProfileTargetRole,
)
from app.services.profile_schema import (
    PROFILE_BUILTIN_SECTION_TYPES,
    canonicalize_profile_section_payload,
    get_category_label,
    is_custom_category_key,
    is_valid_profile_section_type,
    normalize_base_info_payload,
    normalize_section_type_alias,
)

try:
    from app.agents.skills.conversational_extractor import generate_instant_draft as _generate_instant_draft
except Exception:
    _generate_instant_draft = None

router = APIRouter()

VALID_TOPICS = {"education", "experience", "project", "activity", "skill", "general"}
VALID_FITS = {"primary", "secondary", "adjacent"}
VALID_SECTION_TYPES = set(PROFILE_BUILTIN_SECTION_TYPES).union(
    {"general", "custom", "internship", "activity", "competition", "honor", "language"}
)
PROFILE_CATEGORY_ORDER = ["education", "experience", "project", "skill", "certificate"]
ALLOWED_RESUME_IMPORT_EXTENSIONS = {".pdf", ".docx"}
MAX_RESUME_IMPORT_FILE_SIZE = 10 * 1024 * 1024


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    headline: Optional[str] = Field(default=None, max_length=300)
    exit_story: Optional[str] = None
    cross_cutting_advantage: Optional[str] = None
    base_info_json: Optional[dict] = None


class TargetRoleCreateRequest(BaseModel):
    role_name: str = Field(..., min_length=1, max_length=120)
    role_level: str = Field(default="", max_length=60)
    fit: str = Field(default="primary")


class ProfileSectionCreateRequest(BaseModel):
    section_type: str = Field(..., min_length=1, max_length=60)
    category_label: Optional[str] = Field(default=None, max_length=80)
    title: str = Field(default="", max_length=220)
    sort_order: int = 0
    content_json: dict = Field(default_factory=dict)
    source: str = Field(default="manual", max_length=30)
    confidence: float = Field(default=1.0, ge=0, le=1)


class ProfileSectionUpdateRequest(BaseModel):
    section_type: Optional[str] = Field(default=None, min_length=1, max_length=60)
    category_label: Optional[str] = Field(default=None, max_length=80)
    title: Optional[str] = Field(default=None, max_length=220)
    sort_order: Optional[int] = None
    content_json: Optional[dict] = None
    source: Optional[str] = Field(default=None, max_length=30)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class ProfileChatRequest(BaseModel):
    topic: str = Field(default="general")
    message: str = Field(..., min_length=1)
    session_id: Optional[int] = None


class ProfileChatConfirmRequest(BaseModel):
    session_id: int
    bullet_index: int = Field(..., ge=0)
    edits: Optional[dict] = None


class InstantDraftRequest(BaseModel):
    experiences: list[str] = Field(..., min_length=1, max_length=20)
    target_roles: list[str] = Field(default_factory=list)


def _serialize_target_role(role: ProfileTargetRole) -> dict:
    return {
        "id": role.id,
        "profile_id": role.profile_id,
        "role_name": role.role_name,
        "role_level": role.role_level,
        "fit": role.fit,
        "created_at": str(role.created_at),
    }


def _serialize_section(section: ProfileSection) -> dict:
    content_json = section.content_json if isinstance(section.content_json, dict) else {}
    category_key = normalize_section_type_alias(section.section_type)
    if category_key in {"general", "activity", "competition"} or not is_valid_profile_section_type(category_key):
        category_key = "custom:c_legacy"
    category_label = get_category_label(category_key, content_json)
    field_values = content_json.get("field_values") if isinstance(content_json.get("field_values"), dict) else {}
    normalized = content_json.get("normalized") if isinstance(content_json.get("normalized"), dict) else {}

    return {
        "id": section.id,
        "profile_id": section.profile_id,
        "section_type": category_key,
        "raw_section_type": section.section_type,
        "category_key": category_key,
        "category_label": category_label,
        "is_custom_category": is_custom_category_key(category_key),
        "parent_id": section.parent_id,
        "title": section.title,
        "sort_order": section.sort_order,
        "content_json": content_json,
        "field_values": field_values,
        "normalized": normalized,
        "source": section.source,
        "confidence": section.confidence,
        "created_at": str(section.created_at),
        "updated_at": str(section.updated_at),
    }


def _serialize_profile(profile: Profile, roles: list[ProfileTargetRole], sections: list[ProfileSection]) -> dict:
    base_info_json = normalize_base_info_payload(profile.base_info_json)
    return {
        "id": profile.id,
        "name": profile.name,
        "headline": profile.headline,
        "exit_story": profile.exit_story,
        "cross_cutting_advantage": profile.cross_cutting_advantage,
        "base_info_json": base_info_json,
        "is_default": profile.is_default,
        "created_at": str(profile.created_at),
        "updated_at": str(profile.updated_at),
        "target_roles": [_serialize_target_role(item) for item in roles],
        "sections": [_serialize_section(item) for item in sections],
    }


def _serialize_chat_session(session: ProfileChatSession) -> dict:
    return {
        "id": session.id,
        "profile_id": session.profile_id,
        "topic": session.topic,
        "status": session.status,
        "extracted_bullets_count": session.extracted_bullets_count,
        "created_at": str(session.created_at),
        "updated_at": str(session.updated_at),
    }


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_last_candidates(messages_json: list[Any]) -> list[dict[str, Any]]:
    for item in reversed(messages_json or []):
        if isinstance(item, dict) and item.get("kind") == "bullet_candidates":
            candidates = item.get("candidates")
            if isinstance(candidates, list):
                return candidates
    return []


async def _get_or_create_default_profile(db: AsyncSession) -> Profile:
    existing = (
        await db.execute(
            select(Profile).where(Profile.is_default == True).order_by(Profile.id.asc())
        )
    ).scalars().first()
    if existing:
        return existing

    profile = Profile(
        name="默认档案",
        is_default=True,
        base_info_json=normalize_base_info_payload({"name": ""}),
    )
    db.add(profile)
    await db.flush()
    return profile


async def _load_profile_bundle(db: AsyncSession, profile_id: int) -> tuple[Profile, list[ProfileTargetRole], list[ProfileSection]]:
    profile = (
        await db.execute(select(Profile).where(Profile.id == profile_id))
    ).scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    roles = (
        await db.execute(
            select(ProfileTargetRole)
            .where(ProfileTargetRole.profile_id == profile_id)
            .order_by(ProfileTargetRole.created_at.desc())
        )
    ).scalars().all()

    sections = (
        await db.execute(
            select(ProfileSection)
            .where(ProfileSection.profile_id == profile_id)
            .order_by(ProfileSection.sort_order.asc(), ProfileSection.created_at.asc())
        )
    ).scalars().all()

    return profile, roles, sections


def _normalize_candidate(topic: str, candidate: dict[str, Any]) -> dict[str, Any]:
    raw_section_type = (candidate.get("section_type") or topic or "general").strip().lower()
    section_type = normalize_section_type_alias(raw_section_type)
    category_label: Optional[str] = None

    if section_type in {"general", "activity", "competition"}:
        section_type = "custom"
        category_label = "自定义分类"

    if not is_valid_profile_section_type(section_type):
        section_type = "custom"
        category_label = "自定义分类"

    title = (candidate.get("title") or "未命名条目").strip()[:220]
    content_json = candidate.get("content_json")
    if not isinstance(content_json, dict):
        raw = str(candidate.get("content") or candidate.get("bullet") or "").strip()
        content_json = {"bullet": raw}

    try:
        category_key, resolved_label, _, canonical_content_json = canonicalize_profile_section_payload(
            section_type=section_type,
            category_label=category_label,
            title=title,
            raw_content_json=content_json,
        )
    except ValueError:
        category_key, resolved_label, _, canonical_content_json = canonicalize_profile_section_payload(
            section_type="custom",
            category_label="自定义分类",
            title=title,
            raw_content_json=content_json,
        )

    confidence = candidate.get("confidence", 0.7)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.7
    confidence = min(max(confidence, 0.0), 1.0)

    return {
        "section_type": category_key,
        "category_label": resolved_label,
        "title": title,
        "content_json": canonical_content_json,
        "confidence": confidence,
    }


def _fallback_chat_payload(topic: str, user_message: str) -> dict[str, Any]:
    normalized_topic = normalize_section_type_alias(topic)
    if normalized_topic in {"general", "activity", "competition"}:
        normalized_topic = "custom"
    if not is_valid_profile_section_type(normalized_topic):
        normalized_topic = "custom"

    return {
        "assistant_message": (
            "这段已经能成为可写进简历的素材，我先帮你留一条候选。"
            "如果要写得更像样，还差几个 proof points：金额、人数/规模、你具体做的动作、最后结果。"
            "你记得哪个先补哪个。"
        ),
        "bullet_candidates": [
            {
                "section_type": normalized_topic,
                "title": "待确认经历条目",
                "content_json": {"bullet": user_message.strip()},
                "confidence": 0.55,
            }
        ],
        "topic_complete": False,
    }


def _build_profile_chat_prompt(topic: str) -> str:
    topic_label = {
        "education": "教育经历",
        "experience": "实习/工作经历",
        "project": "项目经历",
        "activity": "校园/社团/比赛经历",
        "skill": "技能与证书",
        "general": "综合档案",
    }.get(topic, "综合档案")

    return (
        f"你是 OfferU 的求职档案构建助手，也是一位职业教练。当前主题：{topic_label}。\n"
        "你的目标不是让用户机械填写表单，而是把随口说出的经历转成可复用的简历事实源。\n"
        "如果信息不够，不要硬写漂亮话；只问一个最关键追问，帮助用户补齐背景-动作-结果和 proof points。\n"
        "JSON 格式:\n"
        '{"assistant_message": "对用户的回复。先肯定素材价值，再指出还缺什么；如果足够，则说明已整理候选条目",\n'
        ' "bullet_candidates": [\n'
        '   {"section_type": "education|experience|project|skill|certificate",\n'
        '    "title": "条目标题",\n'
        '    "content_json": {见下方字段规范},\n'
        '    "confidence": 0.0-1.0}\n'
        ' ],\n'
        ' "topic_complete": false}\n\n'
        "## content_json 字段规范（必须按类型填写对应字段名）：\n"
        "education: {\"school\":学校, \"degree\":学位, \"major\":专业, \"start_date\":\"\", \"end_date\":\"\", \"gpa\":\"\", \"description\":描述, \"bullet\":一行摘要}\n"
        "experience: {\"company\":公司, \"position\":职位, \"start_date\":\"\", \"end_date\":\"\", \"description\":用•分隔的多条业绩描述, \"bullet\":一行摘要}\n"
        "project: {\"name\":项目名, \"role\":角色, \"start_date\":\"\", \"end_date\":\"\", \"description\":用•分隔的多条描述, \"bullet\":一行摘要含量化数据}\n"
        "skill: {\"category\":技能分类, \"items\":[技能1,技能2,...], \"bullet\":逗号分隔技能}\n"
        "certificate: {\"name\":证书名, \"issuer\":颁发机构, \"date\":日期, \"bullet\":一行摘要}\n\n"
        "## 核心规则：\n"
        "1. 严禁编造事实，所有数字必须来自用户原文。\n"
        "2. description 中必须保留用户提到的所有量化数据：人数、金额、时长、排名、覆盖范围、增长/下降比例。\n"
        "3. bullet 是一行浓缩摘要，格式：关键词1 | 关键词2 | 量化成果。\n"
        "4. 候选条目 1-3 条，confidence: 1.0=用户明确说了, 0.7=可直接确认, 0.5以下=需继续追问。\n"
        "5. 如果用户只说了短句，也要给出可回答的下一问，例如：你联系了多少人、拉到多少钱、活动规模多大、最后结果如何。\n"
    )


async def _generate_chat_payload(topic: str, user_message: str) -> dict[str, Any]:
    prompt = _build_profile_chat_prompt(topic)

    try:
        llm_result = await chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"topic={topic}\nuser_input={user_message}",
                },
            ],
            temperature=0.2,
            json_mode=True,
            max_tokens=1000,
            tier="standard",
        )
    except Exception:
        return _fallback_chat_payload(topic, user_message)

    parsed = extract_json(llm_result or "")
    if not isinstance(parsed, dict):
        return _fallback_chat_payload(topic, user_message)

    assistant_message = str(parsed.get("assistant_message") or "我已整理候选条目，请确认。")
    raw_candidates = parsed.get("bullet_candidates")
    if not isinstance(raw_candidates, list) or len(raw_candidates) == 0:
        return _fallback_chat_payload(topic, user_message)

    candidates = [_normalize_candidate(topic, item) for item in raw_candidates[:3] if isinstance(item, dict)]
    if not candidates:
        return _fallback_chat_payload(topic, user_message)

    return {
        "assistant_message": assistant_message,
        "bullet_candidates": candidates,
        "topic_complete": bool(parsed.get("topic_complete", False)),
    }


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?86[-\s]?)?(1[3-9]\d{9})")
URL_RE = re.compile(r"https?://[^\s，,；;]+|(?:github|linkedin)\.com/[^\s，,；;]+", re.IGNORECASE)
DATE_TOKEN_PATTERN = r"(?:19|20)\d{2}(?:[./-]\d{1,2}|年\s*\d{1,2}\s*月?)?"
DATE_RANGE_RE = re.compile(
    rf"(?P<start>{DATE_TOKEN_PATTERN})\s*(?:-|–|—|~|至|到)\s*"
    rf"(?P<end>至今|现在|目前|{DATE_TOKEN_PATTERN})",
    re.IGNORECASE,
)

SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("education", re.compile(r"^(教育经历|教育背景|学历|教育|主修课程|课程)$", re.IGNORECASE)),
    ("internship", re.compile(r"^(实习经历|实习经验|实习)$", re.IGNORECASE)),
    ("experience", re.compile(r"^(工作经历|工作经验|实践经历|社会实践|校园经历)$", re.IGNORECASE)),
    ("project", re.compile(r"^(项目经历|项目经验|项目|科研经历|研究经历)$", re.IGNORECASE)),
    ("skill", re.compile(r"^(技能|技能清单|专业技能|技能与证书|技能特长|技术栈|工具)$", re.IGNORECASE)),
    ("certificate", re.compile(r"^(证书|证书资质|语言能力|语言|英语|英语水平|语言水平|资质证书)$", re.IGNORECASE)),
    ("award", re.compile(r"^(获奖经历|荣誉奖项|奖项|荣誉|奖励)$", re.IGNORECASE)),
    ("summary", re.compile(r"^(个人简介|自我评价|个人总结|Profile|Summary)$", re.IGNORECASE)),
]


def _clean_resume_line(raw: str) -> str:
    line = (raw or "").strip().lstrip("-*•·●▪▫").strip()
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def _split_label_value(line: str) -> tuple[str, str] | None:
    match = re.match(r"^([^:：]{1,18})[:：]\s*(.+)$", line)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _section_from_heading(line: str) -> tuple[str | None, str]:
    label_value = _split_label_value(line)
    heading = label_value[0] if label_value else line
    remainder = label_value[1] if label_value else ""
    heading = heading.strip()
    for section_key, pattern in SECTION_PATTERNS:
        if pattern.match(heading):
            return section_key, remainder
    return None, ""


def _is_contact_or_base_line(line: str) -> bool:
    if EMAIL_RE.search(line) or PHONE_RE.search(line):
        return True
    label_value = _split_label_value(line)
    if not label_value:
        return False
    label = label_value[0]
    return bool(
        re.search(
            r"姓名|邮箱|邮件|电话|手机|联系方式|微信|现居|所在地|城市|地址|求职意向|期望职位|目标岗位|GitHub|LinkedIn|个人网站|网站",
            label,
            re.IGNORECASE,
        )
    )


def _extract_labeled_value(lines: list[str], pattern: str) -> str:
    regex = re.compile(pattern, re.IGNORECASE)
    for line in lines:
        label_value = _split_label_value(line)
        if label_value and regex.search(label_value[0]):
            return label_value[1].strip()
    return ""


def _extract_resume_base_info(resume_text: str) -> dict[str, str]:
    lines = [_clean_resume_line(raw) for raw in (resume_text or "").splitlines()]
    lines = [line for line in lines if line]
    joined = "\n".join(lines)

    base_info: dict[str, str] = {}

    email = EMAIL_RE.search(joined)
    if email:
        base_info["email"] = email.group(0)

    phone = PHONE_RE.search(joined)
    if phone:
        base_info["phone"] = phone.group(1)

    website_candidates = URL_RE.findall(joined)
    for url in website_candidates:
        normalized_url = url if url.startswith("http") else f"https://{url}"
        if "github.com" in normalized_url.lower() and "github" not in base_info:
            base_info["github"] = normalized_url
        elif "linkedin.com" in normalized_url.lower() and "linkedin" not in base_info:
            base_info["linkedin"] = normalized_url
        elif "website" not in base_info:
            base_info["website"] = normalized_url

    name = _extract_labeled_value(lines, r"姓名|名字")
    if not name:
        for line in lines[:8]:
            section_key, _ = _section_from_heading(line)
            if section_key or _is_contact_or_base_line(line):
                continue
            if len(line) <= 12 and not re.search(r"\d|@|简历|求职|岗位|电话|邮箱", line, re.IGNORECASE):
                name = line
                break
    if name:
        base_info["name"] = name

    city = _extract_labeled_value(lines, r"现居|所在地|城市|地址")
    if city:
        base_info["current_city"] = city

    job_intention = _extract_labeled_value(lines, r"求职意向|期望职位|目标岗位|应聘岗位")
    if job_intention:
        base_info["job_intention"] = job_intention

    summary = _extract_labeled_value(lines, r"个人简介|自我评价|个人总结|Summary|Profile")
    if not summary:
        collecting = False
        summary_lines: list[str] = []
        for line in lines:
            section_key, remainder = _section_from_heading(line)
            if section_key == "summary":
                collecting = True
                if remainder:
                    summary_lines.append(remainder)
                continue
            if collecting and section_key:
                break
            if collecting and not _is_contact_or_base_line(line):
                summary_lines.append(line)
            if len(summary_lines) >= 3:
                break
        summary = " ".join(summary_lines).strip()
    if summary:
        base_info["summary"] = summary
        base_info["personal_summary"] = summary

    return base_info


def _extract_date_range(line: str) -> tuple[str, str]:
    match = DATE_RANGE_RE.search(line)
    if not match:
        return "", ""
    return re.sub(r"\s+", "", match.group("start")), re.sub(r"\s+", "", match.group("end"))


def _first_match(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.IGNORECASE)
    return match.group(1).strip() if match else default


def _strip_heading_prefix(line: str) -> str:
    label_value = _split_label_value(line)
    if label_value:
        return label_value[1].strip()
    return re.sub(
        r"^(教育经历|教育背景|实习经历|工作经历|项目经历|技能|技能清单|证书|语言能力|获奖经历|荣誉奖项)\s*",
        "",
        line,
        flags=re.IGNORECASE,
    ).strip()


def _infer_resume_section(line: str, current_section: str | None) -> str:
    if re.search(r"六级|四级|CET|IELTS|TOEFL|雅思|托福|资格证|普通话", line, re.IGNORECASE):
        return "certificate"
    if re.search(r"获奖|奖学金|一等奖|二等奖|三等奖|优秀|荣誉", line, re.IGNORECASE):
        return "award"
    if current_section in {"education", "internship", "experience", "project", "skill", "certificate", "award"}:
        return current_section
    if re.search(r"大学|学院|学校|本科|硕士|博士|学士|大专|GPA|绩点|专业", line, re.IGNORECASE):
        return "education"
    if re.search(r"六级|四级|CET|IELTS|TOEFL|雅思|托福|证书|资格证|普通话", line, re.IGNORECASE):
        return "certificate"
    if re.search(r"Python|SQL|Excel|Office|Tableau|PowerBI|React|Java|熟练|技能", line, re.IGNORECASE):
        return "skill"
    if re.search(r"项目|系统|平台|模型|课题|研究|算法|数据分析", line, re.IGNORECASE):
        return "project"
    if re.search(r"公司|集团|银行|科技|咨询|证券|实习|运营|产品|工程师|分析师|研究员|助理|负责", line, re.IGNORECASE):
        return "experience"
    if re.search(r"获奖|奖学金|一等奖|二等奖|三等奖|优秀|荣誉", line, re.IGNORECASE):
        return "award"
    return "custom"


def _candidate_from_resume_line(line: str, current_section: str | None, index: int) -> dict[str, Any] | None:
    text = _strip_heading_prefix(line)
    if len(text) < 4 or _is_contact_or_base_line(text):
        return None

    section_key = _infer_resume_section(text, current_section)
    start_date, end_date = _extract_date_range(text)

    if section_key == "education":
        school = _first_match(r"([\w\u4e00-\u9fa5·（）() -]{2,40}(?:大学|学院|学校|University|College))", text)
        degree = _first_match(r"(博士|硕士|研究生|本科|学士|大专|专科)", text)
        gpa = _first_match(r"(?:GPA|绩点)[:：]?\s*([0-9.]+(?:/[0-9.]+)?)", text)
        major = _first_match(r"(?:专业[:：]?\s*)([\w\u4e00-\u9fa5·（）() -]{2,40})", text)
        if not major and school:
            rest = text.replace(school, " ").replace(degree, " ")
            rest = DATE_RANGE_RE.sub(" ", rest)
            rest = re.sub(r"(?:GPA|绩点)[:：]?\s*[0-9.]+(?:/[0-9.]+)?", " ", rest, flags=re.IGNORECASE).strip()
            major = rest[:40].strip(" ，,|")
        return {
            "section_type": "education",
            "title": school or "教育经历",
            "content_json": {
                "school": school or text[:40],
                "degree": degree,
                "major": major,
                "start_date": start_date,
                "end_date": end_date,
                "gpa": gpa,
                "description": text,
                "bullet": text,
            },
            "confidence": 0.62,
        }

    if section_key in {"experience", "internship"}:
        company = _first_match(
            r"([\w\u4e00-\u9fa5·（）()& -]{2,50}(?:公司|集团|银行|科技|咨询|证券|中心|研究院|实验室|事务所|有限|股份))",
            text,
        )
        compact = DATE_RANGE_RE.sub(" ", text)
        position = ""
        if company:
            tail = compact.replace(company, " ", 1).strip(" ，,|-")
            position = re.split(r"负责|参与|完成|主导|协助", tail, maxsplit=1)[0].strip(" ，,|-")
        if not position:
            position = _first_match(r"((?:产品|运营|数据|市场|研发|算法|后端|前端|财务|咨询|人力|销售|研究|分析)[\w\u4e00-\u9fa5]{0,16}(?:实习生|助理|经理|工程师|分析师|研究员)?)", text)
        is_internship = section_key == "internship" or "实习" in text or "实习" in (current_section or "")
        title = f"实习经历 - {company}" if is_internship and company else (company or ("实习经历" if is_internship else "工作经历"))
        return {
            "section_type": "experience",
            "title": title,
            "content_json": {
                "company": company or text[:40],
                "position": position,
                "start_date": start_date,
                "end_date": end_date,
                "description": text,
                "bullet": text,
            },
            "confidence": 0.62,
        }

    if section_key == "project":
        name = _first_match(r"([\w\u4e00-\u9fa5·（）() -]{2,50}(?:项目|系统|平台|模型|课题|研究))", text)
        if not name:
            name = re.split(r"负责|参与|完成|主导|协助|[:：|]", text, maxsplit=1)[0].strip()[:50]
        return {
            "section_type": "project",
            "title": name or "项目经历",
            "content_json": {
                "name": name or text[:40],
                "role": "",
                "start_date": start_date,
                "end_date": end_date,
                "description": text,
                "bullet": text,
            },
            "confidence": 0.6,
        }

    if section_key == "skill":
        cleaned = re.sub(r"^(技能|技能清单|专业技能)[:：]?", "", text, flags=re.IGNORECASE).strip()
        items = [item.strip() for item in re.split(r"[,，、；;|/\n]", cleaned) if item.strip()]
        return {
            "section_type": "skill",
            "title": "技能清单",
            "content_json": {
                "category": "技能",
                "items": items or [cleaned],
                "bullet": cleaned,
            },
            "confidence": 0.58,
        }

    if section_key == "certificate":
        return {
            "section_type": "certificate",
            "title": "证书资质",
            "content_json": {
                "name": text,
                "issuer": "",
                "date": "",
                "bullet": text,
            },
            "confidence": 0.58,
        }

    if section_key == "award":
        return {
            "section_type": "custom",
            "title": "获奖经历",
            "content_json": {
                "subtitle": "获奖经历",
                "description": text,
                "bullet": text,
            },
            "confidence": 0.55,
        }

    return {
        "section_type": "custom",
        "title": f"个人经历 {index + 1}",
        "content_json": {
            "subtitle": f"个人经历 {index + 1}",
            "description": text,
            "bullet": text,
        },
        "confidence": 0.5,
    }


def _fallback_resume_candidates(resume_text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    current_section: str | None = None
    grouped_section: str | None = None
    grouped_lines: list[str] = []

    def flush_group() -> None:
        nonlocal grouped_section, grouped_lines
        if not grouped_lines:
            return
        text = "\n".join(grouped_lines).strip()
        grouped_lines = []
        section = grouped_section
        grouped_section = None
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        candidate = _candidate_from_resume_line(text, section, len(candidates))
        if candidate:
            candidates.append(_normalize_candidate(candidate["section_type"], candidate))

    for raw in (resume_text or "").splitlines():
        line = _clean_resume_line(raw)
        if not line:
            continue
        section_key, remainder = _section_from_heading(line)
        line_section = current_section
        if section_key:
            if not remainder:
                current_section = section_key
                continue
            line_section = section_key
            line = remainder

        if line_section == "summary" or _is_contact_or_base_line(line):
            continue
        if len(line) < 4:
            continue

        if line_section in {"experience", "internship", "project"}:
            if grouped_section and grouped_section != line_section:
                flush_group()
            grouped_section = line_section
            grouped_lines.append(line)
            continue

        flush_group()

        key = line.lower()
        if key in seen:
            continue
        seen.add(key)

        candidate = _candidate_from_resume_line(line, line_section, len(candidates))
        if candidate:
            candidates.append(_normalize_candidate(candidate["section_type"], candidate))
        if len(candidates) >= 12:
            break

    flush_group()

    if not candidates and (resume_text or "").strip():
        snippet = " ".join((resume_text or "").split())
        candidates.append(
            _normalize_candidate(
                "custom",
                {
                    "section_type": "custom",
                    "title": "个人经历 1",
                    "content_json": {"subtitle": "个人经历 1", "description": snippet[:180], "bullet": snippet[:180]},
                    "confidence": 0.55,
                },
            )
        )
    return candidates


async def _extract_resume_candidates(resume_text: str) -> list[dict[str, Any]]:
    prompt = (
        "你是求职档案结构化助手。从简历文本中抽取 3-12 条可入库的事实 bullet，输出严格JSON。\n"
        "JSON 格式: {\"bullets\": [{\"section_type\": string, \"title\": string, \"content_json\": object, \"confidence\": number}]}\n\n"
        "## content_json 字段规范（必须按类型填写对应字段名）：\n"
        "education: {\"school\":学校, \"degree\":学位, \"major\":专业, \"start_date\":\"\", \"end_date\":\"\", \"gpa\":\"\", \"description\":描述, \"bullet\":一行摘要}\n"
        "experience: {\"company\":公司, \"position\":职位, \"start_date\":\"\", \"end_date\":\"\", \"description\":用•分隔的多条业绩, \"bullet\":一行摘要}\n"
        "project: {\"name\":项目名, \"role\":角色, \"start_date\":\"\", \"end_date\":\"\", \"description\":用•分隔的多条描述, \"bullet\":一行摘要含量化数据}\n"
        "skill: {\"category\":技能分类, \"items\":[技能1,技能2,...], \"bullet\":逗号分隔技能}\n"
        "certificate: {\"name\":证书名, \"issuer\":颁发机构, \"date\":日期, \"bullet\":一行摘要}\n\n"
        "section_type 仅允许 education/experience/project/activity/skill/certificate/honor/language/general。\n"
        "要求: 1) 只改写不编造；2) 所有数字必须来自原文；3) description 保留完整量化数据；4) confidence 在 0-1。"
    )
    prompt += (
        "\n\nIMPORTANT: The source may come from PDF text extraction. PDF visual line wraps are not separate resume bullets. "
        "Merge wrapped lines that belong to the same sentence or responsibility. "
        "For experience/project description, use one string. Only insert newlines or bullet markers when the original text has explicit bullet points, numbering, or clearly separate achievements. "
        "Do not create a separate candidate or description item from each visual line. "
        "Keep Chinese text as valid UTF-8; never output mojibake or question marks for Chinese characters."
    )

    try:
        llm_result = await chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": resume_text[:12000]},
            ],
            temperature=0.2,
            json_mode=True,
            max_tokens=1800,
            tier="standard",
        )
        parsed = extract_json(llm_result or "")
    except Exception:
        parsed = None

    if not isinstance(parsed, dict):
        return _fallback_resume_candidates(resume_text)

    raw_candidates = parsed.get("bullets")
    if not isinstance(raw_candidates, list) or len(raw_candidates) == 0:
        raw_candidates = parsed.get("bullet_candidates")

    if not isinstance(raw_candidates, list) or len(raw_candidates) == 0:
        return _fallback_resume_candidates(resume_text)

    candidates = [
        _normalize_candidate("general", item)
        for item in raw_candidates[:12]
        if isinstance(item, dict)
    ]
    if not candidates:
        return _fallback_resume_candidates(resume_text)
    return candidates


@router.get("/")
async def get_profile(db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)
    normalized_base_info = normalize_base_info_payload(profile.base_info_json)
    if profile.base_info_json != normalized_base_info:
        profile.base_info_json = normalized_base_info
    await db.commit()

    profile, roles, sections = await _load_profile_bundle(db, profile.id)
    return _serialize_profile(profile, roles, sections)


@router.get("/categories")
async def list_profile_categories(db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)
    sections = (
        await db.execute(
            select(ProfileSection)
            .where(ProfileSection.profile_id == profile.id)
            .order_by(ProfileSection.updated_at.desc(), ProfileSection.id.desc())
        )
    ).scalars().all()

    custom_map: dict[str, str] = {}
    for section in sections:
        category_key = normalize_section_type_alias(section.section_type)
        if category_key in {"general", "activity", "competition"} or not is_valid_profile_section_type(category_key):
            category_key = "custom:c_legacy"
        if category_key in PROFILE_BUILTIN_SECTION_TYPES:
            continue
        if not is_custom_category_key(category_key):
            continue
        if category_key in custom_map:
            continue
        content_json = section.content_json if isinstance(section.content_json, dict) else {}
        custom_map[category_key] = get_category_label(category_key, content_json)

    builtin = [
        {
            "key": key,
            "label": get_category_label(key),
            "is_custom": False,
        }
        for key in PROFILE_CATEGORY_ORDER
    ]
    custom = [
        {"key": key, "label": label, "is_custom": True}
        for key, label in sorted(custom_map.items(), key=lambda item: item[1])
    ]

    return {
        "builtin": builtin,
        "custom": custom,
        "all": [*builtin, *custom],
    }


@router.put("/")
async def update_profile(data: ProfileUpdateRequest, db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)

    payload = data.model_dump(exclude_none=True)
    if "base_info_json" in payload:
        payload["base_info_json"] = normalize_base_info_payload(payload["base_info_json"])

    for key, value in payload.items():
        setattr(profile, key, value)

    await db.commit()
    await db.refresh(profile)

    profile, roles, sections = await _load_profile_bundle(db, profile.id)
    return _serialize_profile(profile, roles, sections)


@router.get("/target-roles")
async def list_target_roles(db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)
    roles = (
        await db.execute(
            select(ProfileTargetRole)
            .where(ProfileTargetRole.profile_id == profile.id)
            .order_by(ProfileTargetRole.created_at.desc())
        )
    ).scalars().all()
    await db.commit()
    return [_serialize_target_role(item) for item in roles]


@router.post("/target-roles")
async def create_target_role(data: TargetRoleCreateRequest, db: AsyncSession = Depends(get_db)):
    fit = data.fit.strip().lower()
    if fit not in VALID_FITS:
        raise HTTPException(status_code=400, detail="fit must be primary/secondary/adjacent")

    profile = await _get_or_create_default_profile(db)

    role = ProfileTargetRole(
        profile_id=profile.id,
        role_name=data.role_name.strip(),
        role_level=data.role_level.strip(),
        fit=fit,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    return _serialize_target_role(role)


@router.delete("/target-roles/{role_id}")
async def delete_target_role(role_id: int, db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)
    role = (
        await db.execute(
            select(ProfileTargetRole).where(
                ProfileTargetRole.id == role_id,
                ProfileTargetRole.profile_id == profile.id,
            )
        )
    ).scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="Target role not found")

    await db.delete(role)
    await db.commit()
    return {"deleted": True}


@router.post("/sections")
async def create_profile_section(data: ProfileSectionCreateRequest, db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)

    try:
        normalized_section_type = normalize_section_type_alias(data.section_type)
        if normalized_section_type in {"general", "activity", "competition"}:
            normalized_section_type = "custom"
        if not is_valid_profile_section_type(normalized_section_type):
            normalized_section_type = "custom"

        section_type, _, _, canonical_content_json = canonicalize_profile_section_payload(
            section_type=normalized_section_type,
            category_label=data.category_label,
            title=data.title.strip(),
            raw_content_json=data.content_json,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid section_type")

    sort_order = data.sort_order
    if sort_order <= 0:
        max_sort = (
            await db.execute(
                select(func.max(ProfileSection.sort_order)).where(ProfileSection.profile_id == profile.id)
            )
        ).scalar()
        sort_order = int(max_sort or 0) + 1

    section = ProfileSection(
        profile_id=profile.id,
        section_type=section_type,
        title=data.title.strip() or get_category_label(section_type, canonical_content_json),
        sort_order=sort_order,
        content_json=canonical_content_json,
        source=data.source,
        confidence=data.confidence,
    )
    db.add(section)
    await db.commit()
    await db.refresh(section)
    return _serialize_section(section)


@router.put("/sections/{section_id}")
async def update_profile_section(
    section_id: int,
    data: ProfileSectionUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_or_create_default_profile(db)

    section = (
        await db.execute(
            select(ProfileSection).where(
                ProfileSection.id == section_id,
                ProfileSection.profile_id == profile.id,
            )
        )
    ).scalar_one_or_none()
    if not section:
        raise HTTPException(status_code=404, detail="Profile section not found")

    payload = data.model_dump(exclude_none=True)

    next_section_type = normalize_section_type_alias(str(payload.get("section_type") or section.section_type))
    if next_section_type in {"general", "activity", "competition"}:
        next_section_type = "custom"
    if not is_valid_profile_section_type(next_section_type):
        next_section_type = "custom"
    next_title = str(payload.get("title") or section.title or "").strip()
    next_content_json = payload.get("content_json") if "content_json" in payload else section.content_json
    next_category_label = payload.get("category_label")

    try:
        resolved_section_type, _, _, canonical_content_json = canonicalize_profile_section_payload(
            section_type=next_section_type,
            category_label=next_category_label,
            title=next_title,
            raw_content_json=next_content_json,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid section_type")

    if not next_title:
        next_title = get_category_label(resolved_section_type, canonical_content_json)

    section.section_type = resolved_section_type
    section.title = next_title
    section.content_json = canonical_content_json

    if "sort_order" in payload and payload["sort_order"] is not None:
        section.sort_order = int(payload["sort_order"])
    if "source" in payload and payload["source"] is not None:
        section.source = str(payload["source"])
    if "confidence" in payload and payload["confidence"] is not None:
        section.confidence = float(payload["confidence"])

    await db.commit()
    await db.refresh(section)
    return _serialize_section(section)


@router.delete("/sections/{section_id}")
async def delete_profile_section(section_id: int, db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)

    section = (
        await db.execute(
            select(ProfileSection).where(
                ProfileSection.id == section_id,
                ProfileSection.profile_id == profile.id,
            )
        )
    ).scalar_one_or_none()
    if not section:
        raise HTTPException(status_code=404, detail="Profile section not found")

    await db.delete(section)
    await db.commit()
    return {"deleted": True}


@router.post("/chat")
async def profile_chat(data: ProfileChatRequest, db: AsyncSession = Depends(get_db)):
    topic = data.topic.strip().lower()
    if topic not in VALID_TOPICS:
        raise HTTPException(status_code=400, detail="invalid topic")

    user_message = data.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="message is required")

    async def event_stream():
        try:
            profile = await _get_or_create_default_profile(db)

            if data.session_id is not None:
                session = (
                    await db.execute(
                        select(ProfileChatSession).where(
                            ProfileChatSession.id == data.session_id,
                            ProfileChatSession.profile_id == profile.id,
                        )
                    )
                ).scalar_one_or_none()
                if not session:
                    yield _sse("error", {"message": "chat session not found"})
                    return
            else:
                session = ProfileChatSession(
                    profile_id=profile.id,
                    topic=topic,
                    status="active",
                    messages_json=[],
                    extracted_bullets_count=0,
                )
                db.add(session)
                await db.flush()
                await db.commit()

            messages_json = list(session.messages_json or [])
            messages_json.append({"role": "user", "topic": topic, "content": user_message})

            payload = await _generate_chat_payload(topic, user_message)
            assistant_message = payload["assistant_message"]
            candidates = payload["bullet_candidates"]
            topic_complete = bool(payload.get("topic_complete", False))

            messages_json.append({"role": "assistant", "topic": topic, "content": assistant_message})
            messages_json.append({"kind": "bullet_candidates", "topic": topic, "candidates": candidates})

            session.topic = topic
            session.messages_json = messages_json
            session.extracted_bullets_count = int(session.extracted_bullets_count or 0) + len(candidates)

            await db.commit()

            yield _sse("ai_message", {"content": assistant_message, "session_id": session.id})
            for idx, candidate in enumerate(candidates):
                event_payload = {"index": idx, "session_id": session.id, **candidate}
                yield _sse("bullet_candidate", event_payload)

            if topic_complete:
                yield _sse("topic_complete", {"topic": topic, "bullets_extracted": len(candidates)})

            yield _sse("done", {"session_id": session.id})
        except Exception:
            await db.rollback()
            yield _sse("error", {"message": "profile chat failed"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/sessions")
async def list_profile_chat_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    profile = await _get_or_create_default_profile(db)
    sessions = (
        await db.execute(
            select(ProfileChatSession)
            .where(ProfileChatSession.profile_id == profile.id)
            .order_by(ProfileChatSession.updated_at.desc(), ProfileChatSession.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    await db.commit()
    return [_serialize_chat_session(item) for item in sessions]


@router.get("/chat/sessions/{session_id}")
async def get_profile_chat_session(session_id: int, db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)
    session = (
        await db.execute(
            select(ProfileChatSession).where(
                ProfileChatSession.id == session_id,
                ProfileChatSession.profile_id == profile.id,
            )
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="chat session not found")

    messages_json = list(session.messages_json or [])
    return {
        **_serialize_chat_session(session),
        "messages_json": messages_json,
        "latest_candidates": _extract_last_candidates(messages_json),
    }


@router.post("/chat/confirm")
async def confirm_profile_bullet(data: ProfileChatConfirmRequest, db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)

    session = (
        await db.execute(
            select(ProfileChatSession).where(
                ProfileChatSession.id == data.session_id,
                ProfileChatSession.profile_id == profile.id,
            )
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="chat session not found")

    candidates = _extract_last_candidates(session.messages_json or [])
    if data.bullet_index >= len(candidates):
        raise HTTPException(status_code=400, detail="bullet_index out of range")

    candidate = dict(candidates[data.bullet_index])
    if data.edits:
        for key in ("section_type", "title", "content_json", "confidence"):
            if key in data.edits:
                candidate[key] = data.edits[key]

    candidate = _normalize_candidate(session.topic or "general", candidate)

    # 幂等保护：同一档案下若已存在同类型+同标题+同内容条目，则直接返回，避免重复入库
    existing_sections = (
        await db.execute(
            select(ProfileSection)
            .where(
                ProfileSection.profile_id == profile.id,
                ProfileSection.section_type == candidate["section_type"],
                ProfileSection.title == candidate["title"],
            )
            .order_by(ProfileSection.id.desc())
        )
    ).scalars().all()
    for existing in existing_sections:
        if (existing.content_json or {}) == candidate["content_json"]:
            return _serialize_section(existing)

    max_sort = (
        await db.execute(
            select(func.max(ProfileSection.sort_order)).where(ProfileSection.profile_id == profile.id)
        )
    ).scalar()
    next_sort = int(max_sort or 0) + 1

    section = ProfileSection(
        profile_id=profile.id,
        section_type=candidate["section_type"],
        title=candidate["title"],
        sort_order=next_sort,
        content_json=candidate["content_json"],
        source="ai_chat",
        confidence=candidate["confidence"],
    )
    db.add(section)
    await db.commit()
    await db.refresh(section)

    return _serialize_section(section)


@router.post("/import-resume")
async def import_profile_resume(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")

    filename = file.filename.strip()
    lower = filename.lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_RESUME_IMPORT_EXTENSIONS):
        raise HTTPException(status_code=400, detail="unsupported file type, only .pdf/.docx")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="empty file")
    if len(file_bytes) > MAX_RESUME_IMPORT_FILE_SIZE:
        raise HTTPException(status_code=400, detail="file too large (max 10MB)")

    from app.services.resume_parser import parse_resume_file

    parsed_text = await parse_resume_file(filename, file_bytes)
    if not parsed_text or not parsed_text.strip():
        raise HTTPException(status_code=400, detail="resume text is empty")

    profile = await _get_or_create_default_profile(db)
    base_info = _extract_resume_base_info(parsed_text)
    candidates = await _extract_resume_candidates(parsed_text)

    session = ProfileChatSession(
        profile_id=profile.id,
        topic="general",
        status="completed",
        messages_json=[
            {
                "role": "assistant",
                "topic": "general",
                "content": "已从上传简历中提取候选条目，请逐条确认后入库。",
            },
            {
                "kind": "resume_import_meta",
                "filename": filename,
                "text_length": len(parsed_text),
                "base_info": base_info,
            },
            {
                "kind": "bullet_candidates",
                "topic": "general",
                "candidates": candidates,
            },
        ],
        extracted_bullets_count=len(candidates),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    bullets = [
        {
            "index": idx,
            "session_id": session.id,
            **candidate,
        }
        for idx, candidate in enumerate(candidates)
    ]

    return {
        "session_id": session.id,
        "filename": filename,
        "text_length": len(parsed_text),
        "base_info": base_info,
        "bullets": bullets,
    }


@router.post("/generate-narrative")
async def generate_narrative(db: AsyncSession = Depends(get_db)):
    profile = await _get_or_create_default_profile(db)
    sections = (
        await db.execute(
            select(ProfileSection)
            .where(ProfileSection.profile_id == profile.id)
            .order_by(ProfileSection.sort_order.asc(), ProfileSection.created_at.asc())
        )
    ).scalars().all()

    section_texts = []
    for item in sections[:30]:
        section_texts.append(f"[{item.section_type}] {item.title} {json.dumps(item.content_json, ensure_ascii=False)}")

    context_text = "\n".join(section_texts) if section_texts else "暂无条目"

    prompt = (
        "你是求职档案叙事助手。根据给定档案条目，生成严格 JSON: "
        "{\"headline\": string, \"exit_story\": string, \"cross_cutting_advantage\": string}。"
        "不要编造事实，措辞简洁。"
    )

    try:
        llm_result = await chat_completion(
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": context_text},
            ],
            temperature=0.3,
            json_mode=True,
            max_tokens=800,
            tier="fast",
        )
        parsed = extract_json(llm_result or "")
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        parsed = {
            "headline": profile.headline or "正在构建中的求职者",
            "exit_story": profile.exit_story or "基于现有经历，持续补全并打磨个人叙事。",
            "cross_cutting_advantage": profile.cross_cutting_advantage or "学习快、执行稳、可迁移能力强。",
        }

    profile.headline = str(parsed.get("headline") or profile.headline or "")
    profile.exit_story = str(parsed.get("exit_story") or profile.exit_story or "")
    profile.cross_cutting_advantage = str(
        parsed.get("cross_cutting_advantage") or profile.cross_cutting_advantage or ""
    )

    await db.commit()
    await db.refresh(profile)

    return {
        "headline": profile.headline,
        "exit_story": profile.exit_story,
        "cross_cutting_advantage": profile.cross_cutting_advantage,
    }


@router.post("/instant-draft")
async def instant_draft(data: InstantDraftRequest):
    """Step 2.5 即时价值钩子：根据经历标题快速生成简历草稿框架。"""
    experiences = [item.strip() for item in data.experiences if isinstance(item, str) and item.strip()]
    if not experiences:
        raise HTTPException(status_code=400, detail="请至少提供一段经历名称")

    target_roles = [item.strip() for item in data.target_roles if isinstance(item, str) and item.strip()]

    if _generate_instant_draft is not None:
        try:
            generated = await _generate_instant_draft(experiences=experiences, target_roles=target_roles)
            if isinstance(generated, dict) and generated:
                return generated
        except Exception:
            pass

    role_text = "、".join(target_roles) if target_roles else "通用岗位"
    sections = []
    for exp in experiences[:5]:
        sections.append(
            {
                "section_type": "project",
                "title": exp,
                "bullets": [
                    f"负责{exp}相关事项，完成关键任务，[具体成果待补充]",
                    "与团队协作推进项目落地，[数据指标待补充]",
                ],
            }
        )

    return {
        "headline": f"面向{role_text}方向的候选人",
        "sections": sections,
        "missing_hints": ["请补充量化结果（如增长、转化、效率）", "请补充时间范围和你的具体角色"],
        "encouragement": "你已经有很好的素材，补上细节后会非常有竞争力。",
    }
