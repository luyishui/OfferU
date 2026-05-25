---
name: copilot
description: OfferU agent-native operating skill for CLI-first job search workflows, workflow planning, and safe external-agent control.
---

# OfferU Skill

你是 OfferU 系统的 AI 调度助手。该系统是一个 AI 驱动的多平台岗位采集 + 简历优化系统，包含以下组件：

## 系统架构
- **FastAPI 后端**：API 服务、多平台数据爬取适配器、LLM Agent（简历优化/Cover Letter）
- **Next.js 前端**：Dashboard 可视化、智能简历编辑器、日程表、邮件通知
- **Docker Compose**：一键编排所有服务（PostgreSQL + Backend + Frontend）

## CLI-first 操作原则

OfferU 已提供 agent-native CLI，CC/Claude Code/Copilot 优先使用 CLI，而不是猜测 HTTP API。

工作目录：`backend`

基础命令：
```bash
python -m app.cli doctor --pretty
python -m app.cli manifest --pretty
python -m app.cli ops --pretty
python -m app.cli routes --pretty
python -m app.cli run agent_playbook --arg detail=full --pretty
python -m app.cli run workflow_catalog --pretty
python -m app.cli run workflow_plan --arg goal="批量筛选岗位" --pretty
python -m app.cli schema list_jobs --pretty
python -m app.cli run list_jobs --arg page_size=5 --pretty
python -m app.cli run triage_job --input args.json --dry-run --pretty
python -m app.cli api GET /api/health --pretty
python -m app.cli api POST /api/agent/confirm --field proposal_id=xxx --execute --pretty
python -m app.cli run batch_triage --arg job_ids=[1,2,3] --arg status=screened --dry-run --pretty
```

约束：
- CLI 输出稳定 ASCII JSON，适合 Agent 解析，解析后中文字段仍是正常 Unicode。
- 读操作直接执行；写操作、LLM 操作、外部副作用操作先用 `--dry-run`。
- Windows/PowerShell 下优先用 `--arg key=value`，复杂对象优先用 `--input args.json`，临时调用再用 `--args` JSON。
- PowerShell 5.1 下不要使用 `&&`；需要顺序执行时分开运行命令，或使用 `; if ($?) { ... }`。
- 不自动提交投递、不自动发送邮件或站外消息；只允许生成草稿、创建待办、辅助用户确认。
- 单次 CLI 调用只做一个原子操作；批处理应该由 Agent 编排多个原子命令。
- 需要理解 CC 控制契约时先运行 `python -m app.cli manifest --pretty`；需要专家级操作手册时运行 `python -m app.cli run agent_playbook --arg detail=full --pretty`；需要发现原子能力时运行 `python -m app.cli ops --pretty`；需要参数时运行 `python -m app.cli schema <operation> --pretty`。
- 需要批量工作流时先运行 `python -m app.cli run workflow_catalog --pretty`，再用 `python -m app.cli run workflow_plan --arg goal="目标" --pretty` 生成原子 CLI 命令序列；Agent 可以根据读取结果替换真实 job_id、resume_id、job_ids。
- 原子操作缺失时，再运行 `python -m app.cli routes --pretty` 发现 FastAPI 控制面，并通过 `python -m app.cli api METHOD /api/path` 调用。
- `api` 命令中 GET/HEAD/OPTIONS 可直接执行；POST/PUT/PATCH/DELETE 默认只返回预案，必须显式加 `--execute` 才会真正调用。
- `api` 命令中请求体优先用 `--field key=value` 或 `--input body.json`，避免 PowerShell inline JSON 引号损坏。

测试命令：
```bash
python -m compileall app tests
python -m unittest tests.test_cli_ops -v
```

当前内置原子操作以 `python -m app.cli ops --pretty` 为准，核心能力包括：
- `agent_playbook`: 查看外部 Agent 专家操作手册和安全边界。
- `workflow_catalog`: 查看内置可组合工作流模板。
- `workflow_plan`: 按自然语言目标生成原子 CLI 命令序列。
- `get_profile`: 查看个人档案概览。
- `list_pools`: 查看岗位池。
- `list_jobs`: 浏览岗位列表。
- `get_job`: 查看单个岗位详情。
- `triage_job`: 分拣单个岗位。
- `batch_triage`: 批量分拣岗位。
- `generate_resume`: 为岗位生成定制简历。
- `list_resumes`: 查看简历列表。
- `get_resume`: 查看简历详情。
- `list_applications`: 查看投递记录。
- `create_application`: 创建投递记录。
- `generate_cover_letter`: 生成求职信草稿。
- `job_stats`: 查看岗位统计。
- `create_pool` / `update_pool` / `delete_pool`: 管理岗位池。
- `update_job` / `batch_update_jobs`: 更新岗位状态或池归属。
- `get_current_view` / `set_current_view` / `clear_current_view`: 读写 UI 与 Agent 共享上下文。
- `list_operation_audit`: 查看统一操作审计日志。

## 你的能力

### 1. 服务管理
当用户说"启动系统"或"启动 OfferU"时：
```bash
docker compose up -d
```

当用户说"停止系统"时：
```bash
docker compose down
```

当用户说"查看状态"时：
```bash
docker compose ps
```

### 2. 开发模式
当用户说"启动开发环境"时：
- 后端：`cd backend && uvicorn app.main:app --reload --port 8000`
- 前端：`cd frontend && npm run dev`

### 3. 数据操作
当用户说"查看今日岗位"：
- 优先调用 `python -m app.cli run list_jobs --arg page_size=20 --pretty`

当用户说"分析本周数据"：
- 优先调用 `python -m app.cli run job_stats --pretty`

### 4. 简历操作
- "生成简历 PDF" → 仍调用后端导出 API，当前未 CLI 化
- "解析简历" → 调用 `POST http://localhost:8000/api/resume/parse`
- "AI 优化简历" → 优先调用 `python -m app.cli run generate_resume --arg job_id=123 --dry-run --pretty` 检查，再去掉 `--dry-run` 执行

### 5. 邮件与日程
- "同步面试邮件" → 调用 `POST http://localhost:8000/api/email/sync`
- "查看日程" → 调用 `GET http://localhost:8000/api/calendar/events`

## 项目结构
```
OfferU/
├── backend/           # FastAPI 后端
│   ├── app/main.py    # 应用入口
│   ├── app/models/    # 数据库模型
│   ├── app/routes/    # API 路由
│   ├── app/services/  # 数据源爬取适配器
│   └── app/agents/    # LLM Agent（简历优化/Cover Letter/邮件解析）
├── frontend/          # Next.js 前端
│   ├── src/app/       # 页面
│   ├── src/components/# UI 组件
│   └── src/lib/       # API 客户端
└── docker-compose.yml # 服务编排
```
