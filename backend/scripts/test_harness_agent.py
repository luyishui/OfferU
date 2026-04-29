from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.harness_agent import (  # noqa: E402
    build_career_exploration_fallback,
    build_application_import_preview,
    classify_intent,
    execute_planned_actions,
    get_default_tool_registry,
    plan_action,
    run_harness_agent_turn,
)


def test_registry_declares_risk_levels() -> None:
    registry = get_default_tool_registry()
    assert registry["get_profile"]["risk_level"] == "read"
    assert registry["list_jobs"]["risk_level"] == "read"
    assert registry["batch_triage"]["risk_level"] == "confirm"
    assert registry["import_jobs_to_application_table"]["risk_level"] == "confirm"
    assert registry["career_exploration"]["risk_level"] == "read"


def test_classify_career_exploration_prompt() -> None:
    intent = classify_intent("参考我的档案，找 5 个我没想到但适合我的职业方向")
    assert intent == "career_exploration"


def test_classify_job_workflow_prompt() -> None:
    intent = classify_intent("帮我抓取产品经理实习并筛选适合我的岗位")
    assert intent == "job_workflow"


def test_career_fallback_shape_has_paths_and_next_steps() -> None:
    payload = build_career_exploration_fallback(
        profile={
            "name": "Alex",
            "headline": "content operations intern",
            "target_roles": [{"role_name": "product operations", "fit": "primary"}],
            "sections_by_type": {"experience": 2, "project": 1},
        },
        user_message="帮我找更开阔的职业选择",
    )
    assert payload["transferable_skills_summary"]
    assert len(payload["career_paths"]) == 5
    first = payload["career_paths"][0]
    expected_keys = {
        "title",
        "industry",
        "fit_reason",
        "entry_route",
        "salary_range",
        "search_keywords",
        "application_strategy",
    }
    assert expected_keys <= set(first)
    assert len(payload["quick_wins"]) == 3
    assert payload["reality_check"]["timeline"]


def test_confirm_actions_block_batch_writes() -> None:
    action = plan_action("batch_triage", {"job_ids": [1, 2], "status": "screened"})
    assert action["risk_level"] == "confirm"
    assert action["requires_confirmation"] is True
    assert action["id"] == "batch_triage:1"


def test_confirmed_action_ids_execute_only_matching() -> None:
    calls: list[dict] = []

    async def fake_handler(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "kwargs": kwargs}

    registry = {
        "batch_triage": {
            "name": "batch_triage",
            "risk_level": "confirm",
            "handler": fake_handler,
            "description": "Batch triage jobs",
            "parameters": {},
        }
    }
    planned = [
        plan_action("batch_triage", {"job_ids": [1], "status": "screened"}),
        plan_action("batch_triage", {"job_ids": [2], "status": "ignored"}, index=2),
    ]

    result = asyncio.run(
        execute_planned_actions(
            planned,
            registry=registry,
            confirmed_action_ids=["batch_triage:2"],
        )
    )

    assert len(result["tool_calls"]) == 1
    assert calls == [{"job_ids": [2], "status": "ignored"}]


def test_application_import_preview_uses_stable_fields() -> None:
    job = {
        "id": 7,
        "title": "AI Product Intern",
        "company": "ExampleTech",
        "location": "Shanghai",
        "salary_text": "200/day",
        "source": "shixiseng",
        "apply_url": "https://example.com/apply",
        "url": "https://example.com/job",
    }
    preview = build_application_import_preview(job)
    assert preview["company_name"] == "ExampleTech"
    assert preview["job_title"] == "AI Product Intern"
    assert preview["location"] == "Shanghai"
    assert preview["salary_text"] == "200/day"
    assert preview["source"] == "shixiseng"
    assert preview["job_link"] == "https://example.com/apply"


def test_run_harness_agent_turn_returns_career_mode_with_fallback() -> None:
    async def fake_tool(name: str, args: dict):
        if name == "get_profile":
            return {"name": "Alex", "headline": "content operations"}
        if name == "list_jobs":
            return {"jobs": [], "total": 0}
        return {}

    response = asyncio.run(
        run_harness_agent_turn(
            messages=[{"role": "user", "content": "给我 5 个意想不到的职业方向"}],
            tool_runner=fake_tool,
        )
    )
    assert response["mode"] == "career_exploration"
    assert response["career_paths"]
    assert response["requires_confirmation"] is False


def test_route_request_model_defaults_confirmation_ids() -> None:
    from app.routes.harness_agent import HarnessAgentChatRequest

    request = HarnessAgentChatRequest(
        messages=[{"role": "user", "content": "hello"}],
    )
    assert request.confirmed_action_ids == []
    assert request.messages[0].role == "user"


if __name__ == "__main__":
    test_registry_declares_risk_levels()
    test_classify_career_exploration_prompt()
    test_classify_job_workflow_prompt()
    test_career_fallback_shape_has_paths_and_next_steps()
    test_confirm_actions_block_batch_writes()
    test_confirmed_action_ids_execute_only_matching()
    test_application_import_preview_uses_stable_fields()
    test_run_harness_agent_turn_returns_career_mode_with_fallback()
    test_route_request_model_defaults_confirmation_ids()
    print("harness agent core tests passed")
