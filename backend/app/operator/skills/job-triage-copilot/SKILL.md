# Job Triage Copilot Skill

## Purpose
Review and triage actor-owned job records while preserving source descriptions and screening-note semantics.

## Required sequence
1. Activate the Skill and load the Job query/patch contract.
2. Query candidate jobs and resolve the exact target from visible fields before writing.
3. Keep source description fields and AI-derived analysis fields read-only for generic edits.
4. Combine a triage status change and a screening note for the same Job into one compatible staged intent.
5. Confirm the resulting Plan before any business effect.

## Safety boundaries
- Write screening annotations to `Job.user_notes`, never to `raw_description`, `summary`, or `keywords`.
- Do not change unrelated jobs or create an Application from a triage request alone.
- Preserve expected versions, proposal grouping, and confirmation fences.
