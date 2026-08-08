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
from collections import deque
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
        self._next_sweep = self._clock() + sweep_interval_seconds

    def is_blocked(self, scope: str, key: str, max_attempts: int, window_seconds: int) -> bool:
        return len(self._prune(scope, key, window_seconds)) >= max_attempts

    def record(self, scope: str, key: str, window_seconds: int) -> None:
        bucket = (scope, key)
        hits = self._prune(scope, key, window_seconds)
        hits.append(self._clock())
        # Bound cardinality even during a distributed unique-key spray. Sweep
        # expired buckets first; if all are still live, evict the oldest
        # inserted bucket rather than allowing unbounded process memory.
        if bucket not in self._hits and len(self._hits) >= self._max_keys:
            self._sweep(force=True)
        while bucket not in self._hits and len(self._hits) >= self._max_keys:
            self._drop(next(iter(self._hits)))
        # _prune drops emptied deques from the map — re-store so this hit lands.
        self._hits[bucket] = hits
        self._windows[bucket] = window_seconds

    def reset(self, scope: str, key: str) -> None:
        """Forget all recorded attempts (e.g. after a successful login)."""
        self._drop((scope, key))

    def clear(self) -> None:
        """Test hook: drop all state."""
        self._hits.clear()
        self._windows.clear()
        self._next_sweep = self._clock() + self._sweep_interval_seconds

    def _drop(self, bucket: tuple[str, str]) -> None:
        self._hits.pop(bucket, None)
        self._windows.pop(bucket, None)

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
        return hits


auth_limiter = SlidingWindowRateLimiter()
