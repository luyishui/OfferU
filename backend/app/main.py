# =============================================
# OfferU - FastAPI 应用入口
# =============================================
# 启动命令: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 职责：注册路由、CORS、生命周期事件
# =============================================

import asyncio
import os
from contextlib import asynccontextmanager
from contextlib import suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.operator.continuation_worker import (
    run_continuation_recovery_worker,
    run_plan_group_execution_recovery_worker,
)
try:
    from app.mcp_server import HAS_MCP_SERVER, mcp as mcp_server
    _HAS_MCP = HAS_MCP_SERVER
except ImportError:
    mcp_server = None
    _HAS_MCP = False
from app.routes import jobs, resume, calendar, email, config, applications, scraper, pools, profile, profile_agent, optimize, interview, harness_agent

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时初始化数据库表与 MCP 会话管理器。"""
    await init_db()
    continuation_stop = asyncio.Event()
    continuation_task = asyncio.create_task(
        run_continuation_recovery_worker(continuation_stop),
        name="proposal-continuation-recovery",
    )
    plan_group_task = asyncio.create_task(
        run_plan_group_execution_recovery_worker(continuation_stop),
        name="plan-group-execution-recovery",
    )
    try:
        if _HAS_MCP and mcp_server is not None:
            async with mcp_server.session_manager.run():
                yield
        else:
            yield
    finally:
        continuation_stop.set()
        continuation_task.cancel()
        plan_group_task.cancel()
        with suppress(asyncio.CancelledError):
            await continuation_task
        with suppress(asyncio.CancelledError):
            await plan_group_task


app = FastAPI(
    title="OfferU API",
    description="AI 驱动的智能求职助手后端",
    version="0.2.0",
    lifespan=lifespan,
)

cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

# ---- CORS 允许前端跨域访问 ----
# cors_origins 以逗号分隔多个来源，如 "http://localhost:3000,http://localhost:8080"
# allow_credentials=True 允许带 cookie 的跨域请求（Gmail OAuth 回调需要）
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"^(chrome-extension|ms-browser-extension)://[a-z0-9]{16,64}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Trailing-slash redirect Location must stay relative ----
# FastAPI/Starlette 对集合路由（/api/resume → /api/resume/）发的 307/308 重定向，
# Location 是用请求 Host 头拼的绝对 URL。当请求经 Next dev rewrite / 反向代理转发时，
# Host 变成了内网名（backend:8000），浏览器无法解析 -> 前端拿不到数据。
# 这里把 Location 里的 scheme://host 剥掉，强制相对路径，浏览器相对当前源解析即可。
@app.middleware("http")
async def _relative_redirect_location(request, call_next):
    response = await call_next(request)
    if response.status_code in (301, 302, 303, 307, 308):
        location = response.headers.get("location")
        if location and "://" in location:
            # 剥成 "path[?query]"；浏览器相对当前 origin 解析
            from urllib.parse import urlsplit

            parts = urlsplit(location)
            rel = parts.path or "/"
            if parts.query:
                rel += "?" + parts.query
            if parts.fragment:
                rel += "#" + parts.fragment
            response.headers["location"] = rel
    return response

# ---- 注册路由 ----
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(pools.router, prefix="/api/pools", tags=["Pools"])
app.include_router(profile.router, prefix="/api/profile", tags=["Profile"])
app.include_router(profile_agent.router, prefix="/api/profile/agent", tags=["Profile Agent"])
app.include_router(harness_agent.router, prefix="/api/harness-agent", tags=["Harness Agent"])
app.include_router(optimize.router, prefix="/api/optimize", tags=["Optimize"])
app.include_router(resume.router, prefix="/api/resume", tags=["Resume"])
app.include_router(calendar.router, prefix="/api/calendar", tags=["Calendar"])
app.include_router(email.router, prefix="/api/email", tags=["Email"])
app.include_router(config.router, prefix="/api/config", tags=["Config"])
app.include_router(applications.router, prefix="/api/applications", tags=["Applications"])
app.include_router(scraper.router, prefix="/api/scraper", tags=["Scraper"])
app.include_router(interview.router, prefix="/api/interview", tags=["Interview"])

# ---- 静态文件（头像等上传文件） ----
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# ---- MCP Server (Streamable HTTP) ----
if _HAS_MCP and mcp_server is not None:
    mcp_server.settings.streamable_http_path = "/"
    app.mount("/mcp", mcp_server.streamable_http_app())


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "OfferU"}
