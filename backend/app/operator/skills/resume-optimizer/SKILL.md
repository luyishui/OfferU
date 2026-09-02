# Resume Optimizer Skill

## Purpose
Prepare or optimize a resume using verified profile facts and the selected job description.

## Required sequence
1. Activate this Skill before collecting evidence.
2. Read the target Job, Profile, and relevant ProfileSection records with Operator tools.
3. Record user exclusions and unsupported-claim boundaries in the current session.
4. Explain the tailoring strategy and obtain the strategy confirmation required by the runtime.
5. Use `generate_resume` or `optimize_resume` only after readiness evidence is durable and complete.
6. Treat generated content as a proposal; never claim a write before the confirmation and result receipt succeed.

## Safety boundaries
- Use only facts returned by actor-scoped Operator reads.
- Do not invent employers, dates, metrics, skills, or responsibilities.
- Do not overwrite source profile facts while editing a resume.
- Do not bypass capability loading, proposal confirmation, version fencing, or durable replay.
