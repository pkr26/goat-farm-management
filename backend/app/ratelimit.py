"""Sliding-window rate limiting behind a swappable storage backend.

Per-process and non-persistent by design (single-process deployment): counts
live in a backend keyed by (scope, client key) and are lost on restart, and
under `--workers N` / horizontal scaling each process gets its own bucket
(effective limits multiply by the process count) — a documented constraint.
The auth endpoints use it to throttle login/register/refresh brute force;
the test suite disables it via GOATFARM_AUTH_RATE_LIMIT_ENABLED=false.

Storage is isolated behind the ``LimiterBackend`` protocol so a shared
backend (e.g. Redis) can replace the dicts without touching the policy or
any call site. Only ``MemoryLimiterBackend`` exists today;
``GOATFARM_RATE_LIMIT_BACKEND`` accepts only "memory" and fails startup
otherwise (see app.core.config and the single-worker section of README.md).
The policy layer (``SlidingWindowRateLimiter``) keeps its historical public
signatures, so routers and tests are unchanged.
"""

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Protocol

from .core.config import get_settings


class LimiterBackend(Protocol):
    """Storage seam under the sliding-window policy.

    Buckets are addressed by ``(scope, key)``. Implementations own expiry
    bookkeeping, cardinality bounding and reservation accounting; the policy
    layer above decides what a bucket's hits *mean*. All methods must be
    safe to call from one event-loop thread (the API's deployment model).
    """

    def pruned_hit_count(self, scope: str, key: str, window_seconds: int) -> int:
        """Attempts inside the window after expiry housekeeping.

        A pure probe must not allocate a permanent bucket for an unseen key.
        """
        ...

    def is_known(self, scope: str, key: str) -> bool:
        """Whether the bucket currently holds at least one live hit."""
        ...

    def limit_for(self, scope: str, key: str) -> int | None:
        """The last admission threshold this bucket was judged against."""
        ...

    def promote(self, scope: str, key: str, limit: int) -> None:
        """Refresh a known bucket's threshold (a probe that found history)."""
        ...

    def mark_blocked(self, scope: str, key: str, window_seconds: int, limit: int) -> None:
        """Record that a rejected attempt found the bucket already full.

        The window/threshold are refreshed so denied traffic neither extends
        the window nor loses the bucket's eviction protection; no hit is
        appended (a denied attempt carries no new admission information).
        """
        ...

    def append_hit(
        self,
        scope: str,
        key: str,
        window_seconds: int,
        at: float,
        limit: int | None,
    ) -> None:
        """Append one admitted attempt, evicting at the key ceiling."""
        ...

    def forget(self, scope: str, key: str) -> None:
        """Drop a bucket entirely (e.g. after a successful login)."""
        ...

    def try_reserve(self, scope: str, key: str, *, max_in_flight: int) -> bool:
        """Atomically reserve one bounded in-flight admission slot."""
        ...

    def release(self, scope: str, key: str) -> None:
        """Release a prior reservation; missing releases are safe."""
        ...

    def clear(self) -> None:
        """Test hook: drop all state."""
        ...


class MemoryLimiterBackend:
    """The original dict/deque storage, unchanged, behind the protocol.

    Per-process and non-persistent; counts are lost on restart and multiply
    per replica. O(1) tiered LRU eviction keeps a unique-key spray from
    erasing a hot victim's brute-force history or turning bookkeeping
    saturation into a process-wide denial of service.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        max_keys: int = 50_000,
        sweep_interval_seconds: int = 30,
    ) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        if sweep_interval_seconds < 1:
            raise ValueError("sweep_interval_seconds must be positive")
        self._clock = clock
        self._max_keys = max_keys
        self._sweep_interval_seconds = sweep_interval_seconds
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._windows: dict[tuple[str, str], int] = {}
        # Optional admission thresholds let eviction distinguish a live
        # brute-force block from a one-shot spray key.  It is populated by
        # callers that record a failure and is bounded exactly with _hits.
        self._limits: dict[tuple[str, str], int] = {}
        # O(1) LRU eviction classes. A threshold-reaching failure bucket is
        # protected from ordinary one-shot spray entries; only when every slot
        # is protected do we retire the oldest protected bucket. OrderedDict is
        # used as an insertion-ordered set (values are always None).
        self._cold: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._protected: OrderedDict[tuple[str, str], None] = OrderedDict()
        # Short-lived admission reservations close the check-then-work gap:
        # without them a same-identity burst can have every request observe an
        # empty failure bucket before any expensive password verification has
        # completed and recorded its result.  Reservations never wait; excess
        # callers fail fast and the route releases its slot in ``finally``.
        self._reservations: dict[tuple[str, str], int] = {}
        self._next_sweep = self._clock() + sweep_interval_seconds

    def pruned_hit_count(self, scope: str, key: str, window_seconds: int) -> int:
        return len(self._prune(scope, key, window_seconds))

    def is_known(self, scope: str, key: str) -> bool:
        return (scope, key) in self._hits

    def limit_for(self, scope: str, key: str) -> int | None:
        return self._limits.get((scope, key))

    def promote(self, scope: str, key: str, limit: int) -> None:
        bucket = (scope, key)
        if bucket not in self._hits:
            return
        self._limits[bucket] = limit
        self._reclassify(bucket, touch=True)

    def mark_blocked(self, scope: str, key: str, window_seconds: int, limit: int) -> None:
        bucket = (scope, key)
        self._windows[bucket] = window_seconds
        self._limits[bucket] = limit
        self._reclassify(bucket, touch=True)

    def append_hit(
        self,
        scope: str,
        key: str,
        window_seconds: int,
        at: float,
        limit: int | None,
    ) -> None:
        bucket = (scope, key)
        # Bound cardinality even during a distributed unique-key spray. Sweep
        # expired buckets first. If all are still live, evict the least
        # security-relevant bucket: below-threshold one-shot entries before a
        # blocked counter, then the oldest entry. This keeps a cheap
        # unique-key spray from erasing a hot victim's brute-force history
        # without globally rejecting every unseen benign identity.
        if bucket not in self._hits and len(self._hits) >= self._max_keys:
            # The scheduled sweep in _prune amortizes expiry work. Do not scan
            # all 50k live buckets on every saturated insertion: an attacker
            # could otherwise monopolize the event loop with unique failures.
            eviction_class = self._cold if self._cold else self._protected
            candidate = next(iter(eviction_class))
            self._drop(candidate)
        hits = self._hits.get(bucket)
        if hits is None:
            # _prune drops emptied deques from the map — re-store so this hit lands.
            hits = deque()
            self._hits[bucket] = hits
        hits.append(at)
        self._windows[bucket] = window_seconds
        if limit is not None:
            self._limits[bucket] = limit
        self._reclassify(bucket, touch=True)

    def forget(self, scope: str, key: str) -> None:
        self._drop((scope, key))

    def try_reserve(self, scope: str, key: str, *, max_in_flight: int) -> bool:
        """Atomically reserve one bounded in-flight admission slot.

        The API process runs these synchronous calls on one asyncio event-loop
        thread, so the read/increment pair cannot interleave with another
        request.  A hard cardinality ceiling also keeps a unique-key spray
        from turning the reservation map itself into an availability issue.
        """
        bucket = (scope, key)
        in_flight = self._reservations.get(bucket, 0)
        if in_flight >= max_in_flight:
            return False
        if bucket not in self._reservations and len(self._reservations) >= self._max_keys:
            return False
        self._reservations[bucket] = in_flight + 1
        return True

    def release(self, scope: str, key: str) -> None:
        bucket = (scope, key)
        in_flight = self._reservations.get(bucket)
        if in_flight is None:
            return
        if in_flight <= 1:
            self._reservations.pop(bucket, None)
        else:
            self._reservations[bucket] = in_flight - 1

    def clear(self) -> None:
        self._hits.clear()
        self._windows.clear()
        self._limits.clear()
        self._cold.clear()
        self._protected.clear()
        self._reservations.clear()
        self._next_sweep = self._clock() + self._sweep_interval_seconds

    def _drop(self, bucket: tuple[str, str]) -> None:
        self._hits.pop(bucket, None)
        self._windows.pop(bucket, None)
        self._limits.pop(bucket, None)
        self._cold.pop(bucket, None)
        self._protected.pop(bucket, None)

    def _is_protected(self, bucket: tuple[str, str]) -> bool:
        threshold = self._limits.get(bucket)
        return threshold is not None and len(self._hits[bucket]) >= threshold

    def _reclassify(self, bucket: tuple[str, str], *, touch: bool) -> None:
        """Refresh one bucket's eviction tier, optionally its LRU recency.

        A periodic sweep is passive maintenance, not an access: survivors keep
        their relative order. If expiry demotes a protected bucket, inserting
        it at the cold tier's front makes its old activity the first eviction
        candidate. Actual probes/records are touches and move to the back.
        """
        if bucket not in self._hits:
            return
        if self._is_protected(bucket):
            was_protected = bucket in self._protected
            if not was_protected:
                self._cold.pop(bucket, None)
                self._protected[bucket] = None
                if not touch:
                    self._protected.move_to_end(bucket, last=False)
            elif touch:
                self._protected.move_to_end(bucket)
        else:
            was_cold = bucket in self._cold
            if not was_cold:
                self._protected.pop(bucket, None)
                self._cold[bucket] = None
                if not touch:
                    self._cold.move_to_end(bucket, last=False)
            elif touch:
                self._cold.move_to_end(bucket)

    def _sweep(self, *, force: bool = False) -> None:
        """Expire every stale bucket periodically, not only the key a caller
        happened to revisit. The interval keeps the normal request path O(1)."""
        now = self._clock()
        if not force and now < self._next_sweep:
            return
        for bucket, hits in list(self._hits.items()):
            cutoff = now - self._windows[bucket]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                self._drop(bucket)
            else:
                self._reclassify(bucket, touch=False)
        self._next_sweep = now + self._sweep_interval_seconds

    def _prune(self, scope: str, key: str, window_seconds: int) -> deque[float]:
        self._sweep()
        bucket = (scope, key)
        hits = self._hits.get(bucket)
        if hits is None:
            # A pure block probe must not allocate a permanent dictionary key.
            return deque()
        cutoff = self._clock() - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            self._drop(bucket)
        else:
            self._reclassify(bucket, touch=True)
        return hits


def build_limiter_backend() -> LimiterBackend:
    """Resolve ``GOATFARM_RATE_LIMIT_BACKEND`` into a backend instance.

    Unknown values fail fast here with the single-replica explanation;
    Settings validation normally rejects them first. ``create_app`` calls
    this at startup so a misconfigured backend name cannot boot.
    """
    name = get_settings().rate_limit_backend
    if name != "memory":
        raise RuntimeError(
            f"GOATFARM_RATE_LIMIT_BACKEND={name!r} is not implemented; only 'memory' "
            "exists. The memory backend is per-process — running more than one "
            "backend replica multiplies every auth limit per process. See the "
            "single-worker / single-replica section of README.md before scaling."
        )
    return MemoryLimiterBackend()


class SlidingWindowRateLimiter:
    """Attempts older than the window expire; at `max_attempts` inside the
    window the key is blocked until the oldest attempt ages out."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        *,
        max_keys: int = 50_000,
        sweep_interval_seconds: int = 30,
        backend: LimiterBackend | None = None,
    ) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be positive")
        if sweep_interval_seconds < 1:
            raise ValueError("sweep_interval_seconds must be positive")
        self._clock = clock
        self._backend = (
            backend
            if backend is not None
            else MemoryLimiterBackend(
                clock, max_keys=max_keys, sweep_interval_seconds=sweep_interval_seconds
            )
        )

    def is_blocked(self, scope: str, key: str, max_attempts: int, window_seconds: int) -> bool:
        hits = self._backend.pruned_hit_count(scope, key, window_seconds)
        if self._backend.is_known(scope, key):
            self._backend.promote(scope, key, max_attempts)
        # A full bookkeeping map must never turn a unique-key spray into a
        # process-wide denial of service. Pure probes allocate nothing, so an
        # unseen (and possibly valid) identity remains admissible; record()
        # makes room only if that request later needs to enter the failure
        # ledger.
        return hits >= max_attempts

    def record(
        self,
        scope: str,
        key: str,
        window_seconds: int,
        *,
        max_attempts: int | None = None,
    ) -> None:
        hits = self._backend.pruned_hit_count(scope, key, window_seconds)
        effective_limit = (
            max_attempts if max_attempts is not None else self._backend.limit_for(scope, key)
        )
        if effective_limit is not None:
            if effective_limit < 1:
                raise ValueError("max_attempts must be positive")
            # Once a bucket is blocked, later rejected attempts carry no new
            # admission information. Keeping every one of them lets a single
            # hot IP defeat the key-cardinality bound with an unbounded deque
            # (invalid-token callers deliberately classify a fresh token before
            # consulting their shared IP budget). Preserve the original
            # threshold hits so denied traffic does not extend the window, just
            # as routes that can pre-check a bucket stop recording at the limit.
            if hits >= effective_limit:
                self._backend.mark_blocked(scope, key, window_seconds, effective_limit)
                return
        self._backend.append_hit(scope, key, window_seconds, self._clock(), effective_limit)

    def reset(self, scope: str, key: str) -> None:
        """Forget all recorded attempts (e.g. after a successful login)."""
        self._backend.forget(scope, key)

    def has_attempts(self, scope: str, key: str) -> bool:
        """Whether the bucket currently holds any recorded attempts.

        Diagnostic/test introspection (replaces direct ``_hits`` membership
        checks now that storage lives behind the backend seam); raw bucket
        presence, no time-window pruning.
        """
        return self._backend.is_known(scope, key)

    def try_reserve(
        self,
        scope: str,
        key: str,
        *,
        max_in_flight: int = 1,
    ) -> bool:
        """Atomically reserve one bounded in-flight admission slot."""
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be positive")
        return self._backend.try_reserve(scope, key, max_in_flight=max_in_flight)

    def release(self, scope: str, key: str) -> None:
        """Release a prior admission reservation; missing releases are safe."""
        self._backend.release(scope, key)

    def clear(self) -> None:
        """Test hook: drop all state."""
        self._backend.clear()

    # ------------------------------------------------------------------
    # Historical white-box test surface. test_security_hardening and friends
    # assert against the memory implementation's LRU tiers, windows and
    # reservations; the seam moved that storage behind the backend, so these
    # read-only views keep those tests meaningful and unchanged. They exist
    # only for the memory backend (a Redis backend would expose equivalent
    # state through its own diagnostics, not these dicts).
    # ------------------------------------------------------------------
    def _memory(self) -> MemoryLimiterBackend:
        backend = self._backend
        if not isinstance(backend, MemoryLimiterBackend):
            raise AttributeError("bucket introspection exists only on the memory backend")
        return backend

    @property
    def _hits(self) -> dict[tuple[str, str], deque[float]]:
        return self._memory()._hits

    @property
    def _windows(self) -> dict[tuple[str, str], int]:
        return self._memory()._windows

    @property
    def _limits(self) -> dict[tuple[str, str], int]:
        return self._memory()._limits

    @property
    def _cold(self) -> "OrderedDict[tuple[str, str], None]":
        return self._memory()._cold

    @property
    def _protected(self) -> "OrderedDict[tuple[str, str], None]":
        return self._memory()._protected

    @property
    def _reservations(self) -> dict[tuple[str, str], int]:
        return self._memory()._reservations

    @property
    def _max_keys(self) -> int:
        return self._memory()._max_keys

    @_max_keys.setter
    def _max_keys(self, value: int) -> None:
        self._memory()._max_keys = value

    @property
    def _next_sweep(self) -> float:
        return self._memory()._next_sweep

    @_next_sweep.setter
    def _next_sweep(self, value: float) -> None:
        self._memory()._next_sweep = value


auth_limiter = SlidingWindowRateLimiter()
