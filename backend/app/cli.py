from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any, Optional, Union

import httpx

from app.config import get_settings
from app.database import init_db
from app.ops import execute_operation, get_operation_schema, list_operations


APP_VERSION = "0.3.0"


class CliParseError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliParseError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        if status:
            raise CliParseError(message.strip() if message else f"parser exited with status {status}")
        raise SystemExit(status)


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except CliParseError as exc:
        return _print({"ok": False, "errors": [exc.message], "commands": _commands()}, exit_code=2)
    try:
        if args.command == "doctor":
            return _print(_doctor(), args.pretty)
        if args.command == "manifest":
            return _print(_manifest(), args.pretty)
        if args.command == "ops":
            return _print({"ok": True, "operations": list_operations()}, args.pretty)
        if args.command == "routes":
            return _print(_routes(args.prefix), args.pretty)
        if args.command == "schema":
            schema = get_operation_schema(args.name)
            if not schema:
                return _print({"ok": False, "errors": [f"未知操作: {args.name}"]}, args.pretty, exit_code=1)
            return _print({"ok": True, "schema": schema}, args.pretty)
        if args.command == "run":
            op_args = _parse_args_json(args.args_json)
            if isinstance(op_args, str):
                return _print({"ok": False, "errors": [op_args]}, args.pretty, exit_code=1)
            file_args = _parse_input_file(args.input_file)
            if isinstance(file_args, str):
                return _print({"ok": False, "errors": [file_args]}, args.pretty, exit_code=1)
            pair_args = _parse_arg_pairs(args.arg_pairs)
            if isinstance(pair_args, str):
                return _print({"ok": False, "errors": [pair_args]}, args.pretty, exit_code=1)
            op_args.update(file_args)
            op_args.update(pair_args)
            result = asyncio.run(_run_operation(args.name, op_args, dry_run=args.dry_run))
            return _print(result, args.pretty, exit_code=0 if result.get("ok") else 1)
        if args.command == "api":
            query_args = _parse_arg_pairs(args.query_pairs)
            if isinstance(query_args, str):
                return _print({"ok": False, "errors": [query_args]}, args.pretty, exit_code=1)
            body_args = _parse_args_json(args.body_json)
            if isinstance(body_args, str):
                return _print({"ok": False, "errors": [body_args]}, args.pretty, exit_code=1)
            file_args = _parse_input_file(args.input_file)
            if isinstance(file_args, str):
                return _print({"ok": False, "errors": [file_args]}, args.pretty, exit_code=1)
            field_args = _parse_arg_pairs(args.field_pairs)
            if isinstance(field_args, str):
                return _print({"ok": False, "errors": [field_args]}, args.pretty, exit_code=1)
            body_args.update(file_args)
            body_args.update(field_args)
            result = asyncio.run(
                _run_api(
                    args.method,
                    args.path,
                    query=query_args,
                    body=body_args,
                    execute=args.execute,
                )
            )
            return _print(result, args.pretty, exit_code=0 if result.get("ok") else 1)
        return _print({"ok": False, "errors": ["缺少命令"], "commands": _commands()}, exit_code=2)
    except KeyboardInterrupt:
        return _print({"ok": False, "errors": ["interrupted"]}, getattr(args, "pretty", False), exit_code=130)


def _build_parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(
        prog="offeru",
        description="OfferU agent-native CLI. Every command prints machine-readable JSON.",
        add_help=False,
    )
    sub = parser.add_subparsers(dest="command", parser_class=JsonArgumentParser)

    doctor = sub.add_parser("doctor", help="Check runtime configuration and CLI health.", add_help=False)
    doctor.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    manifest = sub.add_parser("manifest", help="Print the agent control contract for Claude Code and other CLIs.", add_help=False)
    manifest.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    ops = sub.add_parser("ops", help="List all atomic internal operations.", add_help=False)
    ops.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    routes = sub.add_parser("routes", help="List FastAPI routes for full API discovery.", add_help=False)
    routes.add_argument("--prefix", default="/api", help="Only include routes under this prefix.")
    routes.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    schema = sub.add_parser("schema", help="Show one operation schema.", add_help=False)
    schema.add_argument("name", help="Operation name, for example list_jobs.")
    schema.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    run = sub.add_parser("run", help="Run one atomic operation.", add_help=False)
    run.add_argument("name", help="Operation name, for example list_jobs.")
    run.add_argument("--args", dest="args_json", default="{}", help="JSON object passed as operation args.")
    run.add_argument("--input", dest="input_file", default="", help="Path to a JSON object file passed as operation args.")
    run.add_argument("--arg", dest="arg_pairs", action="append", default=[], help="Single key=value arg. May be repeated.")
    run.add_argument("--dry-run", action="store_true", help="Skip mutation, LLM, or external side-effect operations.")
    run.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")

    api = sub.add_parser("api", help="Call an internal FastAPI route through the ASGI app.", add_help=False)
    api.add_argument("method", help="HTTP method, for example GET or POST.")
    api.add_argument("path", help="HTTP path, for example /api/jobs/stats.")
    api.add_argument("--query", dest="query_pairs", action="append", default=[], help="Query key=value arg. May be repeated.")
    api.add_argument("--body", dest="body_json", default="{}", help="JSON object request body.")
    api.add_argument("--input", dest="input_file", default="", help="Path to a JSON object file merged into request body.")
    api.add_argument("--field", dest="field_pairs", action="append", default=[], help="Body key=value arg. May be repeated.")
    api.add_argument("--execute", action="store_true", help="Allow non-GET methods to execute. GET/HEAD/OPTIONS execute without this flag.")
    api.add_argument("--pretty", action="store_true", help="Pretty-print JSON.")
    return parser


def _commands() -> list[str]:
    return ["doctor", "manifest", "ops", "routes", "schema", "run", "api"]


def _doctor() -> dict[str, Any]:
    settings = get_settings()
    return {
        "ok": True,
        "service": "OfferU CLI",
        "version": APP_VERSION,
        "commands": _commands(),
        "operation_count": len(list_operations()),
        "database_url_configured": bool(settings.database_url),
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "safety": {
            "json_output": True,
            "dry_run_for_mutations": True,
            "auto_submit_applications": False,
        },
    }


def _manifest() -> dict[str, Any]:
    operations = list_operations()
    return {
        "ok": True,
        "service": "OfferU CLI",
        "version": APP_VERSION,
        "purpose": "Agent-native control surface for OfferU. Claude Code can discover operations, inspect schemas, run safe reads, and dry-run side-effect operations before confirmation.",
        "commands": {
            "health": "python -m app.cli doctor --pretty",
            "manifest": "python -m app.cli manifest --pretty",
            "list_operations": "python -m app.cli ops --pretty",
            "list_routes": "python -m app.cli routes --pretty",
            "inspect_operation": "python -m app.cli schema <operation> --pretty",
            "agent_playbook": "python -m app.cli run agent_playbook --arg detail=full --pretty",
            "workflow_catalog": "python -m app.cli run workflow_catalog --pretty",
            "workflow_plan": "python -m app.cli run workflow_plan --arg goal=\"批量筛选岗位\" --pretty",
            "run_operation": "python -m app.cli run <operation> --arg key=value --pretty",
            "dry_run_mutation": "python -m app.cli run <operation> --arg key=value --dry-run --pretty",
            "file_input": "python -m app.cli run <operation> --input args.json --pretty",
            "call_get_api": "python -m app.cli api GET /api/health --pretty",
            "call_write_api": "python -m app.cli api POST /api/resource --field key=value --execute --pretty",
        },
        "io_contract": {
            "stdout": "single JSON object",
            "stderr": "reserved for Python/runtime diagnostics only",
            "exit_codes": {"0": "success", "1": "operation or input error", "2": "CLI syntax error", "130": "interrupted"},
            "argument_precedence": ["--args/--body", "--input", "--arg/--field"],
        },
        "safety": {
            "auto_submit_applications": False,
            "machine_mode_interactive_prompts": False,
            "side_effect_operations_require_dry_run_first": True,
            "api_write_methods_require_execute_flag": True,
            "side_effect_labels": sorted({effect for op in operations for effect in op.get("side_effects", [])}),
        },
        "operation_count": len(operations),
        "operations": operations,
    }


def _routes(prefix: str = "/api") -> dict[str, Any]:
    from app.main import app

    route_items: list[dict[str, Any]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if prefix and not path.startswith(prefix):
            continue
        methods = sorted(getattr(route, "methods", []) or [])
        if not methods:
            continue
        route_items.append(
            {
                "path": path,
                "name": getattr(route, "name", ""),
                "methods": [method for method in methods if method not in {"HEAD"} or "GET" not in methods],
                "requires_execute": any(_method_needs_execute(method) for method in methods),
            }
        )
    return {
        "ok": True,
        "prefix": prefix,
        "route_count": len(route_items),
        "routes": route_items,
    }


async def _run_operation(name: str, args: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    await init_db()
    return await execute_operation(name, args, dry_run=dry_run, surface="cli")


async def _run_api(
    method: str,
    path: str,
    *,
    query: dict[str, Any],
    body: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    normalized_method = (method or "").strip().upper()
    normalized_path = _normalize_path(path)
    if not normalized_method:
        return {"ok": False, "errors": ["method is required"]}
    if not normalized_path.startswith("/api/") and normalized_path != "/api/health":
        return {"ok": False, "errors": ["api path must start with /api/"]}

    if _method_needs_execute(normalized_method) and not execute:
        return {
            "ok": True,
            "executed": False,
            "requires_execute": True,
            "method": normalized_method,
            "path": normalized_path,
            "query": query,
            "body": body,
            "warnings": ["非安全 HTTP 方法默认不执行；确认后追加 --execute。"],
        }

    await init_db()
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://offeru.local") as client:
        response = await client.request(
            normalized_method,
            normalized_path,
            params=query,
            json=body if body and normalized_method not in {"GET", "HEAD", "OPTIONS"} else None,
        )
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    return {
        "ok": 200 <= response.status_code < 400,
        "executed": True,
        "method": normalized_method,
        "path": normalized_path,
        "query": query,
        "status_code": response.status_code,
        "outputs": payload,
        "errors": [] if 200 <= response.status_code < 400 else [str(payload)],
    }


def _method_needs_execute(method: str) -> bool:
    return method.upper() not in {"GET", "HEAD", "OPTIONS"}


def _normalize_path(path: str) -> str:
    value = (path or "").strip()
    if not value.startswith("/"):
        value = "/" + value
    return value


def _parse_args_json(raw: str) -> Union[dict[str, Any], str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"--args 必须是合法 JSON 对象: {exc}"
    if not isinstance(value, dict):
        return "--args 必须是 JSON object"
    return value


def _parse_input_file(path: str) -> Union[dict[str, Any], str]:
    if not path:
        return {}
    input_path = Path(path)
    try:
        raw = input_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return f"--input 无法读取文件: {exc}"
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"--input 必须是合法 JSON 对象文件: {exc}"
    if not isinstance(value, dict):
        return "--input 必须是 JSON object 文件"
    return value


def _parse_arg_pairs(pairs: list[str]) -> Union[dict[str, Any], str]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            return f"--arg 必须使用 key=value 格式: {pair}"
        key, raw_value = pair.split("=", 1)
        key = key.strip()
        if not key:
            return f"--arg key 不能为空: {pair}"
        out[key] = _parse_scalar(raw_value)
    return out


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in ("null", "none"):
        return None
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _print(payload: dict[str, Any], pretty: bool = False, exit_code: int = 0) -> int:
    text = json.dumps(payload, ensure_ascii=True, indent=2 if pretty else None)
    sys.stdout.write(text + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
