# Global Harness Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a global OfferU harness agent that can reason over profile, jobs, resumes, applications, scraper tasks, calendar, email, and interview context from one chat surface.

**Architecture:** Add a backend `harness_agent` service with typed response dictionaries, deterministic intent classification, tool registry risk levels, confirmation-gated actions, and a FastAPI route. Replace the globally mounted profile-only dock with a reusable harness dock and move the `/agent` page to the same API contract.

**Tech Stack:** FastAPI, SQLAlchemy async sessions, existing OfferU service functions, standalone Python script tests, Next.js 14, React, NextUI, SWR/fetch.

---

## File Structure

- Create `backend/app/services/harness_agent.py`: pure planning helpers, tool registry, career exploration fallback, confirmation gate, and async orchestration.
- Create `backend/app/routes/harness_agent.py`: FastAPI request/response models and `/api/harness-agent/chat`.
- Modify `backend/app/main.py`: include the harness route.
- Create `backend/scripts/test_harness_agent.py`: standalone TDD tests matching existing backend script style.
- Modify `frontend/src/lib/api.ts`: add shared harness agent types and `harnessAgentApi.chat`.
- Create `frontend/src/components/ai/HarnessAgentDock.tsx`: global chat dock with quick actions, tool traces, job cards, career paths, and confirmation buttons.
- Modify `frontend/src/app/providers.tsx`: mount `HarnessAgentDock` instead of `ProfileAgentDock`.
- Modify `frontend/src/app/agent/page.tsx`: use the harness API and shared response shape.

## Task 1: Backend Core Contract

**Files:**
- Create: `backend/app/services/harness_agent.py`
- Create: `backend/scripts/test_harness_agent.py`

- [ ] **Step 1: Write the failing tests**

Add these tests to `backend/scripts/test_harness_agent.py`:

```python
from __future__ import annotations

import asyncio
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
    assert {"title", "industry", "fit_reason", "entry_route", "salary_range", "search_keywords", "application_strategy"} <= set(first)
    assert len(payload["quick_wins"]) == 3
    assert payload["reality_check"]["timeline"]


if __name__ == "__main__":
    test_registry_declares_risk_levels()
    test_classify_career_exploration_prompt()
    test_classify_job_workflow_prompt()
    test_career_fallback_shape_has_paths_and_next_steps()
    print("harness agent core tests passed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python backend\scripts\test_harness_agent.py
```

Expected: fail with `ModuleNotFoundError: No module named 'app.services.harness_agent'`.

- [ ] **Step 3: Implement the minimal backend core**

Create `backend/app/services/harness_agent.py` with:

```python
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
```

Then add `get_default_tool_registry`, `classify_intent`, and `build_career_exploration_fallback` so the tests pass.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python backend\scripts\test_harness_agent.py
```

Expected: `harness agent core tests passed`.

- [ ] **Step 5: Commit**

Run:

```powershell
git add backend\app\services\harness_agent.py backend\scripts\test_harness_agent.py
git commit -m "feat: add harness agent core"
```

## Task 2: Confirmation Gate and Action Execution

**Files:**
- Modify: `backend/app/services/harness_agent.py`
- Modify: `backend/scripts/test_harness_agent.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
from app.services.harness_agent import (  # noqa: E402
    execute_planned_actions,
    plan_action,
)


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
        plan_action("batch_triage", {"job_ids": [2], "status": "ignored"}),
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
```

Update the `__main__` block to run both tests.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python backend\scripts\test_harness_agent.py
```

Expected: fail with `ImportError` for `execute_planned_actions` or `plan_action`.

- [ ] **Step 3: Implement confirmation helpers**

Add:

```python
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
```

Add `build_action_summary` and `execute_planned_actions`. `execute_planned_actions` should skip confirm actions unless their IDs are present in `confirmed_action_ids`.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python backend\scripts\test_harness_agent.py
```

Expected: `harness agent core tests passed`.

- [ ] **Step 5: Commit**

Run:

```powershell
git add backend\app\services\harness_agent.py backend\scripts\test_harness_agent.py
git commit -m "feat: gate harness agent actions"
```

## Task 3: Tool Wrappers and Route

**Files:**
- Modify: `backend/app/services/harness_agent.py`
- Create: `backend/app/routes/harness_agent.py`
- Modify: `backend/app/main.py`
- Modify: `backend/scripts/test_harness_agent.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
from app.services.harness_agent import (  # noqa: E402
    build_application_import_preview,
    run_harness_agent_turn,
)


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
```

Update the `__main__` block to run the new tests.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python backend\scripts\test_harness_agent.py
```

Expected: fail with `ImportError` for `run_harness_agent_turn` or `build_application_import_preview`.

- [ ] **Step 3: Implement tool wrappers and turn orchestration**

Implement:

```python
async def run_harness_agent_turn(
    *,
    messages: list[dict[str, str]],
    confirmed_action_ids: list[str] | None = None,
    tool_runner: Callable[[str, dict[str, Any]], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    user_message = last_user_message(messages)
    mode = classify_intent(user_message)
    registry = get_default_tool_registry()
    runner = tool_runner or default_tool_runner
```

The function should:

- call `get_profile` for career and workflow requests
- call `list_jobs` for job workflow requests
- return `career_paths` for career exploration
- return `job_cards` from `list_jobs` results
- return proposed confirmation actions for batch-oriented requests

- [ ] **Step 4: Add FastAPI route**

Create `backend/app/routes/harness_agent.py`:

```python
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.harness_agent import run_harness_agent_turn

router = APIRouter()


class HarnessAgentMessage(BaseModel):
    role: str
    content: str


class HarnessAgentChatRequest(BaseModel):
    messages: list[HarnessAgentMessage]
    confirmed_action_ids: list[str] = []


@router.post("/chat")
async def chat(body: HarnessAgentChatRequest) -> dict[str, Any]:
    return await run_harness_agent_turn(
        messages=[m.model_dump() for m in body.messages],
        confirmed_action_ids=body.confirmed_action_ids,
    )
```

Modify `backend/app/main.py`:

```python
from app.routes import harness_agent
app.include_router(harness_agent.router, prefix="/api/harness-agent", tags=["Harness Agent"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```powershell
python backend\scripts\test_harness_agent.py
```

Expected: `harness agent core tests passed`.

- [ ] **Step 6: Commit**

Run:

```powershell
git add backend\app\services\harness_agent.py backend\app\routes\harness_agent.py backend\app\main.py backend\scripts\test_harness_agent.py
git commit -m "feat: add harness agent route"
```

## Task 4: Frontend API and Global Dock

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/ai/HarnessAgentDock.tsx`
- Modify: `frontend/src/app/providers.tsx`
- Modify: `frontend/src/app/agent/page.tsx`

- [ ] **Step 1: Add client API types**

Modify `frontend/src/lib/api.ts` with:

```ts
export interface HarnessAgentMessage {
  role: "user" | "assistant";
  content: string;
}

export interface HarnessAgentToolCall {
  tool: string;
  args: Record<string, unknown>;
  result: unknown;
}

export interface HarnessAgentProposedAction {
  id: string;
  tool: string;
  summary: string;
  risk_level: "read" | "write" | "confirm";
  requires_confirmation: boolean;
  args: Record<string, unknown>;
}

export interface HarnessAgentCareerPath {
  title: string;
  industry: string;
  fit_reason: string;
  entry_route: string;
  salary_range: string;
  search_keywords: string[];
  application_strategy: string;
}

export interface HarnessAgentJobCard {
  id: number;
  title: string;
  company: string;
  location: string;
  salary_text: string;
  source: string;
  apply_url: string;
}

export interface HarnessAgentResponse {
  assistant_message: string;
  mode: string;
  requires_confirmation: boolean;
  tool_calls: HarnessAgentToolCall[];
  proposed_actions: HarnessAgentProposedAction[];
  career_paths?: HarnessAgentCareerPath[];
  job_cards?: HarnessAgentJobCard[];
  next_steps?: string[];
}

export const harnessAgentApi = {
  chat: (data: { messages: HarnessAgentMessage[]; confirmed_action_ids?: string[] }) =>
    request<HarnessAgentResponse>("/api/harness-agent/chat", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};
```

- [ ] **Step 2: Create dock component**

Create `frontend/src/components/ai/HarnessAgentDock.tsx` with these states:

```ts
const [open, setOpen] = useState(false);
const [messages, setMessages] = useState<DockMessage[]>([]);
const [input, setInput] = useState("");
const [pendingActions, setPendingActions] = useState<HarnessAgentProposedAction[]>([]);
const [loading, setLoading] = useState(false);
const [error, setError] = useState("");
```

The dock should render:

- compact floating button when closed
- chat messages when open
- quick action chips for career exploration, job matching, resume generation, and application tracking
- career path cards when present
- job cards when present
- confirm button for `pendingActions`

- [ ] **Step 3: Mount dock globally**

Modify `frontend/src/app/providers.tsx`:

```tsx
import { HarnessAgentDock } from "@/components/ai/HarnessAgentDock";
```

Replace:

```tsx
<ProfileAgentDock />
```

with:

```tsx
<HarnessAgentDock />
```

- [ ] **Step 4: Migrate `/agent` page**

Modify `frontend/src/app/agent/page.tsx` to call `harnessAgentApi.chat` instead of streaming `/api/agent/chat`. Keep the existing visual language, but show `tool_calls`, `career_paths`, `job_cards`, and `proposed_actions` from the new response.

- [ ] **Step 5: Run frontend verification**

Run:

```powershell
npm run build
```

from `frontend`.

Expected: build exits with code 0.

- [ ] **Step 6: Commit**

Run:

```powershell
git add frontend\src\lib\api.ts frontend\src\components\ai\HarnessAgentDock.tsx frontend\src\app\providers.tsx frontend\src\app\agent\page.tsx
git commit -m "feat: add global harness agent UI"
```

## Task 5: End-to-End Verification

**Files:**
- No planned file edits

- [ ] **Step 1: Run backend tests**

Run:

```powershell
python backend\scripts\test_harness_agent.py
python backend\scripts\test_profile_builder_agent.py
```

Expected: both scripts exit with code 0.

- [ ] **Step 2: Run frontend build**

Run:

```powershell
npm run build
```

from `frontend`.

Expected: build exits with code 0.

- [ ] **Step 3: Start or reuse local app server**

If no dev server is running, start:

```powershell
Start-Process -WindowStyle Hidden -FilePath "powershell" -ArgumentList "-NoProfile", "-Command", "cd 'F:\Workspace_For_Vscode\python\OfferU\frontend'; npm run dev -- --port 3001"
```

Expected: `http://127.0.0.1:3001` loads the app.

- [ ] **Step 4: Browser smoke test**

Use the in-app browser on `http://127.0.0.1:3001/profile`:

- open the global harness dock
- send `参考我的档案，给我 5 个意想不到但适合我的职业方向`
- verify career path cards appear
- send `帮我看看适合投哪些岗位`
- verify job cards or scraper guidance appears

- [ ] **Step 5: Final status check**

Run:

```powershell
git status --short --branch
git log --oneline -5
```

Expected: branch is `codex/harness-agent`; only intentional changes are present.
