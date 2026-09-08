"""The --serve endpoint: one handler, one cached estimate."""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import time
from collections.abc import Callable
from typing import Any

from .base import Json, log
from .report import estimate


class BadgeHandler(http.server.BaseHTTPRequestHandler):
    """Serve /badge.json from `compute()`, re-computing at most once per `ttl`.

    `cache` is owned by the caller and shared across every request: http.server
    builds a fresh handler per connection, so anything kept on `self` would be
    a cache of one request.
    """

    def __init__(
        self,
        compute: Callable[[], Json],
        ttl: int,
        cache: dict[str, Any],
        # BaseHTTPRequestHandler's own (request, client_address, server), passed
        # through untouched — typing them here would restate the stdlib's.
        *args: Any,  # noqa: ANN401
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        self._compute = compute
        self._ttl = ttl
        self._cache = cache
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path not in ("/", "/badge.json"):
            self.send_response(404)
            self.end_headers()
            return
        now = time.monotonic()
        if self._cache["t"] is None or now - self._cache["t"] > self._ttl:
            self._cache["body"] = json.dumps(self._compute()).encode()
            self._cache["t"] = now
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(self._cache["body"])

    # Overrides BaseHTTPRequestHandler.log_message(format, *args): the name and
    # the variadic tail are the base class's, and this one drops the line anyway.
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ANN401
        pass


def badge_handler(compute: Callable[[], Json], ttl: int = 300) -> type[http.server.BaseHTTPRequestHandler]:
    """A BadgeHandler bound to one compute() and one shared cache."""
    # `t=None` means "never computed yet" — not 0.0, since time.monotonic()'s
    # epoch is arbitrary (e.g. near-zero shortly after a container boots), so
    # `now - 0.0 > ttl` can be false on the very first request too, leaving
    # cache["body"] permanently empty.
    cache: dict[str, Any] = {"t": None, "body": b""}
    return functools.partial(BadgeHandler, compute, ttl, cache)  # pyright: ignore[reportReturnType]


# The default has to be every interface: the documented --serve deployment is a
# container with a published port (see examples/ci-platforms/), and 127.0.0.1
# inside one is unreachable from outside it. That does mean a process holding a
# CI token is listening on every interface of whatever host runs it, so --bind
# exists for anyone running it directly on a machine that has others.
DEFAULT_BIND = "0.0.0.0"  # noqa: S104 — deliberate, see above


def _logged_estimate(args: argparse.Namespace, token: str | None) -> Json:
    """One estimate, with the same one-line summary the CLI prints."""
    badge, detail = estimate(args, token)
    log.info("%s ≈ %s", detail, badge["message"])
    return badge


def serve(port: int, args: argparse.Namespace, token: str | None, ttl: int = 300, bind: str = DEFAULT_BIND) -> None:
    """Serve the badge JSON at /badge.json on bind:port.

    Recomputes at most once every `ttl` seconds (default 5 min) so repeated
    hits (Shields refreshes the endpoint on every badge view) don't hammer
    the CI/grid APIs.

    Binds every interface by default so the container deployment works; pass
    `bind` (--bind) to narrow it. The endpoint is unauthenticated and the
    process holds a CI token, so on a shared host that is worth doing.
    """

    log.info("serving /badge.json on %s:%d (ttl %ds)", bind, port, ttl)
    handler = badge_handler(lambda: _logged_estimate(args, token), ttl)
    # 0.0.0.0 by default because the usual deployment is a container whose
    # port is published; --bind exists for anyone who wants loopback.
    http.server.HTTPServer((bind, port), handler).serve_forever()
