"""Sweep scheduling and LRU-recency pins for the rate-limiter backend.

The 2026-09-30 mutation campaign showed the background sweep's schedule
(``_next_sweep = now + interval`` in both ``_sweep`` and ``clear``) and the
touch-on-access LRU recency inside ``_reclassify`` mutating freely: bucket
heat promotion and periodic expiry timing were behaviorally unasserted. Drift
there surfaces as stale limits (expired buckets kept) or unbounded memory
(sweep never firing). These tests drive a fake clock through the documented
white-box surface (``_next_sweep``/``_cold``/``_protected`` exist exactly for
these assertions, see ratelimit.py) and pin both the schedule and the
eviction order.
"""

from collections import OrderedDict

from app.ratelimit import MemoryLimiterBackend, SlidingWindowRateLimiter


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def test_sweep_schedule_pinned_to_interval() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(clock, sweep_interval_seconds=30)
    assert limiter._next_sweep == 1000.0 + 30
    limiter.record("login", "a", 60, max_attempts=5)
    # Probes before the scheduled time must not advance the schedule.
    clock.now = 1001.0
    limiter.is_blocked("login", "a", 5, 60)
    assert limiter._next_sweep == 1030.0
    # At exactly the scheduled time the sweep fires and re-arms one full
    # interval ahead — never one interval into the past (which would make
    # every request O(all-buckets)) nor a different offset.
    clock.now = 1030.0
    limiter.is_blocked("login", "a", 5, 60)
    assert limiter._next_sweep == 1060.0
    clock.now = 1059.9
    limiter.is_blocked("login", "a", 5, 60)
    assert limiter._next_sweep == 1060.0
    clock.now = 1060.0
    limiter.is_blocked("login", "a", 5, 60)
    assert limiter._next_sweep == 1090.0


def test_sweep_expires_other_buckets_only_when_scheduled() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(clock, sweep_interval_seconds=30)
    limiter.record("login", "a", 20, max_attempts=5)
    limiter.record("login", "b", 20, max_attempts=5)
    # Both recorded at t=1000 with a 20s window (stale after 1020); the sweep
    # is armed for t=1030.
    clock.now = 1025.0
    limiter.is_blocked("login", "b", 5, 20)
    # 'b' expired and was dropped by ITS OWN prune, but the scheduled sweep
    # has not fired yet (1025 < 1030), so the equally-stale 'a' survives.
    assert not limiter.has_attempts("login", "b")
    assert limiter.has_attempts("login", "a")
    # Probing any key at exactly the scheduled time sweeps every stale
    # bucket: 'a' is dropped even though the probe touched 'b'.
    clock.now = 1030.0
    limiter.is_blocked("login", "b", 5, 20)
    assert not limiter.has_attempts("login", "a")


def test_clear_rearms_sweep_schedule() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(clock, sweep_interval_seconds=30)
    clock.now = 5000.0
    limiter.record("login", "a", 60, max_attempts=5)
    limiter.clear()
    assert limiter._next_sweep == 5030.0


def test_probe_touch_keeps_recently_accessed_bucket_alive() -> None:
    # LRU recency: touching a cold bucket (via a block probe) moves it to
    # the back of the eviction queue, so a later saturation evicts the
    # least-recently-touched bucket instead.
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(clock, max_keys=2, sweep_interval_seconds=30)
    limiter.record("login", "a", 600, max_attempts=5)  # older
    clock.now = 1001.0
    limiter.record("login", "b", 600, max_attempts=5)  # newer
    assert list(limiter._cold) == [("login", "a"), ("login", "b")]
    clock.now = 1002.0
    limiter.is_blocked("login", "a", 5, 600)  # touch 'a' — moves to back
    assert list(limiter._cold) == [("login", "b"), ("login", "a")]
    clock.now = 1003.0
    limiter.record("login", "c", 600, max_attempts=5)  # saturates max_keys=2
    assert not limiter.has_attempts("login", "b")  # LRU evicted
    assert limiter.has_attempts("login", "a")  # touched, survived
    assert limiter.has_attempts("login", "c")


def test_append_hit_touch_updates_recency() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(clock, max_keys=2, sweep_interval_seconds=30)
    limiter.record("login", "a", 600, max_attempts=5)
    clock.now = 1001.0
    limiter.record("login", "b", 600, max_attempts=5)
    # A new *hit* on 'a' is also a touch: 'b' becomes the eviction candidate.
    clock.now = 1002.0
    limiter.record("login", "a", 600, max_attempts=5)
    assert list(limiter._cold) == [("login", "b"), ("login", "a")]
    clock.now = 1003.0
    limiter.record("login", "c", 600, max_attempts=5)
    assert not limiter.has_attempts("login", "b")
    assert limiter.has_attempts("login", "a")


def test_mark_blocked_touch_protects_recently_blocked_bucket() -> None:
    # When every slot is protected (buckets at their threshold), saturation
    # retires the oldest *protected* bucket; a fresh block on an old bucket
    # must move it to the back before that decision.
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(clock, max_keys=2, sweep_interval_seconds=30)
    limiter.record("login", "a", 600, max_attempts=1)  # a: at threshold -> protected
    clock.now = 1001.0
    limiter.record("login", "b", 600, max_attempts=1)  # b: protected, newer
    assert list(limiter._protected) == [("login", "a"), ("login", "b")]
    clock.now = 1002.0
    # 'a' is already blocked: another rejected attempt is mark_blocked(), a
    # touch that must move 'a' behind 'b'.
    limiter.record("login", "a", 600, max_attempts=1)
    assert list(limiter._protected) == [("login", "b"), ("login", "a")]
    clock.now = 1003.0
    limiter.record("login", "c", 600, max_attempts=5)  # cold; _cold empty -> protected tier
    assert not limiter.has_attempts("login", "b")
    assert limiter.has_attempts("login", "a")


def test_memory_backend_direct_schedule_arithmetic() -> None:
    clock = FakeClock(42.0)
    backend = MemoryLimiterBackend(clock, sweep_interval_seconds=7)
    assert backend._next_sweep == 49.0
    clock.now = 100.0
    backend.pruned_hit_count("s", "k", 10)
    assert backend._next_sweep == 107.0
    assert isinstance(backend._cold, OrderedDict)
