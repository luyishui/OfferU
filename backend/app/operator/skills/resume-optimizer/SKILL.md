# Resume Optimizer Skill

## Purpose
Prepare or optimize a resume using verified profile facts and the selected job description.

## Required sequence
1. Activate this Skill before collecting evidence.
2. Read the target Job, Profile, and relevant ProfileSection records with Operator tools.
3. Record user exclusions and unsupported-claim boundaries in the current session.
4. Stage `generate_resume` via `invoke_action` once evidence is durable — the resulting proposal card IS the strategy confirmation the runtime requires; do not wait for a free-text user reply first.
5. Confirm that proposal (or its plan group) to apply the write.
6. Treat generated content as a proposal; never claim a write before the confirmation and result receipt succeed.

## Safety boundaries
- Use only facts returned by actor-scoped Operator reads.
- Do not invent employers, dates, metrics, skills, or responsibilities.
- Do not overwrite source profile facts while editing a resume.
- Do not bypass capability loading, proposal confirmation, version fencing, or durable replay.
