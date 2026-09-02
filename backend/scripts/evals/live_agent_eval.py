from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from app.config import get_settings
from app.database import Base
from live_agent_cases import EvalTurn, LiveAgentCase, cases_for_suite, get_case, list_cases
from live_agent_grader import changed_records, grade_case, render_verdict_md
from live_agent_seed import seed_eval_db, snapshot_db
from live_agent_trace import (
    PROVIDER_FAILURE_HTTP_424_TEXT_MARKERS,
    append_ndjson,
    extract_final_text,
    extract_tool_calls,
    iter_sse_events,
    message_text,
    redact_durable_fact_snapshot,
    snapshot_durable_facts,
    sse_event_payload_is_provider_failure,
    write_json,
    write_text,
)


RUNS_DIR = BACKEND_ROOT / ".eval-runs"
FINDINGS_PATH = BACKEND_ROOT / "scripts" / "evals" / "live_agent_eval_findings.md"

# Provider availability failures are a distinct taxonomy class and must never be
# labeled model behavior: HTTP 424 (service temporarily unavailable) and
# transport-level provider unavailability are recorded separately in eval
# artifacts and summary statistics.
PROVIDER_FAILURE_HTTP_STATUS = 424
PROVIDER_FAILURE_CATEGORY_HTTP_424 = "http_424"
PROVIDER_FAILURE_CATEGORY_UNAVAILABLE = "provider_unavailable"
SSE_EVENTS_CARRYING_PROVIDER_ERROR = ("message_start", "message_end", "turn_end", "agent_end", "final", "error")

# ---- Confirmation-stop handling (runtime interaction injection) ----
# Models frequently end a turn with a confirmation-style question
# ("要不要我...? / Should I...?") instead of executing the write. The harness
# detects that stop (no side effect, no proposal, no confirmation this turn)
# and injects a user "go on" message so the model can actually complete the
# task. This is a runtime interaction, not an eval prompt or case change, and
# never fabricates evidence: if the model still does not progress within the
# injection budget, the original failure verdict stands.
CONFIRM_INJECTION_CAP = 2
CONFIRM_INJECTION_TEXT = "请继续执行，我同意。Go on."
CONFIRMATION_QUESTION_MARKERS = (
    "是否",
    "要不要",
    "需要我",
    "可以吗",
    "确认",
    "继续吗",
    "执行吗",
    "要我先",
    "要我",
    "该不该",
    "应不应该",
    "行吗",
    "对吗",
    "should i",
    "do you want",
    "would you like",
    "can i",
    "shall i",
    "want me to",
    "confirm",
    "proceed",
    "approve",
)
CONFIRMATION_QUESTION_ENDINGS = ("？", "?", "吗", "呢", "么", "吧")
READ_ONLY_TOOL_NAMES = frozenset({"query_records", "get_record", "describe_capability"})


def classify_provider_failure(
    *,
    status_code: int | None = None,
    exc: BaseException | None = None,
    event_data: Any = None,
) -> dict[str, Any] | None:
    """Classify HTTP 424, provider connection failures, and SSE-carried
    provider availability errors as provider_failure.

    `event_data` covers the real failure surface: the SSE event stream always
    returns HTTP 200 and carries provider errors inside event payloads:
    - `stop_reason: "error"` with "Error code: 424" / "Service temporarily
      unavailable" (http_424 family, status_code 424 — the original WP7
      surface, `.eval-runs/20260813-003424`);
    - `stop_reason: "error"` with "Connection error." / "Request timed out."
      transport disconnect text (provider_unavailable, status_code None — this
      round's preserved runs 20260902-130858 / 20260902-131037);
    - the SSE `error` event `{"error": {"code": "agent_sse_failed",
      "message": "Agent stream failed."}}` (provider_unavailable, status_code
      None — preserved run 20260902-130150).
    Returns None for every other status/event/exception so ordinary eval errors
    keep their existing taxonomy. Never invoked for real LLM requests outside
    the eval harness. The 424 family keeps http_424/status_code 424; the other
    availability shapes map to provider_unavailable exactly like
    httpx.TransportError.
    """
    if status_code == PROVIDER_FAILURE_HTTP_STATUS:
        return {
            "category": PROVIDER_FAILURE_CATEGORY_HTTP_424,
            "status_code": PROVIDER_FAILURE_HTTP_STATUS,
            "detail": "HTTP 424 Service temporarily unavailable",
        }
    if event_data is not None and sse_event_payload_is_provider_failure(event_data):
        detail = _provider_failure_detail(event_data) or ""
        if any(marker in detail for marker in PROVIDER_FAILURE_HTTP_424_TEXT_MARKERS):
            return {
                "category": PROVIDER_FAILURE_CATEGORY_HTTP_424,
                "status_code": PROVIDER_FAILURE_HTTP_STATUS,
                "detail": detail or "HTTP 424 Service temporarily unavailable",
            }
        return {
            "category": PROVIDER_FAILURE_CATEGORY_UNAVAILABLE,
            "status_code": None,
            "detail": detail or "Provider stream unavailable",
        }
    if exc is not None and isinstance(exc, httpx.TransportError):
        return {
            "category": PROVIDER_FAILURE_CATEGORY_UNAVAILABLE,
            "status_code": None,
            "detail": f"{type(exc).__name__}: {str(exc) or ''}",
        }
    return None


def _provider_failure_detail(data: Any) -> str:
    """Extract the provider error message from an SSE error payload for
    opaque-credential-free artifact records (redacted at write time)."""
    if not isinstance(data, Mapping):
        return ""
    provider_error = data.get("error")
    if isinstance(provider_error, Mapping):
        message = str(provider_error.get("message") or "")
        code = str(provider_error.get("code") or "")
        if message or code:
            detail = message if message else code
            if message and code:
                detail = f"{message} (code={code})"
            return detail[:1000]
    for candidate in (_message_holder(data), data):
        if not isinstance(candidate, Mapping):
            continue
        detail = str(candidate.get("error_message") or "")
        if detail:
            return detail[:1000]
    return ""


def _message_holder(data: Mapping[str, Any]) -> Any:
    message = data.get("message")
    if isinstance(message, Mapping):
        return message
    messages = data.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
        return messages[0]
    return None


def _turn_events(events: list[Mapping[str, Any]], turn_index: int) -> list[Mapping[str, Any]]:
    return [event for event in events if isinstance(event, Mapping) and event.get("turn_index") == turn_index]


def _turn_final_text(events: list[Mapping[str, Any]], turn_index: int) -> str:
    """Final assistant text produced within a specific turn."""
    final_text = ""
    for event in _turn_events(events, turn_index):
        event_name = str(event.get("event") or "")
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        if event_name == "final":
            text = str(data.get("assistant_message") or "")
            if text:
                final_text = text
        elif event_name in ("message_end", "turn_end", "agent_end"):
            message = data.get("message") if isinstance(data.get("message"), Mapping) else {}
            if str(message.get("role") or "") == "assistant":
                text = message_text(message)
                if text:
                    final_text = text
    return final_text


def _turn_has_tool_execution(events: list[Mapping[str, Any]], turn_index: int, *, side_effecting_only: bool = False) -> bool:
    """Whether the turn started any tool execution (optionally only
    side-effecting ones, i.e. anything but query/get/describe)."""
    for event in _turn_events(events, turn_index):
        if str(event.get("event") or "") != "tool_execution_start":
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        tool_name = str(data.get("tool_name") or data.get("toolName") or "")
        if side_effecting_only and tool_name in READ_ONLY_TOOL_NAMES:
            continue
        return True
    return False


def _turn_has_progress(events: list[Mapping[str, Any]], turn_index: int) -> bool:
    """Whether the turn produced any progress signal: side-effecting tool
    call, proposal, proposal confirmation, or recorded provider failure."""
    for event in _turn_events(events, turn_index):
        event_name = str(event.get("event") or "")
        if event_name in {"proposal", "proposal_confirm", "provider_failure"}:
            return True
        if event_name == "tool_execution_start":
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            tool_name = str(data.get("tool_name") or data.get("toolName") or "")
            if tool_name not in READ_ONLY_TOOL_NAMES:
                return True
    return False


def _is_confirmation_question(text: str) -> bool:
    """True when the assistant text clearly stops at a confirmation-style
    question: it contains a question marker and ends with a questioning
    suffix (both Chinese and English patterns)."""
    stripped = str(text or "").strip()
    if len(stripped) < 4:
        return False
    lowered = stripped.lower()
    has_marker = any(marker in lowered for marker in CONFIRMATION_QUESTION_MARKERS)
    return has_marker and stripped.endswith(CONFIRMATION_QUESTION_ENDINGS)


@dataclass
class CaseRunResult:
    case_id: str
    passed: bool
    run_dir: Path
    grader: dict[str, Any]
    errors: list[str] = field(default_factory=list)
    provider_failures: list[dict[str, Any]] = field(default_factory=list)
    confirm_injections: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ProposalDecisionTarget:
    proposal_id: str
    operator_session_id: str


@dataclass
class RuntimePatch:
    db_path: Path
    history_path: Path
    keep_db: bool = False
    engine: Any = None
    session_factory: Any = None
    _saved: dict[str, Any] = field(default_factory=dict)

    async def __aenter__(self):
        from app import database
        from app.agent import orchestrator
        from app.operator import proposals
        from app.routes import agent as agent_route
        from app.routes import harness_agent, optimize
        from app.services import harness_history

        db_url = "sqlite+aiosqlite:///" + self.db_path.as_posix()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(db_url, echo=False)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        settings = get_settings()
        self._saved = {
            "database_engine": database.engine,
            "database_async_session": database.async_session,
            "settings_database_url": settings.database_url,
            "harness_async_session": harness_agent.async_session,
            "optimize_async_session": optimize.async_session,
            "proposals_async_session": proposals.async_session,
            "agent_async_session": getattr(agent_route, "async_session", None),
            "history_dir": harness_history.HISTORY_DIR,
            "history_path": harness_history.HISTORY_PATH,
            "provider_factory": orchestrator._provider_factory,
        }

        settings.database_url = db_url
        database.engine = self.engine
        database.async_session = self.session_factory
        harness_agent.async_session = self.session_factory
        optimize.async_session = self.session_factory
        proposals.async_session = self.session_factory
        if hasattr(agent_route, "async_session"):
            agent_route.async_session = self.session_factory
        harness_history.HISTORY_DIR = self.history_path.parent
        harness_history.HISTORY_PATH = self.history_path
        orchestrator._SESSION_LOCKS.clear()
        orchestrator._provider_factory = None
        return self.session_factory

    async def __aexit__(self, exc_type, exc, tb):
        from app import database
        from app.agent import orchestrator
        from app.operator import proposals
        from app.routes import agent as agent_route
        from app.routes import harness_agent, optimize
        from app.services import harness_history

        settings = get_settings()
        database.engine = self._saved["database_engine"]
        database.async_session = self._saved["database_async_session"]
        settings.database_url = self._saved["settings_database_url"]
        harness_agent.async_session = self._saved["harness_async_session"]
        optimize.async_session = self._saved["optimize_async_session"]
        proposals.async_session = self._saved["proposals_async_session"]
        if hasattr(agent_route, "async_session"):
            agent_route.async_session = self._saved["agent_async_session"]
        harness_history.HISTORY_DIR = self._saved["history_dir"]
        harness_history.HISTORY_PATH = self._saved["history_path"]
        orchestrator._provider_factory = self._saved["provider_factory"]
        orchestrator._SESSION_LOCKS.clear()
        if self.engine is not None:
            await self.engine.dispose()
        if not self.keep_db:
            _remove_sqlite_files(self.db_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run backend-only live OfferU agent evals against a real LLM.")
    parser.add_argument("--list-cases", action="store_true", help="List live eval cases without requiring live credentials.")
    parser.add_argument("--seed-smoke", action="store_true", help="Create, seed, and snapshot a temporary eval DB without live credentials.")
    parser.add_argument("--case", dest="case_id", help="Run one case id.")
    parser.add_argument("--suite", default="smoke", help="Run a suite. Supported: smoke, deep, complex.")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat the selected case or suite and report pass rate.")
    parser.add_argument("--max-cases", type=int, default=0, help="Limit selected cases.")
    parser.add_argument("--keep-db", action="store_true", help="Keep per-case SQLite files in the run directory.")
    args = parser.parse_args()

    if args.list_cases:
        _print_cases()
        return 0
    if args.seed_smoke:
        asyncio.run(_run_seed_smoke())
        return 0

    missing = _missing_live_env()
    if missing:
        print("未运行 live eval，因为缺少环境变量: " + ", ".join(missing), file=sys.stderr)
        return 2

    _configure_live_settings()
    selected = [get_case(args.case_id)] if args.case_id else list(cases_for_suite(args.suite))
    if args.max_cases and args.max_cases > 0:
        selected = selected[: args.max_cases]
    if not selected:
        print(f"No live eval cases selected for suite={args.suite!r}.", file=sys.stderr)
        return 2

    result = asyncio.run(run_suite_repeats(selected, repeat=args.repeat, keep_db=args.keep_db))
    return 0 if result["passed"] else 1


async def run_suite(cases: list[LiveAgentCase], *, keep_db: bool) -> dict[str, Any]:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / run_id
    return await _run_suite_once(cases, run_id=run_id, run_dir=run_dir, keep_db=keep_db)


async def run_suite_repeats(cases: list[LiveAgentCase], *, repeat: int, keep_db: bool) -> dict[str, Any]:
    repeat_count = max(1, int(repeat or 1))
    if repeat_count == 1:
        summary = await run_suite(cases, keep_db=keep_db)
        return {
            **summary,
            "total_runs": 1,
            "passed_runs": 1 if summary.get("passed") else 0,
            "failed_runs": 0 if summary.get("passed") else 1,
            "pass_rate": 1.0 if summary.get("passed") else 0.0,
        }

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    root_dir = RUNS_DIR / run_id
    root_dir.mkdir(parents=True, exist_ok=True)
    _ensure_findings_file()
    run_summaries: list[dict[str, Any]] = []
    for index in range(1, repeat_count + 1):
        repeat_run_id = f"{run_id}-{index:02d}"
        repeat_dir = root_dir / f"repeat-{index:02d}"
        print(f"[live-eval] repeat {index}/{repeat_count}")
        run_summaries.append(
            await _run_suite_once(cases, run_id=repeat_run_id, run_dir=repeat_dir, keep_db=keep_db)
        )

    passed_runs = sum(1 for summary in run_summaries if summary.get("passed"))
    aggregate = {
        "run_id": run_id,
        "run_dir": str(root_dir),
        "passed": passed_runs == repeat_count,
        "total_runs": repeat_count,
        "passed_runs": passed_runs,
        "failed_runs": repeat_count - passed_runs,
        "pass_rate": passed_runs / repeat_count if repeat_count else 0.0,
        "runs": run_summaries,
    }
    write_json(root_dir / "repeat_summary.json", aggregate)
    write_text(root_dir / "repeat_summary.md", _render_repeat_summary_md(aggregate))
    print(
        f"[live-eval] repeat pass rate: {passed_runs}/{repeat_count} "
        f"({aggregate['pass_rate']:.2%})"
    )
    print(f"[live-eval] trace directory: {root_dir}")
    return aggregate


async def _run_suite_once(cases: list[LiveAgentCase], *, run_id: str, run_dir: Path, keep_db: bool) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    _ensure_findings_file()

    results: list[CaseRunResult] = []
    for case in cases:
        print(f"[live-eval] running {case.case_id}")
        result = await run_case(case, run_dir=run_dir, keep_db=keep_db)
        results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"[live-eval] {case.case_id}: {status}")

    summary = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "passed": all(result.passed for result in results),
        "total": len(results),
        "passed_count": sum(1 for result in results if result.passed),
        "failed_count": sum(1 for result in results if not result.passed),
        "provider_failure_count": sum(len(result.provider_failures) for result in results),
        "cases": [
            {
                "case_id": result.case_id,
                "passed": result.passed,
                "run_dir": str(result.run_dir),
                "scores": result.grader.get("scores"),
                "reasons": result.grader.get("reasons"),
                "errors": result.errors,
                "provider_failures": result.provider_failures,
                "user_confirm_injections": result.confirm_injections,
            }
            for result in results
        ],
    }
    write_json(run_dir / "summary.json", summary)
    write_text(run_dir / "summary.md", _render_summary_md(summary))
    write_text(run_dir / "issues.md", _render_issues_md(results))
    print(f"[live-eval] trace directory: {run_dir}")
    print("[live-eval] This is not deterministic CI; inspect traces and verdicts before treating failures as product bugs.")
    return summary


async def run_case(case: LiveAgentCase, *, run_dir: Path, keep_db: bool) -> CaseRunResult:
    case_dir = run_dir / case.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    db_path = case_dir / "case.sqlite3"
    history_path = case_dir / "harness_agent_conversations.json"
    errors: list[str] = []
    provider_failures: list[dict[str, Any]] = []
    turn_envelopes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    confirm_log: list[dict[str, Any]] = []
    seed_ids: dict[str, Any] = {}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    system_context: dict[str, Any] = {}
    durable_fact_snapshot: dict[str, Any] = {}
    provider_context: dict[str, Any] = {}
    confirm_injections: list[dict[str, Any]] = []

    write_json(case_dir / "case.json", case.public_dict())
    settings_saved = _apply_case_settings(case)
    try:
        async with RuntimePatch(db_path=db_path, history_path=history_path, keep_db=keep_db) as session_factory:
            async with session_factory() as db:
                seed_ids = await seed_eval_db(db)
                before = await snapshot_db(db)
                system_context = await _system_context_for_case(db, case)
            write_json(case_dir / "seed_db.json", {"seed_ids": seed_ids, "snapshot": before})
            write_json(case_dir / "db_before.json", before)
            write_json(case_dir / "system_context.json", system_context)

            app = _load_app_for_live_eval()

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://live-eval.local",
                timeout=None,
            ) as client:
                await _authorize_client_for_case(client, case, session_factory)
                confirm_injections = await _run_case_turns(
                    client,
                    case,
                    case_dir,
                    events,
                    confirm_log,
                    errors,
                    provider_failures=provider_failures,
                    turn_envelopes=turn_envelopes,
                    session_factory=session_factory,
                )

            async with session_factory() as db:
                after = await snapshot_db(db)
                provider_context = await _provider_context_for_case(db, case, system_context)
                durable_fact_snapshot = await snapshot_durable_facts(
                    db,
                    system_protocol=provider_context.get("system_protocol") or None,
                    provider_tools=provider_context.get("provider_tools") or None,
                )
    finally:
        _restore_case_settings(settings_saved)

    tool_calls = extract_tool_calls(events)
    proposals = _proposal_records(after, confirm_log)
    confirmed = [proposal for proposal in proposals if str(proposal.get("status") or "") == "confirmed"]
    grade = grade_case(
        case,
        seed_ids=seed_ids,
        before=before,
        after=after,
        events=events,
        tool_calls=tool_calls,
        confirmed_proposals=confirmed,
        system_context=system_context,
        durable_fact_snapshot=durable_fact_snapshot,
    )
    if errors:
        grade = {
            **grade,
            "passed": False,
            "issue_type": "uncertain",
            "reasons": [*grade.get("reasons", []), *errors],
        }
    if provider_failures:
        grade = {
            **grade,
            "passed": False,
            "issue_type": "provider_failure",
            "reasons": [
                *grade.get("reasons", []),
                f"Provider availability failure: {provider_failures[0].get('category')} "
                f"({provider_failures[0].get('detail') or ''})",
            ],
        }
    changed = changed_records(before, after)

    write_json(case_dir / "tool_calls.json", tool_calls)
    write_json(case_dir / "proposals.json", proposals)
    write_json(case_dir / "db_after.json", after)
    # The artifact is redacted; re-sign the digest over the redacted payload so
    # the persisted durable_fact_snapshot.json passes verify_durable_fact_snapshot
    # (the in-memory snapshot used for scoring keeps its original digest).
    write_json(
        case_dir / "durable_fact_snapshot.json",
        redact_durable_fact_snapshot(durable_fact_snapshot),
        redact=False,
    )
    write_text(case_dir / "assistant_final.txt", extract_final_text(events))
    write_json(case_dir / "grader.json", grade)
    write_text(case_dir / "verdict.md", render_verdict_md(case, grade, events=events, tool_calls=tool_calls, confirmed_proposals=confirmed, changed_records=changed))
    _write_case_trace_artifacts(
        case_dir,
        case,
        events=events,
        tool_calls=tool_calls,
        proposals=proposals,
        confirmed_proposals=confirmed,
        changed=changed,
        grade=grade,
        provider_context=provider_context,
        turn_envelopes=turn_envelopes,
        provider_failures=provider_failures,
        confirm_injections=confirm_injections,
    )

    return CaseRunResult(
        case_id=case.case_id,
        passed=bool(grade.get("passed")),
        run_dir=case_dir,
        grader=grade,
        errors=errors,
        provider_failures=provider_failures,
        confirm_injections=confirm_injections,
    )


async def _authorize_client_for_case(
    client: httpx.AsyncClient,
    case: LiveAgentCase,
    session_factory: Any,
) -> None:
    if not case.route.startswith("/api/optimize/"):
        return
    from app.operator.session_authority import (
        BROWSER_PRINCIPAL_COOKIE_PATH,
        bind_session_authority,
        issue_principal_token,
    )

    token, auth_subject = issue_principal_token()
    async with session_factory() as db:
        await bind_session_authority(
            db,
            session_id=case.conversation_id,
            auth_subject=auth_subject,
            allow_create=True,
        )
        await db.commit()
    client.cookies.set(
        "offeru_browser_principal",
        token,
        path=BROWSER_PRINCIPAL_COOKIE_PATH,
    )


async def _run_case_turns(client: httpx.AsyncClient, case: LiveAgentCase, case_dir: Path, events: list[dict[str, Any]], confirm_log: list[dict[str, Any]], errors: list[str], *, provider_failures: list[dict[str, Any]] | None = None, turn_envelopes: list[dict[str, Any]] | None = None, session_factory: Any = None) -> list[dict[str, Any]]:
    turn_index = 0
    confirm_injections: list[dict[str, Any]] = []
    for turn in case.turns:
        turn_index += 1
        if turn.action == "navigate_previous_leaf":
            await _run_tree_navigation_step(client, case, turn_index, case_dir, events, errors)
            await _record_turn_envelope(turn_index, session_factory, turn_envelopes)
            continue
        await _run_stream_turn(client, case, turn, turn_index, case_dir, events, confirm_log, errors, provider_failures=provider_failures)
        await _record_turn_envelope(turn_index, session_factory, turn_envelopes)
        await _maybe_inject_confirmation(
            client,
            case,
            turn_index,
            case_dir,
            events,
            confirm_log,
            errors,
            confirm_injections,
            provider_failures=provider_failures,
            turn_envelopes=turn_envelopes,
            session_factory=session_factory,
        )

    while case.follow_up_policy == "resume_optimizer" and turn_index < case.max_turns and not _has_confirmed_resume_proposal(confirm_log):
        turn_index += 1
        final_text = extract_final_text(events)
        follow_up = _resume_optimizer_follow_up(final_text, turn_index)
        await _run_stream_turn(client, case, follow_up, turn_index, case_dir, events, confirm_log, errors, provider_failures=provider_failures)
        await _record_turn_envelope(turn_index, session_factory, turn_envelopes)
        await _maybe_inject_confirmation(
            client,
            case,
            turn_index,
            case_dir,
            events,
            confirm_log,
            errors,
            confirm_injections,
            provider_failures=provider_failures,
            turn_envelopes=turn_envelopes,
            session_factory=session_factory,
        )

    return confirm_injections


async def _maybe_inject_confirmation(
    client: httpx.AsyncClient,
    case: LiveAgentCase,
    turn_index: int,
    case_dir: Path,
    events: list[dict[str, Any]],
    confirm_log: list[dict[str, Any]],
    errors: list[str],
    confirm_injections: list[dict[str, Any]],
    *,
    provider_failures: list[dict[str, Any]] | None = None,
    turn_envelopes: list[dict[str, Any]] | None = None,
    session_factory: Any = None,
) -> None:
    """When a turn stops at a confirmation-style question with no side effect,
    proposal, or confirmation, inject one user "go on" message so the model
    can actually execute. At most CONFIRM_INJECTION_CAP injections per case;
    every injection is recorded with before/after state. This is a runtime
    interaction, not an eval prompt or case change, and never fabricates
    evidence: if the model still does not progress, the failure stands.
    """
    while len(confirm_injections) < CONFIRM_INJECTION_CAP:
        if _turn_has_progress(events, turn_index):
            return
        final_text = _turn_final_text(events, turn_index)
        if not _is_confirmation_question(final_text):
            return
        injection_index = len(confirm_injections) + 1
        record: dict[str, Any] = {
            "injection_index": injection_index,
            "turn_index": turn_index,
            "injected_text": CONFIRM_INJECTION_TEXT,
            "final_text_before": final_text[:600],
            "tool_calls_before": _turn_has_tool_execution(events, turn_index),
        }
        inject_event = {
            "turn_index": turn_index,
            "event": "user_confirm_injection",
            "data": {"injection_index": injection_index, "text": CONFIRM_INJECTION_TEXT},
        }
        events.append(inject_event)
        append_ndjson(case_dir / "events.ndjson", inject_event)
        await _run_stream_turn(
            client,
            case,
            EvalTurn(CONFIRM_INJECTION_TEXT, action="confirm"),
            turn_index,
            case_dir,
            events,
            confirm_log,
            errors,
            provider_failures=provider_failures,
        )
        await _record_turn_envelope(turn_index, session_factory, turn_envelopes)
        record["tool_calls_after"] = _turn_has_tool_execution(events, turn_index)
        record["progress_after"] = _turn_has_progress(events, turn_index)
        record["final_text_after"] = _turn_final_text(events, turn_index)[:600]
        confirm_injections.append(record)
        print(
            f"[live-eval] {case.case_id} turn {turn_index}: confirmation-stop detected; "
            f"injected user confirmation {injection_index}/{CONFIRM_INJECTION_CAP} "
            f"(progress_after={record['progress_after']})"
        )


async def _record_turn_envelope(turn_index: int, session_factory: Any, turn_envelopes: list[dict[str, Any]] | None) -> None:
    """Per-turn durable execution-state envelope (independent of SSE events)."""
    if session_factory is None or turn_envelopes is None:
        return
    from live_agent_trace import snapshot_execution_state_envelope

    try:
        async with session_factory() as db:
            envelope = await snapshot_execution_state_envelope(db)
        turn_envelopes.append({"turn_index": turn_index, "envelope": envelope})
    except Exception as exc:  # pragma: no cover - envelope capture must never break the turn.
        turn_envelopes.append({"turn_index": turn_index, "envelope_error": str(exc)})


async def _run_tree_navigation_step(
    client: httpx.AsyncClient,
    case: LiveAgentCase,
    turn_index: int,
    case_dir: Path,
    events: list[dict[str, Any]],
    errors: list[str],
) -> None:
    try:
        tree_response = await client.get(f"/api/harness-agent/conversations/{case.conversation_id}/tree")
        if tree_response.status_code >= 400:
            errors.append(f"Tree GET failed in turn {turn_index}: HTTP {tree_response.status_code} {tree_response.text[:1000]}")
            return
        tree_payload = tree_response.json()
        snapshot_event = {"turn_index": turn_index, "event": "tree_snapshot", "data": tree_payload}
        events.append(snapshot_event)
        append_ndjson(case_dir / "events.ndjson", snapshot_event)
        target_id = _navigation_target_before_mutation(tree_payload)
        if not target_id:
            errors.append(f"Could not find a pre-mutation tree entry to navigate to in turn {turn_index}.")
            return
        navigate_response = await client.post(
            f"/api/harness-agent/conversations/{case.conversation_id}/tree/navigate",
            json={"entry_id": target_id},
        )
        try:
            navigate_payload = navigate_response.json()
        except json.JSONDecodeError:
            navigate_payload = {"raw": navigate_response.text}
        navigation_event = {
            "turn_index": turn_index,
            "event": "tree_navigation",
            "data": {
                "status_code": navigate_response.status_code,
                "target_entry_id": target_id,
                "response": navigate_payload,
            },
        }
        events.append(navigation_event)
        append_ndjson(case_dir / "events.ndjson", navigation_event)
        if navigate_response.status_code >= 400:
            errors.append(f"Tree navigate failed in turn {turn_index}: HTTP {navigate_response.status_code} {navigate_response.text[:1000]}")
    except Exception as exc:
        errors.append(f"Tree navigation step {turn_index} failed: {exc}")


def _navigation_target_before_mutation(tree_payload: Mapping[str, Any]) -> str:
    entries = tree_payload.get("entries") if isinstance(tree_payload.get("entries"), list) else []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if str(entry.get("entry_type") or "") == "message":
            return str(entry.get("entry_id") or "")
    return ""


async def _run_stream_turn(
    client: httpx.AsyncClient,
    case: LiveAgentCase,
    turn: EvalTurn,
    turn_index: int,
    case_dir: Path,
    events: list[dict[str, Any]],
    confirm_log: list[dict[str, Any]],
    errors: list[str],
    *,
    provider_failures: list[dict[str, Any]] | None = None,
) -> None:
    proposal_targets: list[ProposalDecisionTarget] = []
    provider_error_recorded = False
    payload = _payload_for_turn(case, turn)
    try:
        async with client.stream("POST", case.route, json=payload) as response:
            if response.status_code >= 400:
                failure = classify_provider_failure(status_code=response.status_code)
                body = (await response.aread()).decode("utf-8", errors="replace")
                if failure is not None:
                    failure = {**failure, "detail": f"{failure['detail']} body={body[:500]}"}
                    event = {"turn_index": turn_index, "event": "provider_failure", "data": failure}
                    events.append(event)
                    append_ndjson(case_dir / "events.ndjson", event)
                    if provider_failures is not None:
                        provider_failures.append({"phase": "stream", **failure})
                    return
                error = f"HTTP {response.status_code} from {case.route}: {body[:1000]}"
                errors.append(error)
                event = {"turn_index": turn_index, "event": "http_error", "data": {"status_code": response.status_code, "body": body}}
                events.append(event)
                append_ndjson(case_dir / "events.ndjson", event)
                return
            async for event in iter_sse_events(response.aiter_lines()):
                event["turn_index"] = turn_index
                events.append(event)
                append_ndjson(case_dir / "events.ndjson", event)
                if event.get("parse_error"):
                    errors.append(f"SSE JSON parse failed in turn {turn_index}: {event['parse_error']}")
                    continue
                if (
                    not provider_error_recorded
                    and str(event.get("event") or "") in SSE_EVENTS_CARRYING_PROVIDER_ERROR
                ):
                    failure = classify_provider_failure(event_data=event.get("data"))
                    if failure is not None:
                        provider_error_recorded = True
                        provider_event = {"turn_index": turn_index, "event": "provider_failure", "data": failure}
                        events.append(provider_event)
                        append_ndjson(case_dir / "events.ndjson", provider_event)
                        if provider_failures is not None:
                            provider_failures.append({"phase": "stream", **failure})
                if event.get("event") == "proposal":
                    try:
                        target = _proposal_target_from_event(event)
                    except ValueError as exc:
                        errors.append(f"Proposal event in turn {turn_index} is not confirmable: {exc}")
                        continue
                    if target.operator_session_id != case.conversation_id:
                        errors.append(
                            f"Proposal {target.proposal_id} session scope {target.operator_session_id!r} "
                            f"does not match case session {case.conversation_id!r}."
                        )
                        continue
                    proposal_targets.append(target)
    except httpx.TransportError as exc:
        failure = classify_provider_failure(exc=exc)
        if failure is not None:
            event = {"turn_index": turn_index, "event": "provider_failure", "data": failure}
            events.append(event)
            append_ndjson(case_dir / "events.ndjson", event)
            if provider_failures is not None:
                provider_failures.append({"phase": "stream", **failure})
            return
        errors.append(f"Stream turn {turn_index} transport failed: {exc}")
        return
    except Exception as exc:
        errors.append(f"Stream turn {turn_index} failed: {exc}")
        return

    confirm_records = await _drain_proposal_confirm_queue(
        client,
        proposal_targets,
        errors,
        expected_session_id=case.conversation_id,
        provider_failures=provider_failures,
    )
    for confirm_record in confirm_records:
        confirm_record.setdefault("turn_index", turn_index)
        confirm_log.append(confirm_record)
        confirm_event = {"turn_index": turn_index, "event": "proposal_confirm", "data": confirm_record}
        events.append(confirm_event)
        append_ndjson(case_dir / "events.ndjson", confirm_event)


async def _drain_proposal_confirm_queue(
    client: httpx.AsyncClient,
    proposal_targets: list[ProposalDecisionTarget],
    errors: list[str],
    *,
    expected_session_id: str,
    max_depth: int = 20,
    provider_failures: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    queue: list[ProposalDecisionTarget] = []
    queued_sessions: dict[str, str] = {}
    seen: set[str] = set()
    records: list[dict[str, Any]] = []

    def enqueue(target: ProposalDecisionTarget, *, source: str) -> None:
        if target.operator_session_id != expected_session_id:
            errors.append(
                f"Proposal {target.proposal_id} from {source} has session scope "
                f"{target.operator_session_id!r}, expected {expected_session_id!r}."
            )
            return
        previous_session = queued_sessions.get(target.proposal_id)
        if previous_session and previous_session != target.operator_session_id:
            errors.append(
                f"Proposal {target.proposal_id} was presented with conflicting session scopes "
                f"{previous_session!r} and {target.operator_session_id!r}."
            )
            return
        if target.proposal_id in seen or previous_session:
            return
        queued_sessions[target.proposal_id] = target.operator_session_id
        queue.append(target)

    for target in proposal_targets:
        enqueue(target, source="stream")

    while queue:
        if len(records) >= max_depth:
            errors.append(f"Proposal auto-confirm queue exceeded safe depth {max_depth}.")
            break
        target = queue.pop(0)
        queued_sessions.pop(target.proposal_id, None)
        if target.proposal_id in seen:
            continue
        seen.add(target.proposal_id)
        record = await _auto_confirm(client, target, provider_failures=provider_failures)
        discovered: list[str] = []
        for response in record.get("responses") or []:
            payload = response.get("response") if isinstance(response, Mapping) else {}
            try:
                next_targets = _proposal_targets_from_payload(payload)
            except ValueError as exc:
                errors.append(f"Confirmation response proposal scope is invalid: {exc}")
                continue
            for next_target in next_targets:
                before_count = len(queue)
                enqueue(next_target, source=f"confirmation of {target.proposal_id}")
                if len(queue) > before_count:
                    discovered.append(next_target.proposal_id)
        if discovered:
            record["discovered_proposal_ids"] = discovered
        records.append(record)
    return records


async def _auto_confirm(
    client: httpx.AsyncClient,
    target: ProposalDecisionTarget,
    provider_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    responses: list[dict[str, Any]] = []
    body: dict[str, Any] = {"operator_session_id": target.operator_session_id}
    for attempt in range(1, 4):
        response = await client.post(f"/api/harness-agent/proposals/{target.proposal_id}/confirm", json=body)
        raw_text = response.text
        try:
            payload = response.json()
        except json.JSONDecodeError:
            payload = {"ok": False, "raw": raw_text}
        responses.append(
            {
                "attempt": attempt,
                "status_code": response.status_code,
                "request_body": dict(body),
                "response": payload,
            }
        )
        failure = classify_provider_failure(status_code=response.status_code)
        if failure is not None:
            if provider_failures is not None:
                provider_failures.append(
                    {"phase": "confirm", "proposal_id": target.proposal_id, **failure}
                )
            break
        if response.status_code >= 400:
            break
        if not isinstance(payload, dict) or payload.get("status") != "awaiting_next_confirmation":
            break
        challenge = str(payload.get("next_challenge") or payload.get("confirmation_challenge") or "")
        if not challenge:
            break
        body = {
            "operator_session_id": target.operator_session_id,
            "confirmation_challenge": challenge,
        }
    return {
        "proposal_id": target.proposal_id,
        "operator_session_id": target.operator_session_id,
        "responses": responses,
    }


def _payload_for_turn(case: LiveAgentCase, turn: EvalTurn) -> dict[str, Any]:
    if case.route.startswith("/api/optimize/"):
        return {
            "session_id": case.conversation_id,
            "message": turn.user,
            "action": turn.action,
            "feedback": "",
        }
    return {
        "conversation_id": case.conversation_id,
        "messages": [{"role": "user", "content": turn.user}],
    }


def _proposal_target_from_event(event: Mapping[str, Any]) -> ProposalDecisionTarget:
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    proposal = data.get("proposal") if isinstance(data.get("proposal"), Mapping) else {}
    outer_id = str(data.get("proposal_id") or "").strip()
    proposal_id = str(proposal.get("proposal_id") or proposal.get("id") or outer_id).strip()
    if outer_id and proposal_id and outer_id != proposal_id:
        raise ValueError(f"proposal id mismatch between event {outer_id!r} and payload {proposal_id!r}")
    if not proposal_id:
        raise ValueError("proposal_id is missing")
    operator_session_id = str(
        proposal.get("operator_session_id") or data.get("operator_session_id") or ""
    ).strip()
    if not operator_session_id:
        raise ValueError(f"proposal {proposal_id} omitted operator_session_id")
    return ProposalDecisionTarget(proposal_id, operator_session_id)


def _proposal_targets_from_payload(payload: Any) -> list[ProposalDecisionTarget]:
    targets: list[ProposalDecisionTarget] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(candidate: Mapping[str, Any]) -> bool:
        proposal_id = str(candidate.get("proposal_id") or "").strip()
        fallback_id = str(candidate.get("id") or "").strip()
        if not proposal_id and fallback_id.startswith("prop_"):
            proposal_id = fallback_id
        if not proposal_id:
            return False
        operator_session_id = str(candidate.get("operator_session_id") or "").strip()
        if not operator_session_id:
            raise ValueError(f"proposal {proposal_id} omitted operator_session_id")
        identity = (proposal_id, operator_session_id)
        if identity not in seen:
            targets.append(ProposalDecisionTarget(*identity))
            seen.add(identity)
        return True

    def visit(value: Any, *, proposal_context: bool = False) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item, proposal_context=proposal_context)
            return
        if not isinstance(value, Mapping):
            return
        if proposal_context:
            add_candidate(value)
        proposal = value.get("proposal")
        if isinstance(proposal, Mapping):
            visit(proposal, proposal_context=True)
        for key in ("proposals", "next_proposals"):
            proposals = value.get(key)
            if isinstance(proposals, list):
                for item in proposals:
                    visit(item, proposal_context=True)
        for key in ("continuation", "result", "response"):
            nested = value.get(key)
            if isinstance(nested, (Mapping, list)):
                visit(nested, proposal_context=False)

    visit(payload)
    return targets


def _proposal_ids_from_payload(payload: Any) -> list[str]:
    """Compatibility projection for callers that only need identifiers."""
    return [target.proposal_id for target in _proposal_targets_from_payload(payload)]


async def _system_context_for_case(db: Any, case: LiveAgentCase) -> dict[str, Any]:
    from app.agent import orchestrator
    from app.models import models
    from app.operator.guards import ActorContext
    from app.operator.memory import retrieve_memories

    actor = ActorContext(
        actor_id=models.LOCAL_DEFAULT_ACTOR_ID,
        session_id=case.conversation_id,
        adapter="web",
    )
    active_skill = "resume-optimizer" if case.route.startswith("/api/optimize/") else None
    memories = await retrieve_memories(db, actor=actor)
    system_prompt = await orchestrator._build_system_prompt(
        db,
        actor,
        active_skill=active_skill,
        pending_proposals=[],
    )
    return {
        "active_skill": active_skill or "",
        "memory_count": len(memories.get("memories") or []) if isinstance(memories, dict) else 0,
        "memories": memories,
        "prompt_contains_memory": "Memories:" in system_prompt,
        "prompt_preview": system_prompt[:2000],
        # Exact system protocol the provider saw; artifacts redact it on write.
        "system_protocol": system_prompt,
    }


def _provider_tool_definitions() -> list[dict[str, Any]]:
    """Exact provider-visible universal tool definitions (no secrets)."""
    from app.operator.registry import UNIVERSAL_TOOL_NAMES, UNIVERSAL_TOOL_SPECS

    definitions: list[dict[str, Any]] = []
    for name in UNIVERSAL_TOOL_NAMES:
        spec = UNIVERSAL_TOOL_SPECS[name]
        definitions.append(
            {
                "name": name,
                "description": str(getattr(spec, "description", "") or ""),
                "parameters": dict(getattr(spec, "argument_schema", {}) or {}),
                "schema_loading": str(getattr(spec, "schema_loading", "") or ""),
                "side_effecting": bool(getattr(spec, "side_effecting", False)),
            }
        )
    return definitions


def _catalog_summary() -> dict[str, Any]:
    """Compact catalog digest/size retained in eval artifacts."""
    from app.operator.capability_map import capability_schema_digest, export_capability_catalog

    catalog = export_capability_catalog()
    payload = json.dumps(catalog, sort_keys=True, ensure_ascii=False, default=str)
    entries = 0
    if isinstance(catalog, Mapping):
        for section in ("models", "actions", "session_commands", "skills"):
            section_value = catalog.get(section)
            if isinstance(section_value, list):
                entries += len(section_value)
    return {
        "digest": capability_schema_digest(catalog),
        "characters": len(payload),
        "entries": entries,
    }


async def _provider_context_for_case(db: Any, case: LiveAgentCase, system_context: Mapping[str, Any]) -> dict[str, Any]:
    """Exact redacted provider context retained in eval artifacts:
    system protocol, provider-visible tool definitions, catalog digest/size,
    and loaded capability schemas/digests."""
    from sqlalchemy import select

    from app.models import models
    from app.operator.capability_map import describe_capability_contract

    from live_agent_trace import project_capability_load_receipts

    rows = (await db.execute(select(models.AgentCapabilityLoadReceipt))).scalars().all()
    receipts = project_capability_load_receipts(
        [
            {
                "actor_id": row.actor_id,
                "session_id": row.session_id,
                "capability_kind": row.capability_kind,
                "capability_name": row.capability_name,
                "operation": row.operation,
                "schema_digest": row.schema_digest,
                "loaded_at": row.loaded_at,
            }
            for row in rows
        ]
    )
    loaded: list[dict[str, Any]] = []
    for receipt in receipts:
        try:
            schema = describe_capability_contract(
                str(receipt["capability_kind"]),
                str(receipt["capability_name"]),
                str(receipt["operation"]),
            )
        except Exception:
            schema = {}
        loaded.append(
            {
                "capability_kind": receipt["capability_kind"],
                "capability_name": receipt["capability_name"],
                "operation": receipt["operation"],
                "schema_digest": receipt["schema_digest"],
                "schema": schema,
            }
        )
    return {
        "case_id": case.case_id,
        "system_protocol": str(system_context.get("system_protocol") or ""),
        "provider_tools": _provider_tool_definitions(),
        "catalog": _catalog_summary(),
        "loaded_capabilities": loaded,
    }


def _structured_tool_errors(events: Iterable[Mapping[str, Any]], tool_calls: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Structured tool error payloads (tool name, args, status, error detail)."""
    errors_by_call: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if not isinstance(event, Mapping) or str(event.get("event") or "") != "tool_execution_end":
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        call_id = str(data.get("tool_call_id") or data.get("toolCallId") or "")
        if call_id and bool(data.get("is_error") or data.get("isError")):
            errors_by_call[call_id] = data
    structured: list[dict[str, Any]] = []
    for call in tool_calls:
        if not bool(call.get("is_error")):
            continue
        call_id = str(call.get("tool_call_id") or "")
        data = errors_by_call.get(call_id) or {}
        result = data.get("result") if isinstance(data.get("result"), Mapping) else call.get("result") or {}
        structured.append(
            {
                "tool_call_id": call_id,
                "tool_name": call.get("tool_name"),
                "args": call.get("args"),
                "result_status": call.get("result_status"),
                "result": result,
            }
        )
    return structured


def _resume_optimizer_follow_up(final_text: str, turn_index: int) -> EvalTurn:
    text = str(final_text or "")
    if turn_index <= 2 and ("?" in text or "？" in text or "确认" not in text):
        return EvalTurn("嗯，重点放在 agent 产品、数据分析、用户研究和跨团队推进上；没有证据的经历不要加。", action="reply")
    return EvalTurn("这个方向可以，继续。", action="confirm")


def _has_confirmed_resume_proposal(confirm_log: list[dict[str, Any]]) -> bool:
    for item in confirm_log:
        for response in item.get("responses") or []:
            payload = response.get("response") if isinstance(response.get("response"), dict) else {}
            if payload.get("ok") is True and payload.get("status") not in {"awaiting_next_confirmation", None}:
                text = json.dumps(payload, ensure_ascii=False, default=str).lower()
                if "resume" in text or "简历" in text:
                    return True
    return False


def _proposal_records(after: dict[str, Any], confirm_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(item.get("proposal_id") or ""): item for item in confirm_log}
    records: list[dict[str, Any]] = []
    for row in after.get("ProposalCache") or []:
        if not isinstance(row, dict):
            continue
        proposal_id = str(row.get("proposal_id") or "")
        records.append(
            {
                "proposal_id": proposal_id,
                "status": row.get("status"),
                "tool_name": row.get("tool_name"),
                "model_or_action": row.get("model_or_action"),
                "locked_payload": row.get("locked_payload"),
                "summary": row.get("summary"),
                "risk_level": row.get("risk_level"),
                "affected_records": row.get("affected_records"),
                "result": row.get("confirmation_events"),
                "confirm_attempts": by_id.get(proposal_id, {}).get("responses", []),
            }
        )
    return records


def _missing_live_env() -> list[str]:
    required = [
        "OFFERU_LIVE_EVAL",
        "LIVE_EVAL_LLM_PROVIDER",
        "LIVE_EVAL_LLM_MODEL",
        "LIVE_EVAL_LLM_API_KEY",
    ]
    missing = [name for name in required if not str(os.environ.get(name) or "").strip()]
    if os.environ.get("OFFERU_LIVE_EVAL") and os.environ.get("OFFERU_LIVE_EVAL") != "1":
        missing.append("OFFERU_LIVE_EVAL=1")
    return missing


def _configure_live_settings() -> None:
    settings = get_settings()
    settings.llm_provider = str(os.environ["LIVE_EVAL_LLM_PROVIDER"]).strip()
    settings.llm_model = str(os.environ["LIVE_EVAL_LLM_MODEL"]).strip()
    settings.active_llm_api_key = str(os.environ["LIVE_EVAL_LLM_API_KEY"]).strip()
    settings.active_llm_base_url = str(os.environ.get("LIVE_EVAL_LLM_BASE_URL") or "").strip()
    settings.active_llm_config_id = "live-eval"
    settings.tier_model_map = {}


def _load_app_for_live_eval() -> Any:
    from app.main import app

    # app.main imports routes.config, which syncs runtime settings from
    # backend/config.json at import time. Reapply live env after that import so
    # the eval never falls back to a user's persisted local provider config.
    if not _missing_live_env():
        _configure_live_settings()
    return app


async def _run_seed_smoke() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "live-eval-seed-smoke.sqlite3"
        engine = create_async_engine("sqlite+aiosqlite:///" + db_path.as_posix(), echo=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                seed_ids = await seed_eval_db(db)
                snapshot = await snapshot_db(db)
            assert len(snapshot["Job"]) == 25
            assert len(snapshot["Pool"]) == 16
            assert len(snapshot["ProfileSection"]) == 12
            assert len(snapshot["Resume"]) == 30
            assert len(snapshot["ResumeSection"]) == 78
            assert len(snapshot["ApplicationRecord"]) == 8
            assert len(snapshot["AgentMemory"]) == 4
            print(
                "live eval seed smoke passed "
                f"jobs={len(snapshot['Job'])} pools={len(snapshot['Pool'])} "
                f"profile_sections={len(snapshot['ProfileSection'])} "
                f"resumes={len(snapshot['Resume'])} resume_sections={len(snapshot['ResumeSection'])} "
                f"application_records={len(snapshot['ApplicationRecord'])} memories={len(snapshot['AgentMemory'])} "
                f"target_job_id={seed_ids['jobs']['acme_ai_pm']}"
            )
        finally:
            await engine.dispose()


def _apply_case_settings(case: LiveAgentCase) -> dict[str, Any]:
    settings = get_settings()
    saved: dict[str, Any] = {}
    for key, value in case.settings_patch.items():
        saved[key] = getattr(settings, key)
        setattr(settings, key, value)
    return saved


def _restore_case_settings(saved: dict[str, Any]) -> None:
    settings = get_settings()
    for key, value in saved.items():
        setattr(settings, key, value)


def _remove_sqlite_files(db_path: Path) -> None:
    for path in (db_path, db_path.with_suffix(db_path.suffix + "-wal"), db_path.with_suffix(db_path.suffix + "-shm")):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def _print_cases() -> None:
    for case in list_cases():
        print(f"{case.case_id}\t{case.suite}\t{case.route}\t{case.purpose}")


def _render_summary_md(summary: dict[str, Any]) -> str:
    lines = [
        f"# Live Agent Eval {summary['run_id']}",
        "",
        f"- passed: `{summary['passed']}`",
        f"- total: `{summary['total']}`",
        f"- passed_count: `{summary['passed_count']}`",
        f"- failed_count: `{summary['failed_count']}`",
        f"- provider_failure_count: `{summary.get('provider_failure_count', 0)}`",
        "",
        "| Case | Result | Scores |",
        "|---|---|---|",
    ]
    for case in summary["cases"]:
        result = "PASS" if case["passed"] else "FAIL"
        lines.append(f"| {case['case_id']} | {result} | `{json.dumps(case.get('scores') or {}, ensure_ascii=False)}` |")
    provider_cases = [
        case for case in summary["cases"] if case.get("provider_failures")
    ]
    if provider_cases:
        lines.extend(["", "## Provider Availability Failures", ""])
        for case in provider_cases:
            categories = ", ".join(
                str(item.get("category") or "") for item in case["provider_failures"]
            )
            lines.append(f"- `{case['case_id']}`: {categories}")
    injection_cases = [
        case for case in summary["cases"] if case.get("user_confirm_injections")
    ]
    if injection_cases:
        lines.extend(["", "## User Confirmation Injections", ""])
        for case in injection_cases:
            injections = case.get("user_confirm_injections") or []
            progressed = sum(1 for item in injections if item.get("progress_after"))
            lines.append(
                f"- `{case['case_id']}`: {len(injections)} injection(s), "
                f"{progressed} progressed after injection"
            )
    lines.extend(
        [
            "",
            "Provider failures (HTTP 424 / provider_unavailable) are classified separately and are never labeled model behavior.",
            "",
            "This is not deterministic CI. Failures may come from model behavior, prompts, tools, backend bugs, seed data, or graders.",
        ]
    )
    return "\n".join(lines)


def _render_repeat_summary_md(summary: dict[str, Any]) -> str:
    lines = [
        f"# Live Agent Eval Repeats {summary['run_id']}",
        "",
        f"- passed: `{summary['passed']}`",
        f"- total_runs: `{summary['total_runs']}`",
        f"- passed_runs: `{summary['passed_runs']}`",
        f"- failed_runs: `{summary['failed_runs']}`",
        f"- pass_rate: `{summary['pass_rate']:.2%}`",
        "",
        "| Repeat | Result | Trace |",
        "|---|---|---|",
    ]
    for index, run in enumerate(summary.get("runs") or [], start=1):
        result = "PASS" if run.get("passed") else "FAIL"
        lines.append(f"| {index} | {result} | `{run.get('run_dir')}` |")
    return "\n".join(lines)


def _write_case_trace_artifacts(
    case_dir: Path,
    case: LiveAgentCase,
    *,
    events: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    confirmed_proposals: list[dict[str, Any]],
    changed: dict[str, Any],
    grade: dict[str, Any],
    provider_context: dict[str, Any] | None = None,
    turn_envelopes: list[dict[str, Any]] | None = None,
    provider_failures: list[dict[str, Any]] | None = None,
    confirm_injections: list[dict[str, Any]] | None = None,
) -> None:
    pending_proposals = [item for item in proposals if str(item.get("status") or "") == "pending"]
    provider_failures = provider_failures or []
    confirm_injections = confirm_injections or []
    compact = {
        "case_id": case.case_id,
        "purpose": case.purpose,
        "summary": {
            "case_id": case.case_id,
            "confirmed_proposal_count": len(confirmed_proposals),
            "pending_proposal_count": len(pending_proposals),
            "tool_call_count": len(tool_calls),
            "db_changed_tables": sorted(changed.keys()),
            "grader_passed": bool(grade.get("passed")),
            "issue_type": str(grade.get("issue_type") or ""),
            "provider_failure_count": len(provider_failures),
            "user_confirm_injection_count": len(confirm_injections),
        },
        "turns": [{"index": index + 1, "user": turn.user, "action": turn.action} for index, turn in enumerate(case.turns)],
        "final_text": extract_final_text(events),
        "event_sequence": [event.get("event") for event in events],
        "tool_calls": [
            {
                "tool_name": call.get("tool_name"),
                "args": call.get("args"),
                "is_error": call.get("is_error", False),
                "result_status": call.get("result_status"),
                "proposal_id": call.get("proposal_id"),
            }
            for call in tool_calls
        ],
        "proposal_confirms": [
            {
                "proposal_id": item.get("proposal_id"),
                "status": item.get("status"),
                "tool_name": item.get("tool_name"),
                "model_or_action": item.get("model_or_action"),
                "summary": item.get("summary"),
            }
            for item in confirmed_proposals
        ],
        "changed_records": changed,
        "grader": grade,
        "provider_failures": provider_failures,
    }
    write_json(case_dir / "trace_compact.json", compact)
    write_text(case_dir / "trace.md", _render_case_trace_md(compact))
    if provider_context:
        write_json(case_dir / "provider_context.json", provider_context)
    if turn_envelopes:
        write_json(case_dir / "execution_envelope.json", {"per_turn": turn_envelopes})
    if provider_failures:
        write_json(case_dir / "provider_failure_classification.json", provider_failures)
    if confirm_injections:
        write_json(case_dir / "user_confirm_injections.json", confirm_injections)
    tool_errors = _structured_tool_errors(events, tool_calls)
    if tool_errors:
        write_json(case_dir / "tool_errors.json", tool_errors)


def _render_case_trace_md(compact: Mapping[str, Any]) -> str:
    summary = compact.get("summary") if isinstance(compact.get("summary"), Mapping) else {}
    lines = [
        f"# Trace: {compact.get('case_id')}",
        "",
        f"- purpose: {compact.get('purpose')}",
        f"- passed: `{bool((compact.get('grader') or {}).get('passed'))}`",
        f"- issue_type: `{summary.get('issue_type', '')}`",
        f"- confirmed_proposal_count: `{summary.get('confirmed_proposal_count', 0)}`",
        f"- pending_proposal_count: `{summary.get('pending_proposal_count', 0)}`",
        f"- tool_call_count: `{summary.get('tool_call_count', 0)}`",
        f"- db_changed_tables: `{', '.join(str(item) for item in summary.get('db_changed_tables') or []) or '(none)'}`",
        "",
        "## Turns",
        "",
    ]
    for turn in compact.get("turns") or []:
        if not isinstance(turn, Mapping):
            continue
        lines.extend(
            [
                f"### Turn {turn.get('index')} `{turn.get('action')}`",
                "",
                str(turn.get("user") or "(empty system step)"),
                "",
            ]
        )
    lines.extend(["## Final", "", str(compact.get("final_text") or "(empty)"), "", "## Tool Calls", ""])
    tool_calls = compact.get("tool_calls") if isinstance(compact.get("tool_calls"), list) else []
    if tool_calls:
        for index, call in enumerate(tool_calls, start=1):
            if not isinstance(call, Mapping):
                continue
            lines.append(
                f"{index}. `{call.get('tool_name')}` error=`{bool(call.get('is_error'))}` "
                f"status=`{call.get('result_status') or ''}` proposal=`{call.get('proposal_id') or ''}`"
            )
    else:
        lines.append("- No tool calls.")
    lines.extend(["", "## Proposal Confirms", ""])
    proposals = compact.get("proposal_confirms") if isinstance(compact.get("proposal_confirms"), list) else []
    if proposals:
        for proposal in proposals:
            if not isinstance(proposal, Mapping):
                continue
            lines.append(
                f"- `{proposal.get('proposal_id')}` {proposal.get('tool_name') or ''}/"
                f"{proposal.get('model_or_action') or ''}: {proposal.get('summary') or ''}"
            )
    else:
        lines.append("- No confirmed proposals.")
    lines.extend(
        [
            "",
            "## DB Changed Records",
            "",
            "```json",
            json.dumps(compact.get("changed_records") or {}, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
            "## Grader Reasons",
            "",
        ]
    )
    grader = compact.get("grader") if isinstance(compact.get("grader"), Mapping) else {}
    for reason in grader.get("reasons") or []:
        lines.append(f"- {reason}")
    if not grader.get("reasons"):
        lines.append("- No reasons recorded.")
    lines.extend(["", "## Event Sequence", "", "`" + " -> ".join(str(item) for item in compact.get("event_sequence") or []) + "`"])
    return "\n".join(lines)


def _render_issues_md(results: list[CaseRunResult]) -> str:
    lines = ["# Live Agent Eval Issues", ""]
    failed = [result for result in results if not result.passed]
    if not failed:
        lines.append("No failed cases in this run.")
        return "\n".join(lines)
    for result in failed:
        lines.extend(
            [
                f"## {result.case_id}",
                "",
                f"- verdict: `{result.run_dir / 'verdict.md'}`",
                f"- issue_type: `{result.grader.get('issue_type', '')}`",
            ]
        )
        for reason in result.grader.get("reasons", []):
            lines.append(f"- {reason}")
        for error in result.errors:
            lines.append(f"- runner/route error: {error}")
        for failure in result.provider_failures:
            lines.append(
                f"- provider failure ({failure.get('category') or 'provider_unavailable'}): "
                f"{failure.get('detail') or ''}"
            )
        lines.append("")
    return "\n".join(lines)


def _ensure_findings_file() -> None:
    if FINDINGS_PATH.exists():
        return
    FINDINGS_PATH.write_text(
        "# Live Agent Eval Findings\n\nConfirmed production findings surfaced by live eval should be appended here. No findings recorded yet.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
