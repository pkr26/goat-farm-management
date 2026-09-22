"""CPU admission control for simulation-family engine requests.

These lived in ``api/simulation.py`` and are shared verbatim with the planner
router (``api/planner.py``): one in-flight run per farm and per user plus a
process-wide ceiling, a sliding per-user/per-farm CPU budget priced by what the
request will actually cost, thread offloading with cancellation-safe lease
tracking, and the non-finite payload defense. The singletons are per-process by
design — the backend runs exactly one uvicorn worker (see ``app.main``).

Names keep their historical underscore spelling: ``tests/test_simulation_api``
imports the singletons directly from this module, ``api.simulation`` imports
the five it still uses, and both routers must share one set of singletons.
"""

import asyncio
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from contextvars import ContextVar
from typing import cast

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from .. import metrics


def _finite_payload(value: object) -> bool:
    """False if any float anywhere in a ``model_dump``'d payload is NaN/±inf."""
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite_payload(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite_payload(item) for item in value)
    return True


async def _offload[T](work: Callable[[], T]) -> T:
    """Runs are synchronous CPU work — push them off the event loop so the
    request handler itself does not block. The engine is pure Python and holds
    the GIL, so this alone does not protect other requests; the concurrency
    caps and the CPU budget above are what bound the damage.

    ``asyncio.Task.cancel()`` can abandon Starlette/AnyIO's await even though
    the native worker thread cannot be stopped. Register native completion with
    ``_with_run_limits`` so its farm, user, and process slots transfer to a
    completion callback when the HTTP task is canceled. The request can unwind
    promptly without making still-running CPU invisible to admission control.
    """
    completions = _native_run_completions.get()
    if completions is None:
        return await run_in_threadpool(work)

    loop = asyncio.get_running_loop()
    completed: asyncio.Future[None] = loop.create_future()
    completions.append(completed)
    state_lock = threading.Lock()
    started = False
    abandoned = False

    def mark_completed() -> None:
        if not completed.done():
            completed.set_result(None)

    def run_and_signal() -> T:
        nonlocal started
        with state_lock:
            if abandoned:
                # Cancellation won while this call was still queued on AnyIO's
                # thread limiter. The lease was already released and this late
                # wrapper must not start untracked engine work.
                return cast("T", None)
            started = True
        try:
            return work()
        finally:
            # During forced interpreter shutdown there is no live process
            # admission state left to release.
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(mark_completed)

    def abandon_if_queued() -> None:
        nonlocal abandoned
        with state_lock:
            if started:
                return
            abandoned = True
        # This runs on the event-loop thread from the await's exception path.
        # A late queued wrapper sees ``abandoned`` and skips engine work.
        mark_completed()

    try:
        return await run_in_threadpool(run_and_signal)
    except BaseException:
        # Covers raw task cancellation and infrastructure/submission failures.
        # If native work already started, its finally block remains lease owner.
        abandon_if_queued()
        raise


# One run per farm and per user, plus a process-wide ceiling. This prevents a
# user with several farms from consuming the whole shared threadpool. A
# distributed job queue remains the deployment path for multi-replica scale.
_farm_run_locks: dict[int, asyncio.Lock] = {}
_user_run_locks: dict[int, asyncio.Lock] = {}
_global_run_slots = asyncio.BoundedSemaphore(2)
# Populated only while _with_run_limits owns the corresponding lease. Native
# workers resolve these futures from their finally blocks even if raw asyncio
# cancellation abandons the AnyIO await.
_native_run_completions: ContextVar[list[asyncio.Future[None]] | None] = ContextVar(
    "simulation_native_run_completions",
    default=None,
)

# Concurrency caps bound parallelism, not request *rate*: without a budget a
# caller can loop maximum-cost runs forever and hold both process-wide slots,
# which measures as a ~200x latency hit on every other tenant's ordinary read.
# So each run is priced before it starts and charged against a sliding window.
# The unit is one engine pass over one simulated month, which tracks measured
# CPU closely: a default 120-month run costs ~6.4k, while the schema-maximal
# body with Monte Carlo, sensitivity and optimization costs under 570k.
_RUN_BUDGET_WINDOW_SECONDS = 300
_RUN_BUDGET_UNITS = 650_000
_RUN_BUDGET_MAX_KEYS = 50_000  # cardinality ceiling, mirroring app.ratelimit
# The busy 429 means a run is in flight right now (worst case ~25 s). A short
# hint stops naive immediate-retry loops from re-429ing in a tight circle,
# without parking the client for a whole budget window like the budget 429
# does; still-busy retries just get a fresh 429 with a fresh header
# (red-team RT-KL-5).
_BUSY_RETRY_AFTER_SECONDS = 5


class _RunCostWindow:
    """Sliding-window CPU budget keyed by ``(scope, principal id)``.

    Unlike the attempt counter in ``app.ratelimit`` the cost of one simulation
    request spans three orders of magnitude, so the window accumulates cost
    rather than requests: a caller may spend its budget on a single maximal
    run or on many cheap ones. Per-process and non-persistent, with the same
    single-process caveat as the auth limiter.
    """

    def __init__(
        self,
        *,
        window_seconds: int,
        budget: int,
        max_keys: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window_seconds = window_seconds
        self._budget = budget
        self._max_keys = max_keys
        self._clock = clock
        self._spend: dict[tuple[str, int], deque[tuple[float, int]]] = {}
        self._totals: dict[tuple[str, int], int] = {}
        # O(1) LRU eviction tiers. A principal with repeated runs or a spent
        # budget is protected from one-shot cardinality spray; if every slot
        # is protected, the oldest protected principal is retired so map
        # saturation can never become a process-wide admission outage.
        self._cold: OrderedDict[tuple[str, int], None] = OrderedDict()
        self._protected: OrderedDict[tuple[str, int], None] = OrderedDict()

    def is_over_budget(self, scope: str, key: int) -> bool:
        return self._spent(scope, key) >= self._budget

    def would_exceed_budget(self, scope: str, key: int, cost: int) -> bool:
        """Whether admitting ``cost`` would cross the window ceiling."""
        return self.would_exceed_any(((scope, key, cost),))

    def would_exceed_any(self, charges: tuple[tuple[str, int, int], ...]) -> bool:
        """Whether a complete request would cross any principal's budget.

        A pure probe never rejects an unseen principal merely because the
        bounded bookkeeping map is full. ``chargeMany`` makes room only once
        an admitted request actually needs to enter the ledger.
        """
        prospective: dict[tuple[str, int], int] = {}
        for scope, key, cost in charges:
            bucket = (scope, key)
            prospective[bucket] = prospective.get(bucket, 0) + cost
        return any(
            self._spent(scope, key) + cost > self._budget
            for (scope, key), cost in prospective.items()
        )

    def charge(self, scope: str, key: int, cost: int) -> None:
        self.charge_many(((scope, key, cost),))

    def charge_many(self, charges: tuple[tuple[str, int, int], ...]) -> None:
        """Atomically book all principals with bounded, tiered LRU eviction."""
        requested = {(scope, key) for scope, key, _cost in charges}
        if len(requested) > self._max_keys:
            raise RuntimeError("Simulation CPU budget cannot track this principal set")
        # Direct test/internal callers may charge without a preceding probe.
        # Lazily expire the requested keys before computing how many new slots
        # are needed; the public API performs the same work during preflight.
        for bucket in requested:
            self._prune_bucket(bucket, touch=True)
        # Iterate the request's (normally two) principals, never the 50k-key
        # ledger. ``requested - self._spend.keys()`` looks equivalent but
        # CPython's mixed set/dict-view difference walks the large operand.
        missing = {bucket for bucket in requested if bucket not in self._spend}
        while len(self._spend) + len(missing) > self._max_keys:
            if not self._evict_one(exclude=requested):
                raise RuntimeError("Simulation CPU budget principal capacity exhausted")
        charged_at = self._clock()
        for scope, key, cost in charges:
            bucket = (scope, key)
            self._spend.setdefault(bucket, deque()).append((charged_at, cost))
            self._totals[bucket] = self._totals.get(bucket, 0) + cost
            self._reclassify(bucket, touch=True)

    def clear(self) -> None:
        """Test hook: drop all recorded spend."""
        self._spend.clear()
        self._totals.clear()
        self._cold.clear()
        self._protected.clear()

    def _spent(self, scope: str, key: int) -> int:
        return self._prune_bucket((scope, key), touch=True)

    def _prune_bucket(self, bucket: tuple[str, int], *, touch: bool) -> int:
        entries = self._spend.get(bucket)
        if entries is None:
            return 0  # a pure budget probe must not allocate a dictionary key
        cutoff = self._clock() - self._window_seconds
        total = self._totals[bucket]
        while entries and entries[0][0] <= cutoff:
            _recorded_at, expired_cost = entries.popleft()
            total -= expired_cost
        if not entries:
            self._drop(bucket)
            return 0
        self._totals[bucket] = total
        self._reclassify(bucket, touch=touch)
        return total

    def _drop(self, bucket: tuple[str, int]) -> None:
        self._spend.pop(bucket, None)
        self._totals.pop(bucket, None)
        self._cold.pop(bucket, None)
        self._protected.pop(bucket, None)

    def _is_protected(self, bucket: tuple[str, int]) -> bool:
        # Two runs establish a hot principal even if both were cheap. A single
        # run that spends at least half the window budget is equally important:
        # evicting it would let successive schema-maximal runs reset their
        # accounting under one-shot spray pressure.
        return len(self._spend[bucket]) >= 2 or self._totals[bucket] * 2 >= self._budget

    def _reclassify(self, bucket: tuple[str, int], *, touch: bool) -> None:
        """Refresh one principal's eviction tier and optional LRU recency."""
        if bucket not in self._spend:
            return
        target = self._protected if self._is_protected(bucket) else self._cold
        other = self._cold if target is self._protected else self._protected
        was_in_target = bucket in target
        if not was_in_target:
            other.pop(bucket, None)
            target[bucket] = None
            if not touch:
                # Expiry can demote an old protected key. It belongs at the
                # cold tier's eviction front, not among recently used keys.
                target.move_to_end(bucket, last=False)
        elif touch:
            target.move_to_end(bucket)

    def _evict_one(self, *, exclude: set[tuple[str, int]]) -> bool:
        """Retire one least-valuable principal without scanning the key map."""
        for tier in (self._cold, self._protected):
            candidate = next((bucket for bucket in tier if bucket not in exclude), None)
            if candidate is None:
                continue
            # Expiry is lazy and bounded to the selected LRU candidate. If it
            # is still live, evict it; either path frees exactly one slot.
            self._prune_bucket(candidate, touch=False)
            if candidate in self._spend:
                self._drop(candidate)
            return True
        return False


_run_budget = _RunCostWindow(
    window_seconds=_RUN_BUDGET_WINDOW_SECONDS,
    budget=_RUN_BUDGET_UNITS,
    max_keys=_RUN_BUDGET_MAX_KEYS,
)


def _check_run_budget(farm_id: int, user_id: int, cost: int) -> None:
    """429 when admitting ``cost`` would exceed either principal's budget."""
    if _run_budget.would_exceed_any(
        (("user", user_id, cost), ("farm", farm_id, cost)),
    ):
        metrics.record_simulation_admission_rejection("cpu_budget")
        raise HTTPException(
            status_code=429,
            detail="Simulation CPU budget exhausted; try again shortly.",
            headers={"Retry-After": str(_RUN_BUDGET_WINDOW_SECONDS)},
        )


def _charge_run_budget(farm_id: int, user_id: int, cost: int) -> None:
    """Book a request's cost before it runs, so its own spend counts."""
    _run_budget.charge_many(
        (("user", user_id, cost), ("farm", farm_id, cost)),
    )


def _farm_run_lock(farm_id: int) -> asyncio.Lock:
    lock = _farm_run_locks.get(farm_id)
    if lock is None:
        lock = asyncio.Lock()
        _farm_run_locks[farm_id] = lock
    return lock


def _run_lock(store: dict[int, asyncio.Lock], key: int) -> asyncio.Lock:
    lock = store.get(key)
    if lock is None:
        lock = asyncio.Lock()
        store[key] = lock
    return lock


def _release_run_lock(store: dict[int, asyncio.Lock], key: int) -> None:
    """Drop an idle keyed lock so tenant/user cardinality cannot leak memory."""
    lock = store.get(key)
    if lock is not None and not lock.locked():
        store.pop(key, None)


async def _with_run_limits[RunResult](
    farm_id: int,
    user_id: int,
    operation: Callable[[], Awaitable[RunResult]],
) -> RunResult:
    """Execute one bounded CPU operation or fail fast instead of queueing."""
    lock = _farm_run_lock(farm_id)
    user_lock = _run_lock(_user_run_locks, user_id)
    # The busy check lives inside the try so the two locks just created are
    # dropped again on the 429 fast path — that path is reached precisely with
    # unseen farm/user keys (the global semaphore is full for everyone), and
    # leaking one entry per key is the cardinality growth _release_run_lock
    # exists to prevent.
    acquired_user = False
    acquired_farm = False
    acquired_global = False
    released = False
    completions: list[asyncio.Future[None]] = []
    tracker_token = None

    def release_capacity() -> None:
        nonlocal released
        if released:
            return
        released = True
        if acquired_global:
            _global_run_slots.release()
        if acquired_farm:
            lock.release()
        if acquired_user:
            user_lock.release()
        _release_run_lock(_farm_run_locks, farm_id)
        _release_run_lock(_user_run_locks, user_id)

    try:
        if lock.locked() or user_lock.locked() or _global_run_slots.locked():
            metrics.record_simulation_admission_rejection("capacity_busy")
            raise HTTPException(
                status_code=429,
                detail="Simulation capacity is busy; wait for the current run to finish.",
                headers={"Retry-After": str(_BUSY_RETRY_AFTER_SECONDS)},
            )
        await user_lock.acquire()
        acquired_user = True
        await lock.acquire()
        acquired_farm = True
        await _global_run_slots.acquire()
        acquired_global = True
        tracker_token = _native_run_completions.set(completions)
        return await operation()
    finally:
        if tracker_token is not None:
            _native_run_completions.reset(tracker_token)
        pending = [completion for completion in completions if not completion.done()]
        if not pending:
            release_capacity()
        else:
            remaining = len(pending)

            def native_finished(_completion: asyncio.Future[None]) -> None:
                nonlocal remaining
                remaining -= 1
                if remaining == 0:
                    release_capacity()

            for completion in pending:
                completion.add_done_callback(native_finished)
