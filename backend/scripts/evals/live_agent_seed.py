from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import models


SNAPSHOT_MODELS: tuple[type[Any], ...] = (
    models.Job,
    models.Pool,
    models.Profile,
    models.ProfileSection,
    models.Resume,
    models.ResumeSection,
    models.Application,
    models.ApplicationRecord,
    models.AgentTreeEntry,
    models.AgentMemory,
    models.ProposalCache,
    models.AgentSession,
)


async def seed_eval_db(db: AsyncSession) -> dict[str, Any]:
    actor_id = models.LOCAL_DEFAULT_ACTOR_ID
    batch = models.Batch(
        id="legacy-import",
        source="eval",
        keywords=["eval"],
        location="San Francisco",
        total_fetched=25,
        job_count=25,
    )
    db.add(batch)

    pools = {
        "shortlist": models.Pool(
            name="AI PM Shortlist",
            description="AI product and agent product roles for eval.",
            color="#2563eb",
            scope="picked",
            sort_order=10,
            owner_actor_id=actor_id,
        ),
        "backend": models.Pool(
            name="Backend Roles",
            description="Backend engineering roles.",
            color="#64748b",
            scope="picked",
            sort_order=20,
            owner_actor_id=actor_id,
        ),
        "ignored": models.Pool(
            name="Ignored",
            description="Ignored or archived jobs.",
            color="#94a3b8",
            scope="ignored",
            sort_order=30,
            owner_actor_id=actor_id,
        ),
        "data_product": models.Pool(
            name="Data Product Leads",
            description="Data product and analytics PM roles.",
            color="#0ea5e9",
            scope="picked",
            sort_order=40,
            owner_actor_id=actor_id,
        ),
        "agent_2026": models.Pool(
            name="Agent Product 2026",
            description="Historical picked pool for agent product roles.",
            color="#7c3aed",
            scope="picked",
            sort_order=50,
            owner_actor_id=actor_id,
        ),
        "growth_tests": models.Pool(
            name="Growth Product Tests 2026-07",
            description="Historical test-style pool with growth product noise.",
            color="#16a34a",
            scope="picked",
            sort_order=60,
            owner_actor_id=actor_id,
        ),
        "product_ops_nogroup": models.Pool(
            name="Product Ops NoGroup Duplicate",
            description="Continuation-style pool name used as synthetic noise.",
            color="#f59e0b",
            scope="picked",
            sort_order=70,
            owner_actor_id=actor_id,
        ),
        "resume_followup": models.Pool(
            name="Resume Followup Pool",
            description="Synthetic resume follow-up pool.",
            color="#ec4899",
            scope="picked",
            sort_order=80,
            owner_actor_id=actor_id,
        ),
        "duplicate_ai_pm": models.Pool(
            name="Duplicate AI PM Pool",
            description="Near-duplicate AI PM group.",
            color="#14b8a6",
            scope="picked",
            sort_order=90,
            owner_actor_id=actor_id,
        ),
        "zh_pm": models.Pool(
            name="中文互联网产品实习",
            description="Chinese internet product internship style noise.",
            color="#ef4444",
            scope="picked",
            sort_order=100,
            owner_actor_id=actor_id,
        ),
        "community_product": models.Pool(
            name="Community Product",
            description="Community and content product roles.",
            color="#8b5cf6",
            scope="picked",
            sort_order=110,
            owner_actor_id=actor_id,
        ),
        "analytics_roles": models.Pool(
            name="Analytics Roles",
            description="Product analyst and analytics role pool.",
            color="#06b6d4",
            scope="picked",
            sort_order=120,
            owner_actor_id=actor_id,
        ),
        "manual_agent_plan": models.Pool(
            name="manual-agent-plan picked rerun",
            description="Synthetic pool imitating historical agent plan artifacts.",
            color="#64748b",
            scope="picked",
            sort_order=130,
            owner_actor_id=actor_id,
        ),
        "enterprise_launch": models.Pool(
            name="Enterprise Launch PM",
            description="Enterprise launch and cross-functional PM roles.",
            color="#0284c7",
            scope="picked",
            sort_order=140,
            owner_actor_id=actor_id,
        ),
        "archive_duplicate": models.Pool(
            name="Archive Duplicate 2026",
            description="Archive-style pool with historical duplicate artifacts.",
            color="#71717a",
            scope="ignored",
            sort_order=150,
            owner_actor_id=actor_id,
        ),
        "ai_ops_shadow": models.Pool(
            name="AI Ops Shadow Shortlist",
            description="Near-match AI operations roles that should not define the main target.",
            color="#a855f7",
            scope="picked",
            sort_order=160,
            owner_actor_id=actor_id,
        ),
    }
    db.add_all(pools.values())
    await db.flush()

    jobs = {
        "acme_ai_pm": models.Job(
            title="AI Product Manager",
            company="Acme",
            location="San Francisco",
            source="manual",
            triage_status="inbox",
            raw_description=(
                "ACME_AI_PM_TARGET\n"
                "Own agent workflow product strategy, product analytics, cross-functional launch, "
                "and user research for enterprise AI users."
            ),
            summary="",
            keywords=["AI", "product", "agent workflow"],
            hash_key="eval-acme-ai-pm-target",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_backend": models.Job(
            title="Backend Engineer",
            company="Acme",
            location="San Francisco",
            source="manual",
            triage_status="inbox",
            raw_description="Java backend platform role focused on distributed systems and service reliability.",
            summary="Java backend and distributed systems.",
            keywords=["Java backend", "distributed systems"],
            hash_key="eval-acme-backend-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acmelia_pa": models.Job(
            title="Product Analyst",
            company="Acmelia",
            location="San Francisco",
            source="manual",
            triage_status="inbox",
            raw_description="Analytics-heavy product analyst role at Acmelia, not Acme.",
            summary="Product analytics role at a similarly named company.",
            keywords=["analytics"],
            hash_key="eval-acmelia-pa-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "beta_ai_pm": models.Job(
            title="AI Product Manager",
            company="BetaAI",
            location="Remote",
            source="manual",
            triage_status="inbox",
            raw_description="AI Product Manager for agent experiences at BetaAI, not Acme.",
            summary="AI product role at another company.",
            keywords=["AI", "agent"],
            hash_key="eval-betaai-ai-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "oldcorp_pm": models.Job(
            title="Product Manager",
            company="OldCorp",
            location="Remote",
            source="manual",
            triage_status="ignored",
            pool_id=pools["ignored"].id,
            raw_description="Legacy product role that should remain ignored.",
            summary="Ignored legacy PM role.",
            keywords=["legacy"],
            hash_key="eval-oldcorp-ignored-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_ai_ops": models.Job(
            title="AI Product Operations Intern",
            company="Acme",
            location="San Francisco",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["product_ops_nogroup"].id,
            raw_description="Assist AI product operations, release notes, customer feedback triage, and launch dashboards.",
            summary="Acme adjacent AI product operations role, not the target AI Product Manager.",
            keywords=["AI product operations", "launch", "dashboard"],
            hash_key="eval-acme-ai-ops-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_growth_pm": models.Job(
            title="Growth Product Manager",
            company="Acme",
            location="New York",
            source="manual-agent-plan",
            triage_status="picked",
            pool_id=pools["growth_tests"].id,
            raw_description="Growth product role focused on onboarding funnels, lifecycle experiments, and activation metrics.",
            summary="Growth PM at Acme with analytics but not AI workflow ownership.",
            keywords=["growth", "experiments", "analytics"],
            hash_key="eval-acme-growth-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_data_pm": models.Job(
            title="Data Product Manager",
            company="Acme",
            location="San Francisco",
            source="manual",
            triage_status="picked",
            pool_id=pools["data_product"].id,
            raw_description="Short JD. Own data platform roadmap and stakeholder intake.",
            summary="Data product manager at Acme; similar analytics vocabulary but not agent workflow.",
            keywords=["data product", "analytics", "stakeholders"],
            hash_key="eval-acme-data-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_marketing": models.Job(
            title="Product Marketing Associate",
            company="Acme",
            location="Remote",
            source="manual",
            triage_status="inbox",
            raw_description="Coordinate launch messaging, field enablement, and market research for AI tools.",
            summary="Marketing-adjacent role that should not be confused with product manager.",
            keywords=["launch messaging", "market research"],
            hash_key="eval-acme-product-marketing-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_labs_ai_pm": models.Job(
            title="AI Product Manager",
            company="Acme Labs",
            location="San Mateo",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["duplicate_ai_pm"].id,
            raw_description="AI Product Manager for developer tools at Acme Labs with agent prototyping.",
            summary="Same title, similar company name, not Acme.",
            keywords=["AI", "agent", "developer tools"],
            hash_key="eval-acme-labs-ai-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acmex_ai_pm": models.Job(
            title="AI Product Manager",
            company="Acmex",
            location="San Francisco",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["duplicate_ai_pm"].id,
            raw_description="AI PM role for automated analytics assistants at Acmex.",
            summary="Same title and similar company string.",
            keywords=["AI", "analytics assistant"],
            hash_key="eval-acmex-ai-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "agentflow_pm": models.Job(
            title="Product Manager",
            company="AgentFlow",
            location="San Francisco",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["agent_2026"].id,
            raw_description="Own agent workflow builder, enterprise pilots, and customer success feedback loops.",
            summary="Strong agent workflow match but different company and title.",
            keywords=["agent workflow", "enterprise pilots"],
            hash_key="eval-agentflow-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "metricmind_data_pm": models.Job(
            title="Data Product Manager",
            company="MetricMind",
            location="Remote",
            source="manual-agent-plan",
            triage_status="picked",
            pool_id=pools["analytics_roles"].id,
            raw_description="Own metric taxonomy, dashboards, and product analytics instrumentation.",
            summary="Analytics-heavy product role.",
            keywords=["metrics", "dashboard", "product analytics"],
            hash_key="eval-metricmind-data-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "contenthub_pm": models.Job(
            title="Community Product Manager",
            company="ContentHub",
            location="Los Angeles",
            source="中文手动创建",
            triage_status="picked",
            pool_id=pools["community_product"].id,
            raw_description="负责社区产品、内容增长、创作者反馈闭环和活动运营。",
            summary="中文社区产品岗位，方向接近但不是 AI PM。",
            keywords=["社区产品", "内容增长"],
            hash_key="eval-contenthub-community-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "chainlabs_pm": models.Job(
            title="Product Manager",
            company="ChainLabs",
            location="Remote",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["ignored"].id,
            raw_description="Blockchain wallet product role focused on token onboarding and web3 growth.",
            summary="Blockchain product noise that should not become the resume main line.",
            keywords=["blockchain", "web3", "wallet"],
            hash_key="eval-chainlabs-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "javaware_backend": models.Job(
            title="Backend Engineer",
            company="JavaWare",
            location="Austin",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["backend"].id,
            raw_description="Java backend role for distributed systems, API scalability, and storage services.",
            summary="Backend engineering noise.",
            keywords=["Java backend", "distributed systems"],
            hash_key="eval-javaware-backend-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "betaai_ops": models.Job(
            title="Agent Product Operations",
            company="BetaAI",
            location="Remote",
            source="manual",
            triage_status="picked",
            pool_id=pools["agent_2026"].id,
            raw_description="Coordinate agent product launches and customer rollout checklists.",
            summary="Agent product operations at BetaAI, not PM.",
            keywords=["agent", "launch"],
            hash_key="eval-betaai-agent-ops-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "zetaai_ai_pm": models.Job(
            title="AI Product Manager",
            company="ZetaAI",
            location="Seattle",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["duplicate_ai_pm"].id,
            raw_description="AI Product Manager for assistant analytics and customer research tooling.",
            summary="Same title, different company.",
            keywords=["AI", "customer research", "analytics"],
            hash_key="eval-zetaai-ai-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "nogroup_ai_pm": models.Job(
            title="AI Product Manager",
            company="NoGroup Labs",
            location="San Francisco",
            source="manual-agent-plan",
            triage_status="picked",
            raw_description="Duplicate-looking AI PM posting with sparse description and no pool.",
            summary="NoGroup duplicate AI PM artifact.",
            keywords=["AI", "PM", "duplicate"],
            hash_key="eval-nogroup-ai-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "manualplan_analyst": models.Job(
            title="Product Analyst",
            company="ManualPlan",
            location="New York",
            source="manual-agent-plan",
            triage_status="picked",
            pool_id=pools["analytics_roles"].id,
            raw_description="",
            summary="Analytics role with empty JD; requires summary/keywords interpretation.",
            keywords=["analytics", "SQL", "experiments"],
            hash_key="eval-manualplan-analyst-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_enterprise_launch_pm": models.Job(
            title="Enterprise AI Launch PM",
            company="Acme",
            location="San Francisco",
            source="manual-agent-plan",
            triage_status="picked",
            pool_id=pools["enterprise_launch"].id,
            raw_description=(
                "Enterprise launch PM role at Acme focused on rollout governance, "
                "stakeholder enablement, and product analytics dashboards. Similar launch vocabulary, "
                "but not the AI Product Manager target."
            ),
            summary="Acme enterprise launch PM, similar launch and analytics wording.",
            keywords=["enterprise launch", "product analytics", "stakeholders"],
            hash_key="eval-acme-enterprise-launch-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "acme_ai_product_pm": models.Job(
            title="Product Manager",
            company="Acme AI",
            location="San Francisco",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["duplicate_ai_pm"].id,
            raw_description=(
                "Product Manager at Acme AI for customer analytics copilots and agent-style reporting. "
                "Company name is intentionally close to Acme."
            ),
            summary="Similar company name and AI product wording, not Acme.",
            keywords=["AI product", "analytics copilot"],
            hash_key="eval-acme-ai-product-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "bettaai_ai_pm": models.Job(
            title="AI Product Manager",
            company="BettaAI",
            location="Remote",
            source="linkedin",
            triage_status="picked",
            pool_id=pools["duplicate_ai_pm"].id,
            raw_description="AI Product Manager for workflow agents at BettaAI, a near-name duplicate of BetaAI.",
            summary="Same title and near-duplicate company name.",
            keywords=["AI", "agent workflow"],
            hash_key="eval-bettaai-ai-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "nova_analytics_agent_pm": models.Job(
            title="AI Agent PM",
            company="Nova Analytics",
            location="San Francisco",
            source="manual",
            triage_status="picked",
            pool_id=pools["ai_ops_shadow"].id,
            raw_description=(
                "AI Agent PM role for analytics automation and customer-facing insight workflows. "
                "Intentionally close to the create_job_auto_confirm Nova Labs prompt."
            ),
            summary="Nova near-match that should not block creating Nova Labs.",
            keywords=["AI Agent PM", "analytics automation"],
            hash_key="eval-nova-analytics-agent-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
        "edgecase_ai_ops_pm": models.Job(
            title="AI Product Ops Manager",
            company="EdgeCase",
            location="Remote",
            source="中文手动创建",
            triage_status="ignored",
            pool_id=pools["archive_duplicate"].id,
            raw_description="历史测试风格岗位：AI 产品运营、上线检查表、客户反馈整理，已经归档。",
            summary="Archived AI product ops duplicate-style noise.",
            keywords=["AI 产品运营", "归档", "duplicate"],
            hash_key="eval-edgecase-ai-ops-pm-noise",
            batch_id="legacy-import",
            owner_actor_id=actor_id,
        ),
    }
    db.add_all(jobs.values())
    await db.flush()

    profile = models.Profile(
        name="Eval Candidate",
        school="State University",
        major="Information Systems",
        degree="MS",
        headline="Product manager focused on AI workflow products",
        email="eval@example.com",
        is_default=True,
        owner_actor_id=actor_id,
    )
    db.add(profile)
    await db.flush()

    profile_sections = {
        "agent_workflow": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="AI workflow product internship",
            sort_order=10,
            content_json={
                "marker": "PROFILE_AGENT_WORKFLOW_TARGET",
                "description": (
                    "Led an agent workflow product internship with user research, data analysis, "
                    "and cross-functional execution."
                ),
            },
            source="manual",
            confidence=1.0,
            owner_actor_id=actor_id,
        ),
        "analytics_dashboard": models.ProfileSection(
            profile_id=profile.id,
            section_type="project",
            title="Analytics dashboard project",
            sort_order=20,
            content_json={
                "marker": "PROFILE_ANALYTICS_TARGET",
                "description": "Built metrics dashboard and experiment analysis for product decisions.",
            },
            source="manual",
            confidence=1.0,
            owner_actor_id=actor_id,
        ),
        "java_backend": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="Java backend service",
            sort_order=30,
            content_json={
                "marker": "PROFILE_JAVA_NOISE",
                "description": "Implemented Java backend service APIs.",
            },
            source="manual",
            confidence=1.0,
            owner_actor_id=actor_id,
        ),
        "blockchain": models.ProfileSection(
            profile_id=profile.id,
            section_type="project",
            title="Blockchain hackathon",
            sort_order=40,
            content_json={
                "marker": "PROFILE_BLOCKCHAIN_NOISE",
                "description": "Built a blockchain hackathon prototype.",
            },
            source="manual",
            confidence=1.0,
            owner_actor_id=actor_id,
        ),
        "growth_experiment": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="Growth experiment internship",
            sort_order=50,
            content_json={
                "marker": "PROFILE_GROWTH_SECONDARY",
                "description": "Supported onboarding funnel experiments and retention metric reviews for a consumer product.",
            },
            source="manual",
            confidence=0.9,
            owner_actor_id=actor_id,
        ),
        "community_ops": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="Community operations assistant",
            sort_order=60,
            content_json={
                "marker": "PROFILE_COMMUNITY_NOISE",
                "description": "Managed creator community events and content calendar operations.",
            },
            source="manual",
            confidence=0.85,
            owner_actor_id=actor_id,
        ),
        "data_research": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="User research and analytics assistant",
            sort_order=70,
            content_json={
                "marker": "PROFILE_RESEARCH_SECONDARY",
                "description": "Synthesized interview notes, cohort metrics, and experiment readouts for roadmap reviews.",
            },
            source="manual",
            confidence=0.95,
            owner_actor_id=actor_id,
        ),
        "sales_ops": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="Sales operations volunteer",
            sort_order=80,
            content_json={
                "marker": "PROFILE_SALES_NOISE",
                "description": "Cleaned CRM fields and prepared weekly pipeline reports.",
            },
            source="manual",
            confidence=0.75,
            owner_actor_id=actor_id,
        ),
        "ux_case_study": models.ProfileSection(
            profile_id=profile.id,
            section_type="project",
            title="UX case study",
            sort_order=90,
            content_json={
                "marker": "PROFILE_UX_SECONDARY",
                "description": "Ran usability interviews and produced low-fidelity prototypes for a campus planning app.",
            },
            source="manual",
            confidence=0.8,
            owner_actor_id=actor_id,
        ),
        "ops_dashboard": models.ProfileSection(
            profile_id=profile.id,
            section_type="project",
            title="Operations dashboard",
            sort_order=100,
            content_json={
                "marker": "PROFILE_OPS_DASHBOARD_NOISE",
                "description": "Built a lightweight internal dashboard for task tracking and weekly operations reporting.",
            },
            source="manual",
            confidence=0.8,
            owner_actor_id=actor_id,
        ),
        "enterprise_launch": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="Enterprise launch coordination",
            sort_order=110,
            content_json={
                "marker": "PROFILE_ENTERPRISE_LAUNCH_SECONDARY",
                "description": "Coordinated beta launch notes, stakeholder feedback, and rollout metrics for an internal AI assistant.",
            },
            source="manual",
            confidence=0.88,
            owner_actor_id=actor_id,
        ),
        "finance_ops": models.ProfileSection(
            profile_id=profile.id,
            section_type="experience",
            title="Finance operations reporting",
            sort_order=120,
            content_json={
                "marker": "PROFILE_FINANCE_OPS_NOISE",
                "description": "Prepared expense reports and vendor spreadsheets for a campus finance office.",
            },
            source="manual",
            confidence=0.7,
            owner_actor_id=actor_id,
        ),
    }
    db.add_all(profile_sections.values())
    await db.flush()

    resumes: dict[str, models.Resume] = {}
    resume_specs = [
        ("general_product", "General Product Resume", "General product resume not tailored to Acme.", "manual", []),
        ("ai_agent_old", "AI Agent PM Resume - old", "Older generated resume for agent product roles.", "operator_generate_resume", [jobs["agentflow_pm"].id]),
        ("data_product_old", "Data Product Resume", "Generated resume for analytics and metrics roles.", "operator_generate_resume", [jobs["metricmind_data_pm"].id]),
        ("growth_pm_old", "Growth Product Resume 2026-07", "Generated resume for onboarding and growth experiments.", "operator_generate_resume", [jobs["acme_growth_pm"].id]),
        ("backend_misfit", "Backend Platform Resume", "Resume that overweights Java backend service work.", "operator_generate_resume", [jobs["javaware_backend"].id]),
        ("blockchain_misfit", "Web3 Product Resume", "Resume that overweights blockchain hackathon material.", "operator_generate_resume", [jobs["chainlabs_pm"].id]),
        ("community_pm", "Community Product Resume", "Resume for community and content product roles.", "operator_generate_resume", [jobs["contenthub_pm"].id]),
        ("acme_data", "Acme Data PM Resume", "Near-match resume for Acme data product role.", "operator_generate_resume", [jobs["acme_data_pm"].id]),
        ("beta_ai", "BetaAI AI PM Resume", "Generated resume for BetaAI AI PM, not Acme.", "operator_generate_resume", [jobs["beta_ai_pm"].id]),
        ("zeta_ai", "ZetaAI AI PM Resume", "Generated resume for another AI PM target.", "operator_generate_resume", [jobs["zetaai_ai_pm"].id]),
        ("manual_zh", "中文产品实习简历", "手动维护的中文产品实习简历。", "manual", []),
        ("per_job_acme_ops", "Acme AI Ops Resume", "Per-job resume for Acme AI product operations.", "per_job", [jobs["acme_ai_ops"].id]),
        ("duplicate_1", "AI PM Resume Duplicate", "Historical duplicate generated resume.", "operator_generate_resume", [jobs["acme_labs_ai_pm"].id]),
        ("duplicate_2", "AI PM Resume Duplicate rerun", "Continuation rerun generated resume.", "operator_generate_resume", [jobs["acmex_ai_pm"].id]),
        ("analytics_short", "Analytics Short Resume", "Short generated resume with metrics emphasis.", "operator_generate_resume", [jobs["manualplan_analyst"].id]),
        ("ops_general", "Product Ops Resume NoGroup", "NoGroup resume artifact for operations roles.", "operator_generate_resume", [jobs["betaai_ops"].id]),
        ("research_pm", "Research Product Resume", "Generated resume emphasizing user research.", "operator_generate_resume", [jobs["acme_marketing"].id]),
        ("campus_general", "Campus Product Resume", "Generic campus product resume.", "manual", []),
        ("acme_enterprise_launch", "Acme Enterprise Launch Resume", "Generated resume for Acme enterprise launch PM.", "operator_generate_resume", [jobs["acme_enterprise_launch_pm"].id]),
        ("acme_ai_company_confuser", "Acme AI Product Resume", "Generated resume for Acme AI near-name company.", "operator_generate_resume", [jobs["acme_ai_product_pm"].id]),
        ("betta_ai_duplicate", "BettaAI AI PM Resume", "Generated resume for BettaAI duplicate AI PM.", "operator_generate_resume", [jobs["bettaai_ai_pm"].id]),
        ("nova_analytics_agent", "Nova Analytics Agent PM Resume", "Generated resume for Nova Analytics near-match role.", "operator_generate_resume", [jobs["nova_analytics_agent_pm"].id]),
        ("edgecase_ai_ops", "EdgeCase AI Ops Resume archived", "Generated resume artifact for archived AI ops role.", "operator_generate_resume", [jobs["edgecase_ai_ops_pm"].id]),
        ("agent_product_rerun_july", "Agent Product Resume rerun 2026-07", "Continuation rerun resume for generic agent product roles.", "operator_generate_resume", [jobs["agentflow_pm"].id]),
        ("analytics_dashboard_rerun", "Analytics Dashboard Resume Duplicate", "Historical duplicate for analytics dashboard roles.", "operator_generate_resume", [jobs["metricmind_data_pm"].id]),
        ("community_growth_rerun", "Community Growth Resume rerun", "Generated resume for community growth product roles.", "operator_generate_resume", [jobs["contenthub_pm"].id]),
        ("backend_noise_rerun", "Backend Noise Resume rerun", "Generated resume that should remain a Java backend distractor.", "operator_generate_resume", [jobs["acme_backend"].id]),
        ("blockchain_noise_rerun", "Blockchain Noise Resume Duplicate", "Generated resume that should remain a blockchain distractor.", "operator_generate_resume", [jobs["chainlabs_pm"].id]),
        ("enterprise_short", "Enterprise Launch Short Resume", "Short generated resume for launch coordination roles.", "operator_generate_resume", [jobs["acme_enterprise_launch_pm"].id]),
        ("per_job_beta_ops", "BetaAI Ops Per-job Resume", "Per-job resume for BetaAI operations noise.", "per_job", [jobs["betaai_ops"].id]),
    ]
    for index, (key, title, summary, source_mode, source_job_ids) in enumerate(resume_specs, start=1):
        resume = models.Resume(
            user_name=profile.name,
            title=title,
            summary=summary,
            contact_json={"email": profile.email} if key == "general_product" else {},
            language="zh",
            source_mode=source_mode,
            source_job_ids=source_job_ids,
            is_primary=key == "general_product",
            owner_actor_id=actor_id,
        )
        resumes[key] = resume
        db.add(resume)
        await db.flush()
        for section in _resume_sections_for_seed(key, index):
            db.add(
                models.ResumeSection(
                    resume_id=resume.id,
                    section_type=section["section_type"],
                    sort_order=int(section["sort_order"]),
                    title=str(section["title"]),
                    visible=True,
                    content_json=section["content_json"],
                    owner_actor_id=actor_id,
                )
            )

    resume = resumes["general_product"]

    applications = {
        "acme_ai_pm": models.Application(
            job_id=jobs["acme_ai_pm"].id,
            status="draft",
            notes="Eval Acme AI PM application.",
            owner_actor_id=actor_id,
        ),
        "beta_ai_pm": models.Application(
            job_id=jobs["beta_ai_pm"].id,
            status="submitted",
            notes="Eval BetaAI submitted application.",
            owner_actor_id=actor_id,
        ),
        "agentflow": models.Application(
            job_id=jobs["agentflow_pm"].id,
            status="interview",
            notes="Synthetic interview-stage application.",
            owner_actor_id=actor_id,
        ),
    }
    db.add_all(applications.values())
    db.add_all(
        [
            models.ApplicationRecord(
                job_ref_id=jobs["acme_ai_pm"].id,
                company_name="Acme",
                job_title="AI Product Manager",
                location="San Francisco",
                source="eval",
                custom_values={"apply_status": "draft"},
                owner_actor_id=actor_id,
            ),
            models.ApplicationRecord(
                job_ref_id=jobs["beta_ai_pm"].id,
                company_name="BetaAI",
                job_title="AI Product Manager",
                location="Remote",
                source="eval",
                custom_values={"apply_status": "submitted"},
                owner_actor_id=actor_id,
            ),
            models.ApplicationRecord(
                job_ref_id=jobs["agentflow_pm"].id,
                company_name="AgentFlow",
                job_title="Product Manager",
                location="San Francisco",
                source="eval",
                custom_values={"apply_status": "面试中", "round": "一面"},
                owner_actor_id=actor_id,
            ),
            models.ApplicationRecord(
                job_ref_id=jobs["metricmind_data_pm"].id,
                company_name="MetricMind",
                job_title="Data Product Manager",
                location="Remote",
                source="eval",
                custom_values={"apply_status": "待投递"},
                owner_actor_id=actor_id,
            ),
            models.ApplicationRecord(
                job_ref_id=jobs["contenthub_pm"].id,
                company_name="ContentHub",
                job_title="Community Product Manager",
                location="Los Angeles",
                source="eval",
                custom_values={"apply_status": "已拒绝"},
                owner_actor_id=actor_id,
            ),
            models.ApplicationRecord(
                job_ref_id=None,
                company_name="Synthetic Offer Co",
                job_title="Product Intern",
                location="Remote",
                source="eval",
                custom_values={"apply_status": "已录用", "note": "Application record without linked job."},
                owner_actor_id=actor_id,
            ),
            models.ApplicationRecord(
                job_ref_id=jobs["acme_backend"].id,
                company_name="Acme",
                job_title="Backend Engineer",
                location="San Francisco",
                source="eval",
                custom_values={"apply_status": "已拒绝", "reason": "方向不匹配"},
                owner_actor_id=actor_id,
            ),
            models.ApplicationRecord(
                job_ref_id=jobs["nova_analytics_agent_pm"].id,
                company_name="Nova Analytics",
                job_title="AI Agent PM",
                location="San Francisco",
                source="eval",
                custom_values={"apply_status": "待投递", "note": "near-match noise"},
                owner_actor_id=actor_id,
            ),
        ]
    )

    db.add_all(
        [
            models.AgentMemory(
                memory_id=f"eval-memory-{uuid.uuid4().hex[:12]}",
                actor_id=actor_id,
                session_id="",
                category="style_preferences",
                topic="job",
                content_json={"language": "请用中文回答", "style": "简洁，先给结论"},
                confidence=1.0,
            ),
            models.AgentMemory(
                memory_id=f"eval-memory-{uuid.uuid4().hex[:12]}",
                actor_id=actor_id,
                session_id="",
                category="constraints",
                topic="resume",
                content_json={"avoid": ["不要突出 blockchain", "不要突出 Java backend", "不要编造没有证据的经历"]},
                confidence=1.0,
            ),
            models.AgentMemory(
                memory_id=f"eval-memory-{uuid.uuid4().hex[:12]}",
                actor_id=actor_id,
                session_id="",
                category="style_preferences",
                topic="resume",
                content_json={"format": "先说结论，再给 2-3 条理由", "tone": "务实"},
                confidence=0.75,
            ),
            models.AgentMemory(
                memory_id=f"eval-memory-{uuid.uuid4().hex[:12]}",
                actor_id=actor_id,
                session_id="",
                category="workflow_preferences",
                topic="applications",
                content_json={"preference": "高风险批量操作前先说明范围"},
                confidence=0.7,
            ),
        ]
    )

    await db.commit()
    return {
        "actor_id": actor_id,
        "pools": {key: pool.id for key, pool in pools.items()},
        "jobs": {key: job.id for key, job in jobs.items()},
        "profile_id": profile.id,
        "profile_sections": {key: section.id for key, section in profile_sections.items()},
        "resume_id": resume.id,
        "applications": {key: application.id for key, application in applications.items()},
    }


def _resume_sections_for_seed(key: str, index: int) -> list[dict[str, Any]]:
    topic = key.replace("_", " ")
    skill_noise = {
        "backend_misfit": ["Java", "distributed systems", "API reliability"],
        "blockchain_misfit": ["blockchain", "wallet growth", "web3 community"],
        "data_product_old": ["SQL", "dashboard", "experiment analysis"],
        "ai_agent_old": ["agent workflow", "user research", "product analytics"],
    }.get(key, ["product analytics", "user research", "cross-functional execution"])
    sections = [
        {
            "section_type": "skills",
            "sort_order": 10,
            "title": "Skills",
            "content_json": [{"category": "Product", "items": skill_noise}],
        },
        {
            "section_type": "workExperiences" if index % 3 else "personalExperiences",
            "sort_order": 20,
            "title": "Experience",
            "content_json": [
                {
                    "company": "Synthetic Lab",
                    "position": "Product Intern",
                    "description": f"Worked on {topic} planning, stakeholder updates, and launch notes.",
                }
            ],
        },
        {
            "section_type": "education" if index % 4 == 0 else "projects",
            "sort_order": 30,
            "title": "Education" if index % 4 == 0 else "Projects",
            "content_json": [
                {
                    "school": "State University",
                    "degree": "MS",
                    "major": "Information Systems",
                    "description": "Coursework in analytics, product strategy, and human-centered design.",
                }
                if index % 4 == 0
                else {
                    "name": "Synthetic Product Project",
                    "description": f"Built a small {topic} case study with metrics and user feedback.",
                }
            ],
        },
    ]
    if key in _TWO_SECTION_RESUME_KEYS:
        return sections[:2]
    return sections


_TWO_SECTION_RESUME_KEYS = {
    "acme_enterprise_launch",
    "acme_ai_company_confuser",
    "betta_ai_duplicate",
    "nova_analytics_agent",
    "edgecase_ai_ops",
    "agent_product_rerun_july",
    "analytics_dashboard_rerun",
    "community_growth_rerun",
    "backend_noise_rerun",
    "blockchain_noise_rerun",
    "enterprise_short",
    "per_job_beta_ops",
}


async def snapshot_db(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    snapshot: dict[str, list[dict[str, Any]]] = {}
    for model_cls in SNAPSHOT_MODELS:
        mapper = sa_inspect(model_cls)
        order_columns = list(mapper.primary_key)
        statement = select(model_cls)
        if order_columns:
            statement = statement.order_by(*order_columns)
        rows = (await db.execute(statement)).scalars().all()
        snapshot[model_cls.__name__] = [_row_to_dict(row) for row in rows]
    return snapshot


def snapshot_text(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)


def _row_to_dict(row: Any) -> dict[str, Any]:
    mapper = sa_inspect(row.__class__)
    payload: dict[str, Any] = {}
    for column in mapper.columns:
        payload[column.key] = _json_value(getattr(row, column.key))
    return payload


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
