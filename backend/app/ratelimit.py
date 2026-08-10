"""Dependency-free in-memory sliding-window rate limiter.

Per-process and non-persistent by design (single-process deployment): counts
live in a dict keyed by (scope, client key) and are lost on restart, and
under `--workers N` / horizontal scaling each process gets its own bucket
(effective limits multiply by the process count) — a documented constraint;
a shared backend (e.g. Redis) is the fix if multi-process is ever needed.
The auth endpoints use it to throttle login/register/refresh brute force;
the test suite disables it via GOATFARM_AUTH_RATE_LIMIT_ENABLED=false.
"""

import time
from collections import OrderedDict, deque
from collections.abc import Callable


class SlidingWindowRateLimiter:
    """Attempts older than the window expire; at `max_attempts` inside the
    window the key is blocked until the oldest attempt ages out."""

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

    def is_blocked(self, scope: str, key: str, max_attempts: int, window_seconds: int) -> bool:
        bucket = (scope, key)
        hits = self._prune(scope, key, window_seconds)
        if bucket in self._hits:
            self._limits[bucket] = max_attempts
            self._reclassify(bucket, touch=True)
        # A full bookkeeping map must never turn a unique-key spray into a
        # process-wide denial of service. Pure probes allocate nothing, so an
        # unseen (and possibly valid) identity remains admissible; record()
        # makes room only if that request later needs to enter the failure
        # ledger.
        return len(hits) >= max_attempts

    def record(
        self,
        scope: str,
        key: str,
        window_seconds: int,
        *,
        max_attempts: int | None = None,
    ) -> None:
        bucket = (scope, key)
        hits = self._prune(scope, key, window_seconds)
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
        hits.append(self._clock())
        # _prune drops emptied deques from the map — re-store so this hit lands.
        self._hits[bucket] = hits
        self._windows[bucket] = window_seconds
        if max_attempts is not None:
            self._limits[bucket] = max_attempts
        self._reclassify(bucket, touch=True)

    def reset(self, scope: str, key: str) -> None:
        """Forget all recorded attempts (e.g. after a successful login)."""
        self._drop((scope, key))

    def try_reserve(
        self,
        scope: str,
        key: str,
        *,
        max_in_flight: int = 1,
    ) -> bool:
        """Atomically reserve one bounded in-flight admission slot.

        The API process runs these synchronous calls on one asyncio event-loop
        thread, so the read/increment pair cannot interleave with another
        request.  A hard cardinality ceiling also keeps a unique-key spray
        from turning the reservation map itself into an availability issue.
        """
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be positive")
        bucket = (scope, key)
        in_flight = self._reservations.get(bucket, 0)
        if in_flight >= max_in_flight:
            return False
        if bucket not in self._reservations and len(self._reservations) >= self._max_keys:
            return False
        self._reservations[bucket] = in_flight + 1
        return True

    def release(self, scope: str, key: str) -> None:
        """Release a prior admission reservation; missing releases are safe."""
        bucket = (scope, key)
        in_flight = self._reservations.get(bucket)
        if in_flight is None:
            return
        if in_flight <= 1:
            self._reservations.pop(bucket, None)
        else:
            self._reservations[bucket] = in_flight - 1

    def clear(self) -> None:
        """Test hook: drop all state."""
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


auth_limiter = SlidingWindowRateLimiter()
