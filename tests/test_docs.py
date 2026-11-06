"""docs/ quotes the constants it explains and the commands it promises work;
these keep it honest.

The page is the only place a reader can check the working behind a badge, so a
figure there that has drifted from the code is worse than no figure at all.
"""

import http.server
import json
import re
import socket
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import requests

import carbon_badge
import carbon_badge.server
from carbon_badge import Json, format_grams, grams_co2e, runner_power_w
from carbon_badge.base import DEFAULT_GRID_INTENSITY, PUE
from carbon_badge.power import RUNNER_POWER_ESTIMATED, RUNNER_POWER_W

DOCS = Path(__file__).parent.parent / "docs"
ASSUMPTIONS = (DOCS / "assumptions.md").read_text(encoding="utf-8")
GETTING_STARTED = (DOCS / "getting-started.md").read_text(encoding="utf-8")


def _table_after(heading: str) -> list[list[str]]:
    """The body rows of the first Markdown table under `heading`, cells stripped."""
    table = re.search(r"(?:^\|.*\|\n)+", ASSUMPTIONS.split(heading, 1)[1], re.MULTILINE)
    assert table, f"no table under {heading!r}"
    rows = table.group().splitlines()[2:]  # past the header and the |---| rule
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]


def _watts(cell: str) -> float:
    return float(re.findall(r"\d+(?:\.\d+)?", cell)[0])


def test_resulting_table_matches_runner_power_w() -> None:
    """Every row of 'The resulting table' is the code's figure, and no class is missing."""
    rows = {row[0].strip("`"): row for row in _table_after("### The resulting table")}
    assert set(rows) == set(RUNNER_POWER_W)
    for label, (_, _, watts) in rows.items():
        assert _watts(watts) == RUNNER_POWER_W[label], label
        assert ("estimate" in watts) == (label in RUNNER_POWER_ESTIMATED), label


@pytest.mark.parametrize(
    ("cores", "row"),
    [(int(row[0].split("-")[0]), row) for row in _table_after("**Affine, not proportional.**")],
)
def test_affine_table_matches_core_scaling(cores: int, row: list[str]) -> None:
    """The affine column is what a core-counted ubuntu label actually prices at."""
    assert runner_power_w([f"ubuntu-latest-{cores}-cores"]) == pytest.approx(_watts(row[2]), abs=0.05)


def test_quoted_constants_match_code() -> None:
    assert f"`PUE = {PUE:g}`" in ASSUMPTIONS
    assert f"`{DEFAULT_GRID_INTENSITY:g} gCO2e/kWh` by default" in ASSUMPTIONS


def test_documented_serve_command_over_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """getting-started.md's `--serve 8080 --bind 127.0.0.1`, run through main():
    real requests to a local GitHub stand-in, real HTTP out of the badge
    endpoint, and the promised cache — a second hit costs GitHub nothing."""
    assert "--serve 8080 --bind 127.0.0.1" in GETTING_STARTED
    github_hits: list[str] = []

    class FakeGitHub(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            github_hits.append(parsed.path)
            page_one = urllib.parse.parse_qs(parsed.query).get("page", ["1"]) == ["1"]
            if parsed.path.endswith("/runs"):
                payload: Json = {"total_count": 1, "workflow_runs": [{"id": 7}] if page_one else []}
            elif parsed.path.endswith("/jobs"):
                # One hour on the baseline runner.
                job = {
                    "labels": ["ubuntu-latest"],
                    "started_at": "2026-08-01T00:00:00Z",
                    "completed_at": "2026-08-01T01:00:00Z",
                }
                payload = {"jobs": [job] if page_one else []}
            else:
                payload = {"artifacts": []}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

    github = http.server.HTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=github.serve_forever, daemon=True).start()

    # serve() never returns, so keep hold of its server to shut it down.
    badge_servers: list[http.server.HTTPServer] = []

    class Recorded(http.server.HTTPServer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            badge_servers.append(self)

    monkeypatch.setattr(carbon_badge.server.http.server, "HTTPServer", Recorded)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    argv = ["owner/repo", "--token", "t0ken", "--grid-intensity", "480", "--serve", str(port), "--bind", "127.0.0.1"]
    argv += ["--api", f"http://127.0.0.1:{github.server_address[1]}"]
    cli = threading.Thread(target=carbon_badge.main, args=(argv,), daemon=True)
    cli.start()
    try:
        deadline = time.monotonic() + 5
        while not badge_servers and time.monotonic() < deadline:
            time.sleep(0.01)
        url = f"http://127.0.0.1:{port}/badge.json"
        first = requests.get(url, timeout=5)
        hits_after_first = len(github_hits)
        second = requests.get(url, timeout=5)
    finally:
        for server in badge_servers:
            server.shutdown()
        github.shutdown()
        cli.join(timeout=5)

    assert first.status_code == 200
    assert first.headers["Content-Type"] == "application/json"
    badge = first.json()
    assert badge["label"] == "CI carbon"
    assert badge["message"] == f"{format_grams(grams_co2e(60, 480.0))} (estimated)"
    assert hits_after_first > 0
    assert second.json() == badge
    assert len(github_hits) == hits_after_first  # within the 5-min ttl: cached
