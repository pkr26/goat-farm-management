"""Mutation-campaign gap tests: security-relevant module constants and the
origin/IP-key normalization helpers (2026-09-30).

Module-level ints drifted freely (±1) because no test pins them literally;
the browser-origin authority normalization and the clientless-IP fallback
had untested branch behavior. All pinned exactly here.
"""

from starlette.requests import Request

import app.api._run_limits as run_limits
import app.deps as deps
from app.api.auth import _browser_origin
from app.services._common import REBREED_AFTER_RESTING_DAYS


def test_security_module_constants_pinned() -> None:
    # The concurrency budget that stops one tenant from looping max-cost
    # simulation runs forever: 5-minute sliding window, 650k engine units.
    assert run_limits._RUN_BUDGET_WINDOW_SECONDS == 300
    assert run_limits._RUN_BUDGET_UNITS == 650_000
    # Invalid-token callers deliberately classify a fresh token before
    # consulting their shared IP budget: the IP limit is 10x the per-key one.
    assert deps.INVALID_TOKEN_IP_LIMIT_MULTIPLIER == 10
    # A doe's RESTING exit schedules her rebreeding prompt 30 days out.
    assert REBREED_AFTER_RESTING_DAYS == 30


def test_browser_origin_drops_default_ports_keeps_explicit() -> None:
    assert _browser_origin("http://example.com:80/app") == "http://example.com"
    assert _browser_origin("https://example.com:443/app") == "https://example.com"
    # Non-default ports are significant and must survive normalization.
    assert _browser_origin("http://example.com:8080/app") == "http://example.com:8080"
    assert _browser_origin("https://example.com:8443/app") == "https://example.com:8443"
    # No port at all stays clean.
    assert _browser_origin("https://example.com/app") == "https://example.com"
    # Bracketed IPv6 authorities are preserved.
    assert _browser_origin("http://[::1]:8080/x") == "http://[::1]:8080"
    assert _browser_origin("http://[::1]/x") == "http://[::1]"
    # Non-HTTP schemes and hostless URLs normalize to the empty origin.
    assert _browser_origin("ftp://example.com/x") == ""
    assert _browser_origin("file:///etc/passwd") == ""


def test_invalid_token_rate_error_survives_clientless_request() -> None:
    """The rate-limit IP key falls back to 'unknown' when the ASGI scope
    carries no client (internal health checks, some proxies): the request
    must still produce the 429, never a 500."""
    request = Request(scope={"type": "http", "method": "GET", "path": "/", "headers": []})
    assert request.client is None
    error = deps.invalid_token_rate_error(request, "access-token-invalid")
    assert error.status_code == 429
