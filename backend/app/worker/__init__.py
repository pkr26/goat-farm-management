"""Screening worker entrypoint: polls S3 and drives the gate pipeline.

Runs as its own process (same image as the API, different command) so a
long model call can never hold an API request. Kept intentionally dull:
one loop, one cycle at a time, per-image commits inside the cycle, every
cycle failure logged and retried on the next tick.

    python -m app.worker
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from contextlib import suppress
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.config import ScreeningWorkerSettings, get_screening_worker_settings
from ..db import create_sessionmaker
from ..services.screening import (
    CycleSummary,
    ProviderRotation,
    ScreeningStorage,
    build_provider_rotation,
    run_screening_cycle,
)
from .heartbeat import HeartbeatStatus, write_heartbeat

logger = logging.getLogger("goatfarm.screening-worker")

# A cycle can legitimately outlive the poll interval: one bounded batch may
# include many provider calls, each with its own configured timeout. Renew a
# lease while that coroutine makes event-loop progress instead of declaring a
# healthy worker dead halfway through its legitimate work. Keep the cadence
# short enough that a genuinely wedged event loop still fails the probe soon.
_WORKING_HEARTBEAT_MAX_INTERVAL_SECONDS = 60


def _publish_heartbeat(
    settings: ScreeningWorkerSettings,
    status: HeartbeatStatus,
    *,
    consecutive_failures: int = 0,
) -> None:
    try:
        write_heartbeat(
            settings.screening_worker_heartbeat_path,
            status,
            consecutive_failures=consecutive_failures,
        )
    except OSError:
        # Keep the actual worker error visible and let the container probe
        # fail; turning a full filesystem into a crash loop hides the cause.
        logger.exception("could not write screening worker heartbeat")


def _working_heartbeat_interval_seconds(settings: ScreeningWorkerSettings) -> int:
    """A conservative lease cadence derived from the health staleness budget."""
    return max(
        1,
        min(
            _WORKING_HEARTBEAT_MAX_INTERVAL_SECONDS,
            settings.screening_worker_health_max_age_seconds // 3,
        ),
    )


async def _await_cycle_with_heartbeats(
    cycle: Coroutine[Any, Any, CycleSummary],
    settings: ScreeningWorkerSettings,
    stop: asyncio.Event,
) -> CycleSummary | None:
    """Await one screening cycle while publishing a bounded ``working`` lease.

    The pipeline uses asynchronous I/O and ``to_thread`` for image/S3 work,
    so this task can renew the local record during long legitimate batches.
    If the event loop itself wedges, this coroutine cannot run and the Docker
    probe correctly observes a stale heartbeat instead.
    """
    task = asyncio.create_task(cycle)
    stop_waiter = asyncio.create_task(stop.wait())
    interval = _working_heartbeat_interval_seconds(settings)
    _publish_heartbeat(settings, "working")
    try:
        while not task.done():
            done, _pending = await asyncio.wait(
                {task, stop_waiter},
                timeout=interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return task.result()
            if stop_waiter in done:
                # SIGTERM must not wait for an entire worst-case batch (up to
                # many sequential provider calls) before Compose's finite
                # stop grace expires. Cancellation rolls the session back via
                # its context manager; an unclaimed/PROCESSING image is then
                # recovered by the pipeline's normal stale-claim logic.
                logger.info("screening worker stopping: cancelling active cycle")
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                return None
            _publish_heartbeat(settings, "working")
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if not stop_waiter.done():
            stop_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await stop_waiter


async def _wait_for_stop(stop: asyncio.Event, wait_seconds: int) -> None:
    """Wait for a stop request without letting a normal timeout escape."""
    with suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=wait_seconds)


async def _run_loop(stop: asyncio.Event, settings: ScreeningWorkerSettings | None = None) -> int:
    """Run worker cycles until stopped or persistent failures require a restart.

    Returns a process exit status so both module entrypoints can signal a
    restart-worthy failure to Docker. ``0`` means the worker was deliberately
    stopped or was idling with screening disabled; ``1`` means unhandled
    whole-cycle failures reached the configured safety limit.
    """
    settings = settings or get_screening_worker_settings()
    sessionmaker = create_sessionmaker(settings)
    _publish_heartbeat(settings, "starting")

    if not settings.screening_enabled:
        # Compose keeps this service deployed in every environment; an idle
        # loop (rather than exit) avoids a restart-storm while screening is
        # off. Nothing touches S3 or a provider until it is enabled.
        logger.warning("screening disabled (GOATFARM_SCREENING_ENABLED=false); worker idling")
        while not stop.is_set():
            _publish_heartbeat(settings, "disabled")
            await _wait_for_stop(
                stop,
                min(
                    settings.screening_poll_interval_seconds,
                    settings.screening_worker_health_max_age_seconds // 2,
                ),
            )
        return 0

    storage = ScreeningStorage(settings)
    rotation = ProviderRotation(build_provider_rotation(settings))
    interval = settings.screening_poll_interval_seconds
    logger.info(
        "screening worker started: bucket=%s prefix=%s providers=%s interval=%ss",
        settings.s3_bucket,
        settings.screening_s3_prefix,
        rotation.names(),
        interval,
    )
    try:
        return await _run_cycles(stop, settings, sessionmaker, storage, rotation, interval)
    finally:
        # Close every provider's owned httpx transport on shutdown (B-small,
        # 2026-09-21 audit: the adapters' AsyncClients were never closed, so
        # each worker exit leaked its connection pools). Concrete adapters
        # implement aclose; test doubles without transports are skipped.
        try:
            providers = list(rotation)
        except TypeError:  # a patched-in test double without iteration
            providers = []
        for provider in providers:
            aclose = getattr(provider, "aclose", None)
            if aclose is not None:
                with suppress(Exception):
                    await aclose()


async def _run_cycles(
    stop: asyncio.Event,
    settings: ScreeningWorkerSettings,
    sessionmaker: async_sessionmaker[AsyncSession],
    storage: ScreeningStorage,
    rotation: ProviderRotation,
    interval: int,
) -> int:
    consecutive_cycle_failures = 0
    while not stop.is_set():
        try:
            async with sessionmaker() as db:
                summary = await _await_cycle_with_heartbeats(
                    run_screening_cycle(db, settings, storage, rotation), settings, stop
                )
            if summary is None:
                break
            consecutive_cycle_failures = 0
            _publish_heartbeat(settings, "ok")
            if summary.claimed or summary.notes:
                logger.info(
                    "screening cycle: claimed=%d healthy=%d flagged=%d skipped=%d "
                    "errors=%d retried_errors=%d retried_flagged=%d "
                    "expired_uploads=%d terminated_processing=%d notes=%s",
                    summary.claimed,
                    summary.healthy,
                    summary.flagged,
                    summary.skipped,
                    summary.errors,
                    summary.retried_errors,
                    summary.retried_flagged,
                    summary.expired_uploads,
                    summary.terminated_processing,
                    summary.notes,
                )
        except Exception:
            consecutive_cycle_failures += 1
            # The probe tolerates transient failures inside the worker's own
            # recovery window (see screening_worker_healthcheck.py): it only
            # fails the container once this count reaches the configured
            # limit — the same threshold at which the worker exits nonzero.
            _publish_heartbeat(settings, "error", consecutive_failures=consecutive_cycle_failures)
            if (
                consecutive_cycle_failures
                >= settings.screening_worker_max_consecutive_cycle_failures
            ):
                logger.exception(
                    "screening cycle crashed (%d/%d consecutive failures); "
                    "exiting nonzero for orchestrator restart",
                    consecutive_cycle_failures,
                    settings.screening_worker_max_consecutive_cycle_failures,
                )
                return 1
            logger.exception(
                "screening cycle crashed (%d/%d consecutive failures); retrying next interval",
                consecutive_cycle_failures,
                settings.screening_worker_max_consecutive_cycle_failures,
            )
        await _wait_for_stop(stop, interval)
    return 0


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    import signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    settings = get_screening_worker_settings()
    exit_code = await _run_loop(stop, settings)
    if exit_code:
        # Keep the final ``error`` heartbeat written by the failed cycle. It
        # gives a last useful diagnosis if Docker observes the container just
        # before restarting it.
        logger.error("screening worker exiting nonzero after persistent cycle failures")
        return exit_code
    # The process is exiting, so a surviving old heartbeat must not make a
    # stopped worker look fresh to an external probe during shutdown races.
    try:
        write_heartbeat(settings.screening_worker_heartbeat_path, "stopped")
    except OSError:
        logger.exception("could not mark screening worker stopped")
    logger.info("screening worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
