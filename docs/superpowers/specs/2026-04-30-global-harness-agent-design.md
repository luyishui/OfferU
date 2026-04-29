# Global Harness Agent Design

## Goal

Build a global OfferU harness agent that can operate the user's job-search workflow end to end: understand the user's profile, explore career paths, search and triage jobs, generate tailored resumes, write selected jobs into application tracking, and summarize the next actions.

The first implementation should make the existing `/agent` page and always-available dock use the same backend orchestration path. It should not attempt fully automatic external website application submission in this phase.

## Current Context

OfferU already has the pieces needed for a controlled agent loop:

- `backend/app/routes/agent.py` exposes `/api/agent/chat`, but it is a thin LLM-to-tool router with limited safety controls.
- `backend/app/mcp_server.py` defines 13 useful tools for profile, jobs, pools, resumes, applications, cover letters, and stats.
- `backend/app/routes/scraper.py` can trigger job collection tasks for supported sources.
- `backend/app/routes/applications.py` has the newer table workspace and job import APIs.
- `frontend/src/app/agent/page.tsx` is a full-page agent console.
- `frontend/src/components/ai/ProfileAgentDock.tsx` is globally mounted, but its behavior is profile-building specific.

The target design is to keep these working pieces and introduce a new orchestration layer around them.

## Non-Goals

- No one-click submission to arbitrary company career sites in this phase.
- No deletion, destructive overwrite, or bulk record movement without explicit confirmation.
- No hidden writes. The agent must expose what it is about to change.
- No new external web search dependency for the first pass. The agent should prefer OfferU's existing scraper/job database.

## User Experience

The global agent should feel like an operating layer for OfferU:

- The user can ask high-level requests such as "help me find roles I did not think of" or "find suitable internships and prepare applications".
- The agent replies with a short plan, executes safe read-only steps, then proposes write actions.
- The agent shows tool calls and results in the UI so the user can understand what happened.
- The agent can produce career-path recommendations inspired by the provided Xiaohongshu prompt:
  - transferable skills summary
  - 5 to 7 unexpected career paths
  - each path includes industry, fit reason, entry route, salary range, search keywords, and application strategy
  - quick wins and transition timeline
- The agent can turn selected paths into OfferU actions:
  - run or suggest scraper tasks
  - find matching jobs in the job database
  - triage jobs into a pool
  - generate tailored resumes for selected jobs
  - import selected jobs into application tracking

## Architecture

### Backend

Create a focused harness agent service:

- `backend/app/services/harness_agent.py`
  - owns intent planning, tool execution policy, career-path prompt construction, and response normalization
  - exposes pure helpers that can be tested without running FastAPI
  - separates read-only tools, safe write tools, and confirmation-required tools
- `backend/app/routes/harness_agent.py`
  - exposes `/api/harness-agent/chat`
  - returns one JSON response per request in the first pass
  - can be upgraded to SSE later without changing the internal service contract

Keep `backend/app/routes/agent.py` available for compatibility, but update frontend to use the new endpoint for the primary experience.

### Tool Layer

Represent each tool as a registry entry:

- `name`
- `description`
- `parameters`
- `risk_level`: `read`, `write`, or `confirm`
- `handler`

Initial tools:

- `get_profile`
- `list_jobs`
- `get_job`
- `job_stats`
- `list_pools`
- `triage_job`
- `batch_triage`
- `run_scraper`
- `list_scraper_sources`
- `list_scraper_tasks`
- `list_resumes`
- `generate_resume`
- `list_applications`
- `import_jobs_to_application_table`
- `create_application`
- `list_calendar_events`
- `auto_fill_calendar`
- `list_email_notifications`
- `sync_email_notifications`
- `list_interview_questions`
- `career_exploration`

The first pass can implement `career_exploration` as a structured LLM call plus deterministic fallback. The fallback should still return the expected sections if the LLM is unavailable.

### Safety Policy

The service must never blindly execute high-impact actions.

- `read`: may run immediately.
- `write`: may run if the user directly asked for the action and the action is small and reversible, such as creating a pending application record.
- `confirm`: must return `requires_confirmation: true` with a proposed action list. Examples: batch triage, batch import, batch resume generation, email sync, calendar auto-fill.

The request can include a `confirmed_action_ids` list. Only matching planned actions should execute.

### Data Flow

1. Frontend sends messages to `/api/harness-agent/chat`.
2. Backend builds a compact context from recent messages.
3. Planner classifies intent:
   - career exploration
   - job search or scraping
   - job triage
   - resume generation
   - application tracking
   - interview/calendar/email follow-up
   - general answer
4. Backend runs read-only context tools.
5. Backend either executes safe actions or returns a confirmation plan.
6. Response contains:
   - `assistant_message`
   - `mode`
   - `tool_calls`
   - `proposed_actions`
   - `requires_confirmation`
   - optional `career_paths`
   - optional `job_cards`
   - optional `next_steps`

## Frontend

Create a reusable global agent UI:

- `frontend/src/components/ai/HarnessAgentDock.tsx`
  - replaces the global profile-only dock in `Providers`
  - supports natural-language chat, quick actions, tool-call preview, proposed-action confirmation, and result cards
- `frontend/src/app/agent/page.tsx`
  - reuses the same client API and rendering model
  - remains a larger workspace for long sessions
- `frontend/src/lib/api.ts`
  - add `harnessAgentApi.chat`
  - define shared TypeScript types for responses, proposed actions, tool calls, career paths, and job cards

The existing `ProfileAgentDock` can remain in the repository during transition, but it should not be globally mounted once the harness dock is ready.

## Error Handling

- If LLM output is invalid, fallback to deterministic intent handling and ask a concise follow-up or return a safe summary.
- If a tool fails, include the failed tool in `tool_calls` and continue with available context.
- If a write action is blocked by confirmation, return the proposed actions instead of executing.
- If the database has no profile or jobs, the agent should guide the user to import a resume, complete profile fields, or run scrapers.
- If a scraper source is not ready, the agent should report that source status and suggest a ready source.

## Testing

Use TDD for implementation.

Backend tests should cover:

- tool registry risk levels
- intent classification for career exploration and job workflow prompts
- career exploration response shape with deterministic fallback
- confirmation gate blocks batch writes
- confirmed actions execute only matching action IDs
- job-to-application import maps company, title, location, salary, source, and apply URL into stable fields

Frontend tests are not currently configured in the repo. For the first pass, verify TypeScript build and use browser smoke testing. If frontend test tooling is added, cover response rendering and confirmation flow.

## Acceptance Criteria

- A user can ask the global agent for unexpected career paths and receive structured, useful output.
- A user can ask the agent to find suitable OfferU jobs and receive job cards from the local job database or scraper task suggestions.
- The agent can propose batch actions without executing them before confirmation.
- The agent can write selected jobs into application tracking without field mix-ups.
- The `/agent` page and global dock both use the harness agent API.
- Existing profile, jobs, resume, applications, scraper, calendar, email, and interview routes continue to work.

## Implementation Scope

This is one feature slice:

- Backend harness orchestration and route
- Tool wrappers for existing OfferU capabilities
- Career exploration structured output
- Global dock replacement
- Agent page API migration
- Focused backend tests and build verification

External all-network application-link discovery is a future slice after the harness loop is stable.
