from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from typing import Any, Awaitable, Callable, Mapping

from sse_starlette.sse import EventSourceResponse

from app.agent.orchestrator import PUBLIC_AGENT_RESPONSE_KEYS
from app.config import get_settings
from app.operator.public_redaction import redact_public_payload

_logger = logging.getLogger(__name__)

_PUBLIC_EVENT_TYPES = {
    "agent_start",
    "turn_start",
    "message_start",
    "message_update",
    "message_end",
    "tool_execution_start",
    "tool_execution_update",
    "tool_execution_end",
    "turn_end",
    "agent_end",
    "proposal",
    "plan_status",
    "card",
    "compaction",
    "final",
    "error",
}

_DROP_KEYS = {"context", "new_messages", "stream_event"}
_FINAL_PUBLIC_KEYS = set(PUBLIC_AGENT_RESPONSE_KEYS)


RunAgentSse = Callable[[Callable[[dict[str, Any]], Awaitable[None]]], Awaitable[dict[str, Any]]]


def agent_sse_response(run_turn: RunAgentSse) -> EventSourceResponse:
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=1000)
    state = {
        "discard": False,
        "pending_update": None,
        "pending_count": 0,
        "pending_started": 0.0,
        "flush_task": None,
    }
    update_lock = asyncio.Lock()

    async def put_event(event_name: str, payload: Mapping[str, Any]) -> None:
        if state["discard"]:
            return
        item = {"event": event_name, "data": json.dumps(redact_public_payload(dict(payload)), ensure_ascii=False, default=str)}
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            pass
        kept: list[dict[str, Any] | None] = []
        dropped = False
        while True:
            try:
                current = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not dropped and isinstance(current, dict) and current.get("event") == "message_update":
                dropped = True
                continue
            kept.append(current)
        for current in kept:
            queue.put_nowait(current)
        if dropped:
            queue.put_nowait(item)
        else:
            await queue.put(item)

    def clear_flush_timer() -> None:
        task = state.get("flush_task")
        if isinstance(task, asyncio.Task):
            current = asyncio.current_task()
            if task is not current and not task.done():
                task.cancel()
        state["flush_task"] = None

    async def delayed_flush_update() -> None:
        try:
            await asyncio.sleep(0.08)
            async with update_lock:
                await flush_update_locked()
        except asyncio.CancelledError:
            return
        except Exception:
            _logger.exception("Agent SSE delayed message_update flush failed")

    def schedule_flush_timer() -> None:
        clear_flush_timer()
        state["flush_task"] = asyncio.create_task(delayed_flush_update())

    async def flush_update_locked() -> None:
        pending = state.get("pending_update")
        clear_flush_timer()
        if not isinstance(pending, dict):
            return
        state["pending_update"] = None
        state["pending_count"] = 0
        state["pending_started"] = 0.0
        await put_event("message_update", pending)

    async def flush_update() -> None:
        async with update_lock:
            await flush_update_locked()

    async def sink(raw_event: dict[str, Any]) -> None:
        public = _public_event(raw_event)
        if public is None:
            return
        event_name = str(public.pop("type"))
        if event_name == "message_update":
            async with update_lock:
                pending = state.get("pending_update")
                delta_text = _delta_text(public)
                now = time.monotonic()
                if not isinstance(pending, dict):
                    state["pending_update"] = public
                    state["pending_count"] = 1
                    state["pending_started"] = now
                    schedule_flush_timer()
                    return
                _merge_update(pending, public, delta_text)
                state["pending_count"] = int(state.get("pending_count") or 0) + 1
                if state["pending_count"] >= 10 or now - float(state.get("pending_started") or now) >= 0.08:
                    await flush_update_locked()
            return
        await flush_update()
        await put_event(event_name, public)

    async def producer() -> None:
        failed = False
        try:
            response = await run_turn(sink)
            await sink({"type": "final", **dict(response or {})})

        except Exception as exc:
            failed = True
            _logger.exception("Agent SSE producer failed")
            await flush_update()
            await put_event(
                "error",
                {"error": {"code": "agent_sse_failed", "message": str(exc) or "Agent stream failed."}},
            )
        finally:
            if not failed:
                await flush_update()
            if not state["discard"]:
                await queue.put(None)

    task = asyncio.create_task(producer())

    async def generator():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        except asyncio.CancelledError:
            state["discard"] = True
            task.add_done_callback(lambda finished: finished.exception() if not finished.cancelled() else None)
            raise
        finally:
            if task.done():
                try:
                    task.result()
                except Exception:
                    _logger.exception("Agent SSE background task failed after stream close")

    return EventSourceResponse(
        generator(),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _public_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("type") or "")
    if event_type not in _PUBLIC_EVENT_TYPES:
        return None
    if event_type == "final":
        return {
            str(key): _plain(value)
            for key, value in event.items()
            if str(key) in _FINAL_PUBLIC_KEYS or str(key) == "type"
        }
    payload = {
        str(key): _plain(value)
        for key, value in event.items()
        if str(key) not in _DROP_KEYS
    }
    if event_type == "turn_end":
        payload.pop("context", None)
        payload.pop("new_messages", None)
    return payload


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items() if str(key) not in _DROP_KEYS}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump(mode="json"))
    if inspect.isclass(value):
        return str(value)
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def _delta_text(event: Mapping[str, Any]) -> str:
    delta = event.get("delta")
    if not isinstance(delta, Mapping):
        return ""
    for key in ("text", "content", "thinking"):
        value = delta.get(key)
        if isinstance(value, str):
            return value
    return ""


def _merge_update(pending: dict[str, Any], event: Mapping[str, Any], delta_text: str) -> None:
    pending.update({key: value for key, value in event.items() if key != "delta"})
    pending_delta = pending.get("delta")
    event_delta = event.get("delta")
    if not isinstance(pending_delta, dict):
        pending_delta = {}
    if isinstance(event_delta, Mapping):
        for key, value in event_delta.items():
            key_text = str(key)
            if key_text not in {"text", "content", "thinking"}:
                pending_delta[key_text] = _plain(value)
    if delta_text:
        if isinstance(event_delta, Mapping):
            key = "text" if "text" in event_delta else "content" if "content" in event_delta else "thinking"
        else:
            key = "text" if "text" in pending_delta else "content" if "content" in pending_delta else "thinking"
        pending_delta[key] = str(pending_delta.get(key) or "") + delta_text
    pending["delta"] = pending_delta
