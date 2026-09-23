# =============================================
# Optimize Sessions — 对话式简历优化会话的持久化 CRUD
# =============================================
# 从 app/agents/optimize_agent.py 提取（旧 ReAct agent 栈已拆除）。
# 仅保留 session 的创建 / 列表 / 详情 / 删除，供 routes/optimize.py 的
# /agent/* 会话管理端点复用。
# =============================================

from __future__ import annotations

import uuid

PHASE_CONFIRMING = "confirming"
PHASE_ANALYZING = "analyzing"
PHASE_FRAMEWORK = "framework"
PHASE_REWRITING = "rewriting"
PHASE_COMPLETED = "completed"

VALID_PHASES = {PHASE_CONFIRMING, PHASE_ANALYZING, PHASE_FRAMEWORK, PHASE_REWRITING, PHASE_COMPLETED}

_SECTION_TYPE_LABELS = {
    "education": "教育",
    "internship": "实习",
    "experience": "经历",
    "project": "项目",
    "activity": "活动",
    "competition": "竞赛",
    "skill": "技能",
    "certificate": "证书",
    "honor": "荣誉",
    "language": "语言",
    "general": "通用",
    "custom": "自定义",
    "other": "其他",
    "custom:c_internship": "实习",
    "custom:c_awards": "获奖",
    "custom:c_personal": "个人经历",
    "custom:c_generic": "自定义",
}


def _section_type_label(section_type: str) -> str:
    return _SECTION_TYPE_LABELS.get(section_type) or section_type


class OptimizeSession:
    def __init__(
        self,
        session_id: str | None = None,
        job_ids: list[int] | None = None,
        mode: str = "per_job",
        profile_id: int | None = None,
    ):
        self.session_id = session_id or f"opt_{uuid.uuid4().hex[:12]}"
        self.job_ids = job_ids or []
        self.mode = mode
        self.profile_id = profile_id
        self.phase = PHASE_CONFIRMING
        self.messages: list[dict] = []
        self.jd_analysis: dict = {}
        self.match_analysis: dict = {}
        self.reorder_result: dict = {}
        self.framework: dict = {}
        self.rows: list[dict] = []
        self.current_section_index: int = 0
        self.resume_id: int | None = None
        self.interview_experiences: list[dict] = []
        self.raw_jd: str = ""
        self.job_titles: list[str] = []
        self.pending_action: dict | None = None
        self.confirmed_sections: dict = {}
        self.original_rows: dict = {}


_sessions: dict[str, OptimizeSession] = {}


def _get_session(session_id: str) -> OptimizeSession | None:
    return _sessions.get(session_id)


def _save_session(session: OptimizeSession) -> None:
    _sessions[session.session_id] = session


async def _load_session_from_db(session_id: str, db) -> OptimizeSession | None:
    from app.models.models import OptimizeSession as OptimizeSessionModel
    from sqlalchemy import select

    result = await db.execute(
        select(OptimizeSessionModel).where(OptimizeSessionModel.session_id == session_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    session = OptimizeSession(
        session_id=row.session_id,
        job_ids=row.job_ids or [],
        mode=row.mode or "per_job",
        profile_id=row.profile_id,
    )
    session.phase = row.phase or PHASE_CONFIRMING
    session.messages = row.messages_json or []
    session.jd_analysis = row.jd_analysis_json or {}
    session.match_analysis = row.match_analysis_json or {}
    session.reorder_result = row.reorder_json or {}
    session.framework = row.framework_json or {}
    session.rows = row.rows_json or []
    session.current_section_index = row.current_section_index or 0
    session.resume_id = row.resume_id
    session.interview_experiences = row.interview_experiences_json or []
    session.raw_jd = row.raw_jd_json or ""
    session.job_titles = row.job_titles_json or []
    session.confirmed_sections = row.confirmed_sections_json or {}
    session.original_rows = row.original_rows_json or {}
    session.pending_action = row.pending_action_json or None

    _save_session(session)
    return session


async def _persist_session(session: OptimizeSession, db) -> None:
    from app.models.models import OptimizeSession as OptimizeSessionModel
    from sqlalchemy import select

    result = await db.execute(
        select(OptimizeSessionModel).where(OptimizeSessionModel.session_id == session.session_id)
    )
    row = result.scalar_one_or_none()

    if row:
        row.phase = session.phase
        row.job_ids = session.job_ids
        row.mode = session.mode
        row.profile_id = session.profile_id
        row.messages_json = session.messages
        row.jd_analysis_json = session.jd_analysis
        row.match_analysis_json = session.match_analysis
        row.reorder_json = session.reorder_result
        row.framework_json = session.framework
        row.rows_json = session.rows
        row.current_section_index = session.current_section_index
        row.resume_id = session.resume_id
        row.interview_experiences_json = session.interview_experiences
        row.raw_jd_json = session.raw_jd
        row.job_titles_json = session.job_titles or None
        row.confirmed_sections_json = session.confirmed_sections or None
        row.original_rows_json = session.original_rows or None
        row.pending_action_json = session.pending_action or None
    else:
        row = OptimizeSessionModel(
            session_id=session.session_id,
            profile_id=session.profile_id,
            phase=session.phase,
            job_ids=session.job_ids,
            mode=session.mode,
            messages_json=session.messages,
            jd_analysis_json=session.jd_analysis,
            match_analysis_json=session.match_analysis,
            reorder_json=session.reorder_result,
            framework_json=session.framework,
            rows_json=session.rows,
            current_section_index=session.current_section_index,
            resume_id=session.resume_id,
            interview_experiences_json=session.interview_experiences,
            raw_jd_json=session.raw_jd,
            job_titles_json=session.job_titles or None,
            confirmed_sections_json=session.confirmed_sections or None,
            original_rows_json=session.original_rows or None,
            pending_action_json=session.pending_action or None,
        )
        db.add(row)

    await db.commit()


async def list_sessions_from_db(db) -> list[dict]:
    from app.models.models import OptimizeSession as OptimizeSessionModel
    from sqlalchemy import select

    result = await db.execute(
        select(OptimizeSessionModel).order_by(OptimizeSessionModel.updated_at.desc()).limit(50)
    )
    rows = result.scalars().all()
    return [
        {
            "session_id": r.session_id,
            "phase": r.phase,
            "job_ids": r.job_ids or [],
            "mode": r.mode,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "updated_at": r.updated_at.isoformat() if r.updated_at else "",
            "resume_id": r.resume_id,
        }
        for r in rows
    ]


async def get_session_detail(session_id: str, db, actor=None) -> dict | None:
    """获取完整会话详情，包含对话历史。

    actor 提供时（已通过 browser principal / actor-session 授权绑定）追加 durable
    恢复投影：pending Plan/Group/proposal 卡片与 plan_status 信封。legacy
    `pending_action_json` 不再是权威，也不作为可操作确认请求返回——确认只能来自
    durable proposal/plan 状态。
    """
    from app.models.models import OptimizeSession as OptimizeSessionModel
    from sqlalchemy import select

    result = await db.execute(
        select(OptimizeSessionModel).where(OptimizeSessionModel.session_id == session_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None

    detail: dict = {
        "session_id": row.session_id,
        "phase": row.phase,
        "job_ids": row.job_ids or [],
        "mode": row.mode,
        "messages": row.messages_json or [],
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "resume_id": row.resume_id,
    }
    if actor is not None:
        from app.operator.plan_runtime import (
            PlanMaterializationError,
            pending_plan_bootstrap,
        )

        try:
            bootstrap = await pending_plan_bootstrap(db, actor)
        except PlanMaterializationError as exc:
            bootstrap = {
                "proposals": [],
                "plan_events": [],
                "recovery_error": str(exc),
            }
        detail["durable"] = bootstrap
    return detail


async def delete_session(session_id: str, db) -> bool:
    """删除指定会话"""
    from app.models.models import OptimizeSession as OptimizeSessionModel
    from sqlalchemy import select, delete

    result = await db.execute(
        select(OptimizeSessionModel).where(OptimizeSessionModel.session_id == session_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        return False

    await db.execute(
        delete(OptimizeSessionModel).where(OptimizeSessionModel.session_id == session_id)
    )
    await db.commit()

    _sessions.pop(session_id, None)
    return True


async def _get_session_profile(session: OptimizeSession, db):
    from app.models.models import Profile
    from sqlalchemy import select

    if session.profile_id:
        result = await db.execute(select(Profile).where(Profile.id == session.profile_id))
        return result.scalar_one_or_none()
    result = await db.execute(
        select(Profile).order_by(Profile.is_default.desc(), Profile.updated_at.desc())
    )
    return result.scalars().first()


async def start_session(
    job_ids: list[int],
    mode: str = "per_job",
    profile_id: int | None = None,
    db=None,
) -> dict:
    session = OptimizeSession(
        job_ids=job_ids,
        mode=mode,
        profile_id=profile_id,
    )
    _save_session(session)

    job_titles: list[str] = []
    profile_summary = ""

    if db is not None:
        from app.models.models import Job, ProfileSection
        from sqlalchemy import select

        jobs_result = await db.execute(select(Job).where(Job.id.in_(job_ids)))
        jobs = list(jobs_result.scalars().all())
        job_titles = [" - ".join(part for part in [j.company, j.title] if part) for j in jobs]
        session.job_titles = job_titles

        jd_parts = []
        for j in jobs:
            jd_text = j.raw_description or ""
            if jd_text.strip():
                label = " - ".join(part for part in [j.company, j.title] if part)
                jd_parts.append(f"### {label}\n{jd_text[:3000]}")
        if jd_parts:
            session.raw_jd = "\n\n---\n\n".join(jd_parts)
            session.messages.append({
                "role": "system",
                "content": "以下是候选人选择的目标岗位 JD：\n\n" + "\n\n---\n\n".join(jd_parts),
            })

        profile = await _get_session_profile(session, db)
        if profile:
            session.profile_id = profile.id
            sections_result = await db.execute(
                select(ProfileSection)
                .where(ProfileSection.profile_id == profile.id)
                .order_by(ProfileSection.sort_order.asc())
            )
            sections = list(sections_result.scalars().all())
            type_counts: dict[str, int] = {}
            section_details: list[str] = []
            for s in sections:
                st = s.section_type or "other"
                type_counts[st] = type_counts.get(st, 0) + 1
                title = s.title or ""
                bullet = ""
                payload = s.content_json
                if isinstance(payload, dict):
                    bullet = payload.get("bullet", "")
                    if not bullet:
                        normalized = payload.get("normalized")
                        if isinstance(normalized, dict):
                            bullet = normalized.get("description", "")
                    if not bullet:
                        field_values = payload.get("field_values")
                        if isinstance(field_values, dict):
                            for v in field_values.values():
                                if isinstance(v, str) and len(v) > 5:
                                    bullet = v[:200]
                                    break
                if title:
                    detail = f"- [{_section_type_label(st)}] {title}"
                    if bullet:
                        detail += f"：{bullet[:150]}"
                    section_details.append(detail)
            parts = [f"{_section_type_label(k)}: {v} 条" for k, v in type_counts.items()]
            profile_summary = f"👤 你的档案：{len(sections)} 条经历条目（{'、'.join(parts)}）"

            if section_details:
                session.messages.append({
                    "role": "system",
                    "content": f"以下是候选人的档案条目摘要：\n\n{profile_summary}\n\n" + "\n".join(section_details[:30]),
                })

            # Build session.rows from profile sections so that downstream
            # section tooling works immediately.
            from app.services.resume_builder import build_resume_sections, rank_profile_sections
            if sections and session.raw_jd:
                ranked = rank_profile_sections(sections, session.raw_jd, limit=12)
                selected = [item[0] for item in ranked]
                session.rows = build_resume_sections(selected)
            else:
                session.rows = build_resume_sections(sections)
        else:
            profile_summary = "⚠️ 未找到个人档案，请先在档案页创建"

    mode_label = "逐岗位输出" if mode == "per_job" else "合并输出"
    confirm_message = f"👋 我来帮你针对目标岗位优化简历。先确认一下：\n\n📋 生成方式：{mode_label}"

    if job_titles:
        confirm_message += "\n\n🏢 目标岗位："
        for i, title in enumerate(job_titles, 1):
            confirm_message += f"\n{i}. {title}"

    if profile_summary:
        confirm_message += f"\n\n{profile_summary}"

    confirm_message += "\n\n确认无误就开始分析？"

    session.messages.append({"role": "assistant", "content": confirm_message})

    if db is not None:
        await _persist_session(session, db)

    return {
        "session_id": session.session_id,
        "phase": session.phase,
        "assistant_message": confirm_message,
    }
