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
import http.server
import re
import sys
import json
import time
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
                (
                    int(m.group(1))
                    for label in labels_lower
                    if (m := re.search(r"-(\d+)-cores?\b", label))
                ),
                None,
            )
            return watts * (cores / 2) if cores else watts
    return DEFAULT_RUNNER_POWER_W


def run_jobs(run_id, repo, token, api="https://api.github.com"):
    """Fetch the jobs (with per-job runner labels/timing) for one run."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(
        f"{api}/repos/{repo}/actions/runs/{run_id}/jobs", headers=headers, timeout=30
    )
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
                dt = datetime.fromisoformat(
                    end.replace("Z", "+00:00")
                ) - datetime.fromisoformat(start.replace("Z", "+00:00"))
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


def gitlab_kwh_last_30d(project, token, api="https://gitlab.com/api/v4"):
    """Sum kWh across the last 30 days of GitLab CI jobs (ubuntu-baseline power).

    GitLab's job list already carries per-job `duration` and `runner` info,
    so unlike GitHub this is a single paginated endpoint. Jobs on a
    project/group (non-shared) runner are skipped (unknown power draw).
    """
    since = datetime.now(timezone.utc) - timedelta(days=30)
    headers = {"PRIVATE-TOKEN": token} if token else {}
    project_path = project.replace("/", "%2F")
    kwh, skipped_self_hosted, page = 0.0, 0, 1
    while page <= 10:  # cap pagination defensively
        r = requests.get(
            f"{api}/projects/{project_path}/jobs",
            params={"per_page": 100, "page": page},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        jobs = r.json()
        if not jobs:
            break
        for job in jobs:
            created = job.get("created_at")
            if (
                not created
                or datetime.fromisoformat(created.replace("Z", "+00:00")) < since
            ):
                continue
            runner = job.get("runner") or {}
            if runner and runner.get("is_shared") is False:
                skipped_self_hosted += 1
                continue
            duration = job.get("duration")
            if duration:
                kwh += (duration / 3600) * DEFAULT_RUNNER_POWER_W / 1000
        page += 1
    if skipped_self_hosted:
        print(
            f"carbon-badge: skipped {skipped_self_hosted} self-hosted job(s) (unknown power draw)",
            file=sys.stderr,
        )
    return kwh


def live_grid_intensity(
    zone, token=None, api="https://api.electricitymap.org/v3", get=requests.get
):
    """Fetch a zone's current carbon intensity (gCO2eq/kWh) from Electricity Maps.

    `get` is injectable (defaults to `requests.get`) so callers/tests can
    supply a fake without a live network call.
    """
    headers = {"auth-token": token} if token else {}
    r = get(
        f"{api}/carbon-intensity/latest",
        params={"zone": zone},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    return float(r.json()["carbonIntensity"])


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


def estimate(args, token):
    """Run one carbon estimate for the parsed CLI args. Returns (endpoint_json, detail)."""
    import os

    grid_intensity = args.grid_intensity
    if args.grid_region:
        em_token = args.electricitymaps_token or os.environ.get("ELECTRICITYMAPS_TOKEN")
        grid_intensity = live_grid_intensity(args.grid_region, token=em_token)
        print(
            f"carbon-badge: live grid intensity for {args.grid_region} = {grid_intensity:.0f} gCO2e/kWh",
            file=sys.stderr,
        )
    if args.minutes is not None:
        grams = grams_co2e(args.minutes, grid_intensity)
        detail = f"{args.minutes:.0f} CI min/30d"
    elif args.provider == "gitlab":
        kwh = gitlab_kwh_last_30d(
            args.repo, token, api=args.api or "https://gitlab.com/api/v4"
        )
        grams = grams_co2e_kwh(kwh, grid_intensity)
        detail = f"{kwh:.3f} kWh/30d"
    else:
        kwh = ci_kwh_last_30d(
            args.repo, token, api=args.api or "https://api.github.com"
        )
        grams = grams_co2e_kwh(kwh, grid_intensity)
        detail = f"{kwh:.3f} kWh/30d"
    return endpoint_json(grams), detail


def badge_handler(compute, ttl=300):
    """Build a request handler serving /badge.json from compute(), cached for ttl seconds."""
    cache = {"t": 0.0, "body": b""}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/badge.json"):
                self.send_response(404)
                self.end_headers()
                return
            now = time.monotonic()
            if now - cache["t"] > ttl:
                cache["body"] = json.dumps(compute()).encode()
                cache["t"] = now
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(cache["body"])

        def log_message(self, *a):
            pass

    return Handler


def serve(port, args, token, ttl=300):
    """Serve the badge JSON at /badge.json on localhost:port.

    Recomputes at most once every `ttl` seconds (default 5 min) so repeated
    hits (Shields refreshes the endpoint on every badge view) don't hammer
    the CI/grid APIs.
    """

    def compute():
        data, detail = estimate(args, token)
        print(f"carbon-badge: {detail} ≈ {data['message']}", file=sys.stderr)
        return data

    print(f"carbon-badge: serving /badge.json on :{port} (ttl {ttl}s)", file=sys.stderr)
    http.server.HTTPServer(
        ("0.0.0.0", port), badge_handler(compute, ttl)
    ).serve_forever()


def main(argv=None):
    """CLI entry point: parse args, estimate emissions, emit badge JSON."""
    p = argparse.ArgumentParser(
        prog="carbon-badge",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("repo", help="owner/repo (GitHub) or group/project (GitLab)")
    p.add_argument(
        "--provider",
        choices=["github", "gitlab"],
        default="github",
        help="CI provider to query (default %(default)s)",
    )
    p.add_argument(
        "--api",
        default=None,
        help="override API base URL (GitHub Enterprise / self-hosted GitLab)",
    )
    p.add_argument(
        "--token",
        default=None,
        help="API token (or GITHUB_TOKEN / GITLAB_TOKEN env, matching --provider)",
    )
    p.add_argument(
        "--grid-intensity",
        type=float,
        default=DEFAULT_GRID_INTENSITY,
        metavar="G",
        help="gCO2e per kWh (default %(default)s, world avg); ignored if --grid-region is set",
    )
    p.add_argument(
        "--grid-region",
        default=None,
        metavar="ZONE",
        help="Electricity Maps zone (e.g. SE, US-CAL-CISO) for live grid intensity",
    )
    p.add_argument(
        "--electricitymaps-token",
        default=None,
        help="Electricity Maps API token (or ELECTRICITYMAPS_TOKEN env)",
    )
    p.add_argument(
        "--minutes",
        type=float,
        default=None,
        help="skip the API and use this many CI minutes (testing/offline)",
    )
    p.add_argument(
        "--serve",
        type=int,
        default=None,
        metavar="PORT",
        help="serve /badge.json on this port instead of printing once and exiting",
    )
    args = p.parse_args(argv)

    import os

    env_var = "GITLAB_TOKEN" if args.provider == "gitlab" else "GITHUB_TOKEN"
    token = args.token or os.environ.get(env_var)

    if args.serve:
        serve(args.serve, args, token)
        return 0

    data, detail = estimate(args, token)
    json.dump(data, sys.stdout, indent=2)
    print(file=sys.stderr)
    print(f"carbon-badge: {detail} ≈ {data['message']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
