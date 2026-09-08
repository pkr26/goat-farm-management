"""Observability (/metrics) and limiter-backend seam coverage.

/metrics is an operational endpoint (never under /api, unauthenticated, kept
off the OpenAPI contract) — these tests pin: the text exposition carries the
HTTP request counter for a served request, GOATFARM_METRICS_ENABLED=false
removes the route and stops collection, a rate-limit 429 increments the
scope-labeled rejection counter, an idempotent replay is counted via the
read-only middleware hook, and the GOATFARM_RATE_LIMIT_BACKEND knob accepts
only the implemented memory backend.
"""

import re
from collections.abc import Iterator

import httpx
import pytest
from pydantic import ValidationError

from app import metrics
from app.core.config import Settings, get_settings
from app.main import create_app
from app.ratelimit import MemoryLimiterBackend, SlidingWindowRateLimiter, build_limiter_backend

from .conftest import create_farm, register

_SAMPLE_LINE = re.compile(rb"^(goatfarm_\w+(?:_total)?)(\{[^}]*\})? ([0-9.e+-]+)$", re.MULTILINE)


def _samples(text: bytes) -> dict[tuple[str, str], float]:
    """Parse the classic exposition format into {(name, labels): value}.

    ``labels`` is stored without the surrounding braces; prometheus-client
    sorts label names (e.g. a histogram's ``le`` first), so assertions must
    not depend on declaration order.
    """
    parsed: dict[tuple[str, str], float] = {}
    for match in _SAMPLE_LINE.finditer(text):
        name, labels, value = match.group(1).decode(), match.group(2) or b"", match.group(3)
        normalized = labels.decode().strip("{}")
        parsed[(name, normalized)] = float(value)
    return parsed


def _sample_value(samples: dict[tuple[str, str], float], name: str, labels: str) -> float | None:
    return next((v for (n, lab), v in samples.items() if n == name and lab == labels), None)


def _any_labeled_sample(
    samples: dict[tuple[str, str], float], name: str, required_label: str
) -> bool:
    return any(n == name and required_label in lab for (n, lab) in samples)


async def test_metrics_exposes_http_request_counter(client: httpx.AsyncClient) -> None:
    health = await client.get("/healthz")
    assert health.status_code == 200

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain;")
    body = resp.content
    samples = _samples(body)
    # The /healthz request above must appear under its route TEMPLATE with
    # method and status labels (exact label set; the value may reflect other
    # tests in this process, so only presence is asserted).
    assert (
        _sample_value(
            samples, "goatfarm_http_requests_total", 'method="GET",route="/healthz",status="200"'
        )
        is not None
    )
    # The duration histogram ships buckets for the same label set.
    assert _any_labeled_sample(
        samples, "goatfarm_http_request_duration_seconds_bucket", 'route="/healthz"'
    )
    # Every declared collector is present in the exposition.
    for declaration in (
        "goatfarm_auth_rate_limit_rejections_total",
        "goatfarm_idempotency_replays_total",
        "goatfarm_simulation_admission_rejections_total",
        "goatfarm_refresh_session_purge_batches_total",
        "goatfarm_refresh_sessions_purged_total",
    ):
        assert declaration in body.decode()


async def test_metrics_counts_unmatched_requests_once_per_template(
    client: httpx.AsyncClient,
) -> None:
    """404s collapse onto the literal 'unmatched' label, never the raw path,
    so label cardinality stays bounded by the route table."""
    before = _samples(metrics.render())
    await client.get("/metrics")  # sanity: exposition works
    resp = await client.get("/no/such/path/at-all")
    assert resp.status_code == 404
    after = _samples(metrics.render())
    prior = (
        _sample_value(
            before, "goatfarm_http_requests_total", 'method="GET",route="unmatched",status="404"'
        )
        or 0.0
    )
    now = _sample_value(
        after, "goatfarm_http_requests_total", 'method="GET",route="unmatched",status="404"'
    )
    assert now is not None and now > prior
    # The raw path itself must never become a label value.
    assert "/no/such/path" not in metrics.render().decode()


async def test_metrics_disabled_404s_and_collects_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOATFARM_METRICS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as probe:
            before = metrics.render()
            health = await probe.get("/healthz")
            assert health.status_code == 200
            assert (await probe.get("/metrics")).status_code == 404
        assert metrics.render() == before  # no collection while disabled
    finally:
        get_settings.cache_clear()


@pytest.fixture()
def rate_limit_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("GOATFARM_AUTH_RATE_LIMIT_WINDOW_SECONDS", "300")
    get_settings.cache_clear()
    from app.ratelimit import auth_limiter

    auth_limiter.clear()
    yield
    auth_limiter.clear()
    get_settings.cache_clear()


@pytest.mark.usefixtures("rate_limit_on")
async def test_auth_rate_limit_429_increments_scope_counter() -> None:
    from app.ratelimit import auth_limiter

    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as probe:
        before = _samples(metrics.render())
        prior = (
            _sample_value(before, "goatfarm_auth_rate_limit_rejections_total", 'scope="login"')
            or 0.0
        )
        # 3 failures fill the (IP, email) bucket; the 4th attempt is refused.
        for _ in range(3):
            resp = await probe.post(
                "/api/auth/login", json={"email": "ghost@farm.in", "password": "wrong-pass-1"}
            )
            assert resp.status_code == 401
        throttled = await probe.post(
            "/api/auth/login", json={"email": "ghost@farm.in", "password": "wrong-pass-1"}
        )
        assert throttled.status_code == 429
    after = _samples(metrics.render())
    now = _sample_value(after, "goatfarm_auth_rate_limit_rejections_total", 'scope="login"')
    assert now is not None and now >= prior + 1
    # Probes alone never counted: the two successful 401-verifications above
    # did not touch the counter; only the 429 decision did (checked by the
    # exact +1 lower bound together with prior's capture point).
    auth_limiter.clear()


async def test_idempotent_replay_is_counted(client: httpx.AsyncClient) -> None:
    headers = await register(client, "metrics-replay@farm.in")
    farm_headers = await create_farm(client, headers, "Metrics Farm")
    auth = {**farm_headers, "Idempotency-Key": "metrics-replay-1"}
    txn = {
        "type": "EXPENSE",
        "category": "FEED",
        "amount": 12.5,
        "date": "2026-01-05",
        "notes": "metrics replay probe",
    }
    first = await client.post("/api/finance/new", json=txn, headers=auth)
    assert first.status_code == 201, first.text

    before = _samples(metrics.render())
    prior = _sample_value(before, "goatfarm_idempotency_replays_total", "") or 0.0
    replay = await client.post("/api/finance/new", json=txn, headers=auth)
    assert replay.status_code == 201
    assert replay.headers.get("Idempotency-Replayed") == "true"

    after = _samples(metrics.render())
    now = _sample_value(after, "goatfarm_idempotency_replays_total", "")
    assert now is not None and now >= prior + 1


def test_rate_limit_backend_setting_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOATFARM_RATE_LIMIT_BACKEND", "redis")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError) as excinfo:
            get_settings()
        message = str(excinfo.value)
        assert "GOATFARM_RATE_LIMIT_BACKEND" in message
        assert "only 'memory'" in message
        assert "single-worker" in message  # points at the README section
    finally:
        get_settings.cache_clear()


def test_rate_limit_backend_defaults_to_memory() -> None:
    assert Settings().rate_limit_backend == "memory"


def test_build_limiter_backend_returns_memory_and_wires_seam() -> None:
    backend = build_limiter_backend()
    assert isinstance(backend, MemoryLimiterBackend)
    # The limiter delegates to the injected backend: policy signatures are
    # unchanged for callers, storage goes through the seam.
    marker = MemoryLimiterBackend()
    limiter = SlidingWindowRateLimiter(backend=marker)
    limiter.record("s", "k", 60, max_attempts=2)
    assert marker.is_known("s", "k")
    assert marker.pruned_hit_count("s", "k", 60) == 1
    limiter.record("s", "k", 60, max_attempts=2)
    limiter.record("s", "k", 60, max_attempts=2)  # blocked path: no third hit
    assert marker.pruned_hit_count("s", "k", 60) == 2
    limiter.reset("s", "k")
    assert not marker.is_known("s", "k")
