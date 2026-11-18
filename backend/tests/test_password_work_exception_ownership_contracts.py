"""Cancelled callers leave owned work with a responsible exception consumer."""

import asyncio
import gc
import weakref
from typing import Any

from app.api import auth
from app.ratelimit import auth_limiter


async def test_cancelled_password_caller_consumes_owned_work_late_failure() -> None:
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    late_failure = RuntimeError("owned password job failed after caller cancellation")
    observed: list[tuple[str, bool, str]] = []
    finalized = asyncio.Event()
    scope, key = "password-work-ownership", "cancelled-caller-late-failure"

    def record_exception(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        # Retaining a Future or a traceback here would defeat the finalization
        # witness. Keep only the message and the controlled exception identity.
        exception = context.get("exception")
        observed.append(
            (
                str(context.get("message", "")),
                exception is late_failure,
                type(exception).__name__,
            )
        )

    async def drain_ready_callbacks() -> None:
        barrier = loop.create_future()
        loop.call_soon(barrier.set_result, None)
        await barrier

    async def finish_cancelled_caller() -> weakref.ReferenceType[asyncio.Future[None]]:
        owned: asyncio.Future[None] = loop.create_future()
        reference = weakref.ref(owned, lambda _future: finalized.set())
        entered = asyncio.Event()
        reservation = auth._reserve_password_work(scope, key)

        def work() -> asyncio.Future[None]:
            entered.set()
            return owned

        caller = asyncio.create_task(reservation.run(work))
        completed_schedule = False
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            caller.cancel()
            try:
                await caller
            except asyncio.CancelledError:
                pass
            else:
                raise RuntimeError("The owning caller did not observe cancellation")
            if owned.done():
                raise RuntimeError("Caller cancellation unexpectedly finished the shielded job")
            reservation.release_when_idle()
            if auth_limiter.try_reserve(scope, key):
                auth_limiter.release(scope, key)
                raise RuntimeError("The pending job lost its real identity reservation")

            # A legitimate owned asynchronous job can fail after its caller
            # disconnects. Complete it only after cancellation has unwound.
            owned.set_exception(late_failure)
            await drain_ready_callbacks()
            await drain_ready_callbacks()
            if not auth_limiter.try_reserve(scope, key):
                raise RuntimeError("Completed work did not release its identity reservation")
            auth_limiter.release(scope, key)
            completed_schedule = True
            return reference
        finally:
            if not caller.done():
                caller.cancel()
            reservation.release_when_idle()
            if not owned.done():
                owned.set_result(None)
            await asyncio.gather(caller, return_exceptions=True)
            await drain_ready_callbacks()
            if not completed_schedule and not owned.cancelled():
                # Unexpected fixture failures must clean up their owned work;
                # the successful schedule leaves consumption to production.
                owned.exception()
            auth_limiter.release(scope, key)

    loop.set_exception_handler(record_exception)
    try:
        reference = await finish_cancelled_caller()
        gc.collect()
        await drain_ready_callbacks()
        if reference() is not None or not finalized.is_set():
            raise RuntimeError("The test could not finalize its completed owned Future")
        unexpected = [entry for entry in observed if not entry[1]]
        if unexpected:
            raise RuntimeError(f"Unexpected loop or callback errors: {unexpected!r}")
        assert not observed, "Cancelled work must consume its late failure before finalization"
    finally:
        loop.set_exception_handler(previous_handler)
        auth_limiter.release(scope, key)
