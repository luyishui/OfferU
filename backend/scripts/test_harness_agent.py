from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.harness_agent import (  # noqa: E402
    build_career_exploration_fallback,
    classify_intent,
    get_default_tool_registry,
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


if __name__ == "__main__":
    test_registry_declares_risk_levels()
    test_classify_career_exploration_prompt()
    test_classify_job_workflow_prompt()
    test_career_fallback_shape_has_paths_and_next_steps()
    print("harness agent core tests passed")
