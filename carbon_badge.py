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
import re
import sys
import json
from datetime import datetime, timedelta, timezone

import requests

# Assumptions (documented, overridable): a standard GitHub-hosted ubuntu
# runner ~ 12.5 W average draw incl. PUE overhead -> kWh per CI minute,
# times grid intensity (gCO2e/kWh, world average default).
KWH_PER_RUNNER_MINUTE = 12.5 / 1000 / 60
DEFAULT_GRID_INTENSITY = 480.0  # gCO2e/kWh, ~world average

# Per-runner-type average power draw (W, incl. PUE), from published
# GitHub-hosted runner specs. "ubuntu" is the baseline (2-core); Windows and
# macOS hosts draw more for the same job. Larger runners (N-core labels)
# scale roughly linearly off the 2-core baseline.
RUNNER_POWER_W = {
    "macos": 65.0,
    "windows": 30.0,
    "ubuntu": 12.5,
}
DEFAULT_RUNNER_POWER_W = RUNNER_POWER_W["ubuntu"]

THRESHOLDS = [  # gCO2e/month -> badge color
    (100, "brightgreen"),
    (500, "green"),
    (2000, "yellow"),
    (10000, "orange"),
]


def runner_power_w(labels):
    """Map GitHub Actions job labels (e.g. ["windows-latest"]) to a W draw.

    Larger runners are labelled e.g. "ubuntu-latest-4-cores"; scale the
    matching OS baseline by core count over the 2-core default.
    """
    labels_lower = [label.lower() for label in labels]
    for key, watts in RUNNER_POWER_W.items():
        if any(key in label for label in labels_lower):
            cores = next(
                (int(m.group(1)) for label in labels_lower if (m := re.search(r"-(\d+)-cores?\b", label))),
                None,
            )
            return watts * (cores / 2) if cores else watts
    return DEFAULT_RUNNER_POWER_W


def run_jobs(run_id, repo, token, api="https://api.github.com"):
    """Fetch the jobs (with per-job runner labels/timing) for one run."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(f"{api}/repos/{repo}/actions/runs/{run_id}/jobs", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json().get("jobs", [])


def ci_kwh_last_30d(repo, token, api="https://api.github.com"):
    """Sum kWh across the last 30 days of runs, weighted by runner type.

    Self-hosted jobs are skipped (unknown power draw) and counted so the
    caller can warn about them.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    kwh, skipped_self_hosted, page = 0.0, 0, 1
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
            for job in run_jobs(run["id"], repo, token, api):
                labels = job.get("labels", [])
                if any("self-hosted" in label.lower() for label in labels):
                    skipped_self_hosted += 1
                    continue
                start, end = job.get("started_at"), job.get("completed_at")
                if not (start and end):
                    continue
                dt = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(
                    start.replace("Z", "+00:00")
                )
                hours = max(dt.total_seconds(), 0) / 3600
                kwh += hours * runner_power_w(labels) / 1000
        page += 1
    if skipped_self_hosted:
        print(
            f"carbon-badge: skipped {skipped_self_hosted} self-hosted job(s) (unknown power draw)",
            file=sys.stderr,
        )
    return kwh


def grams_co2e(minutes, grid_intensity=DEFAULT_GRID_INTENSITY):
    """Convert CI runner-minutes to gCO2e for the given grid intensity."""
    return minutes * KWH_PER_RUNNER_MINUTE * grid_intensity


def grams_co2e_kwh(kwh, grid_intensity=DEFAULT_GRID_INTENSITY):
    """Convert kWh to gCO2e for the given grid intensity."""
    return kwh * grid_intensity


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
    if args.minutes is not None:
        grams = grams_co2e(args.minutes, args.grid_intensity)
        detail = f"{args.minutes:.0f} CI min/30d"
    else:
        kwh = ci_kwh_last_30d(args.repo, token)
        grams = grams_co2e_kwh(kwh, args.grid_intensity)
        detail = f"{kwh:.3f} kWh/30d"
    json.dump(endpoint_json(grams), sys.stdout, indent=2)
    print(file=sys.stderr)
    print(f"carbon-badge: {detail} ≈ {format_grams(grams)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
