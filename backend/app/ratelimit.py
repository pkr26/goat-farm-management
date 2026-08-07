"""Dependency-free in-memory sliding-window rate limiter.

Per-process and non-persistent by design (single-process deployment): counts
live in a dict keyed by (scope, client key) and are lost on restart. The auth
endpoints use it to throttle login/register brute force; the test suite
disables it via GOATFARM_AUTH_RATE_LIMIT_ENABLED=false.
"""

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowRateLimiter:
    """Attempts older than the window expire; at `max_attempts` inside the
    window the key is blocked until the oldest attempt ages out."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = {}

    def is_blocked(self, scope: str, key: str, max_attempts: int, window_seconds: int) -> bool:
        return len(self._prune(scope, key, window_seconds)) >= max_attempts

    def record(self, scope: str, key: str, window_seconds: int) -> None:
        hits = self._prune(scope, key, window_seconds)
        hits.append(self._clock())
        # _prune drops emptied deques from the map — re-store so this hit lands.
        self._hits[(scope, key)] = hits

    def reset(self, scope: str, key: str) -> None:
        """Forget all recorded attempts (e.g. after a successful login)."""
        self._hits.pop((scope, key), None)

    def clear(self) -> None:
        """Test hook: drop all state."""
        self._hits.clear()

    def _prune(self, scope: str, key: str, window_seconds: int) -> deque[float]:
        hits = self._hits.setdefault((scope, key), deque())
        cutoff = self._clock() - window_seconds
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            # Never keep an emptied deque: the map would otherwise grow one
            # permanent entry per distinct key (a memory leak under sprayed
            # IPs/emails). Pure probes then cost no memory either.
            del self._hits[(scope, key)]
        return hits


auth_limiter = SlidingWindowRateLimiter()
