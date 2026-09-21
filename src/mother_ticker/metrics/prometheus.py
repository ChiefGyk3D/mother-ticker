# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Renegade Penguin LLC
"""Prometheus text exposition over a stdlib HTTP server.

No prometheus_client dependency: the format is four lines of rules and a
dependency is one more thing to pre-stage for the offline unit. The server
serves a cached rendering; a collector thread refreshes it on a timer, so a
scrape storm cannot hammer gpsd or chronyc.
"""

from __future__ import annotations

import logging
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

from mother_ticker.metrics.model import Metric

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger(__name__)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(value: float) -> str:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def render(metrics: list[Metric]) -> str:
    """Render in name order, with HELP and TYPE once per metric family."""
    families: dict[str, list[Metric]] = {}
    for m in metrics:
        families.setdefault(m.name, []).append(m)
    lines: list[str] = []
    for name in sorted(families):
        group = families[name]
        lines.append(f"# HELP {name} {group[0].help}")
        lines.append(f"# TYPE {name} {group[0].kind}")
        for m in group:
            if m.labels:
                labels = ",".join(f'{k}="{_escape_label(v)}"' for k, v in m.labels)
                lines.append(f"{name}{{{labels}}} {_fmt(m.value)}")
            else:
                lines.append(f"{name} {_fmt(m.value)}")
    return "\n".join(lines) + "\n"


class MetricsCache:
    """Thread-safe holder of the latest rendering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._body = "# no data yet\n"

    def update(self, metrics: list[Metric]) -> None:
        body = render(metrics)
        with self._lock:
            self._body = body

    @property
    def body(self) -> str:
        with self._lock:
            return self._body


def make_handler(cache: MetricsCache) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "mother-ticker"
        sys_version = ""

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] not in ("/metrics", "/"):
                self.send_error(404)
                return
            payload = cache.body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            log.debug("http %s", format % args)

    return Handler


def serve_forever(
    bind: str,
    port: int,
    cache: MetricsCache,
    *,
    refresh: Callable[[], list[Metric]],
    interval_s: float,
    stop: threading.Event,
) -> None:
    """Run the collector loop in this thread and the HTTP server in a daemon thread."""
    server = ThreadingHTTPServer((bind, port), make_handler(cache))
    server.daemon_threads = True
    http_thread = threading.Thread(target=server.serve_forever, daemon=True, name="metrics-http")
    http_thread.start()
    log.info("prometheus exporter listening on %s:%d", bind, port)
    try:
        while not stop.is_set():
            try:
                cache.update(refresh())
            except Exception:
                log.exception("metrics refresh failed")
            stop.wait(interval_s)
    finally:
        server.shutdown()
        server.server_close()
