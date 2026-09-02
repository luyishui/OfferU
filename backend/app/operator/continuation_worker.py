from __future__ import annotations

import asyncio
import logging

from app.operator.proposals import recover_plan_group_execution_jobs, recover_proposal_continuations


logger = logging.getLogger(__name__)


async def run_continuation_recovery_worker(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 2.0,
    batch_limit: int = 100,
) -> None:
    """Continuously recover queued, retryable, and stale continuation jobs."""

    while not stop_event.is_set():
        try:
            await recover_proposal_continuations(limit=batch_limit)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Proposal continuation recovery scan failed")
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(float(interval_seconds), 0.05))
        except TimeoutError:
            continue


async def run_plan_group_execution_recovery_worker(
    stop_event: asyncio.Event,
    *,
    interval_seconds: float = 2.0,
    batch_limit: int = 100,
) -> None:
    """Continuously recover queued and stale durable PlanGroup execution jobs."""

    while not stop_event.is_set():
        try:
            await recover_plan_group_execution_jobs(limit=batch_limit)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("PlanGroup execution recovery scan failed")
        if stop_event.is_set():
            break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=max(float(interval_seconds), 0.05))
        except TimeoutError:
            continue
