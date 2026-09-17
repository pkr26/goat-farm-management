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
from contextlib import suppress

from ..core.config import get_settings
from ..db import get_sessionmaker
from ..services.screening import (
    ProviderRotation,
    ScreeningStorage,
    build_provider_rotation,
    run_screening_cycle,
)

logger = logging.getLogger("goatfarm.screening-worker")


async def _run_loop(stop: asyncio.Event) -> None:
    settings = get_settings()
    sessionmaker = get_sessionmaker()

    if not settings.screening_enabled:
        # Compose keeps this service deployed in every environment; an idle
        # loop (rather than exit) avoids a restart-storm while screening is
        # off. Nothing touches S3 or a provider until it is enabled.
        logger.warning("screening disabled (GOATFARM_SCREENING_ENABLED=false); worker idling")
        while not stop.is_set():
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=3600)
        return

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
    while not stop.is_set():
        try:
            async with sessionmaker() as db:
                summary = await run_screening_cycle(db, settings, storage, rotation)
            if summary.claimed or summary.notes:
                logger.info(
                    "screening cycle: listed=%d claimed=%d healthy=%d flagged=%d "
                    "skipped=%d errors=%d expired_uploads=%d notes=%s",
                    summary.listed,
                    summary.claimed,
                    summary.healthy,
                    summary.flagged,
                    summary.skipped,
                    summary.errors,
                    summary.expired_uploads,
                    summary.notes,
                )
        except Exception:
            logger.exception("screening cycle crashed; retrying next interval")
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


async def main() -> None:
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
    await _run_loop(stop)
    logger.info("screening worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
