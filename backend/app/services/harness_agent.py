from __future__ import annotations

from typing import Any, Awaitable, Callable

ToolHandler = Callable[..., Awaitable[Any]]
RiskLevel = str

READ_TOOLS = {
    "get_profile",
    "list_jobs",
    "get_job",
    "job_stats",
    "list_pools",
    "list_scraper_sources",
    "list_scraper_tasks",
    "list_resumes",
    "list_applications",
    "list_calendar_events",
    "list_email_notifications",
    "list_interview_questions",
    "career_exploration",
}

CONFIRM_TOOLS = {
    "batch_triage",
    "run_scraper",
    "generate_resume",
    "import_jobs_to_application_table",
    "auto_fill_calendar",
    "sync_email_notifications",
}

WRITE_TOOLS = {
    "triage_job",
    "create_application",
}

TOOL_DESCRIPTIONS = {
    "get_profile": "Read the current user profile.",
    "list_jobs": "Read jobs from the local OfferU job database.",
    "get_job": "Read a single job detail.",
    "job_stats": "Read job statistics.",
    "list_pools": "Read job pools.",
    "list_scraper_sources": "Read available scraper sources.",
    "list_scraper_tasks": "Read scraper task history.",
    "list_resumes": "Read saved resumes.",
    "list_applications": "Read application records.",
    "list_calendar_events": "Read calendar events.",
    "list_email_notifications": "Read parsed email notifications.",
    "list_interview_questions": "Read interview questions.",
    "career_exploration": "Generate transferable-skill career paths.",
    "batch_triage": "Batch triage jobs into a status or pool.",
    "run_scraper": "Start a job scraper task.",
    "generate_resume": "Generate a tailored resume for a job.",
    "import_jobs_to_application_table": "Import jobs into application tracking.",
    "auto_fill_calendar": "Create calendar events from parsed interview emails.",
    "sync_email_notifications": "Sync and parse email notifications.",
    "triage_job": "Triage one job.",
    "create_application": "Create one pending application record.",
}


def get_default_tool_registry() -> dict[str, dict[str, Any]]:
    registry: dict[str, dict[str, Any]] = {}
    for name in sorted(READ_TOOLS):
        registry[name] = _tool_entry(name, "read")
    for name in sorted(WRITE_TOOLS):
        registry[name] = _tool_entry(name, "write")
    for name in sorted(CONFIRM_TOOLS):
        registry[name] = _tool_entry(name, "confirm")
    return registry


def _tool_entry(name: str, risk_level: RiskLevel) -> dict[str, Any]:
    return {
        "name": name,
        "description": TOOL_DESCRIPTIONS.get(name, name.replace("_", " ")),
        "parameters": {},
        "risk_level": risk_level,
        "handler": None,
    }


def classify_intent(message: str) -> str:
    text = (message or "").strip().lower()
    if not text:
        return "general"

    career_keywords = (
        "职业",
        "方向",
        "职业规划",
        "没想到",
        "意想不到",
        "可迁移",
        "转行",
        "career",
        "path",
        "direction",
        "transferable",
    )
    job_keywords = (
        "岗位",
        "职位",
        "实习",
        "抓取",
        "爬取",
        "筛选",
        "投递",
        "job",
        "intern",
        "scrape",
        "apply",
    )
    resume_keywords = ("简历", "resume", "优化", "生成")
    application_keywords = ("投递表", "投递管理", "application", "跟进")
    interview_keywords = ("面试", "interview", "日程", "calendar", "邮件", "email")

    if any(keyword in text for keyword in career_keywords):
        return "career_exploration"
    if any(keyword in text for keyword in job_keywords):
        return "job_workflow"
    if any(keyword in text for keyword in resume_keywords):
        return "resume_workflow"
    if any(keyword in text for keyword in application_keywords):
        return "application_tracking"
    if any(keyword in text for keyword in interview_keywords):
        return "follow_up"
    return "general"


def build_career_exploration_fallback(
    *,
    profile: dict[str, Any] | None,
    user_message: str,
) -> dict[str, Any]:
    profile = profile or {}
    target_roles = profile.get("target_roles") or []
    role_names = [
        str(item.get("role_name") or "").strip()
        for item in target_roles
        if isinstance(item, dict) and str(item.get("role_name") or "").strip()
    ]
    headline = str(profile.get("headline") or "").strip()
    anchor = role_names[0] if role_names else headline or "your current strengths"
    sections_by_type = profile.get("sections_by_type") or {}
    section_count = sum(int(value or 0) for value in sections_by_type.values()) if isinstance(sections_by_type, dict) else 0

    summary = (
        f"Your profile suggests a reusable base around {anchor}: problem framing, communication, "
        f"information synthesis, and execution follow-through. I found {section_count} profile items to treat as evidence, "
        "so these paths stay adjacent to your demonstrated strengths instead of inventing unrelated experience."
    )

    career_paths = [
        {
            "title": "AI Product Operations",
            "industry": "AI tools and SaaS",
            "fit_reason": "Connects user insight, workflow design, content clarity, and cross-functional coordination.",
            "entry_route": "Start with product operations intern, AI workflow assistant, or growth operations roles.",
            "salary_range": "China internship: 150-350 CNY/day; entry full-time: 12k-25k CNY/month varies by city.",
            "search_keywords": ["AI product operations", "workflow operations", "product assistant"],
            "application_strategy": "Show one workflow you improved, the metric you watched, and a concise product sense memo.",
        },
        {
            "title": "Employer Branding Strategist",
            "industry": "Recruiting, campus hiring, and HR tech",
            "fit_reason": "Uses storytelling, audience segmentation, event thinking, and platform content skills.",
            "entry_route": "Target campus recruitment, employer branding, HR content, or talent marketing internships.",
            "salary_range": "China internship: 120-300 CNY/day; entry full-time: 10k-20k CNY/month.",
            "search_keywords": ["employer branding", "campus recruitment", "talent marketing"],
            "application_strategy": "Prepare a sample campaign for one company and explain channel, message, and conversion goal.",
        },
        {
            "title": "Customer Education Designer",
            "industry": "SaaS, developer tools, fintech, and education technology",
            "fit_reason": "Turns complex information into usable lessons, onboarding flows, docs, and workshops.",
            "entry_route": "Look for user education, academy operations, knowledge base, or customer success enablement roles.",
            "salary_range": "China internship: 150-300 CNY/day; entry full-time: 11k-22k CNY/month.",
            "search_keywords": ["customer education", "user onboarding", "knowledge operations"],
            "application_strategy": "Submit a mini onboarding guide that teaches a real product feature in under five minutes.",
        },
        {
            "title": "Research and Insights Analyst",
            "industry": "Consulting, consumer research, internet strategy, and venture research",
            "fit_reason": "Rewards curiosity, synthesis, structured writing, interviews, and pattern finding.",
            "entry_route": "Apply for user research assistant, industry research intern, strategy analyst intern roles.",
            "salary_range": "China internship: 150-400 CNY/day; entry full-time: 12k-28k CNY/month.",
            "search_keywords": ["user research", "industry research", "strategy analyst intern"],
            "application_strategy": "Attach a two-page research brief with sources, insight, implication, and recommended action.",
        },
        {
            "title": "Community Growth Operator",
            "industry": "Consumer apps, education, creator economy, and B2B communities",
            "fit_reason": "Combines communication, event design, content rhythm, feedback loops, and retention thinking.",
            "entry_route": "Search for community operations, creator operations, user growth, or content community roles.",
            "salary_range": "China internship: 120-300 CNY/day; entry full-time: 10k-22k CNY/month.",
            "search_keywords": ["community operations", "creator operations", "user growth"],
            "application_strategy": "Bring a 30-day community activation plan with audience, cadence, and measurable outcomes.",
        },
    ]

    return {
        "transferable_skills_summary": summary,
        "career_paths": career_paths,
        "quick_wins": [
            "Pick two paths and search 20 real job descriptions to validate keyword overlap.",
            "Rewrite one resume section for each selected path without changing any facts.",
            "Create one proof-of-work artifact: campaign brief, research memo, workflow map, or onboarding guide.",
        ],
        "reality_check": {
            "best_fit": career_paths[0]["title"],
            "timeline": "1-2 weeks for validation, 2-4 weeks for portfolio evidence, 4-8 weeks for targeted applications.",
            "note": f"Prompt signal: {user_message[:120]}",
        },
    }


def build_action_summary(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "batch_triage":
        count = len(args.get("job_ids") or [])
        return f"Update {count} jobs to {args.get('status', 'selected status')}."
    if tool_name == "import_jobs_to_application_table":
        count = len(args.get("job_ids") or [])
        return f"Import {count} jobs into application tracking."
    if tool_name == "run_scraper":
        keywords = ", ".join(str(item) for item in (args.get("keywords") or []))
        source = args.get("source") or "selected source"
        return f"Run {source} scraper for {keywords or 'configured keywords'}."
    if tool_name == "generate_resume":
        return f"Generate tailored resume for job #{args.get('job_id', '')}."
    if tool_name == "auto_fill_calendar":
        return "Create calendar events from parsed interview notifications."
    if tool_name == "sync_email_notifications":
        return "Sync email notifications and parse interview-related messages."
    return f"Run {tool_name.replace('_', ' ')}."


def plan_action(tool_name: str, args: dict[str, Any], index: int = 1) -> dict[str, Any]:
    registry = get_default_tool_registry()
    risk_level = registry.get(tool_name, {}).get("risk_level", "confirm")
    return {
        "id": f"{tool_name}:{index}",
        "tool": tool_name,
        "args": args,
        "risk_level": risk_level,
        "requires_confirmation": risk_level == "confirm",
        "summary": build_action_summary(tool_name, args),
    }


async def execute_planned_actions(
    planned_actions: list[dict[str, Any]],
    *,
    registry: dict[str, dict[str, Any]] | None = None,
    confirmed_action_ids: list[str] | None = None,
) -> dict[str, Any]:
    registry = registry or get_default_tool_registry()
    confirmed = set(confirmed_action_ids or [])
    tool_calls: list[dict[str, Any]] = []
    blocked_actions: list[dict[str, Any]] = []

    for action in planned_actions:
        action_id = str(action.get("id") or "")
        tool_name = str(action.get("tool") or "")
        risk_level = str(action.get("risk_level") or "confirm")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}

        if risk_level == "confirm" and action_id not in confirmed:
            blocked_actions.append(action)
            continue

        entry = registry.get(tool_name) or {}
        handler = entry.get("handler")
        if handler is None:
            result = {"error": f"Tool {tool_name} has no handler"}
        else:
            clean_args = {k: v for k, v in args.items() if v is not None}
            result = await handler(**clean_args)
        tool_calls.append(
            {
                "tool": tool_name,
                "args": args,
                "result": result,
                "action_id": action_id,
            }
        )

    return {
        "tool_calls": tool_calls,
        "blocked_actions": blocked_actions,
    }
