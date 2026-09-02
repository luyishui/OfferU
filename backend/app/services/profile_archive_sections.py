from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ProfileSection
from app.services.profile_schema import (
    PROFILE_SECTION_SCHEMA_VERSION,
    canonicalize_profile_section_payload,
)


PERSONAL_ARCHIVE_SCHEMA_VERSION = "personal.archive.v1"
_EXPERIENCE_MAPPED_CUSTOM_TYPES = {"custom:c_internship"}


def _html_to_plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _build_archive_entry(
    section_type: str,
    category_label: str,
    title: str,
    normalized: dict[str, Any],
) -> tuple[str, str, dict[str, Any]] | None:
    if section_type in _EXPERIENCE_MAPPED_CUSTOM_TYPES:
        desc = str(normalized.get("description", "") or "")
        bullet = " | ".join(
            [str(normalized.get("company", "") or ""), str(normalized.get("position", "") or ""), desc]
        ).strip(" |")
        content_json = {
            "schema_version": PROFILE_SECTION_SCHEMA_VERSION,
            "category_key": section_type,
            "category_label": category_label,
            "field_values": {
                f"{section_type}.subtitle": title,
                f"{section_type}.description": desc,
            },
            "normalized": normalized,
            "bullet": bullet,
            "title": title,
        }
        return section_type, title, content_json
    try:
        resolved_type, _resolved_label, _is_custom, content_json = canonicalize_profile_section_payload(
            section_type=section_type,
            title=title,
            raw_content_json={"normalized": normalized},
            category_label=category_label,
        )
        return resolved_type, title, content_json
    except ValueError:
        return None


def build_personal_archive_sections(profile: Any) -> list[ProfileSection] | None:
    base_info = profile.base_info_json or {}
    personal_archive = base_info.get("personal_archive") if isinstance(base_info, dict) else None
    if not isinstance(personal_archive, dict):
        return None
    if personal_archive.get("schemaVersion") != PERSONAL_ARCHIVE_SCHEMA_VERSION:
        return None

    resume_archive = personal_archive.get("resumeArchive")
    if not isinstance(resume_archive, dict):
        return None

    entries: list[tuple[str, str, dict[str, Any]]] = []

    for item in resume_archive.get("education", []):
        if not isinstance(item, dict):
            continue
        title = (item.get("schoolName") or "").strip() or "教育经历"
        normalized = {
            "school": item.get("schoolName", ""),
            "degree": (item.get("degree") or item.get("educationLevel") or ""),
            "major": item.get("major", ""),
            "start_date": item.get("startDate", ""),
            "end_date": item.get("endDate", ""),
            "gpa": item.get("gpa", ""),
            "description": _html_to_plain_text(item.get("description", "")),
        }
        result = _build_archive_entry("education", "教育经历", title, normalized)
        if result:
            entries.append(result)

    for item in resume_archive.get("workExperiences", []):
        if not isinstance(item, dict):
            continue
        title = (item.get("companyName") or "").strip() or "工作经历"
        normalized = {
            "company": item.get("companyName", ""),
            "department": item.get("department", ""),
            "position": item.get("positionName", ""),
            "start_date": item.get("startDate", ""),
            "end_date": item.get("endDate", ""),
            "description": _html_to_plain_text(item.get("description", "")),
        }
        result = _build_archive_entry("experience", "工作经历", title, normalized)
        if result:
            entries.append(result)

    for item in resume_archive.get("internshipExperiences", []):
        if not isinstance(item, dict):
            continue
        title = (item.get("companyName") or "").strip() or "实习经历"
        desc = _html_to_plain_text(item.get("description", ""))
        normalized = {
            "company": item.get("companyName", ""),
            "position": item.get("positionName", ""),
            "start_date": item.get("startDate", ""),
            "end_date": item.get("endDate", ""),
            "description": desc,
            "subtitle": title,
        }
        result = _build_archive_entry("custom:c_internship", "实习经历", title, normalized)
        if result:
            entries.append(result)

    for item in resume_archive.get("projects", []):
        if not isinstance(item, dict):
            continue
        title = (item.get("projectName") or "").strip() or "项目经历"
        normalized = {
            "name": item.get("projectName", ""),
            "role": item.get("projectRole", ""),
            "url": item.get("projectLink", ""),
            "start_date": item.get("startDate", ""),
            "end_date": item.get("endDate", ""),
            "description": _html_to_plain_text(item.get("description", "")),
        }
        result = _build_archive_entry("project", "项目经历", title, normalized)
        if result:
            entries.append(result)

    skill_groups: dict[str, dict[str, list[str]]] = {}
    for item in resume_archive.get("skills", []):
        if not isinstance(item, dict):
            continue
        proficiency = (item.get("proficiency") or "").strip() or "技能"
        if proficiency not in skill_groups:
            skill_groups[proficiency] = {"names": [], "remarks": []}
        name = (item.get("skillName") or "").strip()
        if name:
            skill_groups[proficiency]["names"].append(name)
        remark = (item.get("remark") or "").strip()
        if remark:
            skill_groups[proficiency]["remarks"].append(remark)

    for proficiency, group in skill_groups.items():
        normalized = {
            "category": proficiency,
            "items": group["names"],
            "description": "\n".join(group["remarks"]) if group["remarks"] else "",
        }
        result = _build_archive_entry("skill", "技能与证书", proficiency, normalized)
        if result:
            entries.append(result)

    for item in resume_archive.get("certificates", []):
        if not isinstance(item, dict):
            continue
        title = (item.get("certificateName") or "").strip() or "证书"
        normalized = {
            "name": item.get("certificateName", ""),
            "issuer": item.get("issuer", ""),
            "date": item.get("acquiredAt", ""),
            "score": item.get("scoreOrLevel", ""),
            "description": item.get("scoreOrLevel", ""),
        }
        result = _build_archive_entry("certificate", "技能与证书", title, normalized)
        if result:
            entries.append(result)

    for item in resume_archive.get("awards", []):
        if not isinstance(item, dict):
            continue
        title = (item.get("awardName") or "").strip() or "获奖经历"
        desc = _html_to_plain_text(item.get("description", ""))
        normalized = {
            "subtitle": title,
            "description": desc,
            "issuer": item.get("issuer", ""),
            "date": item.get("awardedAt", ""),
        }
        result = _build_archive_entry("custom:c_awards", "获奖经历", title, normalized)
        if result:
            entries.append(result)

    for item in resume_archive.get("personalExperiences", []):
        if not isinstance(item, dict):
            continue
        title = (item.get("experienceTitle") or "").strip() or "个人经历"
        desc = _html_to_plain_text(item.get("description", ""))
        normalized = {
            "subtitle": title,
            "description": desc,
            "start_date": item.get("startDate", ""),
            "end_date": item.get("endDate", ""),
        }
        result = _build_archive_entry("custom:c_personal", "个人经历", title, normalized)
        if result:
            entries.append(result)

    return [
        ProfileSection(
                profile_id=profile.id,
                section_type=resolved_type,
                title=title,
                sort_order=sort_order,
                content_json=content_json,
                source="archive_sync",
                confidence=1.0,
            )
        for sort_order, (resolved_type, title, content_json) in enumerate(entries)
    ]


async def sync_personal_archive_to_sections(profile: Any, db: AsyncSession) -> int:
    sections = build_personal_archive_sections(profile)
    if sections is None:
        return 0

    await db.execute(
        ProfileSection.__table__.delete().where(
            ProfileSection.profile_id == profile.id,
            ProfileSection.source == "archive_sync",
        )
    )

    db.add_all(sections)

    await db.flush()
    return len(sections)
