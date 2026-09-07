"""carbon-badge — estimate a repo's CI carbon footprint and emit a badge.

Sums 30 days of per-job runtime — from jobs that recorded themselves where
they did, and from the GitHub Actions API where they did not — prices each at
its runner's power draw, applies a grid factor, and emits a Shields.io
endpoint JSON you can serve from a gist, S3, or GitHub Pages.

The badge states how the figure was arrived at (measured / partial / estimated
/ rough), because grams from instrumented jobs and grams from a wattage table
are different claims. docs/assumptions.md has every constant and its source.

  carbon-badge owner/repo --token $GITHUB_TOKEN > badge.json
  # README: ![CI carbon](https://img.shields.io/endpoint?url=<badge.json url>)
"""

import argparse
import functools
import http.server
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

# One logger for the process. Diagnostics go here; the badge JSON — the tool's
# actual result — stays on stdout. Configured once in main(); a caller using
# this module as a library gets logging's own default behaviour.
from . import power
from .artifacts import RegionFactors, _expected_markers, artifact_kwh_by_run
from .artifacts import list_artifacts as list_artifacts
from .artifacts import parse_carbon_artifact as parse_carbon_artifact
from .base import BASELINE_MEM_GB as BASELINE_MEM_GB
from .base import BASELINE_VCPU as BASELINE_VCPU
from .base import DEFAULT_GRID_INTENSITY, GRAMS_PER_KILOGRAM, Getter, Json, RunnerWatts, log
from .base import MEM_PER_VCPU_MB as MEM_PER_VCPU_MB
from .base import PUE as PUE
from .base import RECONCILE_EPSILON_G as RECONCILE_EPSILON_G
from .base import Response as Response
from .base import carbon_artifact_slug as carbon_artifact_slug
from .base import job_slug as job_slug
from .ci import _MAX_PAGES, _PAGE_SIZE, _list_runs, _run_kwh
from .ci import run_jobs as run_jobs
from .grid import CI_API_BASE as CI_API_BASE
from .grid import CI_API_MAX_AGE_S as CI_API_MAX_AGE_S
from .grid import IPCC_FUEL_G_PER_KWH as IPCC_FUEL_G_PER_KWH
from .grid import _ci_api_factor as _ci_api_factor
from .grid import _eia_factor as _eia_factor
from .grid import _energy_charts_factor as _energy_charts_factor
from .grid import _uk_factor as _uk_factor
from .grid import live_region_factor as live_region_factor
from .power import (
    _ESTIMATED_USED,
    ANY_RUNNER,
    BADGE_COLOR,
    DEFAULT_RUNNER_POWER_W,
    CiUsage,
    _warn_estimated_classes,
    grid_factor_for,
    parse_runner_watts,
    runner_power_w,
)
from .power import AZURE_REGION_GRID as AZURE_REGION_GRID
from .power import IDLE_FRACTION as IDLE_FRACTION
from .power import RUNNER_POWER_ESTIMATED as RUNNER_POWER_ESTIMATED
from .power import RUNNER_POWER_W as RUNNER_POWER_W
from .power import WATTS_BASE as WATTS_BASE
from .power import WATTS_PER_GB as WATTS_PER_GB
from .power import RunnerWattsError as RunnerWattsError
from .power import RunTotals as RunTotals
from .power import apply_load_factor as apply_load_factor
from .power import watts_from_specs as watts_from_specs
from .reconcile import Divergence as Divergence
from .reconcile import Reconciliation as Reconciliation
from .reconcile import _warn_undeclared_runners, format_reconciliation, grams_co2e, reconcile_last_30d
from .reconcile import grams_co2e_kwh as grams_co2e_kwh
from .reconcile import rows_by_gap as rows_by_gap


# newly launched region degrades rather than breaks.
def ci_kwh_last_30d(  # noqa: PLR0913 — the repo/token/api trio plus independent optional knobs
    repo: str,
    token: str | None,
    *,
    api: str = "https://api.github.com",
    runner_watts: RunnerWatts | None = None,
    use_artifacts: bool = True,
    grid_override: float | None = None,
    eia_key: str | None = None,
) -> CiUsage:
    """kWh across the last 30 days of runs: one API call per run, summing each
    job at its own runner's power draw.

    Accurate but expensive — ~400 requests on a busy repo, against the 1,000
    per hour per repository that GITHUB_TOKEN allows inside Actions. See
    artifact_kwh_last_30d() for the cheap path, which needs one request per 100
    runs and is exact rather than approximate.

    Sampling a subset of runs was tried and removed. Four estimators were
    measured against this function on real repositories and none converged:
    per-run cost is right-skewed, so the sample size needed for a usable answer
    was the same order as the population, and the error was non-monotonic in
    the sample size. Approximating here is not viable; collecting the data at
    source is.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    runs = _list_runs(repo, token, api, since)
    undeclared: dict[str, int] = {}
    factor_for = RegionFactors(eia_key=eia_key, override=grid_override).factor_for

    # Runs whose jobs reported themselves are already measured — exactly, and
    # for free. Only the rest need a request each. Instrumentation therefore
    # pays off from the very first workflow file rather than at some threshold:
    # every job you add removes a request from next week's refresh, and the
    # answer stays correct the whole way through.
    by_run = {}
    if use_artifacts:
        by_run, _ = artifact_kwh_by_run(repo, token, api, runner_watts, factor_for)
    # How many markers a fully instrumented run of each workflow should have.
    expected: dict[int, int] = _expected_markers(runs, by_run, repo, token, api) if by_run else {}

    kwh, grams, total_jobs, used_markers = 0.0, 0.0, 0, 0
    measured_kwh, guessed_kwh = 0.0, 0.0
    # A run priced from the API reports no region, so it takes the repo-level
    # factor: no region to look up means no provider to ask. A partly
    # instrumented repo therefore mixes live-per-region and world-average
    # pricing, which is still strictly better than all-average.
    api_factor = grid_factor_for(None, grid_override)
    for run in runs:
        entry = by_run.get(int(run.get("id", 0)))
        want: int = expected.get(int(run.get("workflow_id", 0)), 0)
        # Trust a run only when it reported every job. A short count means
        # instrumentation is still landing on that workflow, so the markers are
        # discarded and the API priced for the whole run — adding them would
        # double-count the jobs that did report.
        if entry is not None and entry.markers >= want:
            kwh += entry.kwh
            grams += entry.grams
            measured_kwh += entry.kwh
            used_markers += entry.markers
            total_jobs += entry.markers
        else:
            run_kwh, run_jobs_seen, run_guessed = _run_kwh(run, repo, token, api, runner_watts, undeclared)
            kwh += run_kwh
            grams += run_kwh * api_factor
            guessed_kwh += run_guessed
            total_jobs += run_jobs_seen
    if by_run:
        # Counted after the completeness check, not before: a run with a marker
        # is not necessarily a run we could use, and saying otherwise reported
        # full coverage on a half-instrumented workflow.
        trusted = sum(
            1
            for run in runs
            if (entry := by_run.get(int(run.get("id", 0))))
            and entry.markers >= expected.get(int(run.get("workflow_id", 0)), 0)
        )
        log.info(
            "%d/%d run(s) fully self-reported; %d priced from the API. Instrument the remaining jobs to shrink that.",
            trusted,
            len(runs),
            len(runs) - trusted,
        )
    _warn_undeclared_runners(undeclared)
    # used_markers, not every marker read: markers from runs outside the window,
    # or from runs that turned out to be partial, are not part of this figure.
    return CiUsage(kwh, grams, used_markers, total_jobs, measured_kwh, guessed_kwh)


def gitlab_kwh_last_30d(
    project: str,
    token: str | None,
    api: str = "https://gitlab.com/api/v4",
    runner_watts: RunnerWatts | None = None,
    grid_override: float | None = None,
) -> CiUsage:
    """Sum kWh across the last 30 days of GitLab CI jobs.

    GitLab's job list already carries per-job `duration` and `runner` info,
    so unlike GitHub this is a single paginated endpoint. Self-managed runners
    are charged at the baseline and reported rather than skipped, for the same
    reason as the GitHub path — and matched against --runner-watts by tag or
    runner description, GitLab's equivalent of a label.
    """
    since = datetime.now(timezone.utc) - timedelta(days=30)
    headers = {"PRIVATE-TOKEN": token} if token else {}
    project_path = project.replace("/", "%2F")
    kwh = 0.0
    undeclared: dict[str, int] = {}
    page = 1
    guessed_kwh, total_jobs = 0.0, 0
    while page <= _MAX_PAGES:
        response = requests.get(
            f"{api}/projects/{project_path}/jobs",
            params={"per_page": _PAGE_SIZE, "page": page},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        jobs = response.json()
        if not jobs:
            break
        for job in jobs:
            created = job.get("created_at")
            if not created or datetime.fromisoformat(created.replace("Z", "+00:00")) < since:
                continue
            duration = job.get("duration")
            if not duration:
                continue
            runner: Json = job.get("runner") or {}
            # Tags and the runner's description are the only size signal GitLab
            # gives, exactly as labels are on GitHub.
            tags = list(job.get("tag_list") or [])
            if runner.get("description"):
                tags.append(runner["description"])
            # No `if tags` guard: an untagged job is the common GitLab case,
            # and runner_power_w([], {"*": 180}) already resolves the blanket
            # figure. Guarding on tags skipped it entirely.
            watts = runner_power_w(tags, runner_watts)
            guessed = watts is None
            if watts is None:
                watts = DEFAULT_RUNNER_POWER_W
                if runner.get("is_shared") is False:
                    key = ",".join(tags) or "(self-managed, untagged)"
                    undeclared[key] = undeclared.get(key, 0) + 1
            job_kwh = (duration / 3600) * watts / 1000
            kwh += job_kwh
            guessed_kwh += job_kwh if guessed else 0.0
            total_jobs += 1
        page += 1
    else:
        # Loop ran to the cap without a short page: GitLab returns its total in
        # a header rather than the body, so we cannot say by how much — only
        # that it may be short. Silence would read as a quiet month.
        log.warning(
            "read %d pages of jobs and stopped at the cap; the figure may be an undercount.",
            _MAX_PAGES,
        )
    _warn_undeclared_runners(undeclared)
    # record/ is a GitHub Action, so nothing self-reports on GitLab and measured
    # is always zero — but the guessed share still separates "estimated" from
    # "rough", which is the distinction a GitLab user needs most.
    factor = grid_factor_for(None, grid_override)
    return CiUsage(kwh, kwh * factor, 0, total_jobs, 0.0, guessed_kwh)


def _mean_reading(readings: list[float]) -> float:
    """The mean of a day's readings, or a refusal to average nothing."""
    if not readings:
        msg = "history returned no usable readings"
        raise ValueError(msg)
    return sum(readings) / len(readings)


def live_grid_intensity(
    zone: str, token: str | None = None, api: str = "https://api.electricitymap.org/v3", get: Getter = requests.get
) -> float | None:
    """A zone's recent mean carbon factor (gCO2eq/kWh) from Electricity Maps.

    The mean of the past 24 hours, not the instantaneous reading. A single
    instant is a poor multiplier for a 30-day total: grids swing by 2-3x across
    a day, and the refresh runs on a fixed cron — the fleet's fires at 02:17 on
    a Monday — so `latest` would price a whole month at an overnight low, and
    the figure would move week to week on nothing but the clock.

    A day's mean is still not right. Properly, each job would be priced at the
    factor while it actually ran, which needs 30 days of history; Electricity
    Maps puts that behind a paid tier. The 24-hour mean removes the
    time-of-day bias, which is the part that moved the number for no reason.

    Falls back to the instantaneous reading when history is unavailable —
    coverage varies by zone and plan — so a token that can only reach `latest`
    still works, with the caveat above.

    `get` is injectable (defaults to `requests.get`) so callers and tests can
    supply a fake without a live network call.
    """
    headers = {"auth-token": token} if token else {}

    try:
        response = get(
            f"{api}/carbon-intensity/history",
            params={"zone": zone},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        readings = [
            float(h["carbonIntensity"])
            for h in response.json().get("history", [])
            if h.get("carbonIntensity") is not None
        ]
        return _mean_reading(readings)
    except Exception as exc:
        log.warning(
            "24h grid history unavailable (%s); using the instantaneous reading, which prices a month at one moment",
            exc,
        )

    response = get(
        f"{api}/carbon-intensity/latest",
        params={"zone": zone},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return float(response.json()["carbonIntensity"])


def format_grams(grams: float) -> str:
    """Format gCO2e as a human-readable per-month string (g or kg)."""
    if grams >= GRAMS_PER_KILOGRAM:
        return f"{grams / GRAMS_PER_KILOGRAM:.1f} kgCO2e/mo"
    return f"{grams:.0f} gCO2e/mo"


# Share of energy that must be directly measured before the figure is called
# measured rather than estimated. Not 1.0: a cancelled job never reaches its
# recording step, so a fully instrumented repo still sits just under parity.
MEASURED_THRESHOLD = 0.95
# Share of energy priced at the fallback wattage — an unrecognised runner whose
# real draw nobody declared — above which the total is only a rough figure. A
# quarter of the energy resting on a guess is enough to move a badge a colour.
ROUGH_THRESHOLD = 0.25


def confidence(usage: CiUsage) -> str:
    """How far the total can be trusted, in one word.

    Driven by the share of *energy*, not the share of jobs: a single long job on
    an unknown runner undermines the total far more than a dozen short known
    ones. Coverage alone would rank an all-`ubuntu-latest` repo at 0%
    instrumentation the same as one running entirely on unpriced self-hosted
    hardware, and those are not the same claim.

    "measured"  - essentially all the energy came from jobs that timed
                  themselves and reported their own CPU and memory.
    "partial"   - some did, the rest is inferred from the API.
    "rough"     - a meaningful share rests on a fallback wattage for a runner
                  nobody declared, so the number could be out by a lot.
    "estimated" - nothing self-reported, but every runner was recognised;
                  durations are real, only the wattages are modelled.
    """
    if usage.kwh <= 0:
        return "no CI"
    if usage.guessed_kwh / usage.kwh >= ROUGH_THRESHOLD:
        return "rough"
    if usage.measured_kwh / usage.kwh >= MEASURED_THRESHOLD:
        return "measured"
    if usage.measured_kwh > 0:
        return "partial"
    return "estimated"


def endpoint_json(grams: float, usage: CiUsage | None = None) -> Json:
    """Build the Shields.io endpoint JSON payload for the badge.

    The message carries how the figure was arrived at, because "113 gCO2e/mo"
    from fully instrumented jobs and "113 gCO2e/mo" from a wattage table are
    very different claims and a badge that renders them identically is
    misleading. Only a wholly measured figure gets to say so unqualified.

    The grams always cover *all* CI, measured or inferred — a badge that
    silently shrank as instrumentation lagged would reward not instrumenting.
    """
    message = format_grams(grams)
    if usage is not None:
        level = confidence(usage)
        if level == "partial":
            message += f" (~{usage.measured_jobs}/{usage.total_jobs} measured)"
        elif level in ("estimated", "rough"):
            message += f" ({level})"
    return {
        "schemaVersion": 1,
        "label": "CI carbon",
        "message": message,
        "color": BADGE_COLOR,
    }


def _gitlab_estimate(
    args: argparse.Namespace,
    token: str | None,
    runner_watts: RunnerWatts | None,
    grid_intensity: float | None,
) -> tuple[CiUsage, float, str]:
    """The GitLab branch of estimate() -> (usage, grams, detail)."""
    usage = gitlab_kwh_last_30d(
        args.repo,
        token,
        api=args.api or "https://gitlab.com/api/v4",
        runner_watts=runner_watts,
        grid_override=grid_intensity,
    )
    return usage, usage.grams, f"{usage.kwh:.3f} kWh/30d, confidence: {confidence(usage)}"


def _github_estimate(
    args: argparse.Namespace,
    token: str | None,
    runner_watts: RunnerWatts | None,
    grid_intensity: float | None,
) -> tuple[CiUsage, float, str]:
    """The GitHub branch of estimate() -> (usage, grams, detail).

    The detail line carries more than GitLab's because only GitHub has the
    two things worth reporting: what share of the energy self-reported, and
    what share rests on a runner nobody declared.
    """
    usage = ci_kwh_last_30d(
        args.repo,
        token,
        api=args.api or "https://api.github.com",
        runner_watts=runner_watts,
        use_artifacts=not getattr(args, "ignore_self_reported", False),
        grid_override=grid_intensity,
        eia_key=getattr(args, "eia_key", None) or os.environ.get("EIA_API_KEY"),
    )
    guessed_pct = 100 * usage.guessed_kwh / usage.kwh if usage.kwh else 0
    effective = usage.grams / usage.kwh if usage.kwh else 0
    detail = (
        f"{usage.kwh:.3f} kWh/30d at {effective:.0f} gCO2e/kWh effective, "
        f"confidence: {confidence(usage)} "
        f"({usage.measured_jobs}/{usage.total_jobs} job(s) self-reported, "
        f"{guessed_pct:.0f}% of energy on unrecognised runners)"
    )
    return usage, usage.grams, detail


def estimate(args: argparse.Namespace, token: str | None) -> tuple[Json, str]:
    """Run one carbon estimate for the parsed CLI args. Returns (endpoint_json, detail)."""
    _ESTIMATED_USED.clear()
    # None means "nothing declared, use each job's own region where it reported
    # one". Any explicit figure overrides that for every job.
    grid_intensity = args.grid_intensity
    if args.grid_region:
        em_token = args.electricitymaps_token or os.environ.get("ELECTRICITYMAPS_TOKEN")
        grid_intensity = live_grid_intensity(args.grid_region, token=em_token)
        log.info(
            "grid factor for %s = %.0f gCO2e/kWh (24h mean)",
            args.grid_region,
            grid_intensity,
        )
    runner_watts = parse_runner_watts(list(getattr(args, "runner_watts", None) or []))
    usage: CiUsage | None = None
    if args.minutes is not None:
        watts = runner_watts.get(ANY_RUNNER, DEFAULT_RUNNER_POWER_W)
        # No jobs, so no regions: the offline path takes one factor.
        grams = grams_co2e(args.minutes, grid_factor_for(None, grid_intensity), watts)
        detail = f"{args.minutes:.0f} CI min/30d at {watts:g} W"
    elif args.provider == "gitlab":
        usage, grams, detail = _gitlab_estimate(args, token, runner_watts, grid_intensity)
    else:
        usage, grams, detail = _github_estimate(args, token, runner_watts, grid_intensity)
    _warn_estimated_classes()
    return endpoint_json(grams, usage), detail


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


def _build_parser() -> argparse.ArgumentParser:
    """The CLI surface: every flag, its default and its help text."""
    parser = argparse.ArgumentParser(
        prog="carbon-badge",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("repo", help="owner/repo (GitHub) or group/project (GitLab)")
    parser.add_argument(
        "--provider",
        choices=["github", "gitlab"],
        default="github",
        help="CI provider to query (default %(default)s)",
    )
    parser.add_argument(
        "--api",
        default=None,
        help="override API base URL (GitHub Enterprise / self-hosted GitLab)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="API token (or GITHUB_TOKEN / GITLAB_TOKEN env, matching --provider)",
    )
    parser.add_argument(
        "--grid-intensity",
        type=float,
        default=None,
        metavar="G",
        help=(
            "gCO2e per kWh, applied to every job. Unset, each job that reported "
            "its Azure region is priced on that region's grid and the rest fall "
            f"back to {DEFAULT_GRID_INTENSITY:g} (world average). Ignored if "
            "--grid-region is set"
        ),
    )
    parser.add_argument(
        "--grid-region",
        default=None,
        metavar="ZONE",
        help="Electricity Maps zone (e.g. SE, US-CAL-CISO) for live grid intensity",
    )
    parser.add_argument(
        "--electricitymaps-token",
        default=None,
        help="Electricity Maps API token (or ELECTRICITYMAPS_TOKEN env)",
    )
    parser.add_argument(
        "--runner-watts",
        action="append",
        metavar="WATTS|LABEL=WATTS",
        help=(
            "average power draw of your runners, set once. A bare number "
            "(--runner-watts 180) applies to every job; LABEL=WATTS, "
            "repeatable, is only needed if you mix runner types. The API "
            "exposes no CPU/memory for any runner, so size cannot be detected, "
            "only declared"
        ),
    )
    parser.add_argument(
        "--eia-key",
        default=None,
        help=(
            "EIA API key (or EIA_API_KEY env) to price US regions from their "
            "balancing authority's live fuel mix. Free from "
            "eia.gov/opendata. European and UK regions need no key at all"
        ),
    )
    parser.add_argument(
        "--load-factor",
        type=float,
        default=1.0,
        metavar="F",
        help=(
            "average CPU utilisation of these jobs, 0-1. The API exposes no "
            "utilisation, so the default (1.0) prices every job at full load — "
            "which overstates an I/O-bound job by up to ~3x. Only the variable "
            "part of the draw scales: at 0 a machine still draws its idle "
            "power. See docs/assumptions.md"
        ),
    )
    parser.add_argument(
        "--ignore-self-reported",
        action="store_true",
        help=(
            "query the API for every run, ignoring what jobs recorded about "
            "themselves. Slower and no more accurate, so this exists for one "
            "job: reconciling the two paths against each other to see what the "
            "self-reported figure is missing (see docs/getting-started.md)"
        ),
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help=(
            "run BOTH paths over the same runs and report where they disagree, "
            "split into setup time, the watts model and the grid factor. A "
            "diagnostic, not a badge: it prices every run from the API as well "
            "as reading every marker, which is the work the normal path exists "
            "to avoid. See docs/assumptions.md"
        ),
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=None,
        help="skip the API and use this many CI minutes (testing/offline)",
    )
    parser.add_argument(
        "--serve",
        type=int,
        default=None,
        metavar="PORT",
        help="serve /badge.json on this port instead of printing once and exiting",
    )
    parser.add_argument(
        "--bind",
        default=DEFAULT_BIND,
        metavar="ADDR",
        help=(
            f"interface for --serve (default: {DEFAULT_BIND}, which a container needs; use 127.0.0.1 on a shared host)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, estimate emissions, emit badge JSON."""
    # stderr, and the tool's name in the format rather than in every message.
    logging.basicConfig(level=logging.INFO, format="carbon-badge: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not 0 <= args.load_factor <= 1:
        parser.error("--load-factor must be between 0 and 1")
    # The load factor is a property of the run, read by functions several
    # layers down that take no config object. Threading one through purely to
    # carry a single float would be a larger change than the rule prevents.
    #
    # Set on the module that reads it, not on a copy imported here: rebinding a
    # name in this namespace would leave power.LOAD_FACTOR at its default and
    # the flag would quietly do nothing.
    power.LOAD_FACTOR = float(args.load_factor)

    # Validated up front so a typo fails the command rather than surfacing as a
    # traceback from inside estimate() — or, under --serve, on every request.
    try:
        parse_runner_watts(args.runner_watts)
    except ValueError as exc:
        parser.error(f"--runner-watts: {exc}")

    env_var = "GITLAB_TOKEN" if args.provider == "gitlab" else "GITHUB_TOKEN"
    token = args.token or os.environ.get(env_var)

    if args.serve:
        serve(args.serve, args, token, bind=args.bind)
        return 0

    if args.reconcile:
        # Prints to stdout and emits no badge JSON: this is a report to read,
        # not a value to pipe into Shields.
        if args.provider != "github":
            parser.error("--reconcile needs the GitHub artifact markers; not available for GitLab")
        if args.minutes is not None:
            parser.error("--reconcile compares two API paths; --minutes uses neither")
        grid = args.grid_intensity
        if args.grid_region:
            em_token = args.electricitymaps_token or os.environ.get("ELECTRICITYMAPS_TOKEN")
            grid = live_grid_intensity(args.grid_region, token=em_token)
        rec = reconcile_last_30d(
            args.repo,
            token,
            api=args.api or "https://api.github.com",
            runner_watts=parse_runner_watts(list(getattr(args, "runner_watts", None) or [])),
            grid_override=grid,
            eia_key=getattr(args, "eia_key", None) or os.environ.get("EIA_API_KEY"),
        )
        print(format_reconciliation(rec))  # noqa: T201 — the tool's output
        return 0

    badge, detail = estimate(args, token)
    json.dump(badge, sys.stdout, indent=2)
    # Not a diagnostic: a blank line separating the JSON from the summary.
    print(file=sys.stderr)  # noqa: T201 — the tool's output
    log.info("%s ≈ %s", detail, badge["message"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
