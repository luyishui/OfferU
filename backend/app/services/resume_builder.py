# =============================================
# Resume Builder — 简历生成共享服务
# =============================================
# 从 optimize.py 提取的共享函数，避免 optimize_agent.py ↔ optimize.py 循环导入。
# 旧 /generate 路由栈已拆除；此处保留 profile→resume 组装与会话预填所需的
# 纯函数（rank/build/keyword），供 optimize_sessions 与其它服务复用。
# =============================================

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from typing import Iterable

import jieba

from app.models.models import Profile, ProfileSection, Resume, ResumeSection


def _profile_to_contact_json(profile: Profile) -> dict:
    info = profile.base_info_json or {}
    if not isinstance(info, dict):
        info = {}
    contact = {
        "email": info.get("email") or getattr(profile, "email", "") or "",
        "phone": info.get("phone") or getattr(profile, "phone", "") or "",
        "wechat": info.get("wechat") or getattr(profile, "wechat", "") or "",
    }
    return {key: value for key, value in contact.items() if isinstance(value, str) and value.strip()}


def _build_source_profile_snapshot(profile: Profile, selected: list[ProfileSection]) -> dict:
    return {
        "profile_id": profile.id,
        "profile_updated_at": str(profile.updated_at),
        "selected_section_ids": [item.id for item in selected],
        "selected_count": len(selected),
    }


async def _create_generated_resume(
    *,
    db,
    profile: Profile,
    title: str,
    summary: str,
    source_mode: str,
    source_job_ids: list[int],
    contact_json: dict,
    style_config: dict,
    template_id: int | None,
    source_profile_snapshot: dict,
    rows: list[dict],
) -> Resume:
    resume = Resume(
        user_name=profile.name or "默认候选人",
        title=title,
        summary=summary,
        contact_json=contact_json,
        style_config=style_config,
        template_id=template_id,
        is_primary=False,
        language="zh",
        source_mode=source_mode,
        source_job_ids=source_job_ids,
        source_profile_snapshot=source_profile_snapshot,
    )
    db.add(resume)
    await db.flush()

    for row in rows:
        db.add(
            ResumeSection(
                resume_id=resume.id,
                section_type=row["section_type"],
                sort_order=row["sort_order"],
                title=row["title"],
                visible=True,
                content_json=row["content_json"],
            )
        )

    await db.commit()
    await db.refresh(resume)
    return resume


async def generate_for_job(
    profile: Profile,
    job,
    sections: list[ProfileSection],
    db,
    reference_resume: Resume | None = None,
) -> dict:
    """单岗位定制简历生成（供 MCP / agent 复用）。

    旧 optimize.py `_generate_for_job` 的 SkillPipeline 改写段已随旧 agent 栈拆除；
    此版本做 profile→resume 的确定性组装（rank + build + create），返回匹配元数据。
    """
    jd_text = (job.raw_description or "").strip()
    if not jd_text:
        raise ValueError(f"岗位 {job.id} 缺少 JD 文本")

    ranked = rank_profile_sections(sections, jd_text, limit=12)
    selected = [item[0] for item in ranked]
    rows = build_resume_sections(selected)

    base_contact_json = (
        (reference_resume.contact_json or {})
        if reference_resume and isinstance(reference_resume.contact_json, dict)
        else _profile_to_contact_json(profile)
    )
    base_style_config = (
        (reference_resume.style_config or {})
        if reference_resume and isinstance(reference_resume.style_config, dict)
        else {}
    )
    base_template_id = reference_resume.template_id if reference_resume else None
    base_summary = profile.headline or profile.exit_story or ""
    if not base_summary and reference_resume and isinstance(reference_resume.summary, str):
        base_summary = reference_resume.summary

    resume = await _create_generated_resume(
        db=db,
        profile=profile,
        title=f"{job.company} - {job.title} 定制简历",
        summary=base_summary,
        source_mode="per_job",
        source_job_ids=[job.id],
        contact_json=base_contact_json,
        style_config=base_style_config,
        template_id=base_template_id,
        source_profile_snapshot=_build_source_profile_snapshot(profile, selected),
        rows=rows,
    )

    used_bullets = [
        {"id": s.id, "section_type": s.section_type, "title": s.title}
        for s in selected
    ]
    used_texts = [bullet_text(s) for s in selected]
    missing = missing_keywords(jd_text, used_texts)

    return {
        "job_id": job.id,
        "job_title": job.title,
        "resume_id": resume.id,
        "resume_title": resume.title,
        "used_bullets": used_bullets,
        "used_bullets_count": len(used_bullets),
        "missing_keywords": missing,
        "missing_capabilities": missing,
        "profile_hit_ratio": f"{len(selected)}/{len(sections)}",
        "match_rate": f"{len(selected)}/{len(sections)}",
        "rewrite_applied": False,
        "pipeline": {},
    }


# ---- Profile → Resume 组装辅助（自 optimize.py 迁移）----

STOPWORDS = {
    "and", "the", "for", "with", "you", "your", "that", "this", "have",
    "from", "will", "are", "was", "our",
    "职位", "岗位", "负责", "要求", "能力", "熟悉", "相关", "以上", "优先", "具备",
}

SECTION_TYPE_MAP = {
    "education": "education",
    "experience": "experience",
    "internship": "experience",
    "custom:c_internship": "experience",
    "project": "project",
    "activity": "custom",
    "competition": "custom",
    "skill": "skill",
    "certificate": "skill",
    "language": "skill",
    "honor": "custom",
    "general": "custom",
    "custom": "custom",
    "custom:c_awards": "custom",
    "custom:c_personal": "custom",
    "custom:c_generic": "custom",
}

SECTION_TITLE_MAP = {
    "education": "教育经历",
    "experience": "实践经历",
    "project": "项目经历",
    "skill": "技能清单",
    "custom": "补充亮点",
}


def _to_tokens(text: str) -> list[str]:
    text = (text or "").lower()
    en_words = re.findall(r"[a-zA-Z][a-zA-Z0-9]*", text)
    cn_text = re.sub(r"[a-zA-Z0-9]+", " ", text)
    cn_words = [w for w in jieba.cut(cn_text) if len(w) >= 2]
    words = en_words + cn_words
    return [w for w in words if w not in STOPWORDS]


def bullet_text(section: ProfileSection) -> str:
    payload = section.content_json or {}
    if isinstance(payload, dict):
        bullet = payload.get("bullet")
        if isinstance(bullet, str) and bullet.strip():
            return bullet.strip()
    return section.title or ""


def rank_profile_sections(
    sections: list[ProfileSection], jd_text: str, limit: int = 12
) -> list[tuple[ProfileSection, int]]:
    jd_tokens = set(_to_tokens(jd_text))
    scored: list[tuple[ProfileSection, int, float]] = []

    for section in sections:
        text = f"{section.title} {bullet_text(section)}"
        overlap = len(jd_tokens.intersection(set(_to_tokens(text))))
        scored.append((section, overlap, float(section.confidence or 0.0)))

    scored.sort(key=lambda item: (item[1], item[2]), reverse=True)
    picked = scored[:limit] if scored else []

    if picked and picked[0][1] <= 0:
        scored.sort(key=lambda item: item[2], reverse=True)
        picked = scored[:limit]

    return [(section, overlap) for section, overlap, _ in picked]


def keywords_from_bullets(texts: Iterable[str], limit: int = 10) -> list[str]:
    words: list[str] = []
    for text in texts:
        words.extend(_to_tokens(text))
    if not words:
        return []
    counter = Counter(words)
    return [token for token, _ in counter.most_common(limit)]


def missing_keywords(job_text: str, used_texts: Iterable[str], limit: int = 8) -> list[str]:
    job_counter = Counter(_to_tokens(job_text))
    used = set(_to_tokens(" ".join(used_texts)))
    missing = [token for token, _ in job_counter.most_common() if token not in used]
    return missing[:limit]


def build_resume_sections(selected: list[ProfileSection]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)

    for section in selected:
        mapped = SECTION_TYPE_MAP.get((section.section_type or "general").lower(), "custom")
        bullet = bullet_text(section)

        if mapped == "education":
            payload = section.content_json or {}
            normalized = payload.get("normalized") if isinstance(payload, dict) else {}
            if not isinstance(normalized, dict):
                normalized = {}
            grouped[mapped].append(
                {
                    "school": normalized.get("school") or section.title or "教育经历",
                    "degree": normalized.get("degree", ""),
                    "major": normalized.get("major", ""),
                    "description": normalized.get("description") or bullet,
                }
            )
            continue

        if mapped == "experience":
            payload = section.content_json or {}
            normalized = payload.get("normalized") if isinstance(payload, dict) else {}
            if not isinstance(normalized, dict):
                normalized = {}
            grouped[mapped].append(
                {
                    "company": normalized.get("company") or section.title or "实践经历",
                    "position": normalized.get("position", ""),
                    "description": normalized.get("description") or bullet,
                }
            )
            continue

        if mapped == "project":
            payload = section.content_json or {}
            normalized = payload.get("normalized") if isinstance(payload, dict) else {}
            if not isinstance(normalized, dict):
                normalized = {}
            grouped[mapped].append(
                {
                    "name": normalized.get("name") or section.title or "项目经历",
                    "role": normalized.get("role", ""),
                    "description": normalized.get("description") or bullet,
                }
            )
            continue

        if mapped == "skill":
            payload = section.content_json or {}
            normalized = payload.get("normalized") if isinstance(payload, dict) else None
            items = (normalized.get("items") if isinstance(normalized, dict) else None) or []
            if not items:
                items = keywords_from_bullets([bullet], limit=8)
            if not items:
                items = [bullet] if bullet else []
            grouped[mapped].append(
                {
                    "category": section.title or "核心技能",
                    "items": items,
                }
            )
            continue

        grouped[mapped].append(
            {
                "subtitle": section.title or "补充亮点",
                "description": bullet,
            }
        )

    ordered_types = ["education", "experience", "project", "skill", "custom"]
    rows: list[dict] = []
    for index, section_type in enumerate(ordered_types):
        content = grouped.get(section_type)
        if not content:
            continue
        rows.append(
            {
                "section_type": section_type,
                "title": SECTION_TITLE_MAP[section_type],
                "sort_order": index,
                "visible": True,
                "content_json": content,
            }
        )
    return rows
