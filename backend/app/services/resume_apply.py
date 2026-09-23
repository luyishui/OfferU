# =============================================
# Resume Apply — 将 AI 优化建议应用到简历内容
# =============================================
# 从 routes/resume.py 的 /ai/apply + /ai/apply-batch 提取。
# 负责把已采纳的建议写入 ResumeSection.content_json / sort_order，
# 通过正规写路径（ORM + commit），路由层只做适配。
# =============================================

from __future__ import annotations

import re
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ResumeSection


class ApplyError(Exception):
    """建议应用失败（携带 HTTP 语义）。"""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _strip_html(text: Any) -> str:
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


async def _get_section(
    db: AsyncSession, resume_id: int, section_id: Any
) -> ResumeSection:
    result = await db.execute(
        select(ResumeSection).where(
            ResumeSection.id == section_id,
            ResumeSection.resume_id == resume_id,
        )
    )
    section = result.scalar_one_or_none()
    if not section:
        raise ApplyError(404, "Section not found")
    return section


async def apply_suggestion(
    db: AsyncSession, resume_id: int, suggestion: Mapping[str, Any]
) -> dict:
    """应用单条 AI 优化建议到简历。

    支持类型：
      - bullet_rewrite: 更新经历/项目描述
      - keyword_add: 更新技能列表
      - section_reorder: 更新段落排序
    """
    suggestion_type = suggestion.get("type")

    if suggestion_type == "bullet_rewrite":
        section_id = suggestion.get("section_id")
        item_index = suggestion.get("item_index", 0)
        suggested = suggestion.get("suggested")
        if not section_id or suggested is None:
            raise ApplyError(400, "Missing section_id or suggested")

        section = await _get_section(db, resume_id, section_id)
        content = list(section.content_json or [])
        if item_index < len(content):
            content[item_index]["description"] = suggested
            section.content_json = content
            await db.commit()
        return {"message": "Suggestion applied"}

    if suggestion_type == "keyword_add":
        section_id = suggestion.get("section_id")
        suggested = suggestion.get("suggested")
        if not section_id or suggested is None:
            raise ApplyError(400, "Missing section_id or suggested")

        section = await _get_section(db, resume_id, section_id)
        content = list(section.content_json or [])
        if content and isinstance(suggested, list):
            content[0]["items"] = suggested
            section.content_json = content
            await db.commit()
        return {"message": "Suggestion applied"}

    if suggestion_type == "section_reorder":
        suggested_order = suggestion.get("suggested_order", [])
        for idx, section_id in enumerate(suggested_order):
            result = await db.execute(
                select(ResumeSection).where(
                    ResumeSection.id == section_id,
                    ResumeSection.resume_id == resume_id,
                )
            )
            section = result.scalar_one_or_none()
            if section:
                section.sort_order = idx
        await db.commit()
        return {"message": "Sections reordered"}

    raise ApplyError(400, f"Unknown suggestion type: {suggestion_type}")


async def apply_batch(
    db: AsyncSession, resume_id: int, payload: Mapping[str, Any]
) -> dict:
    """批量应用已采纳建议（改写/注入 + 模块重排）。

    payload:
      {
        "suggestions": [{ "section_title", "original", "suggested", ... }],
        "reorder": { "suggested_order": ["段落1", ...] }  // 可选
      }
    """
    applied = 0
    failed = 0

    for sug in payload.get("suggestions", []) or []:
        section_title = sug.get("section_title", "")
        original = sug.get("original", "")
        suggested = sug.get("suggested", "")
        if not section_title or not original or not suggested:
            failed += 1
            continue

        result = await db.execute(
            select(ResumeSection).where(
                ResumeSection.resume_id == resume_id,
                ResumeSection.title.icontains(section_title),
            )
        )
        section = result.scalar_one_or_none()
        if not section:
            failed += 1
            continue

        content = list(section.content_json or [])
        matched = False
        for item in content:
            desc = item.get("description", "")
            if not desc:
                continue
            plain_desc = _strip_html(desc)
            plain_original = _strip_html(original)
            if plain_original and plain_original in plain_desc:
                item["description"] = (
                    desc.replace(original, suggested) if original in desc else suggested
                )
                matched = True
                break

        if matched:
            section.content_json = content
            applied += 1
        else:
            failed += 1

    reorder = payload.get("reorder")
    if reorder and reorder.get("suggested_order"):
        for idx, sec_title in enumerate(reorder["suggested_order"]):
            result = await db.execute(
                select(ResumeSection).where(
                    ResumeSection.resume_id == resume_id,
                    ResumeSection.title.icontains(sec_title),
                )
            )
            section = result.scalar_one_or_none()
            if section:
                section.sort_order = idx

    await db.commit()
    return {
        "message": f"已应用 {applied} 条建议" + (f"，{failed} 条未匹配" if failed else ""),
        "applied": applied,
        "failed": failed,
    }
