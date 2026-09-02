# Live Agent Eval Findings

Confirmed production findings surfaced by live eval should be appended here.

## 2026-07-05 - Agent compaction build_context can hit MissingGreenlet after commit

- Runs:
  - `backend/.eval-runs/20260705-212920/`
  - `backend/.eval-runs/20260705-213736/`
- Provider/model: `deepseek` / `deepseek-v4-flash`
- Symptom: during the smoke live eval, production code logged `Agent turn failed` with `sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; can't call await_only() here`.
- Stack root:
  - `backend/app/agent/orchestrator.py:_maybe_compact()` calls `tree.build_context()` after the turn has committed.
  - `backend/app/agent/session/tree.py:path_to_root()` accesses `entry.entry_type`.
  - SQLAlchemy attempts async lazy refresh for an expired `AgentTreeEntry` ORM object outside a greenlet.
- Impact: a turn or proposal-confirmation continuation can be marked failed after useful work has already been persisted, especially around post-turn compaction checks.
- Current task action: recorded only. Production agent/session logic was not changed by the live eval task.

## 2026-07-06 - Proposal confirmation can persist the write but return transient MissingGreenlet during continuation

- Run:
  - `backend/.eval-runs/20260706-002621/repeat-01/resume_revision_chain/`
  - `backend/.eval-runs/20260706-122821/repeat-03/resume_revision_chain/`
- Provider/model: `deepseek` / `deepseek-v4-flash`
- Symptom: the resume create proposal was persisted and `ProposalCache.status` became `confirmed`, but the public confirm response recorded by the eval runner was:
  - `ok=false`
  - `error.code=transient_error`
  - details include `greenlet_spawn has not been called; can't call await_only() here`
- Evidence:
  - `proposals.json` shows `prop_20722d132fb24626b0302f4b3580c8ca` as `confirmed` and a new `resume` record `id=31`.
  - The same proposal's `confirm_attempts[0].response` is the transient MissingGreenlet error.
  - Continuation-created `resume_section` proposals remained pending because the confirm response surfaced as an error.
  - In the `20260706-122821` run, `prop_7d58ec8418ed4a12be105b8610c40b68` shows the same pattern: `ProposalCache.status=confirmed` while `confirm_attempts[0].response.ok=false` with `greenlet_spawn has not been called`.
- Impact: a user or caller can see proposal confirmation fail even though the guarded write has already executed, leaving UI/eval state inconsistent and follow-up proposals unconfirmed.
- Current task action: recorded only. Production proposal/orchestrator/session logic was not changed by the live eval task.

## 2026-07-06 - Concurrent tool reads can trip SQLAlchemy session provisioning errors

- Run:
  - `backend/.eval-runs/20260706-110609/repeat-02/compaction_multi_proposal_survival/`
  - `backend/.eval-runs/20260706-122821/repeat-03/resume_revision_chain/`
- Provider/model: `deepseek` / `deepseek-v4-flash`
- Symptom: several `get_record` tool executions returned `transient_error` with SQLAlchemy reporting `This session is provisioning a new connection; concurrent operations are not permitted`.
- Evidence:
  - `tool_calls.json` contains multiple tool errors with that exact SQLAlchemy message.
  - `events.ndjson` records the errors as `tool_execution_end` events, so the agent loop continued instead of crashing.
  - In the `20260706-122821` run, concurrent `create_record(resume_section)` calls in `resume_revision_chain` surfaced the same SQLAlchemy message before the agent retried and created replacement proposals.
- Impact: the dual-loop correctly feeds tool errors back to the model, but concurrent tool execution may be using one async DB session in a way SQLAlchemy rejects under live multi-tool batches.
- Current task action: recorded only. Production executor/orchestrator/session logic was not changed by the live eval task.

## 2026-07-06 - Application workspace import action is exposed but cannot execute in this worktree

- Run:
  - `backend/.eval-runs/20260706-114927/repeat-01/job_application_resume_bundle/`
- Provider/model: `deepseek` / `deepseek-v4-flash`
- Symptom: the agent created and confirmed an `application_table`, then continuation proposed `invoke_action(import_jobs_to_application_table)`. Auto-confirm attempted that proposal, but the confirm response returned:
  - `ok=false`
  - `error.code=validation_error`
  - `message=Application workspace import helper is not available in this worktree.`
  - details include `cannot import name 'create_records_from_jobs_no_commit' from 'app.services.application_workspace'`
- Evidence:
  - `proposals.json` shows `prop_ca69b15fdbd743b5bed7aa4da7547df7` confirmed for `create_record(application_table)`.
  - `proposals.json` shows continuation proposal `prop_5d02f86c16a2451e8dd5a8893842b02b` remained `pending` after one confirm attempt with the validation error above.
- Impact: the agent can plan and request a guarded application import that the production confirm path cannot execute, leaving multi-proposal application workflows partially completed.
- Current task action: recorded only. Production action/proposal code was not changed by the live eval task.

## 2026-07-06 - Proposal confirmation can create follow-up resume_section proposals while returning MissingGreenlet

- Run:
  - `backend/.eval-runs/20260706-114927/repeat-01/resume_revision_chain/`
- Provider/model: `deepseek` / `deepseek-v4-flash`
- Symptom: the runner confirmed `create_record(resume)`, and the DB shows the resume proposal became `confirmed`. The public confirm response still returned:
  - `ok=false`
  - `error.code=transient_error`
  - details include `greenlet_spawn has not been called; can't call await_only() here`
- Evidence:
  - `proposals.json` shows `prop_b585d89593c740a0b6ce0d6c21334efc` as `confirmed` with a new `Resume` row `id=31`.
  - The same file shows pending continuation proposals for `create_record(resume_section)`: `prop_590674bb481342a69ff5ce3e4ce7f86b`, `prop_d8055b85b26a406d92f96b4accebea57`, and `prop_f789972d600a4a0781cb200c7aa77fe1`.
  - Those section proposals had no confirm attempts because the confirm response surfaced as an error instead of returning continuation proposals.
- Impact: recursive auto-confirm cannot safely drain proposals that production created but failed to return due to the continuation error; user-visible state can report failure after the primary write already persisted.
- Current task action: recorded only. Production proposal/orchestrator/session logic was not changed by the live eval task.

## 2026-07-06 - Pending proposal returned in final response can be expired before immediate confirmation

- Run:
  - `backend/.eval-runs/20260706-194133/job_application_resume_bundle/`
  - `backend/.eval-runs/20260706-195537/repeat-02/job_application_resume_bundle/`
- Provider/model: `deepseek` provider config via OpenAI-compatible gateway / `mimo-v2.5`
- Symptom: the final SSE response included a pending proposal `prop_af1650e127d849598f0d4beca93fd052`, but the runner's immediate confirm call returned:
  - `ok=false`
  - `error.code=conflict_error`
  - `message=Proposal is no longer pending and cannot be confirmed.`
  - details show `status=expired`
- Evidence:
  - `events.ndjson` line 385 contains the final response with the proposal status still `pending` and `expires_at=2026-07-06T11:52:16.063886`.
  - `events.ndjson` line 386 records the immediate `proposal_confirm` event returning the conflict above.
  - `proposals.json` shows the same proposal as `expired` with no confirmation events.
  - The trace shows the same turn later used `manage_session` to activate/update `resume-optimizer`; `AgentSession.pending_proposal_ids` is empty in `db_after.json`.
  - In the repeat run, `events.ndjson` line 355 records the same immediate confirm conflict for `prop_f27433bea51c4ebe99f921936f0d235d`, and `proposals.json` shows it as `expired`.
- Likely root: production confirmation requires the proposal id to still be present in `AgentSession.pending_proposal_ids`. A same-turn session state update appears to drop or invalidate that pending list while the public final response still exposes the proposal as confirmable.
- Impact: users can see a proposal card and immediately confirm it, but confirmation fails as expired even though TTL has not elapsed.
- Current task action: recorded only. Production proposal/session/orchestrator logic was not changed by the live eval task.

## 2026-07-06 - MiMo proposal continuation can also hit MissingGreenlet after partial multi-proposal progress

- Run:
  - `backend/.eval-runs/20260706-195537/repeat-02/job_application_resume_bundle/`
- Provider/model: `deepseek` provider config via OpenAI-compatible gateway / `mimo-v2.5`
- Symptom: after the agent produced several related proposal ids, one confirmation returned:
  - `ok=false`
  - `error.code=transient_error`
  - details include `greenlet_spawn has not been called; can't call await_only() here`
- Evidence:
  - `events.ndjson` line 356 records the transient MissingGreenlet response for `prop_fcdcc41df54f4b38969279976ed34a8b`.
  - The next proposal in the same chain, `prop_be878f4fe5bd4a2b9088a1c8b7e0f1f4`, confirmed successfully and returned a continuation that summarized completed resume optimization plus still-pending application import work.
  - `proposals.json` for the case shows 5 total proposals, with 4 confirmed and 1 expired.
- Impact: complex multi-proposal workflows can become internally inconsistent: some guarded writes finish, another confirmation fails transiently, and the final answer can describe partially completed work plus pending work.
- Current task action: recorded only. Production proposal/orchestrator/session logic was not changed by the live eval task.
