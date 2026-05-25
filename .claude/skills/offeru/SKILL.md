---
name: offeru
description: Use when operating OfferU through Claude Code or another external agent. Provides CLI-first discovery, atomic operations, safe workflow planning, and human-confirmed side-effect execution.
---

# OfferU Agent-Native Operating Skill

Work from [backend/](../../backend/) unless the user asks for frontend or extension work.

## First commands

Run these before controlling the product:

```powershell
python -m app.cli doctor --pretty
python -m app.cli manifest --pretty
python -m app.cli run agent_playbook --arg detail=full --pretty
```

Use the returned JSON as the live source of truth. Do not guess API paths or operation parameters.

## Control rules

- Prefer `python -m app.cli run <operation>` over raw HTTP.
- One CLI call performs one atomic operation.
- Discover operations with `python -m app.cli ops --pretty`.
- Inspect parameters with `python -m app.cli schema <operation> --pretty`.
- Use `python -m app.cli run workflow_catalog --pretty` to see built-in workflows.
- Use `python -m app.cli run workflow_plan --arg goal="<goal>" --pretty` to generate a command sequence.
- Read operations execute directly.
- Operations with `write`, `llm`, or `external` side effects must be dry-run first.
- Never auto-submit applications, send emails, or message external parties.

## Core workflows

- Daily review: `workflow_plan --arg goal="今日岗位概览"`
- Batch triage: `workflow_plan --arg goal="批量筛选岗位"`
- Tailored resume: `workflow_plan --arg goal="定制简历"`
- Application pipeline: `workflow_plan --arg goal="创建投递待办"`
- Workspace handoff: `workflow_plan --arg goal="当前页面上下文接管"`

## Validation

After changing backend agent/CLI behavior, run:

```powershell
python -m compileall app tests
python -m unittest tests.test_cli_ops -v
```
