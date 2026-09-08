"""Prometheus metrics for the API process.

A dedicated ``CollectorRegistry`` (not the client's process-wide default) so
test and application counters can never mix, and so rendering never picks up
unrelated libraries' collectors.

Every collector is module-level and therefore per-process, matching the
single-replica deployment model: counters reset on restart and are not
aggregated across workers. ``GOATFARM_METRICS_ENABLED=false`` turns off both
collection (all ``record_*``/``observe_*`` helpers become no-ops after one
cached settings read) and the ``/metrics`` route itself (see ``app.main``).

Label cardinality is deliberately bounded:
- ``route`` is the route *template* (e.g. ``/api/animals/{animal_id}``), never
  the raw path, so the label set is fixed by the route table; unmatched
  requests (404s, CORS preflights, middleware short-circuits) share the
  literal ``unmatched``.
- ``scope`` values are the fixed scope constants used by ``app.ratelimit``
  call sites; ``reason`` the two simulation admission outcomes.
"""

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from .core.config import get_settings

REGISTRY = CollectorRegistry()

# Seconds; the last bucket is implicit (+Inf). Covers sub-millisecond health
# probes through multi-second simulation runs without fine-grained noise.
_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

HTTP_REQUESTS = Counter(
    "goatfarm_http_requests",
    "HTTP requests processed by route template, method and response status.",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "goatfarm_http_request_duration_seconds",
    "HTTP request wall-clock duration in seconds.",
    labelnames=("method", "route", "status"),
    buckets=_DURATION_BUCKETS,
    registry=REGISTRY,
)
AUTH_RATE_LIMIT_REJECTIONS = Counter(
    "goatfarm_auth_rate_limit_rejections",
    "Auth rate-limit 429 decisions, by limiter scope.",
    labelnames=("scope",),
    registry=REGISTRY,
)
IDEMPOTENCY_REPLAYS = Counter(
    "goatfarm_idempotency_replays",
    "Committed mutation results served again via Idempotency-Key replay.",
    registry=REGISTRY,
)
SIMULATION_ADMISSION_REJECTIONS = Counter(
    "goatfarm_simulation_admission_rejections",
    "Simulation-family requests refused before running, by admission control.",
    labelnames=("reason",),
    registry=REGISTRY,
)
REFRESH_SESSION_PURGE_BATCHES = Counter(
    "goatfarm_refresh_session_purge_batches",
    "Refresh-session expiry cleanup batches executed by the maintenance loop.",
    registry=REGISTRY,
)
REFRESH_SESSIONS_PURGED = Counter(
    "goatfarm_refresh_sessions_purged",
    "Expired refresh_sessions rows deleted by the maintenance loop.",
    registry=REGISTRY,
)


def enabled() -> bool:
    """Collection (and the /metrics route) is off unless configured on."""
    return get_settings().metrics_enabled


def observe_http_request(method: str, route: str, status: int, duration_seconds: float) -> None:
    if enabled():
        labels = {"method": method, "route": route, "status": str(status)}
        HTTP_REQUESTS.labels(**labels).inc()
        HTTP_REQUEST_DURATION.labels(**labels).observe(duration_seconds)


def record_auth_rate_limit_rejection(scope: str) -> None:
    """Count one decision to answer 429 from the auth limiter (not probes)."""
    if enabled():
        AUTH_RATE_LIMIT_REJECTIONS.labels(scope=scope).inc()


def record_idempotency_replay() -> None:
    if enabled():
        IDEMPOTENCY_REPLAYS.inc()


def record_simulation_admission_rejection(reason: str) -> None:
    """``reason`` is one of "cpu_budget" (window exhausted) or
    "capacity_busy" (per-farm/user/process slot occupied)."""
    if enabled():
        SIMULATION_ADMISSION_REJECTIONS.labels(reason=reason).inc()


def record_refresh_session_purge_batch(purged_rows: int) -> None:
    if enabled():
        REFRESH_SESSION_PURGE_BATCHES.inc()
        if purged_rows:
            REFRESH_SESSIONS_PURGED.inc(purged_rows)


def render() -> bytes:
    """Prometheus text exposition of every collector above."""
    return generate_latest(REGISTRY)


__all__ = [
    "AUTH_RATE_LIMIT_REJECTIONS",
    "CONTENT_TYPE_LATEST",
    "HTTP_REQUESTS",
    "HTTP_REQUEST_DURATION",
    "IDEMPOTENCY_REPLAYS",
    "REFRESH_SESSIONS_PURGED",
    "REFRESH_SESSION_PURGE_BATCHES",
    "REGISTRY",
    "SIMULATION_ADMISSION_REJECTIONS",
    "enabled",
    "observe_http_request",
    "record_auth_rate_limit_rejection",
    "record_idempotency_replay",
    "record_refresh_session_purge_batch",
    "record_simulation_admission_rejection",
    "render",
]
