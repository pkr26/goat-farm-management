"""Protected exposition of the worker's own registry on its internal listener."""

from __future__ import annotations

import hmac
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from socketserver import ThreadingMixIn
from typing import Any, cast
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from prometheus_client import make_wsgi_app

from .. import metrics
from ..core.config import ScreeningWorkerSettings


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Handler(WSGIRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        # No access log: authentication headers and operational data stay private.
        pass

    def handle(self) -> None:
        self.connection.settimeout(5)
        super().handle()


@contextmanager
def worker_metrics_server(
    settings: ScreeningWorkerSettings, *, host: str = "0.0.0.0", port: int = 9101
) -> Iterator[WSGIServer | None]:
    """Serve only authenticated GET /metrics, closing the listener on shutdown.

    A missing token or disabled collection opens no socket. Compose exposes no
    host port; the scraper joins the private worker network. Binding failures
    propagate at startup instead of silently disabling operational telemetry.
    """
    token = settings.metrics_bearer_token
    if not settings.metrics_enabled or token is None:
        yield None
        return
    expected = f"Bearer {token.get_secret_value()}".encode()
    exposition = make_wsgi_app(registry=metrics.REGISTRY)

    def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        if environ.get("PATH_INFO") != "/metrics" or environ.get("REQUEST_METHOD") != "GET":
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not found\n"]
        supplied = environ.get("HTTP_AUTHORIZATION", "").encode("utf-8")
        if not hmac.compare_digest(supplied, expected):
            start_response("401 Unauthorized", [("Content-Type", "text/plain")])
            return [b"Not authenticated\n"]
        return cast(Iterable[bytes], exposition(environ, start_response))

    server = make_server(host, port, app, server_class=_Server, handler_class=_Handler)
    thread = threading.Thread(target=server.serve_forever, name="worker-metrics", daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
