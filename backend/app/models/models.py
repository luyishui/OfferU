# =============================================
# OfferU - 鏁版嵁搴撴ā鍨嬪畾涔?
# =============================================
# 鏍稿績琛細jobs, resumes, resume_sections, resume_templates,
#         interview_notifications, calendar_events, applications
# 浣跨敤 SQLAlchemy 2.0 Mapped 澹版槑寮忚娉?
# =============================================

from datetime import datetime
from typing import Optional, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    ForeignKey,
    ForeignKeyConstraint,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates


from app.database import Base


LOCAL_DEFAULT_ACTOR_ID = "local-default"


class OperatorOwnedMixin:
    """Ownership and optimistic-version markers for mutable user records."""

    owner_actor_id: Mapped[str] = mapped_column(
        String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True
    )
    operator_version_hash: Mapped[str] = mapped_column(String(128), default="")


class Job(OperatorOwnedMixin, Base):
    """岗位表：存储从各平台爬取的岗位信息"""
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # ---- 岗位基本信息 ----
    title: Mapped[str] = mapped_column(String(500), index=True)
    company: Mapped[str] = mapped_column(String(300), index=True)
    location: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(50), index=True, default="linkedin")
    raw_description: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # ---- 岗位详情（校招场景关键字段） ----
    salary_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 月薪下限（元）
    salary_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 月薪上限（元）
    salary_text: Mapped[str] = mapped_column(String(100), default="")  # 原始薪资文本，如 "15-25K·13薪"
    education: Mapped[str] = mapped_column(String(50), default="")  # 学历要求，如 "本科" "硕士"
    experience: Mapped[str] = mapped_column(String(100), default="")  # 经验要求，如 "1-3年" "应届"
    job_type: Mapped[str] = mapped_column(String(50), default="")  # 岗位类型，如 "全职" "实习" "校招"
    company_size: Mapped[str] = mapped_column(String(100), default="")  # 公司规模，如 "100-499人"
    company_industry: Mapped[str] = mapped_column(String(200), default="")  # 行业，如 "游戏" "AI"
    company_logo: Mapped[str] = mapped_column(Text, default="")  # 公司 Logo URL
    is_campus: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否校招岗位

    # ---- AI 分析输出 ----
    summary: Mapped[str] = mapped_column(Text, default="")
    keywords: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    user_notes: Mapped[str] = mapped_column(Text, default="")

    # ---- Inbox 分拣与池分组 ----
    triage_status: Mapped[str] = mapped_column(String(20), default="inbox", index=True)
    pool_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("pools.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # 采集批次 ID；历史数据统一回填为 legacy-import
    batch_id: Mapped[str] = mapped_column(String(64), default="legacy-import", index=True)

    # ---- 元数据 ----
    hash_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    pool: Mapped[Optional["Pool"]] = relationship(back_populates="jobs")


class Pool(OperatorOwnedMixin, Base):
    """岗位池：用于在已筛选岗位中按主题做分组（前端语义为文件夹）"""

    __tablename__ = "pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    color: Mapped[str] = mapped_column(String(20), default="#3B82F6")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    scope: Mapped[str] = mapped_column(String(20), default="picked", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    jobs: Mapped[list["Job"]] = relationship(back_populates="pool")


class Batch(Base):
    """采集批次：记录一次采集任务的上下文，用于 Inbox 分区"""

    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(50), default="")
    keywords: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    location: Mapped[str] = mapped_column(String(100), default="")
    max_results: Mapped[int] = mapped_column(Integer, default=0)
    job_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    total_fetched: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Profile(OperatorOwnedMixin, Base):
    """个人档案主表：承载基础信息与叙事字段"""

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), default="默认档案")
    school: Mapped[str] = mapped_column(String(200), default="")
    major: Mapped[str] = mapped_column(String(200), default="")
    degree: Mapped[str] = mapped_column(String(50), default="")
    gpa: Mapped[str] = mapped_column(String(20), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(50), default="")
    wechat: Mapped[str] = mapped_column(String(100), default="")
    headline: Mapped[str] = mapped_column(String(300), default="")
    exit_story: Mapped[str] = mapped_column(Text, default="")
    cross_cutting_advantage: Mapped[str] = mapped_column(Text, default="")
    base_info_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True)
    onboarding_step: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    target_roles: Mapped[list["ProfileTargetRole"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    sections: Mapped[list["ProfileSection"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="ProfileSection.sort_order",
    )
    chat_sessions: Mapped[list["ProfileChatSession"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class ProfileTargetRole(OperatorOwnedMixin, Base):
    """目标岗位条目：支持 fit 分级"""

    __tablename__ = "profile_target_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    role_name: Mapped[str] = mapped_column(String(120), index=True)
    role_level: Mapped[str] = mapped_column(String(60), default="")
    fit: Mapped[str] = mapped_column(String(30), default="primary")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    profile: Mapped["Profile"] = relationship(back_populates="target_roles")


class ProfileSection(OperatorOwnedMixin, Base):
    """档案条目：Bullet 级事实条目，支持来源与置信度"""

    __tablename__ = "profile_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    section_type: Mapped[str] = mapped_column(String(60), index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("profile_sections.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(220), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped["Profile"] = relationship(back_populates="sections")


class ProfileChatSession(Base):
    """档案对话会话：记录多轮消息与候选条目提取结果"""

    __tablename__ = "profile_chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str] = mapped_column(String(60), default="general")
    messages_json: Mapped[list] = mapped_column(JSON, default=list)
    extracted_bullets: Mapped[list] = mapped_column(JSON, default=list)
    extracted_bullets_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped["Profile"] = relationship(back_populates="chat_sessions")


class ResumeTemplate(Base):
    """
    简历模板表：存储内置和用户自定义的简历模板
    ─────────────────────────────────────────────
    模板通过 CSS 变量控制样式（主色调/字号/边距等），
    html_layout 使用 Jinja2 语法定义 A4 页面的 HTML 结构。
    前端预览时通过 css_variables 注入 CSS 自定义属性，
    后端 PDF 导出时同样将 css_variables 渲染进 HTML。
    """
    __tablename__ = "resume_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    thumbnail_url: Mapped[str] = mapped_column(String(500), default="")
    # CSS 变量集合：{ primaryColor, accentColor, bodySize, headingSize, lineHeight, pageMargin, sectionGap, fontFamily }
    css_variables: Mapped[dict] = mapped_column(JSON, default=dict)
    # Jinja2 HTML 模板，渲染简历为 A4 页面
    html_layout: Mapped[str] = mapped_column(Text, default="")
    is_builtin: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Resume(OperatorOwnedMixin, Base):
    """
    简历主表：存储简历元信息和全局设置
    ─────────────────────────────────────────────
    一个用户可拥有多份简历（不同语言/不同方向）。
    简历的具体内容段落存储在 ResumeSection 子表中，
    通过 resume_id FK 关联，删除简历时级联删除所有段落。
    style_config 存储用户对模板样式的覆盖（如修改字号/颜色），
    与模板的 css_variables 合并后生成最终样式。
    """
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300), default="未命名简历")
    photo_url: Mapped[str] = mapped_column(String(500), default="")
    # 个人简介 HTML（TipTap 富文本输出）
    summary: Mapped[str] = mapped_column(Text, default="")
    # 联系方式结构化数据：{ phone, email, linkedin, website, github, ... }
    contact_json: Mapped[dict] = mapped_column(JSON, default=dict)
    # 关联模板（可为空，使用系统默认）
    template_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("resume_templates.id"), nullable=True
    )
    # 用户对模板样式的覆盖：{ primaryColor, bodySize, lineHeight, ... }
    style_config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_primary: Mapped[bool] = mapped_column(default=True)
    language: Mapped[str] = mapped_column(String(10), default="zh")
    source_mode: Mapped[str] = mapped_column(String(30), default="manual")
    source_job_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    source_profile_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # ORM 关系：简历包含的段落列表，按 sort_order 排序
    sections: Mapped[list["ResumeSection"]] = relationship(
        back_populates="resume", cascade="all, delete-orphan",
        order_by="ResumeSection.sort_order"
    )
    template: Mapped[Optional["ResumeTemplate"]] = relationship()


class ResumeSection(OperatorOwnedMixin, Base):
    """
    简历段落通用块表：每一段（教育/经历/技能/项目/自定义）是一条记录
    ─────────────────────────────────────────────
    采用通用块设计：section_type 区分类型，content_json 内部按类型存不同结构。
    这样新增段落类型（如"证书""荣誉"）不需要修改数据库表结构。

    content_json 按 section_type 的约定结构：
      education:   [{ school, degree, major, gpa, startDate, endDate, description }]
      experience:  [{ company, position, startDate, endDate, description }]
      skill:       [{ category, items: ["Python", "React", ...] }]
      project:     [{ name, role, url, startDate, endDate, description }]
      certificate: [{ name, issuer, date, url }]
      custom:      [{ subtitle, description }]

    description 字段存储 TipTap 输出的 HTML，支持富文本排版。
    """
    __tablename__ = "resume_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    resume_id: Mapped[int] = mapped_column(Integer, ForeignKey("resumes.id", ondelete="CASCADE"))
    section_type: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200), default="")
    visible: Mapped[bool] = mapped_column(default=True)
    content_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    resume: Mapped["Resume"] = relationship(back_populates="sections")


class InterviewNotification(OperatorOwnedMixin, Base):
    """面试通知表：从邮件中解析出的面试邀请"""
    __tablename__ = "interview_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_subject: Mapped[str] = mapped_column(String(500), default="")
    email_from: Mapped[str] = mapped_column(String(300), default="")
    email_body: Mapped[str] = mapped_column(Text, default="")
    company: Mapped[str] = mapped_column(String(300), default="")
    position: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(50), default="unknown")  # 8种校招状态分类
    interview_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    location: Mapped[str] = mapped_column(String(500), default="")
    action_required: Mapped[str] = mapped_column(String(500), default="")  # 用户待办操作
    parsed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # 关联日历事件
    calendar_events: Mapped[list["CalendarEvent"]] = relationship(back_populates="notification")


class CalendarEvent(OperatorOwnedMixin, Base):
    """日程表：面试日程 + 自动同步事件"""
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    event_type: Mapped[str] = mapped_column(String(50), default="interview")  # interview / deadline / other
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    location: Mapped[str] = mapped_column(String(500), default="")

    # 关联
    related_job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("jobs.id"), nullable=True
    )
    related_notification_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("interview_notifications.id"), nullable=True
    )
    notification: Mapped[Optional["InterviewNotification"]] = relationship(
        back_populates="calendar_events"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Application(OperatorOwnedMixin, Base):
    """投递记录表：跟踪自动/手动投递状态"""
    __tablename__ = "applications"
    __table_args__ = (
        # One canonical Application per (owner, job): the workspace guarantees
        # exactly one lifecycle/material record per tracked job.
        UniqueConstraint("owner_actor_id", "job_id", name="uq_applications_owner_job"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(
        String(50), default="pending"
    )  # pending / submitted / rejected / interview / offer
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    apply_url: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class OperationAuditLog(Base):
    """统一操作审计日志：记录 UI/Agent/CLI/MCP 通过 action model 执行的动作。"""

    __tablename__ = "operation_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(120), index=True)
    operation_version: Mapped[str] = mapped_column(String(40), default="")
    surface: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    side_effects: Mapped[list] = mapped_column(JSON, default=list)
    inputs_json: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs_json: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings_json: Mapped[list] = mapped_column(JSON, default=list)
    errors_json: Mapped[list] = mapped_column(JSON, default=list)
    elapsed_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AgentWorkspaceState(Base):
    """Agent 与 UI 共享的当前工作区上下文。"""

    __tablename__ = "agent_workspace_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(80), default="default", unique=True, index=True)
    route: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    entity_type: Mapped[str] = mapped_column(String(80), default="")
    entity_id: Mapped[str] = mapped_column(String(120), default="")
    selection_json: Mapped[dict] = mapped_column(JSON, default=dict)
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    context_json: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(80), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True
    )


class ApplicationWorkspaceSettings(Base):
    """投递管理模块全局显示与行为设置"""
    __tablename__ = "application_workspace_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auto_row_height: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_column_width: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_subtable_sync_total_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ApplicationTemplate(Base):
    """默认投递模板：用于初始化新子表与全量覆盖"""
    __tablename__ = "application_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    schema_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ApplicationTable(OperatorOwnedMixin, Base):
    """投递表容器：总表 + 子表"""
    __tablename__ = "application_tables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    is_total: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    schema_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    table_records: Mapped[list["ApplicationTableRecord"]] = relationship(
        back_populates="table",
        cascade="all, delete-orphan",
    )


class ApplicationRecord(OperatorOwnedMixin, Base):
    """投递业务记录实体：总表与子表共享同一实体，保证值同步"""
    __tablename__ = "application_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_ref_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=True, index=True)
    # Canonical Application binding: this workspace projection row is backed by
    # exactly one canonical Application lifecycle/material record. Nullable so
    # legacy rows can be backfilled unambiguously or left for manual review.
    application_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("applications.id", name="fk_application_record_application", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # Canonical lifecycle stage (draft/pending/submitted/interview/rejected/offer)
    # projected from the ApplicationLifecycleSpec authority. The workspace UI
    # vocabulary (待投递/已投递/面试中/已拒绝/已录用) lives in custom_values and maps
    # through the same lifecycle registry labels.
    apply_status: Mapped[str] = mapped_column(String(50), default="pending")
    company_name: Mapped[str] = mapped_column(String(300), default="", index=True)
    job_title: Mapped[str] = mapped_column(String(500), default="", index=True)
    location: Mapped[str] = mapped_column(String(300), default="", index=True)
    job_link: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(120), default="")
    salary_text: Mapped[str] = mapped_column(String(120), default="")
    updated_at_value: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    custom_values: Mapped[dict] = mapped_column(JSON, default=dict)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    duplicate_group: Mapped[str] = mapped_column(String(160), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    application: Mapped[Optional["Application"]] = relationship(
        foreign_keys=[application_id],
    )

    table_links: Mapped[list["ApplicationTableRecord"]] = relationship(
        back_populates="record",
        cascade="all, delete-orphan",
    )


class ApplicationTableRecord(Base):
    """投递表与记录关联：支持一条记录挂在多张表"""
    __tablename__ = "application_table_records"
    __table_args__ = (
        UniqueConstraint("table_id", "record_id", name="uq_application_table_record"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_id: Mapped[int] = mapped_column(Integer, ForeignKey("application_tables.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[int] = mapped_column(Integer, ForeignKey("application_records.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    table: Mapped["ApplicationTable"] = relationship(back_populates="table_records")
    record: Mapped["ApplicationRecord"] = relationship(back_populates="table_links")


# =============================================
# 面经模块 (PRD §8.5)
# =============================================

class InterviewExperience(OperatorOwnedMixin, Base):
    """收集到的面经原文"""
    __tablename__ = "interview_experiences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(300), index=True)
    role: Mapped[str] = mapped_column(String(300), index=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_platform: Mapped[str] = mapped_column(String(50), default="manual")  # manual / niuke / zhihu
    raw_text: Mapped[str] = mapped_column(Text)
    interview_rounds: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON: 面试轮次
    job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    questions: Mapped[list["InterviewQuestion"]] = relationship(
        back_populates="experience", cascade="all, delete-orphan"
    )


class InterviewQuestion(OperatorOwnedMixin, Base):
    """从面经中提炼的结构化问题"""
    __tablename__ = "interview_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experience_id: Mapped[int] = mapped_column(Integer, ForeignKey("interview_experiences.id"))
    question_text: Mapped[str] = mapped_column(Text)
    round_type: Mapped[str] = mapped_column(String(50), default="department")  # hr / department / final
    category: Mapped[str] = mapped_column(String(50), default="behavioral")  # behavioral / technical / case / motivation
    difficulty: Mapped[int] = mapped_column(Integer, default=3)  # 1-5
    frequency: Mapped[int] = mapped_column(Integer, default=1)  # 出现次数
    suggested_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("jobs.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    experience: Mapped["InterviewExperience"] = relationship(back_populates="questions")


class OptimizeSession(OperatorOwnedMixin, Base):
    """对话式简历优化会话"""
    __tablename__ = "optimize_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    profile_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True)
    phase: Mapped[str] = mapped_column(String(30), default="confirming")
    job_ids: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(20), default="per_job")
    messages_json: Mapped[list] = mapped_column(JSON, default=list)
    jd_analysis_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    match_analysis_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    reorder_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    framework_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rows_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    current_section_index: Mapped[int] = mapped_column(Integer, default=0)
    resume_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True)
    interview_experiences_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    raw_jd_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    job_titles_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    confirmed_sections_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    original_rows_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    pending_action_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SmartFillMapCache(Base):
    """SmartFill 映射缓存：后端缓存域（SQLite 优先）"""
    __tablename__ = "smartfill_map_cache"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_smartfill_map_cache_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(128), index=True)
    adapter_id: Mapped[str] = mapped_column(String(50), default="unknown", index=True)
    model_signature: Mapped[str] = mapped_column(String(128), default="", index=True)
    mappings_json: Mapped[list] = mapped_column(JSON, default=list)
    channel: Mapped[str] = mapped_column(String(30), default="backend")
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SmartFillRun(Base):
    """SmartFill 运行记录：run 级摘要"""
    __tablename__ = "smartfill_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_smartfill_run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class SmartFillRunLog(Base):
    """SmartFill 分层诊断日志：run/field/control 级结构化记录"""
    __tablename__ = "smartfill_run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("smartfill_runs.run_id"), index=True)
    stage: Mapped[str] = mapped_column(String(40), index=True)
    severity: Mapped[str] = mapped_column(String(20), default="info", index=True)
    scope: Mapped[str] = mapped_column(String(20), default="run", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    field_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class AgentSession(Base):
    """Universal Operator session state."""

    __tablename__ = "agent_sessions"

    session_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    adapter: Mapped[str] = mapped_column(String(60), default="", index=True)
    active_skill: Mapped[str] = mapped_column(String(120), default="")
    current_step: Mapped[str] = mapped_column(String(120), default="")
    current_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_resume_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_profile_section_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_application_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pending_proposal_ids: Mapped[list] = mapped_column(JSON, default=list)
    pending_list_version: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active_intent_scopes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=None)
    checkpoint_id: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AgentConversation(Base):
    """Universal Operator conversation transcript and summary."""

    __tablename__ = "agent_conversations"

    conversation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    messages_json: Mapped[list] = mapped_column(JSON, default=list)
    conversation_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AgentMemory(Base):
    """Universal Operator scoped memory entry."""

    __tablename__ = "agent_memories"

    memory_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    session_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    category: Mapped[str] = mapped_column(String(80), default="", index=True)
    topic: Mapped[str] = mapped_column(String(160), default="", index=True)
    skill: Mapped[str] = mapped_column(String(120), default="", index=True)
    content_json: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AgentCapabilityLoadReceipt(Base):
    """ORM model for AgentCapabilityLoadReceipt."""

    __tablename__ = "agent_capability_load_receipts"

    actor_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    capability_kind: Mapped[str] = mapped_column(String(40), primary_key=True)
    capability_name: Mapped[str] = mapped_column(String(160), primary_key=True)
    operation: Mapped[str] = mapped_column(String(80), primary_key=True)
    schema_digest: Mapped[str] = mapped_column(String(64), index=True)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

class AgentPlanDraft(Base):
    """ORM model for AgentPlanDraft."""

    __tablename__ = "agent_plan_drafts"
    __table_args__ = (
        UniqueConstraint("actor_id", "session_id", "turn_key", name="uq_agent_plan_draft_turn"),
    )

    draft_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    turn_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default="collecting", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    sealed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AgentPlanIntent(Base):
    """ORM model for AgentPlanIntent."""

    __tablename__ = "agent_plan_intents"
    __table_args__ = (
        UniqueConstraint("draft_id", "canonical_effect_key", name="uq_agent_plan_intent_effect"),
        UniqueConstraint("draft_id", "sequence", name="uq_agent_plan_intent_sequence"),
    )

    intent_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("agent_plan_drafts.draft_id", ondelete="RESTRICT"), index=True)
    canonical_effect_key: Mapped[str] = mapped_column(String(240))
    sequence: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(40), default="active", index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    target_kind: Mapped[str] = mapped_column(String(40), default="")
    target_name: Mapped[str] = mapped_column(String(160), default="")
    record_id: Mapped[str] = mapped_column(String(160), default="")
    atomic_group_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    base_version: Mapped[str] = mapped_column(String(160), default="")
    args_json: Mapped[dict] = mapped_column(JSON, default=dict)
    args_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProposalPlan(Base):
    """ORM model for ProposalPlan."""

    __tablename__ = "proposal_plans"
    __table_args__ = (UniqueConstraint("lineage_id", "revision", name="uq_proposal_plan_lineage_revision"),)

    plan_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("agent_plan_drafts.draft_id", ondelete="RESTRICT"), unique=True, index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(40), default="sealed", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    lineage_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    parent_plan_id: Mapped[Optional[str]] = mapped_column(ForeignKey("proposal_plans.plan_id", ondelete="RESTRICT"), nullable=True)
    current_lineage_key: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, unique=True)
    plan_digest: Mapped[str] = mapped_column(String(64), unique=True)
    immutable_json: Mapped[dict] = mapped_column(JSON, default=dict)
    replaced_by_plan_id: Mapped[Optional[str]] = mapped_column(ForeignKey("proposal_plans.plan_id", ondelete="RESTRICT"), nullable=True)
    execution_started: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ConfirmationGroup(Base):
    """ORM model for ConfirmationGroup."""

    __tablename__ = "confirmation_groups"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence", name="uq_confirmation_group_sequence"),
        UniqueConstraint("plan_id", "group_id", name="uq_confirmation_group_plan_identity"),
    )

    group_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("proposal_plans.plan_id", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    # Immutable structural digest sealed into ProposalPlan.
    group_digest: Mapped[str] = mapped_column(String(64))
    # Snapshot-bound authorization digest; it must never rewrite group_digest.
    authorization_digest: Mapped[str] = mapped_column(String(64), default="")
    policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    dependency_group_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ConfirmationDecision(Base):
    """ORM model for ConfirmationDecision."""

    __tablename__ = "confirmation_decisions"
    __table_args__ = (
        UniqueConstraint("group_id", "sequence", name="uq_confirmation_decision_sequence"),
        ForeignKeyConstraint(["plan_id", "group_id"], ["confirmation_groups.plan_id", "confirmation_groups.group_id"], ondelete="RESTRICT"),
    )

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    group_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(20))
    plan_digest: Mapped[str] = mapped_column(String(64))
    group_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OperationNode(Base):
    """ORM model for OperationNode."""

    __tablename__ = "operation_nodes"
    __table_args__ = (
        UniqueConstraint("plan_id", "sequence", name="uq_operation_node_sequence"),
        UniqueConstraint("plan_id", "node_id", name="uq_operation_node_plan_identity"),
        ForeignKeyConstraint(["plan_id", "confirmation_group_id"], ["confirmation_groups.plan_id", "confirmation_groups.group_id"], ondelete="RESTRICT"),
    )

    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    source_intent_ids: Mapped[list] = mapped_column(JSON, default=list)
    tool_name: Mapped[str] = mapped_column(String(120))
    target_kind: Mapped[str] = mapped_column(String(40), default="")
    target_name: Mapped[str] = mapped_column(String(160), default="")
    record_id: Mapped[str] = mapped_column(String(160), default="")
    base_version: Mapped[str] = mapped_column(String(160), default="")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    typed_outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_contract_json: Mapped[dict] = mapped_column(JSON, default=dict)
    execution_contract_digest: Mapped[str] = mapped_column(String(64), default="")
    node_digest: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    confirmation_group_id: Mapped[str] = mapped_column(String(80), index=True)
    atomic_group_id: Mapped[str] = mapped_column(String(80), default="")
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    compensation_policy: Mapped[str] = mapped_column(String(80), default="registry_only")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NodeDependency(Base):
    """ORM model for NodeDependency."""

    __tablename__ = "node_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(["plan_id", "node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["plan_id", "depends_on_node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),
    )

    plan_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    depends_on_node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    output_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    semantic_type: Mapped[str] = mapped_column(String(120))
    reference_path: Mapped[str] = mapped_column(String(500), default="")


class AtomicGroupExecutionClaim(Base):
    """ORM model for AtomicGroupExecutionClaim."""

    __tablename__ = "atomic_group_execution_claims"
    __table_args__ = (
        UniqueConstraint("plan_id", "atomic_group_id", name="uq_atomic_group_execution_identity"),
        ForeignKeyConstraint(
            ["plan_id", "confirmation_group_id"],
            ["confirmation_groups.plan_id", "confirmation_groups.group_id"],
            ondelete="RESTRICT",
        ),
    )

    atomic_group_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    confirmation_group_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    claim_token: Mapped[str] = mapped_column(String(120), default="", index=True)
    claim_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class NodeExecutionReceipt(Base):
    __tablename__ = "node_execution_receipts"
    __table_args__ = (ForeignKeyConstraint(["plan_id", "node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),)
    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    input_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str] = mapped_column(String(120), default="", index=True)
    claim_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, unique=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    typed_outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    receipt_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    effect_manifest_schema_version: Mapped[int] = mapped_column(Integer, default=0)
    effect_manifest_json: Mapped[dict] = mapped_column(JSON, default=dict)
    effect_manifest_digest: Mapped[str] = mapped_column(String(64), default="")
    execution_contract_digest: Mapped[str] = mapped_column(String(64), default="")
    before_version: Mapped[str] = mapped_column(String(160), default="")
    after_version: Mapped[str] = mapped_column(String(160), default="")
    write_occurred: Mapped[bool] = mapped_column(Boolean, default=False)
    completion_reason: Mapped[str] = mapped_column(String(160), default="")
    error_classification: Mapped[str] = mapped_column(String(40), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class NodeExecutionOutcome(Base):
    """ORM model for NodeExecutionOutcome."""

    __tablename__ = "node_execution_outcomes"
    __table_args__ = (
        UniqueConstraint("node_id", name="uq_node_execution_outcome_node"),
        ForeignKeyConstraint(["plan_id", "node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["plan_id", "group_id"], ["confirmation_groups.plan_id", "confirmation_groups.group_id"], ondelete="RESTRICT"),
    )

    outcome_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    node_id: Mapped[str] = mapped_column(String(80), index=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    group_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    receipt_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    node_digest: Mapped[str] = mapped_column(String(64))
    execution_contract_digest: Mapped[str] = mapped_column(String(64))
    resolved_input_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(40), index=True)
    effect_state: Mapped[str] = mapped_column(String(40), default="no_effect", index=True)
    completion_reason: Mapped[str] = mapped_column(String(160))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    public_result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    public_result_digest: Mapped[str] = mapped_column(String(64))
    typed_outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    typed_outputs_digest: Mapped[str] = mapped_column(String(64))
    effect_manifest_json: Mapped[dict] = mapped_column(JSON, default=dict)
    effect_manifest_digest: Mapped[str] = mapped_column(String(64))
    error_classification: Mapped[str] = mapped_column(String(40), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PlanGroupResultReceipt(Base):
    """ORM model for PlanGroupResultReceipt."""

    __tablename__ = "plan_group_result_receipts"
    __table_args__ = (
        UniqueConstraint("plan_id", "group_id", name="uq_plan_group_result_identity"),
        ForeignKeyConstraint(["plan_id", "group_id"], ["confirmation_groups.plan_id", "confirmation_groups.group_id"], ondelete="RESTRICT"),
    )

    result_receipt_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    group_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    projection_schema_version: Mapped[int] = mapped_column(Integer, default=1)
    plan_digest: Mapped[str] = mapped_column(String(64))
    group_digest: Mapped[str] = mapped_column(String(64))
    node_outcome_set_digest: Mapped[str] = mapped_column(String(64))
    canonical_result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    canonical_result_digest: Mapped[str] = mapped_column(String(64))
    terminal_status: Mapped[str] = mapped_column(String(40), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ManualReviewCase(Base):
    """ORM model for ManualReviewCase."""

    __tablename__ = "manual_review_cases"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_manual_review_case_dedupe"),)

    case_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(180), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    plan_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    group_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    node_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    proposal_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    reason_code: Mapped[str] = mapped_column(String(120), index=True)
    subject_type: Mapped[str] = mapped_column(String(80), default="plan_execution")
    effect_state: Mapped[str] = mapped_column(String(40), default="unknown_external", index=True)
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    case_generation: Mapped[int] = mapped_column(Integer, default=1)
    evidence_digest: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    resolution_json: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution_result_digest: Mapped[str] = mapped_column(String(64), default="")
    resolution_event_digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ManualReviewResolution(Base):
    """ORM model for ManualReviewResolution."""

    __tablename__ = "manual_review_resolutions"
    __table_args__ = (
        UniqueConstraint("case_id", "sequence", name="uq_manual_review_resolution_sequence"),
        UniqueConstraint("case_id", "idempotency_key", name="uq_manual_review_resolution_idempotency"),
    )

    resolution_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("manual_review_cases.case_id", name="fk_manual_review_resolution_case", ondelete="RESTRICT"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    resolution: Mapped[str] = mapped_column(String(80))
    case_generation: Mapped[int] = mapped_column(Integer, default=0)
    evidence_digest: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str] = mapped_column(String(160), default="")
    request_digest: Mapped[str] = mapped_column(String(64), default="")
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_digest: Mapped[str] = mapped_column(String(64), default="")
    event_id: Mapped[str] = mapped_column(String(120), default="")
    event_digest: Mapped[str] = mapped_column(String(64), default="")
    audit_id: Mapped[str] = mapped_column(String(80), default="")
    retry_plan_id: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PlanRebaseReceipt(Base):
    __tablename__ = "plan_rebase_receipts"
    __table_args__ = (
        UniqueConstraint("node_id", "event_key", name="uq_plan_rebase_event"),
        ForeignKeyConstraint(["plan_id", "node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),
    )
    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    event_key: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), index=True)
    current_version: Mapped[str] = mapped_column(String(160), default="")
    current_digest: Mapped[str] = mapped_column(String(64))
    rebased_updates: Mapped[dict] = mapped_column(JSON, default=dict)
    competing_fields: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NodeExecutionRevision(Base):
    __tablename__ = "node_execution_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["plan_id", "node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),
    )
    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    current_version: Mapped[str] = mapped_column(String(160), default="")
    resolved_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    receipt_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SagaGroup(Base):
    __tablename__ = "saga_groups"
    plan_id: Mapped[str] = mapped_column(ForeignKey("proposal_plans.plan_id", ondelete="RESTRICT"), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SagaCompensationReceipt(Base):
    __tablename__ = "saga_compensation_receipts"
    __table_args__ = (
        ForeignKeyConstraint(["plan_id", "node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["plan_id"], ["saga_groups.plan_id"], ondelete="RESTRICT"),
    )
    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    operation: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str] = mapped_column(String(120), default="", index=True)
    claim_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(180), nullable=True, unique=True)
    fence_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_classification: Mapped[str] = mapped_column(String(40), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PlanNodeExecutionSnapshot(Base):
    """ORM model for PlanNodeExecutionSnapshot."""

    __tablename__ = "plan_node_execution_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(["plan_id", "node_id"], ["operation_nodes.plan_id", "operation_nodes.node_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["plan_id", "confirmation_group_id"], ["confirmation_groups.plan_id", "confirmation_groups.group_id"], ondelete="RESTRICT"),
    )

    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    confirmation_group_id: Mapped[str] = mapped_column(String(80), index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    model_or_action: Mapped[str] = mapped_column(String(120), default="")
    record_id: Mapped[str] = mapped_column(String(160), default="")
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    locked_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    affected_records: Mapped[list] = mapped_column(JSON, default=list)
    before: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    expected_version_or_hash: Mapped[str] = mapped_column(String(160), default="")
    snapshot_digest: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProposalCache(Base):
    """ORM model for ProposalCache."""

    __tablename__ = "proposal_cache"

    proposal_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    confirmation_group_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    node_ids: Mapped[list] = mapped_column(JSON, default=list)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    operation_type: Mapped[str] = mapped_column(String(80), default="")
    tool_name: Mapped[str] = mapped_column(String(120), default="")
    model_or_action: Mapped[str] = mapped_column(String(120), default="")
    record_id: Mapped[str] = mapped_column(String(120), default="")
    affected_records: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    user_message_snapshot: Mapped[str] = mapped_column(Text, default="")
    before: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    diff: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    locked_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    expected_version_or_hash: Mapped[str] = mapped_column(String(128), default="")
    idempotency_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    confirmation_events: Mapped[list] = mapped_column(JSON, default=list)
    confirmation_invariant_version: Mapped[int] = mapped_column(Integer, default=0)
    confirmation_count: Mapped[int] = mapped_column(Integer, default=0)
    confirmations_required: Mapped[int] = mapped_column(Integer, default=0)
    confirmations_received: Mapped[int] = mapped_column(Integer, default=0)
    confirmation_challenges: Mapped[list] = mapped_column(JSON, default=list)
    first_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    second_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    requires_second_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmation_challenge: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    confirmation_text: Mapped[str] = mapped_column(Text, default="")


class PlanGroupExecutionJob(Base):
    """ORM model for PlanGroupExecutionJob."""

    __tablename__ = "plan_group_execution_jobs"
    __table_args__ = (
        ForeignKeyConstraint(["plan_id", "group_id"], ["confirmation_groups.plan_id", "confirmation_groups.group_id"], ondelete="RESTRICT"),
    )

    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposal_cache.proposal_id", ondelete="RESTRICT"), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(80), index=True)
    group_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    claim_token: Mapped[str] = mapped_column(String(120), default="", index=True)
    claim_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    result_receipt_id: Mapped[Optional[str]] = mapped_column(ForeignKey("plan_group_result_receipts.result_receipt_id", name="fk_plan_group_execution_result_receipt", ondelete="RESTRICT"), nullable=True, default=None, index=True)
    result_digest: Mapped[str] = mapped_column(String(64), default="")
    error_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ProposalContinuation(Base):
    """ORM model for ProposalContinuation."""

    __tablename__ = "proposal_continuations"

    proposal_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    confirmed_event_id: Mapped[str] = mapped_column(String(120), unique=True)
    invocation_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    result_receipt_id: Mapped[Optional[str]] = mapped_column(ForeignKey("plan_group_result_receipts.result_receipt_id", name="fk_proposal_continuation_result_receipt", ondelete="RESTRICT"), nullable=True, default=None, index=True)
    result_digest: Mapped[str] = mapped_column(String(64), default="")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str] = mapped_column(String(80), default="")
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentContinuationInvocation(Base):
    """ORM model for AgentContinuationInvocation."""
    __tablename__ = "agent_continuation_invocations"
    invocation_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    session_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    lease_token: Mapped[str] = mapped_column(String(80), default="")
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AgentSessionExecutionLease(Base):
    """ORM model for AgentSessionExecutionLease."""

    __tablename__ = "agent_session_execution_leases"

    actor_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_invocation_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    lease_token: Mapped[str] = mapped_column(String(80), default="")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentToolInvocationReceipt(Base):
    """ORM model for AgentToolInvocationReceipt."""

    __tablename__ = "agent_tool_invocation_receipts"
    __table_args__ = (
        UniqueConstraint(
            "invocation_key",
            "tool_call_id",
            name="uq_agent_tool_invocation_receipt_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invocation_key: Mapped[str] = mapped_column(String(160), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(160))
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    session_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(160), default="")
    args_hash: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AgentAuditLog(Base):
    """ORM model for AgentAuditLog."""

    __tablename__ = "agent_audit_logs"

    audit_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    session_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    adapter: Mapped[str] = mapped_column(String(60), default="", index=True)
    request_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    tool_call_id: Mapped[str] = mapped_column(String(120), default="")
    user_message: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str] = mapped_column(String(120), default="")
    args_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    args_redacted: Mapped[dict] = mapped_column(JSON, default=dict)
    risk_level: Mapped[int] = mapped_column(Integer, default=0)
    proposal_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    confirmation_event_id: Mapped[str] = mapped_column(String(120), default="")
    idempotency_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    confirmation_status: Mapped[str] = mapped_column(String(60), default="")
    result_status: Mapped[str] = mapped_column(String(60), default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    changed_records: Mapped[list] = mapped_column(JSON, default=list)
    result_receipt_id: Mapped[Optional[str]] = mapped_column(ForeignKey("plan_group_result_receipts.result_receipt_id", name="fk_agent_audit_result_receipt", ondelete="RESTRICT"), nullable=True, default=None, index=True)
    result_digest: Mapped[str] = mapped_column(String(64), default="")
    before_version_or_hash: Mapped[str] = mapped_column(String(128), default="")
    after_version_or_hash: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    error: Mapped[str] = mapped_column(Text, default="")

    @validates("result_receipt_id")
    def _validate_result_receipt_id(self, key: str, value: Any) -> Optional[str]:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None



class AgentCheckpoint(Base):
    """ORM model for AgentCheckpoint."""

    __tablename__ = "agent_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    active_skill: Mapped[str] = mapped_column(String(120), default="")
    current_step: Mapped[str] = mapped_column(String(120), default="")
    current_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_resume_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_profile_section_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_application_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pending_proposal_ids: Mapped[list] = mapped_column(JSON, default=list)
    state_blob: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")


class HarnessSession(Base):
    """ORM model for HarnessSession."""

    __tablename__ = "harness_sessions"

    session_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    adapter: Mapped[str] = mapped_column(String(60), default="", index=True)
    phase: Mapped[str] = mapped_column(String(60), default="idle", index=True)
    phase_token: Mapped[str] = mapped_column(String(120), default="", index=True)
    active_turn_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    last_entry_seq: Mapped[int] = mapped_column(Integer, default=0)
    current_save_point_id: Mapped[str] = mapped_column(String(120), default="")
    pending_writes: Mapped[list] = mapped_column(JSON, default=list)
    recovery_status: Mapped[str] = mapped_column(String(60), default="clean", index=True)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class HarnessEntry(Base):
    """ORM model for HarnessEntry."""

    __tablename__ = "harness_entries"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_harness_entries_session_seq"),)

    entry_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    seq: Mapped[int] = mapped_column(Integer, index=True)
    turn_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    phase: Mapped[str] = mapped_column(String(60), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class HarnessTurnSnapshot(Base):
    """ORM model for HarnessTurnSnapshot."""

    __tablename__ = "harness_turn_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    turn_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    phase: Mapped[str] = mapped_column(String(60), default="")
    last_entry_seq: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    pending_writes: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class HarnessSavePoint(Base):
    """ORM model for HarnessSavePoint."""

    __tablename__ = "harness_save_points"

    save_point_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(120), default=LOCAL_DEFAULT_ACTOR_ID, index=True)
    turn_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    phase: Mapped[str] = mapped_column(String(60), default="")
    last_entry_seq: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    pending_writes: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentTreeEntry(Base):
    """ORM model for AgentTreeEntry."""

    __tablename__ = "agent_tree_entries"
    __table_args__ = (
        UniqueConstraint(
            "invocation_key",
            "invocation_sequence",
            name="uq_agent_tree_entries_invocation_sequence",
        ),
    )

    ord: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    parent_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entry_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    invocation_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    invocation_sequence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
