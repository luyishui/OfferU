from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from app.operator.guards import json_safe
from app.services.profile_schema import normalize_section_type_alias


PERSONAL_ARCHIVE_SCHEMA_VERSION = "personal.archive.v1"


def sync_profile_sections_to_personal_archive(profile: Any, sections: Sequence[Any]) -> bool:
    changed = False
    for section in sections:
        changed = sync_profile_section_to_personal_archive(profile, section) or changed
    return changed


def sync_profile_section_to_personal_archive(profile: Any, section: Any) -> bool:
    base_info = dict(profile.base_info_json) if isinstance(getattr(profile, "base_info_json", None), dict) else {}
    archive = _valid_archive(base_info.get("personal_archive")) or _default_personal_archive(base_info)
    resume_archive = archive.setdefault("resumeArchive", _default_resume_archive(base_info))
    _ensure_resume_lists(resume_archive)

    entry_spec = _archive_entry_from_section(section)
    if entry_spec is None:
        return False
    bucket, entry, identity_keys = entry_spec
    target = resume_archive.setdefault(bucket, [])
    if not isinstance(target, list):
        target = []
        resume_archive[bucket] = target

    changed = _upsert_entry(target, entry, identity_keys)
    if not changed:
        return False

    archive["schemaVersion"] = PERSONAL_ARCHIVE_SCHEMA_VERSION
    archive["updatedAt"] = _now_iso()
    archive["resumeArchive"] = resume_archive
    archive["applicationArchive"] = _application_archive_with_shared(
        archive.get("applicationArchive"),
        resume_archive,
    )
    archive["syncSettings"] = _sync_settings(archive.get("syncSettings"))
    profile.base_info_json = json_safe({**base_info, "personal_archive": archive})
    return True


def remove_profile_section_from_personal_archive(profile: Any, section: Any) -> bool:
    base_info = dict(profile.base_info_json) if isinstance(getattr(profile, "base_info_json", None), dict) else {}
    archive = _valid_archive(base_info.get("personal_archive"))
    if not archive:
        return False
    resume_archive = archive.get("resumeArchive") if isinstance(archive.get("resumeArchive"), dict) else {}
    entry_spec = _archive_entry_from_section(section)
    if entry_spec is None:
        return False
    bucket, entry, identity_keys = entry_spec
    target = resume_archive.get(bucket)
    if not isinstance(target, list):
        return False

    before_count = len(target)
    section_id = _section_archive_id(section)
    target[:] = [
        item
        for item in target
        if not _same_archive_entry(item, entry, identity_keys, section_id=section_id)
    ]
    if len(target) == before_count:
        return False

    archive["updatedAt"] = _now_iso()
    archive["resumeArchive"] = resume_archive
    archive["applicationArchive"] = _application_archive_with_shared(
        archive.get("applicationArchive"),
        resume_archive,
    )
    profile.base_info_json = json_safe({**base_info, "personal_archive": archive})
    return True


def _archive_entry_from_section(section: Any) -> tuple[str, dict[str, Any], tuple[str, ...]] | None:
    raw_type = _text(getattr(section, "section_type", ""))
    section_type = normalize_section_type_alias(raw_type)
    if section_type == "work_experience":
        section_type = "experience"
    title = _text(getattr(section, "title", ""))
    content = getattr(section, "content_json", None)
    if not isinstance(content, dict):
        content = {}
    normalized = content.get("normalized") if isinstance(content.get("normalized"), dict) else content
    label = _text(content.get("category_label"))
    hint = " ".join(
        part
        for part in [
            raw_type,
            section_type,
            title,
            label,
            _pick(normalized, "type", "position", "positionName", "position_name", "job_title"),
        ]
        if part
    ).lower()
    description = _description_text(normalized, content)

    if section_type == "education":
        entry = {
            "id": _section_archive_id(section) or _archive_id("edu", title + _json(normalized)),
            "schoolName": _pick(normalized, "school", "schoolName", "school_name") or title,
            "educationLevel": _pick(normalized, "educationLevel", "degree"),
            "degree": _pick(normalized, "degree"),
            "major": _pick(normalized, "major"),
            "startDate": _pick(normalized, "startDate", "start_date"),
            "endDate": _pick(normalized, "endDate", "end_date"),
            "gpa": _pick(normalized, "gpa"),
            "description": description,
        }
        return "education", entry, ("schoolName", "degree", "major", "startDate", "endDate")

    if section_type == "experience":
        is_internship = _is_internship_hint(hint)
        entry = {
            "id": _section_archive_id(section) or _archive_id("intern" if is_internship else "work", title + _json(normalized)),
            "companyName": _pick(normalized, "company", "companyName", "company_name") or title,
            "positionName": _pick(normalized, "position", "positionName", "position_name", "job_title"),
            "startDate": _pick(normalized, "startDate", "start_date"),
            "endDate": _pick(normalized, "endDate", "end_date"),
            "description": description,
        }
        if is_internship:
            return "internshipExperiences", entry, ("companyName", "positionName", "startDate", "endDate")
        entry["department"] = _pick(normalized, "department")
        return "workExperiences", entry, ("companyName", "positionName", "startDate", "endDate")

    if section_type == "project":
        entry = {
            "id": _section_archive_id(section) or _archive_id("proj", title + _json(normalized)),
            "projectName": _pick(normalized, "name", "projectName", "project_name") or title,
            "projectRole": _pick(normalized, "role", "projectRole", "project_role"),
            "startDate": _pick(normalized, "startDate", "start_date"),
            "endDate": _pick(normalized, "endDate", "end_date"),
            "projectLink": _pick(normalized, "url", "projectLink", "project_link"),
            "description": description,
        }
        return "projects", entry, ("projectName", "projectRole", "startDate", "endDate")

    if section_type == "skill":
        items = _as_list(normalized.get("items")) or _as_list(content.get("bullet")) or [_pick(normalized, "category") or title]
        first = _text(items[0] if items else title)
        if not first:
            return None
        entry = {
            "id": _section_archive_id(section) or _archive_id("skill", first),
            "skillName": first,
            "proficiency": _pick(normalized, "proficiency"),
            "remark": description,
        }
        return "skills", entry, ("skillName",)

    if section_type == "certificate":
        entry = {
            "id": _section_archive_id(section) or _archive_id("cert", title + _json(normalized)),
            "certificateName": _pick(normalized, "name", "certificateName", "certificate_name") or title,
            "scoreOrLevel": _pick(normalized, "score", "scoreOrLevel", "level"),
            "acquiredAt": _pick(normalized, "date", "acquiredAt", "issuedDate"),
            "issuer": _pick(normalized, "issuer", "organization"),
        }
        return "certificates", entry, ("certificateName", "issuer")

    if "award" in hint or "奖" in hint:
        entry = {
            "id": _section_archive_id(section) or _archive_id("award", title + description),
            "awardName": title or _pick(normalized, "name") or "获奖经历",
            "issuer": _pick(normalized, "issuer"),
            "awardedAt": _pick(normalized, "date", "awardedAt"),
            "description": description,
        }
        return "awards", entry, ("awardName", "issuer")

    if title or description:
        entry = {
            "id": _section_archive_id(section) or _archive_id("personal", title + description),
            "experienceTitle": title or "个人经历",
            "startDate": _pick(normalized, "startDate", "start_date"),
            "endDate": _pick(normalized, "endDate", "end_date"),
            "description": description,
        }
        return "personalExperiences", entry, ("experienceTitle", "startDate", "endDate")

    return None


def _upsert_entry(target: list[dict[str, Any]], entry: dict[str, Any], identity_keys: tuple[str, ...]) -> bool:
    section_id = _text(entry.get("id"))
    for index, existing in enumerate(target):
        if _same_archive_entry(existing, entry, identity_keys, section_id=section_id):
            merged = {**existing, **entry}
            if existing != merged:
                target[index] = merged
                return True
            return False
    target.append(entry)
    return True


def _same_archive_entry(
    existing: Any,
    entry: dict[str, Any],
    identity_keys: tuple[str, ...],
    *,
    section_id: str,
) -> bool:
    if not isinstance(existing, dict):
        return False
    if section_id and _text(existing.get("id")) == section_id:
        return True
    identity = tuple(_text(entry.get(key)) for key in identity_keys)
    if not any(identity):
        return False
    return tuple(_text(existing.get(key)) for key in identity_keys) == identity


def _valid_archive(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("schemaVersion") != PERSONAL_ARCHIVE_SCHEMA_VERSION:
        return None
    return json_safe(value)


def _default_personal_archive(base_info: dict[str, Any]) -> dict[str, Any]:
    resume_archive = _default_resume_archive(base_info)
    return {
        "schemaVersion": PERSONAL_ARCHIVE_SCHEMA_VERSION,
        "updatedAt": _now_iso(),
        "resumeArchive": resume_archive,
        "applicationArchive": _application_archive_with_shared({}, resume_archive),
        "syncSettings": {"autoSyncEnabled": True, "overriddenFieldPaths": []},
    }


def _default_resume_archive(base_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "basicInfo": {
            "name": _text(base_info.get("name")),
            "phone": _text(base_info.get("phone")),
            "email": _text(base_info.get("email")),
            "currentCity": _text(base_info.get("current_city") or base_info.get("currentCity")),
            "jobIntention": _text(base_info.get("job_intention") or base_info.get("jobIntention")),
            "website": _text(base_info.get("website")),
            "github": _text(base_info.get("github")),
        },
        "personalSummary": _text(base_info.get("summary") or base_info.get("personal_summary")),
        "education": [],
        "workExperiences": [],
        "internshipExperiences": [],
        "projects": [],
        "skills": [],
        "certificates": [],
        "awards": [],
        "personalExperiences": [],
    }


def _ensure_resume_lists(resume_archive: dict[str, Any]) -> None:
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
    if not isinstance(resume_archive.get("basicInfo"), dict):
        resume_archive["basicInfo"] = _default_resume_archive({})["basicInfo"]
    if "personalSummary" not in resume_archive:
        resume_archive["personalSummary"] = ""


def _application_archive_with_shared(current: Any, resume_archive: dict[str, Any]) -> dict[str, Any]:
    archive = dict(current) if isinstance(current, dict) else {}
    archive["shared"] = json.loads(json.dumps(resume_archive, ensure_ascii=False))
    return archive


def _sync_settings(current: Any) -> dict[str, Any]:
    settings = current if isinstance(current, dict) else {}
    overridden = settings.get("overriddenFieldPaths")
    return {
        "autoSyncEnabled": bool(settings.get("autoSyncEnabled", True)),
        "overriddenFieldPaths": overridden if isinstance(overridden, list) else [],
    }


def _is_internship_hint(hint: str) -> bool:
    return "实习" in hint or "intern" in hint


def _description_text(normalized: dict[str, Any], content: dict[str, Any]) -> str:
    value = _pick(normalized, "description", "desc")
    if value:
        return value
    value = _text(content.get("description") or content.get("bullet"))
    if value:
        return value
    bullets = content.get("bullets")
    if isinstance(bullets, list):
        return "\n".join(_text(item) for item in bullets if _text(item))
    return ""


def _pick(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if _text(value):
            return _text(value)
    return ""


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,，、\n]", text) if part.strip()]


def _section_archive_id(section: Any) -> str:
    section_id = getattr(section, "id", None)
    return f"profile_section_{section_id}" if section_id not in (None, "") else ""


def _archive_id(prefix: str, seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _json(value: Any) -> str:
    return json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()
