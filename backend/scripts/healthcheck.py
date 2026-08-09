#!/usr/bin/env python3
"""Container readiness probe that remains compatible with TrustedHost.

Production deliberately refuses loopback Host headers.  Docker still connects
to the loopback socket, but this probe sends one configured, allowed virtual
host so a correctly hardened container is not marked unhealthy.
"""

from __future__ import annotations

import http.client

from app.core.config import get_settings


def _probe_host() -> str:
    allowed = get_settings().allowed_hosts
    if not allowed:  # Settings already rejects this in production.
        raise RuntimeError("GOATFARM_ALLOWED_HOSTS is empty")
    configured = allowed[0].strip()
    if configured.startswith("*."):
        # Starlette's wildcard requires a subdomain.  Use a synthetic one;
        # DNS is irrelevant because the TCP connection stays on 127.0.0.1.
        return "healthcheck" + configured[1:]
    return configured


def main() -> None:
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=4)
    try:
        connection.request("GET", "/readyz", headers={"Host": _probe_host()})
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise SystemExit(f"readiness returned HTTP {response.status}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
