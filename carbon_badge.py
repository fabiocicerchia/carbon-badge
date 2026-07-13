#!/usr/bin/env python3
"""carbon-badge — estimate a repo's CI carbon footprint and emit a badge.

Estimates monthly CI minutes from the GitHub Actions API, converts them to
gCO2e using published per-minute runner power draw and a grid intensity
factor, and emits a Shields.io endpoint JSON (or a full SVG) you can serve
from a gist, S3, or GitHub Pages.

  carbon-badge owner/repo --token $GITHUB_TOKEN > badge.json
  # README: ![CI carbon](https://img.shields.io/endpoint?url=<badge.json url>)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import requests

# Assumptions (documented, overridable): a standard GitHub-hosted ubuntu
# runner ~ 12.5 W average draw incl. PUE overhead -> kWh per CI minute,
# times grid intensity (gCO2e/kWh, world average default).
KWH_PER_RUNNER_MINUTE = 12.5 / 1000 / 60
DEFAULT_GRID_INTENSITY = 480.0  # gCO2e/kWh, ~world average

THRESHOLDS = [  # gCO2e/month -> badge color
    (100, "brightgreen"),
    (500, "green"),
    (2000, "yellow"),
    (10000, "orange"),
]


def ci_minutes_last_30d(repo, token, api="https://api.github.com"):
    """Sum billable-ish run durations over the last 30 days (public data)."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    minutes, page = 0.0, 1
    while page <= 10:  # cap pagination defensively
        r = requests.get(
            f"{api}/repos/{repo}/actions/runs",
            params={"per_page": 100, "page": page, "created": f">={since}"},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        runs = r.json().get("workflow_runs", [])
        if not runs:
            break
        for run in runs:
            start = run.get("run_started_at")
            end = run.get("updated_at")
            if start and end:
                dt = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(
                    start.replace("Z", "+00:00")
                )
                minutes += max(dt.total_seconds(), 0) / 60
        page += 1
    return minutes


def grams_co2e(minutes, grid_intensity=DEFAULT_GRID_INTENSITY):
    """Convert CI runner-minutes to gCO2e for the given grid intensity."""
    return minutes * KWH_PER_RUNNER_MINUTE * grid_intensity


def badge_color(grams):
    """Return the Shields badge color for a monthly gCO2e figure."""
    for limit, color in THRESHOLDS:
        if grams < limit:
            return color
    return "red"


def format_grams(grams):
    """Format gCO2e as a human-readable per-month string (g or kg)."""
    return f"{grams / 1000:.1f} kgCO2e/mo" if grams >= 1000 else f"{grams:.0f} gCO2e/mo"


def endpoint_json(grams):
    """Build the Shields.io endpoint JSON payload for the badge."""
    return {
        "schemaVersion": 1,
        "label": "CI carbon",
        "message": format_grams(grams),
        "color": badge_color(grams),
    }


def main(argv=None):
    """CLI entry point: parse args, estimate emissions, emit badge JSON."""
    p = argparse.ArgumentParser(
        prog="carbon-badge",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("repo", help="owner/repo")
    p.add_argument("--token", default=None, help="GitHub token (or GITHUB_TOKEN env)")
    p.add_argument(
        "--grid-intensity",
        type=float,
        default=DEFAULT_GRID_INTENSITY,
        metavar="G",
        help="gCO2e per kWh (default %(default)s, world avg)",
    )
    p.add_argument(
        "--minutes",
        type=float,
        default=None,
        help="skip the API and use this many CI minutes (testing/offline)",
    )
    args = p.parse_args(argv)

    import os

    token = args.token or os.environ.get("GITHUB_TOKEN")
    minutes = args.minutes if args.minutes is not None else ci_minutes_last_30d(args.repo, token)
    grams = grams_co2e(minutes, args.grid_intensity)
    json.dump(endpoint_json(grams), sys.stdout, indent=2)
    print(file=sys.stderr)
    print(f"carbon-badge: {minutes:.0f} CI min/30d ≈ {format_grams(grams)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
