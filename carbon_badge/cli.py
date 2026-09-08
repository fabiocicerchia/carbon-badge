"""The command line."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import power
from .base import DEFAULT_GRID_INTENSITY, log
from .power import parse_runner_watts
from .providers import live_grid_intensity
from .reconcile import format_reconciliation, reconcile_last_30d
from .report import estimate
from .server import DEFAULT_BIND, serve


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
