from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from live_agent_cases import EvalTurn, HARNESS_STREAM, LiveAgentCase
from live_agent_eval import (
    RUNS_DIR,
    RuntimePatch,
    _configure_live_settings,
    _load_app_for_live_eval,
    _missing_live_env,
    _run_stream_turn,
)
from live_agent_grader import changed_records
from live_agent_seed import seed_eval_db, snapshot_db
from live_agent_trace import extract_final_text, extract_tool_calls, write_json, write_text


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run a live agent conversation one user turn at a time.")
    parser.add_argument("--conversation-id", default="interactive_live_probe")
    parser.add_argument("--case-id", default="interactive_live_probe")
    parser.add_argument("--keep-db", action="store_true")
    args = parser.parse_args()

    missing = _missing_live_env()
    if missing:
        print("missing live eval env: " + ", ".join(missing), file=sys.stderr)
        return 2
    _configure_live_settings()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-interactive")
    case_dir = RUNS_DIR / run_id / args.case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    db_path = case_dir / "case.sqlite3"
    history_path = case_dir / "harness_agent_conversations.json"
    events: list[dict[str, Any]] = []
    confirm_log: list[dict[str, Any]] = []
    errors: list[str] = []

    case = LiveAgentCase(
        case_id=args.case_id,
        purpose="Interactive live probe with human-selected follow-up turns.",
        route=HARNESS_STREAM,
        conversation_id=args.conversation_id,
        turns=(),
    )
    write_json(case_dir / "case.json", case.public_dict())

    async with RuntimePatch(db_path=db_path, history_path=history_path, keep_db=args.keep_db) as session_factory:
        async with session_factory() as db:
            seed_ids = await seed_eval_db(db)
            before = await snapshot_db(db)
        write_json(case_dir / "seed_db.json", {"seed_ids": seed_ids, "snapshot": before})
        write_json(case_dir / "db_before.json", before)

        app = _load_app_for_live_eval()
        print(f"[interactive-probe] run_dir={case_dir}")
        print("[interactive-probe] enter one user turn per line; /quit to finish")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://live-eval.local",
            timeout=None,
        ) as client:
            turn_index = 0
            while True:
                print("\nUSER> ", end="", flush=True)
                line = await asyncio.to_thread(sys.stdin.readline)
                if line == "":
                    break
                user_text = line.rstrip("\n")
                if user_text.strip() in {"/quit", "/exit"}:
                    break
                if not user_text.strip():
                    continue
                turn_index += 1
                previous_confirm_count = len(confirm_log)
                previous_error_count = len(errors)
                await _run_stream_turn(
                    client,
                    case,
                    EvalTurn(user_text),
                    turn_index,
                    case_dir,
                    events,
                    confirm_log,
                    errors,
                )
                final_text = extract_final_text(events)
                print("\nASSISTANT_FINAL:")
                print(final_text or "(empty)")
                new_confirms = confirm_log[previous_confirm_count:]
                if new_confirms:
                    print("\nAUTO_CONFIRMS:")
                    for record in new_confirms:
                        proposal_id = record.get("proposal_id")
                        statuses = [
                            f"{item.get('status_code')}:{_response_status(item.get('response'))}"
                            for item in record.get("responses", [])
                            if isinstance(item, dict)
                        ]
                        discovered = record.get("discovered_proposal_ids") or []
                        suffix = f" discovered={discovered}" if discovered else ""
                        print(f"- {proposal_id} attempts={statuses}{suffix}")
                if len(errors) > previous_error_count:
                    print("\nERRORS:")
                    for error in errors[previous_error_count:]:
                        print(f"- {error}")

    async with session_factory() as db:
        after = await snapshot_db(db)
    changed = changed_records(before, after)
    write_json(case_dir / "db_after.json", after)
    write_json(case_dir / "tool_calls.json", extract_tool_calls(events))
    write_json(case_dir / "confirm_log.json", confirm_log)
    write_json(case_dir / "changed_records.json", changed)
    write_text(case_dir / "assistant_final.txt", extract_final_text(events))

    print("\n[interactive-probe] finished")
    print(f"[interactive-probe] run_dir={case_dir}")
    print("[interactive-probe] changed_tables=" + ", ".join(changed.keys() or ["(none)"]))
    return 0 if not errors else 1


def _response_status(payload: Any) -> str:
    if isinstance(payload, dict):
        status = str(payload.get("status") or payload.get("stop_reason") or "")
        ok = payload.get("ok")
        if ok is not None:
            return f"ok={ok},status={status}"
        return status
    return ""


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
