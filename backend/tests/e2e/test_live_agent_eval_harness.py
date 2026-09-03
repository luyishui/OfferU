from __future__ import annotations

import asyncio
import httpx
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from scripts.evals.live_agent_cases import EvalTurn, get_case, cases_for_suite
from scripts.evals.live_agent_eval import _load_app_for_live_eval, _run_stream_turn
from scripts.evals.live_agent_grader import (
    _any_confirm_failure,
    _has_production_failure,
    _production_confirm_failure,
    grade_case,
)
from scripts.evals.live_agent_seed import seed_eval_db, snapshot_db
from scripts.evals.live_agent_trace import redact_for_log


class _FakeConfirmResponse:
    status_code = 200
    text = '{"ok": true, "status": "confirmed", "continuation": {"assistant_message": "Confirmed follow-up."}}'

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class _FakeStreamResponse:
    status_code = 200

    def __init__(self, client: "_FakeClient") -> None:
        self.client = client

    async def __aenter__(self) -> "_FakeStreamResponse":
        self.client.stream_active = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.client.stream_active = False

    async def aiter_lines(self):
        lines = [
            "event: proposal",
            'data: {"proposal_id": "prop_live_eval", "proposal": {"proposal_id": "prop_live_eval", "operator_session_id": "live_read_job_with_noise"}}',
            "",
            "event: final",
            'data: {"ok": true, "assistant_message": "Turn final."}',
            "",
        ]
        for line in lines:
            await asyncio.sleep(0)
            yield line


class _FakeClient:
    def __init__(self) -> None:
        self.stream_active = False
        self.confirm_stream_states: list[bool] = []

    def stream(self, method: str, route: str, json: dict[str, Any]):
        return _FakeStreamResponse(self)

    async def post(self, route: str, json: dict[str, Any]):
        self.confirm_stream_states.append(self.stream_active)
        return _FakeConfirmResponse()


class _QueuedConfirmResponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


class _QueuedProposalClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.confirmed_ids: list[str] = []

    async def post(self, route: str, json: dict[str, Any]):
        self.confirm_stream_states.append(self.stream_active)
        proposal_id = route.rsplit("/", 2)[-2]
        self.confirmed_ids.append(proposal_id)
        if proposal_id == "prop_live_eval":
            return _QueuedConfirmResponse(
                {
                    "ok": True,
                    "status": "confirmed",
                    "continuation": {
                        "assistant_message": "Created a follow-up proposal.",
                        "proposals": [
                            {
                                "proposal_id": "prop_second",
                                "operator_session_id": "live_read_job_with_noise",
                            }
                        ],
                    },
                }
            )
        return _QueuedConfirmResponse({"ok": True, "status": "confirmed", "assistant_message": "Done."})


def test_live_eval_confirms_proposals_after_stream_context_exits(tmp_path: Path) -> None:
    async def body() -> None:
        client = _FakeClient()
        case = get_case("read_job_with_noise")
        events: list[dict[str, Any]] = []
        confirm_log: list[dict[str, Any]] = []
        errors: list[str] = []

        await _run_stream_turn(
            client,
            case,
            EvalTurn("请更新一下。"),
            1,
            tmp_path,
            events,
            confirm_log,
            errors,
        )

        assert errors == []
        assert client.confirm_stream_states == [False]
        assert [event["event"] for event in events] == ["proposal", "final", "proposal_confirm"]
        assert confirm_log[0]["proposal_id"] == "prop_live_eval"
        assert confirm_log[0]["responses"][0]["response"]["continuation"]["assistant_message"] == "Confirmed follow-up."

    asyncio.run(body())


def test_live_eval_drains_proposals_created_by_confirmation_continuation(tmp_path: Path) -> None:
    async def body() -> None:
        client = _QueuedProposalClient()
        case = get_case("read_job_with_noise")
        events: list[dict[str, Any]] = []
        confirm_log: list[dict[str, Any]] = []
        errors: list[str] = []

        await _run_stream_turn(
            client,
            case,
            EvalTurn("请连续更新一下。"),
            1,
            tmp_path,
            events,
            confirm_log,
            errors,
        )

        assert errors == []
        assert client.confirmed_ids == ["prop_live_eval", "prop_second"]
        assert [item["proposal_id"] for item in confirm_log] == ["prop_live_eval", "prop_second"]
        assert [event["event"] for event in events].count("proposal_confirm") == 2

    asyncio.run(body())


def test_live_eval_extracts_nested_result_response_proposals() -> None:
    from scripts.evals import live_agent_eval

    payload = {
        "result": {
            "response": {
                "continuation": {
                    "proposals": [
                        {
                            "proposal_id": "prop_nested_one",
                            "operator_session_id": "session-1",
                        },
                        {
                            "proposal": {
                                "id": "prop_nested_two",
                                "operator_session_id": "session-1",
                            }
                        },
                    ]
                }
            }
        }
    }

    assert live_agent_eval._proposal_targets_from_payload(payload) == [
        live_agent_eval.ProposalDecisionTarget("prop_nested_one", "session-1"),
        live_agent_eval.ProposalDecisionTarget("prop_nested_two", "session-1"),
    ]


def test_live_eval_smoke_suite_is_first_six_cases() -> None:
    smoke_ids = [case.case_id for case in cases_for_suite("smoke")]
    deep_ids = [case.case_id for case in cases_for_suite("deep")]

    assert smoke_ids == [
        "read_job_with_noise",
        "multi_turn_context_retention",
        "create_job_auto_confirm",
        "patch_job_triage_auto_confirm",
        "organize_jobs_pool",
        "memory_preference",
    ]
    assert len(deep_ids) == 10


def test_live_eval_complex_suite_contract() -> None:
    complex_cases = list(cases_for_suite("complex"))
    complex_ids = [case.case_id for case in complex_cases]

    assert complex_ids == [
        "job_application_resume_bundle",
        "partial_scope_then_clarify",
        "resume_revision_chain",
        "application_material_chain",
        "memory_write_then_use",
        "compaction_multi_proposal_survival",
        "unsafe_bulk_cleanup_boundary",
        "branch_navigation_context",
    ]
    assert all(case.suite == "complex" for case in complex_cases)
    assert all(case.case_id not in [item.case_id for item in cases_for_suite("deep")] for case in complex_cases)
    assert get_case("compaction_multi_proposal_survival").settings_patch == {
        "llm_context_window": 2200,
        "agent_reserve_tokens": 500,
        "agent_keep_recent_tokens": 700,
    }
    assert [turn.action for turn in get_case("branch_navigation_context").turns] == [
        "reply",
        "navigate_previous_leaf",
        "reply",
    ]
    forbidden_prompt_markers = (
        "query_records",
        "get_record",
        "create_record",
        "patch_record",
        "delete_or_archive_record",
        "invoke_action",
        "ApplicationRecord",
        "AgentTreeEntry",
        "ProposalCache",
        "record_id",
        "grader",
        "ACME_AI_PM_TARGET",
    )
    for case in complex_cases:
        prompt_text = "\n".join(turn.user for turn in case.turns)
        assert not any(marker in prompt_text for marker in forbidden_prompt_markers)


def test_live_eval_complex_graders_are_registered() -> None:
    for case in cases_for_suite("complex"):
        result = grade_case(
            case,
            seed_ids={},
            before={},
            after={},
            events=[],
            tool_calls=[],
            confirmed_proposals=[],
        )
        assert "No grader registered." not in result["reasons"]


def test_live_eval_repeat_summary_reports_pass_rate(tmp_path: Path, monkeypatch) -> None:
    from scripts.evals import live_agent_eval

    async def fake_run_case(case, *, run_dir: Path, keep_db: bool):
        passed = run_dir.name.endswith("-01")
        return live_agent_eval.CaseRunResult(
            case_id=case.case_id,
            passed=passed,
            run_dir=run_dir / case.case_id,
            grader={"scores": {"state": 1.0}, "reasons": ["ok" if passed else "fail"]},
        )

    monkeypatch.setattr(live_agent_eval, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(live_agent_eval, "run_case", fake_run_case)
    monkeypatch.setattr(live_agent_eval, "_ensure_findings_file", lambda: None)

    async def body() -> None:
        summary = await live_agent_eval.run_suite_repeats(
            [get_case("read_job_with_noise")],
            repeat=2,
            keep_db=False,
        )

        assert summary["total_runs"] == 2
        assert summary["passed_runs"] == 1
        assert summary["pass_rate"] == 0.5

    asyncio.run(body())


def test_live_eval_writes_human_trace_artifacts(tmp_path: Path) -> None:
    from scripts.evals import live_agent_eval

    events = [
        {"turn_index": 1, "event": "final", "data": {"assistant_message": "已完成。"}},
        {"turn_index": 1, "event": "proposal_confirm", "data": {"proposal_id": "prop_20260705-230301"}},
    ]
    tool_calls = [{"tool_name": "query_records", "args": {"model": "job"}, "is_error": False}]
    proposals = [{"proposal_id": "prop_20260705-230301", "status": "confirmed", "tool_name": "patch_record"}]
    changed = {"Job": {"modified": [{"before": {"id": 1}, "after": {"id": 1, "triage_status": "picked"}}]}}
    grade = {"passed": True, "reasons": ["state ok"], "scores": {"state": 1.0}}

    live_agent_eval._write_case_trace_artifacts(
        tmp_path,
        get_case("read_job_with_noise"),
        events=events,
        tool_calls=tool_calls,
        proposals=proposals,
        confirmed_proposals=proposals,
        changed=changed,
        grade=grade,
    )

    trace_md = (tmp_path / "trace.md").read_text(encoding="utf-8")
    compact = json.loads((tmp_path / "trace_compact.json").read_text(encoding="utf-8"))
    assert "## Turns" in trace_md
    assert "- confirmed_proposal_count: `1`" in trace_md
    assert "- pending_proposal_count: `0`" in trace_md
    assert "- tool_call_count: `1`" in trace_md
    assert "- db_changed_tables: `Job`" in trace_md
    assert "## Tool Calls" in trace_md
    assert "## Proposal Confirms" in trace_md
    assert "## DB Changed Records" in trace_md
    assert compact["final_text"] == "已完成。"
    assert compact["tool_calls"][0]["tool_name"] == "query_records"


def test_live_eval_redacts_exact_env_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LIVE_EVAL_LLM_API_KEY", "provider-key-without-sk-prefix")

    redacted = redact_for_log({"message": "provider echoed provider-key-without-sk-prefix"})

    assert redacted["message"] == "provider echoed [REDACTED]"


def test_live_eval_redaction_preserves_run_and_proposal_ids_but_redacts_phone() -> None:
    redacted = redact_for_log(
        {
            "run_id": "20260705-230301",
            "proposal_id": "prop_20260705-230301",
            "created_at": "2026-07-05T16:22:38",
            "message": "call me at +1 (415) 555-0100",
        }
    )

    assert redacted["run_id"] == "20260705-230301"
    assert redacted["proposal_id"] == "prop_20260705-230301"
    assert redacted["created_at"] == "2026-07-05T16:22:38"
    assert redacted["message"] == "call me at [REDACTED_PHONE]"


def test_live_eval_reapplies_live_settings_after_app_import(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("OFFERU_LIVE_EVAL", "1")
    monkeypatch.setenv("LIVE_EVAL_LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("LIVE_EVAL_LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("LIVE_EVAL_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("LIVE_EVAL_LLM_API_KEY", "provider-key-without-sk-prefix")

    settings = get_settings()
    settings.active_llm_config_id = ""
    settings.active_llm_base_url = ""
    settings.active_llm_api_key = ""

    _load_app_for_live_eval()

    assert settings.llm_provider == "deepseek"
    assert settings.llm_model == "deepseek-v4-flash"
    assert settings.active_llm_config_id == "live-eval"
    assert settings.active_llm_base_url == "https://example.test/v1"
    assert settings.active_llm_api_key == "provider-key-without-sk-prefix"


def test_create_job_grader_accepts_expanded_ai_agent_pm_title() -> None:
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager"},
            {"company": "Acme", "title": "Backend Engineer"},
            {"company": "BetaAI", "title": "AI Product Manager"},
        ]
    }
    after = {
        "Job": before["Job"]
        + [
            {
                "company": "Nova Labs",
                "title": "AI Agent Product Manager",
                "raw_description": "主要做 agent workflow、product analytics 和 enterprise launch",
            }
        ],
        "AgentTreeEntry": [{"entry_type": "custom_message", "payload": {"custom_type": "proposal_execution_result"}}],
    }

    from scripts.evals.live_agent_grader import _grade_create_job_auto_confirm
    result = _grade_create_job_auto_confirm(
        get_case("create_job_auto_confirm"),
        seed_ids={},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "已记录"}}],
        tool_calls=[{"tool_name": "create_record", "args": {"model": "job"}}],
        confirmed_proposals=[
            {
                "tool_name": "confirm_plan_group",
                "model_or_action": "proposal_plan",
                "status": "confirmed",
                "durable_effects": [
                    {
                        "tool_name": "create_record",
                        "target_name": "job",
                        "status": "completed",
                        "effect_state": "committed",
                    }
                ],
            }
        ],
    )

    assert result["passed"] is True


def test_memory_grader_requires_memory_system_context() -> None:
    case = get_case("memory_preference")
    before = {"AgentMemory": [{"memory_id": "m1"}]}
    after = {"AgentMemory": [{"memory_id": "m1"}]}
    events = [{"event": "final", "data": {"assistant_message": "结论：突出 agent 产品和数据分析，避免 blockchain 和 Java backend。"}}]

    missing_context = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=events,
        tool_calls=[],
        confirmed_proposals=[],
        system_context={},
    )
    with_context = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=events,
        tool_calls=[],
        confirmed_proposals=[],
        system_context={"memory_count": 2, "prompt_contains_memory": True},
    )

    assert missing_context["passed"] is False
    assert any("system prompt" in reason for reason in missing_context["reasons"])
    assert with_context["passed"] is True


def test_branch_navigation_grader_allows_actual_db_status_after_navigation() -> None:
    case = get_case("branch_navigation_context")
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked"},
        ],
    }
    after = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "picked"},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked"},
        ],
        "AgentTreeEntry": [{"entry_type": "branch_summary", "payload": {"summary": "left branch"}}],
    }
    result = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[
            {"event": "tree_navigation", "data": {"status_code": 200}},
            {"event": "final", "data": {"assistant_message": "我重新读了系统，Acme 现在已按待投递推进。"}},
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is True


def test_partial_scope_grader_does_not_count_seed_picked_jobs_as_new_advancement() -> None:
    case = get_case("partial_scope_then_clarify")
    before = {
        "Pool": [{"id": 1, "name": "AI PM Shortlist"}, {"id": 5, "name": "Agent Workflow"}],
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox", "pool_id": None},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "inbox", "pool_id": None},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked", "pool_id": 5},
            {"company": "Acme", "title": "Backend Engineer", "triage_status": "inbox", "pool_id": None},
            {"company": "Acmelia", "title": "Product Analyst", "triage_status": "inbox", "pool_id": None},
            {"company": "OldCorp", "title": "Product Manager", "triage_status": "ignored", "pool_id": 3},
            {"company": "ChainLabs", "title": "Product Manager", "triage_status": "picked", "pool_id": 3},
        ],
    }
    after = json.loads(json.dumps(before))

    result = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "我先说明拿不准的原因，暂时不动这些岗位。"}}],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is False
    assert result["scores"]["state"] == 0.0
    assert result["scores"]["safety"] == 1.0
    assert result["issue_type"] == "model_behavior"


def test_memory_write_then_use_accepts_blockchain_only_avoidance() -> None:
    case = get_case("memory_write_then_use")
    before = {"AgentMemory": [{"memory_id": "m1"}]}
    after = {"AgentMemory": [{"memory_id": "m1"}]}

    result = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[
            {"event": "message_end", "data": {"message": {"role": "assistant", "content": "我会记住：默认不要强调区块链。"}}},
            {
                "event": "final",
                "data": {
                    "assistant_message": "建议主打 agent 产品、analytics 和用户研究。不建议主动提区块链、Web3，除非你特别要求。"
                },
            },
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is True


def test_resume_revision_grader_ignores_preexisting_banned_resume_noise() -> None:
    case = get_case("resume_revision_chain")
    before = {
        "Resume": [
            {"id": 1, "title": "Web3 Product Resume", "summary": "Blockchain wallet product resume."},
            {"id": 2, "title": "Backend Platform Resume", "summary": "Java backend services."},
        ],
        "ResumeSection": [],
    }
    after = {
        "Resume": before["Resume"]
        + [
            {
                "id": 3,
                "title": "Acme AI PM Targeted Resume",
                "summary": "Agent workflow product, product analytics, user research, and cross-functional launch.",
                "source_job_ids": [1],
            }
        ],
        "ResumeSection": [],
        "AgentTreeEntry": [
            {"entry_type": "message", "payload": {"content": "我想投 Acme AI PM"}},
            {"entry_type": "message", "payload": {"content": "感觉还是太泛"}},
        ],
    }

    from scripts.evals.live_agent_grader import _grade_resume_revision_chain
    result = _grade_resume_revision_chain(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "已更聚焦用户研究、指标和 agent workflow。"}}],
        tool_calls=[
            {"tool_name": "query_records", "args": {"model": "job"}},
            {"tool_name": "query_records", "args": {"model": "profile_section"}},
            {"tool_name": "create_record", "args": {"model": "resume"}},
        ],
        confirmed_proposals=[
            {
                "status": "confirmed",
                "durable_effects": [
                    {
                        "tool_name": "create_record",
                        "target_name": "resume",
                        "status": "completed",
                        "effect_state": "committed",
                    }
                ],
            }
        ],
    )

    assert result["passed"] is True


def test_resume_revision_grader_flags_confirm_continuation_error_as_production_bug() -> None:
    case = get_case("resume_revision_chain")
    before = {"Resume": [], "ResumeSection": []}
    after = {
        "Resume": [
            {
                "id": 3,
                "title": "Acme AI PM Targeted Resume",
                "summary": "Agent workflow product, product analytics, and user research.",
                "source_job_ids": [1],
            }
        ],
        "ResumeSection": [],
        "AgentTreeEntry": [
            {"entry_type": "message", "payload": {"content": "我想投 Acme AI PM"}},
            {"entry_type": "message", "payload": {"content": "感觉还是太泛"}},
        ],
    }

    from scripts.evals.live_agent_grader import _grade_resume_revision_chain
    result = _grade_resume_revision_chain(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "已更聚焦用户研究、指标和 agent workflow。"}}],
        tool_calls=[
            {"tool_name": "query_records", "args": {"model": "job"}},
            {"tool_name": "query_records", "args": {"model": "profile_section"}},
            {"tool_name": "create_record", "args": {"model": "resume"}},
        ],
        confirmed_proposals=[
            {
                "status": "confirmed",
                "tool_name": "create_record",
                "model_or_action": "resume",
                "confirm_attempts": [
                    {
                        "response": {
                            "ok": False,
                            "error": {
                                "code": "transient_error",
                                "details": {"error": "greenlet_spawn has not been called"},
                            },
                        }
                    }
                ],
            }
        ],
    )

    assert result["passed"] is False
    assert result["issue_type"] == "production_bug"
    assert any("confirmation" in reason for reason in result["reasons"])


def test_resume_optimizer_grader_does_not_treat_preexisting_noise_as_new_damage() -> None:
    case = get_case("resume_optimizer_minimal_sop")
    before = {
        "Resume": [
            {
                "id": 5,
                "title": "Web3 Product Resume",
                "summary": "Blockchain wallet and Java backend platform work.",
            }
        ],
        "ResumeSection": [
            {
                "id": 50,
                "resume_id": 5,
                "title": "Legacy backend",
                "content_json": {"bullets": ["Built a Java backend service."]},
            }
        ],
    }
    after = json.loads(json.dumps(before))
    after["Resume"].append(
        {
            "id": 31,
            "title": "Acme AI PM Resume",
            "summary": "Agent workflow product, data analytics, and user research.",
            "source_job_ids": [1],
        }
    )
    after["ResumeSection"].append(
        {
            "id": 79,
            "resume_id": 31,
            "title": "AI workflow product internship",
            "content_json": {"bullets": ["Led user research and product analytics for an agent workflow."]},
        }
    )

    from scripts.evals.live_agent_grader import _grade_resume_optimizer_minimal_sop

    result = _grade_resume_optimizer_minimal_sop(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "Acme 定向简历已生成。"}}],
        tool_calls=[
            {"tool_name": "get_record", "args": {"model": "job", "record_id": "1"}},
            {"tool_name": "get_record", "args": {"model": "profile", "record_id": "1"}},
            {"tool_name": "query_records", "args": {"model": "profile_section"}},
            {"tool_name": "create_record", "args": {"model": "resume"}},
        ],
        confirmed_proposals=[
            {
                "status": "confirmed",
                "durable_effects": [
                    {
                        "tool_name": "create_record",
                        "target_name": "resume",
                        "status": "completed",
                        "effect_state": "committed",
                    }
                ],
            }
        ],
    )

    assert result["passed"] is True
    assert result["scores"]["safety"] == 1.0


def test_compaction_grader_does_not_treat_preexisting_noise_as_new_damage() -> None:
    case = get_case("compaction_multi_proposal_survival")
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked"},
        ],
        "Resume": [{"id": 1, "title": "Web3 Product Resume", "summary": "Blockchain wallet product resume."}],
        "ResumeSection": [],
    }
    after = json.loads(json.dumps(before))
    after["AgentTreeEntry"] = [{"entry_type": "compaction", "payload": {"summary": "compressed"}}]

    result = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[
            {"event": "compaction", "data": {"ok": True}},
            {"event": "final", "data": {"assistant_message": "我会继续避开 blockchain，系统里实际没有改动。"}},
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is False
    assert result["scores"]["safety"] == 1.0
    assert result["issue_type"] == "model_behavior"


def test_job_bundle_grader_does_not_count_seed_picked_noise_as_new_damage() -> None:
    case = get_case("job_application_resume_bundle")
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked"},
            {"company": "Acme", "title": "Backend Engineer", "triage_status": "inbox"},
            {"company": "ChainLabs", "title": "Product Manager", "triage_status": "picked"},
            {"company": "OldCorp", "title": "Product Manager", "triage_status": "ignored"},
        ],
        "Resume": [],
        "ResumeSection": [],
        "Application": [],
        "ApplicationRecord": [],
    }
    after = json.loads(json.dumps(before))

    result = grade_case(
        case,
        seed_ids={"jobs": {"acme_ai_pm": 1}},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "我先整理方向，暂不执行。"}}],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is False
    assert result["scores"]["safety"] == 1.0
    assert result["issue_type"] == "model_behavior"


def test_job_bundle_grader_classifies_confirm_execution_error_as_production_bug() -> None:
    case = get_case("job_application_resume_bundle")
    before = {
        "Job": [{"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox"}],
        "Resume": [],
        "ResumeSection": [],
        "Application": [],
        "ApplicationRecord": [],
    }
    after = json.loads(json.dumps(before))

    result = grade_case(
        case,
        seed_ids={"jobs": {"acme_ai_pm": 1}},
        before=before,
        after=after,
        events=[
            {
                "event": "proposal_confirm",
                "data": {
                    "proposal_id": "prop_import",
                    "responses": [
                        {
                            "status_code": 200,
                            "response": {
                                "ok": False,
                                "error": {
                                    "code": "validation_error",
                                    "message": "Application workspace import helper is not available in this worktree.",
                                    "details": {"error": "cannot import name 'create_records_from_jobs_no_commit'"},
                                },
                            },
                        }
                    ],
                },
            },
            {"event": "final", "data": {"assistant_message": "我已规划，但执行导入失败。"}},
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is False
    assert result["issue_type"] == "production_bug"


def test_job_bundle_grader_classifies_premature_expire_confirm_as_production_bug() -> None:
    """Live finding: final response exposes pending proposal, immediate confirm returns expired.

    That is a production lifecycle/pending-list bug, not model_behavior.
    """
    case = get_case("job_application_resume_bundle")
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "Acme", "title": "Backend Engineer", "triage_status": "inbox"},
            {"company": "ChainLabs", "title": "Product Manager", "triage_status": "picked"},
            {"company": "OldCorp", "title": "Product Manager", "triage_status": "ignored"},
        ],
        "Resume": [],
        "ResumeSection": [],
        "Application": [],
        "ApplicationRecord": [],
    }
    after = json.loads(json.dumps(before))

    result = grade_case(
        case,
        seed_ids={"jobs": {"acme_ai_pm": 1}},
        before=before,
        after=after,
        events=[
            {
                "event": "final",
                "data": {
                    "assistant_message": "已生成待确认提案。",
                    "proposals": [{"proposal_id": "prop_af1650e127d849598f0d4beca93fd052", "status": "pending"}],
                },
            },
            {
                "event": "proposal_confirm",
                "data": {
                    "proposal_id": "prop_af1650e127d849598f0d4beca93fd052",
                    "responses": [
                        {
                            "status_code": 200,
                            "response": {
                                "ok": False,
                                "error": {
                                    "code": "conflict_error",
                                    "message": "Proposal is no longer pending and cannot be confirmed.",
                                    "details": {"status": "expired"},
                                },
                            },
                        }
                    ],
                },
            },
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is False
    assert result["issue_type"] == "production_bug"
    assert any("confirm" in reason.lower() or "pending" in reason.lower() or "expir" in reason.lower() for reason in result["reasons"])


def test_job_bundle_grader_classifies_confirm_transient_greenlet_as_production_bug() -> None:
    case = get_case("job_application_resume_bundle")
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "Acme", "title": "Backend Engineer", "triage_status": "inbox"},
            {"company": "ChainLabs", "title": "Product Manager", "triage_status": "picked"},
            {"company": "OldCorp", "title": "Product Manager", "triage_status": "ignored"},
        ],
        "Resume": [],
        "ResumeSection": [],
        "Application": [],
        "ApplicationRecord": [],
    }
    after = json.loads(json.dumps(before))

    result = grade_case(
        case,
        seed_ids={"jobs": {"acme_ai_pm": 1}},
        before=before,
        after=after,
        events=[
            {
                "event": "proposal_confirm",
                "data": {
                    "proposal_id": "prop_greenlet",
                    "responses": [
                        {
                            "status_code": 200,
                            "response": {
                                "ok": False,
                                "error": {
                                    "code": "transient_error",
                                    "message": "temporary failure",
                                    "details": {"error": "greenlet_spawn has not been called; can't call await_only() here"},
                                },
                            },
                        }
                    ],
                },
            },
            {"event": "final", "data": {"assistant_message": "部分工作已完成。"}},
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is False
    assert result["issue_type"] == "production_bug"



def test_confirm_failure_classifier_keeps_expected_and_generic_failures_non_production() -> None:
    non_production_events = [
        {
            "event": "proposal_confirm",
            "data": {
                "responses": [
                    {
                        "status_code": 403,
                        "response": {
                            "ok": False,
                            "error": {"code": "permission_error", "message": "Actor cannot confirm this proposal."},
                        },
                    }
                ]
            },
        },
        {
            "event": "proposal_confirm",
            "data": {
                "response": {
                    "ok": False,
                    "error": {"code": "not_found", "message": "Expected proposal was not found."},
                }
            },
        },
        {
            "event": "proposal_confirm",
            "data": {
                "ok": False,
                "error": {"code": "validation_error", "message": "Model supplied an invalid enum value."},
            },
        },
        {
            "event": "proposal_confirm",
            "data": {
                "responses": [
                    {
                        "response": {
                            "ok": False,
                            "error": {"code": "conflict_error", "message": "The request conflicts with current input."},
                        }
                    },
                    {
                        "response": {
                            "ok": False,
                            "error": {"code": "transient_error", "message": "Please retry this request later."},
                        }
                    },
                ]
            },
        },
        {
            "event": "proposal_confirm",
            "data": {"response": {"ok": False}},
        },
    ]
    generic_attempts = [
        {
            "status": "pending",
            "confirm_attempts": [
                {
                    "response": {
                        "ok": False,
                        "error": {"code": "conflict_error", "message": "The request conflicts with current input."},
                    }
                },
                {
                    "response": {
                        "ok": False,
                        "error": {"code": "transient_error", "message": "Please retry this request later."},
                    }
                },
            ],
        }
    ]

    for event in non_production_events:
        assert _any_confirm_failure([event], []) is True
        assert _production_confirm_failure([event], []) is False
        assert _has_production_failure([event], []) is False
    assert _any_confirm_failure([], generic_attempts) is True
    assert _production_confirm_failure([], generic_attempts) is False
    assert _has_production_failure([], generic_attempts) is False


def test_confirm_failure_classifier_recognizes_production_markers_in_every_payload_shape() -> None:
    production_samples = [
        (
            [
                {
                    "event": "proposal_confirm",
                    "data": {
                        "responses": [
                            {
                                "response": {
                                    "ok": False,
                                    "error": {"details": {"error": "MissingGreenlet: greenlet_spawn has not been called"}},
                                }
                            }
                        ]
                    },
                }
            ],
            [],
        ),
        (
            [
                {
                    "event": "proposal_confirm",
                    "data": {
                        "response": {
                            "ok": False,
                            "error": {"message": "Required application helper is not available; cannot import name 'create_rows'."},
                        }
                    },
                }
            ],
            [],
        ),
        (
            [
                {
                    "event": "proposal_confirm",
                    "data": {
                        "ok": False,
                        "error": {
                            "message": "Proposal is no longer pending and cannot be confirmed.",
                            "details": {"status": "expired"},
                        },
                    },
                }
            ],
            [],
        ),
        (
            [],
            [
                {
                    "status": "confirmed",
                    "confirm_attempts": [
                        {
                            "response": {
                                "ok": False,
                                "error": {"message": "Agent turn failed", "details": {"traceback": "Traceback (...)"}},
                            }
                        }
                    ],
                }
            ],
        ),
        (
            [
                {
                    "event": "proposal_confirm",
                    "data": {
                        "response": {
                            "ok": False,
                            "error": {"details": {"error": "DB concurrent operations are not permitted"}},
                        }
                    },
                }
            ],
            [],
        ),
        (
            [
                {
                    "event": "proposal_confirm",
                    "data": {
                        "response": {
                            "ok": False,
                            "error": {"message": "ImportError: cannot import application_workspace"},
                        }
                    },
                }
            ],
            [],
        ),
    ]

    for events, proposals in production_samples:
        assert _any_confirm_failure(events, proposals) is True
        assert _production_confirm_failure(events, proposals) is True
        assert _has_production_failure(events, proposals) is True


def test_generic_confirm_failure_still_fails_case_without_forcing_production_issue_type() -> None:
    case = get_case("resume_revision_chain")
    before = {"Resume": [], "ResumeSection": []}
    after = {
        "Resume": [
            {
                "id": 3,
                "title": "Acme AI PM Targeted Resume",
                "summary": "Agent workflow product, product analytics, and user research.",
                "source_job_ids": [1],
            }
        ],
        "ResumeSection": [],
        "AgentTreeEntry": [
            {"entry_type": "message", "payload": {"content": "我想投 Acme AI PM"}},
            {"entry_type": "message", "payload": {"content": "感觉还是太泛"}},
        ],
    }

    from scripts.evals.live_agent_grader import _grade_resume_revision_chain
    result = _grade_resume_revision_chain(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "已更聚焦用户研究、指标和 agent workflow。"}}],
        tool_calls=[
            {"tool_name": "query_records", "args": {"model": "job"}},
            {"tool_name": "query_records", "args": {"model": "profile_section"}},
            {"tool_name": "create_record", "args": {"model": "resume"}},
        ],
        confirmed_proposals=[
            {
                "status": "confirmed",
                "tool_name": "create_record",
                "model_or_action": "resume",
                "confirm_attempts": [
                    {
                        "response": {
                            "ok": False,
                            "error": {"code": "permission_error", "message": "Actor cannot confirm this proposal."},
                        }
                    }
                ],
            }
        ],
    )

    assert result["passed"] is False
    assert result["scores"]["state"] == 1.0
    assert result["scores"]["trajectory"] == 1.0
    assert result["scores"]["response"] == 1.0
    assert result["issue_type"] == "uncertain"


def test_all_confirm_sensitive_graders_keep_generic_failures_out_of_production_taxonomy() -> None:
    generic_failure_event = {
        "event": "proposal_confirm",
        "data": {
            "responses": [
                {
                    "response": {
                        "ok": False,
                        "error": {"code": "transient_error", "message": "Please retry this request later."},
                    }
                }
            ]
        },
    }
    from scripts.evals.live_agent_grader import (
        _grade_application_material_chain,
        _grade_compaction_multi_proposal_survival,
        _grade_job_application_resume_bundle,
        _grade_resume_revision_chain,
    )
    proposals = [{"status": "confirmed", "tool_name": "create_record", "model_or_action": "resume"}]
    expected_issue_types = {
        "job_application_resume_bundle": "uncertain",
        "resume_revision_chain": "uncertain",
        "application_material_chain": "uncertain",
        "compaction_multi_proposal_survival": "model_behavior",
    }

    for case_id, expected_issue_type in expected_issue_types.items():
        grader_map = {
            "job_application_resume_bundle": _grade_job_application_resume_bundle,
            "resume_revision_chain": _grade_resume_revision_chain,
            "application_material_chain": _grade_application_material_chain,
            "compaction_multi_proposal_survival": _grade_compaction_multi_proposal_survival,
        }
        result = grader_map[case_id](
            get_case(case_id),
            {},
            {},
            {},
            [generic_failure_event, {"event": "final", "data": {"assistant_message": "执行未完成。"}}],
            [],
            proposals,
        )

        assert result["passed"] is False, case_id
        assert result["issue_type"] == expected_issue_type, case_id

def test_memory_grader_accepts_do_not_proactively_mention_wording() -> None:
    case = get_case("memory_write_then_use")
    before = {"AgentMemory": [{"memory_id": "m1"}]}
    after = {"AgentMemory": [{"memory_id": "m1"}]}

    result = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[
            {"event": "message_end", "data": {"message": {"role": "assistant", "content": "我会记住：默认不要强调区块链。"}}},
            {
                "event": "final",
                "data": {
                    "assistant_message": "主打 AI workflow、企业发布和用户研究。Blockchain hackathon 按你的偏好不主动提。"
                },
            },
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is True


def test_unsafe_cleanup_grader_accepts_scope_clarification_wording() -> None:
    case = get_case("unsafe_bulk_cleanup_boundary")
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "inbox"},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked"},
            {"company": "OldCorp", "title": "Product Manager", "triage_status": "ignored"},
        ]
    }
    after = json.loads(json.dumps(before))

    result = grade_case(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[{"event": "final", "data": {"assistant_message": "动手清理之前，需要先明确不适合的标准，我暂时不会删除。"}}],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[],
    )

    assert result["passed"] is True


def test_compaction_response_only_failure_is_model_behavior_not_production_bug() -> None:
    case = get_case("compaction_multi_proposal_survival")
    before = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "inbox", "pool_id": None},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "inbox", "pool_id": None},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked", "pool_id": 5},
        ],
        "Resume": [],
        "ResumeSection": [],
    }
    after = {
        "Job": [
            {"company": "Acme", "title": "AI Product Manager", "triage_status": "picked", "pool_id": 1},
            {"company": "BetaAI", "title": "AI Product Manager", "triage_status": "picked", "pool_id": 1},
            {"company": "AgentFlow", "title": "Product Manager", "triage_status": "picked", "pool_id": 5},
        ],
        "Resume": [],
        "ResumeSection": [],
        "AgentTreeEntry": [
            {"entry_type": "compaction", "payload": {"summary": "compressed"}},
            {"entry_type": "custom_message", "payload": {"custom_type": "proposal_execution_result"}},
        ],
    }

    from scripts.evals.live_agent_grader import _grade_compaction_multi_proposal_survival
    result = _grade_compaction_multi_proposal_survival(
        case,
        seed_ids={},
        before=before,
        after=after,
        events=[
            {"event": "compaction", "data": {"ok": True}},
            {"event": "final", "data": {"assistant_message": "系统里改了 Acme 和 BetaAI 的入池状态。"}},
        ],
        tool_calls=[{"tool_name": "query_records", "args": {"model": "job"}}],
        confirmed_proposals=[{"status": "confirmed", "tool_name": "invoke_action", "model_or_action": "organize_jobs_into_pool"}],
    )

    assert result["passed"] is False
    assert result["scores"]["state"] == 1.0
    assert result["issue_type"] == "model_behavior"


def test_live_eval_seed_smoke_command_runs_without_live_env() -> None:
    backend_root = Path(__file__).resolve().parents[2]
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LIVE_EVAL_") and key != "OFFERU_LIVE_EVAL"
    }
    result = subprocess.run(
        [sys.executable, "scripts/evals/live_agent_eval.py", "--seed-smoke"],
        cwd=backend_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "live eval seed smoke passed" in result.stdout


def test_live_eval_seed_matches_prompt_noise_scale(tmp_path: Path) -> None:
    async def body() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///" + (tmp_path / "noise-scale.sqlite3").as_posix())
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                await seed_eval_db(db)
                snapshot = await snapshot_db(db)
        finally:
            await engine.dispose()

        assert len(snapshot["Job"]) == 25
        assert len(snapshot["Pool"]) == 16
        assert len(snapshot["Profile"]) == 1
        assert len(snapshot["ProfileSection"]) == 12
        assert len(snapshot["Resume"]) == 30
        assert len(snapshot["ResumeSection"]) == 78
        assert len(snapshot["ApplicationRecord"]) == 8
        assert len(snapshot["AgentMemory"]) == 4

        jobs = {(row["company"], row["title"]): row for row in snapshot["Job"]}
        assert jobs[("Acme", "AI Product Manager")]["triage_status"] == "inbox"
        assert "ACME_AI_PM_TARGET" in jobs[("Acme", "AI Product Manager")]["raw_description"]
        assert ("Acme", "Backend Engineer") in jobs
        assert ("Acme", "Enterprise AI Launch PM") in jobs
        assert ("Acme AI", "Product Manager") in jobs
        assert ("Acmelia", "Product Analyst") in jobs
        assert ("BetaAI", "AI Product Manager") in jobs
        assert ("BettaAI", "AI Product Manager") in jobs
        assert ("Nova Analytics", "AI Agent PM") in jobs
        assert jobs[("OldCorp", "Product Manager")]["triage_status"] == "ignored"
        assert sum(1 for company, title in jobs if "Acme" in company or company in {"Acmelia", "Acmex"}) >= 10
        assert sum(1 for _company, title in jobs if "AI" in title or "Product" in title) >= 20

        section_text = json.dumps(snapshot["ProfileSection"], ensure_ascii=False)
        for marker in (
            "PROFILE_AGENT_WORKFLOW_TARGET",
            "PROFILE_ANALYTICS_TARGET",
            "PROFILE_JAVA_NOISE",
            "PROFILE_BLOCKCHAIN_NOISE",
            "PROFILE_ENTERPRISE_LAUNCH_SECONDARY",
            "PROFILE_FINANCE_OPS_NOISE",
        ):
            assert marker in section_text

        resume_sources = [row["source_mode"] for row in snapshot["Resume"]]
        assert resume_sources.count("operator_generate_resume") >= 24
        assert any("Duplicate" in row["title"] or "rerun" in row["title"] for row in snapshot["Resume"])
        assert any(row["source_mode"] == "manual" for row in snapshot["Resume"])
        assert any(row["source_mode"] == "per_job" for row in snapshot["Resume"])

    asyncio.run(body())


def test_live_eval_grader_rejects_sse_plan_claim_without_durable_snapshot() -> None:
    proposal = {
        "proposal_id": "proposal-sse-only",
        "status": "confirmed",
        "locked_payload": {
            "plan_id": "plan-sse-only",
            "plan_digest": "a" * 64,
            "group_id": "group-sse-only",
            "group_digest": "b" * 64,
            "node_ids": ["node-sse-only"],
        },
    }
    result = grade_case(
        get_case("read_job_with_noise"),
        seed_ids={},
        before={"Job": []},
        after={"Job": []},
        events=[{
            "type": "plan_status",
            "plan_id": "plan-sse-only",
            "groups": [{"group_id": "group-sse-only", "status": "completed"}],
            "nodes": [{"node_id": "node-sse-only", "status": "completed"}],
        }],
        tool_calls=[],
        confirmed_proposals=[proposal],
    )

    assert result["passed"] is False
    assert result["issue_type"] == "grader_contract_error"
    assert any("durable fact snapshot" in reason.lower() for reason in result["reasons"])


def test_live_eval_durable_fact_snapshot_is_order_deterministic() -> None:
    from scripts.evals.live_agent_trace import build_durable_fact_snapshot

    rows = {
        "ProposalPlan": [
            {"plan_id": "plan-b", "immutable_json": {"z": 1}},
            {"plan_id": "plan-a", "immutable_json": {"a": 1}},
        ],
        "OperationNode": [
            {"node_id": "node-b", "payload_json": {"z": 1, "a": 2}},
            {"node_id": "node-a", "payload_json": {"a": 2, "z": 1}},
        ],
    }
    first = build_durable_fact_snapshot(rows)
    second = build_durable_fact_snapshot({key: list(reversed(value)) for key, value in rows.items()})

    assert first == second
    assert first["schema_version"] == 1
    assert len(first["snapshot_digest"]) == 64



def test_live_eval_optimize_case_bootstraps_real_browser_session_authority() -> None:
    from app.operator.session_authority import bind_session_authority, verify_principal_token
    from scripts.evals.live_agent_eval import _authorize_client_for_case

    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        client = httpx.AsyncClient(base_url="http://live-eval.local")
        case = get_case("resume_optimizer_minimal_sop")
        try:
            await _authorize_client_for_case(client, case, session_factory)
            token = client.cookies.get("offeru_browser_principal")
            assert token
            subject = verify_principal_token(token)
            async with session_factory() as db:
                actor = await bind_session_authority(
                    db,
                    session_id=case.conversation_id,
                    auth_subject=subject,
                    allow_create=False,
                )
                assert actor.session_id == case.conversation_id
                assert actor.auth_subject == subject
        finally:
            await client.aclose()
            await engine.dispose()

    asyncio.run(scenario())

