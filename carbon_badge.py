#!/usr/bin/env python3
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
import http.server
import json
import os
import re
import sys
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import requests

# --------------------------------------------------------------------------
# Where these wattages come from. See docs/assumptions.md for the full working.
#
# Base figures are the Cloud Energy power curves shipped by Green Coding
# Solutions' Eco-CI, modelled from SPECpower data for the exact machines GitHub
# runs jobs on. They are machine draw at full CPU; PUE is applied separately
# below so the datacentre overhead is visible rather than baked in.
#
#   4-core EPYC 7763 shared (GitHub Linux/Windows):  1.76 W idle -> 8.18 W @100%
#   Mac mini M1 (GitHub macOS):                      4.45 W idle -> 15.53 W @100%
#
# https://github.com/green-coding-solutions/eco-ci-energy-estimation
#   /blob/main/machine-power-data/
# --------------------------------------------------------------------------

# Hyperscale operators publish 1.1-1.2; 1.15 is mid-range. Applied to machine
# draw to get wall power.
PUE = 1.15

# Public repos have had 4-vCPU / 16 GiB standard runners since December 2023,
# not the 2-vCPU / 7 GiB machines older estimates assume. Larger runners scale
# off this, so it must match the baseline the wattages above describe.
# https://github.blog/news-insights/product-news/
#   github-hosted-runners-double-the-power-for-open-source/
BASELINE_VCPU = 4

DEFAULT_GRID_INTENSITY = 480.0  # gCO2e/kWh, ~world average

# Per-runner-type average power draw (W, incl. PUE), from published
# GitHub-hosted runner specs. "ubuntu" is the baseline (2-core); Windows and
# macOS hosts draw more for the same job. Larger runners (N-core labels)
# scale roughly linearly off the 2-core baseline.
# Checked in order, first substring match wins, so the specific entries must
# come before the generic ones: "ubuntu-22.04-arm" contains "ubuntu", and a GPU
# runner's label normally names its OS too. Every figure here is a default —
# --runner-watts arm=... / gpu=... overrides any of them.
RUNNER_POWER_W = {
    # T4-class accelerator (~70 W TDP) plus its host. The weakest figure here —
    # no measured curve exists for GitHub's GPU runners, so declare your own
    # with --runner-watts gpu=<watts> if you use them seriously. Flat, not
    # core-scaled: the accelerator dominates and does not grow with vCPUs.
    "gpu": round(130.0 * PUE, 1),
    # ARM (Cobalt/Graviton class): no measured curve either, taken as ~40% below
    # the x86 baseline. Deliberately not the "3-4x more efficient" claim that
    # circulates — AWS's own published figure is up to 60% less energy for equal
    # work, and independent benchmarks land nearer 1.5-2.5x.
    "arm": round(8.18 * 0.6 * PUE, 1),
    # Mac mini M1, whole machine (dedicated hardware, not a shared VM slice).
    # Apple silicon is far more efficient than the 65 W this used to assume.
    "macos": round(15.53 * PUE, 1),
    # Same Azure hardware as Linux, so the same draw. GitHub bills Windows at 2x
    # and macOS at 10x, but those are *price* multipliers and say nothing about
    # power — using them as energy factors would be wrong by a wide margin.
    "windows": round(8.18 * PUE, 1),
    "ubuntu": round(8.18 * PUE, 1),
}
# Core count scales the CPU share; on a GPU runner the accelerator is fixed and
# dominates, so scaling it by cores would double-count.
_NO_CORE_SCALING = {"gpu"}
DEFAULT_RUNNER_POWER_W = RUNNER_POWER_W["ubuntu"]

# What a pass measured, and how much of it came from jobs that reported
# themselves. The badge shows the ratio so a reader can tell a fully measured
# figure from a partly inferred one.
CiUsage = namedtuple("CiUsage", "kwh measured_jobs total_jobs measured_kwh guessed_kwh")

THRESHOLDS = [  # gCO2e/month -> badge color
    (100, "brightgreen"),
    (500, "green"),
    (2000, "yellow"),
    (10000, "orange"),
]


# Core counts appear in labels in several shapes: GitHub's own convention is
# "ubuntu-latest-4-cores", but "linux-x64-16core" and "...-8vcpu" are both
# common in hand-named larger runners. Matching only the first shape priced a
# 16-core runner as a 2-core one.
_CORES_RE = re.compile(r"(\d+)\s*-?(?:cores?|vcpus?)\b")


# Stands for "every runner in this repo". Not a valid Actions label (labels
# can't contain "*"), so it can never collide with a real one.
ANY_RUNNER = "*"


def parse_runner_watts(pairs):
    """["180"] -> {"*": 180.0}; ["my-builder=180"] -> {"my-builder": 180.0}.

    A bare number is the common case and the whole point of the flag: nearly
    every repo runs one runner type, so declaring it is a single value set once
    when the workflow is first added. The LABEL=WATTS form is only for repos
    that genuinely mix runner types.

    Raises ValueError on anything malformed rather than silently dropping it: a
    typo'd override that quietly does nothing would leave the badge wrong in
    exactly the case the user was trying to correct.
    """
    table = {}
    for pair in pairs or []:
        pair = pair.strip()
        if not pair:
            continue
        label, sep, value = pair.partition("=")
        if not sep:
            # Bare number: applies to every runner.
            label, value = ANY_RUNNER, pair
        label = label.strip().lower()
        if not label:
            raise ValueError(f"expected LABEL=WATTS or a bare number, got {pair!r}")
        try:
            watts = float(value)
        except ValueError:
            raise ValueError(f"{value!r} is not a number, in {pair!r}") from None
        if watts <= 0:
            raise ValueError(f"watts must be positive, got {watts} in {pair!r}")
        table[label] = watts
    return table


def runner_power_w(labels, overrides=None):
    """Map job labels (e.g. ["windows-latest"]) to a W draw, or None if unknown.

    Resolution order: an exact --runner-watts label, then a substring one (so
    one entry can price a whole family, e.g. "gpu=320"), then the built-in
    GitHub-hosted table scaled by any core count in the label.

    None means "cannot be determined" rather than a guess. The Actions API
    exposes no CPU or memory for any runner, hosted or self-hosted — labels are
    the only signal there is, and they are arbitrary user-chosen text. So a
    self-hosted or custom-named runner can only be priced by declaring it, and
    the caller needs to be able to tell that apart from a known runner to warn.
    """
    labels_lower = [label.lower() for label in labels]
    overrides = overrides or {}
    for label in labels_lower:
        if label in overrides:
            return overrides[label]
    for key, watts in overrides.items():
        if key != ANY_RUNNER and any(key in label for label in labels_lower):
            return watts
    # A declared blanket figure beats the built-in guess table — the developer
    # knows what their runners are and the API does not.
    if ANY_RUNNER in overrides:
        return overrides[ANY_RUNNER]
    for key, watts in RUNNER_POWER_W.items():
        if any(key in label for label in labels_lower):
            if key in _NO_CORE_SCALING:
                return watts
            cores = next(
                (int(m.group(1)) for label in labels_lower if (m := _CORES_RE.search(label))),
                None,
            )
            # Scaled off BASELINE_VCPU, not a hardcoded 2: the figures above
            # describe a 4-vCPU machine, so dividing by 2 would double every
            # larger runner.
            return watts * (cores / BASELINE_VCPU) if cores else watts
    return None


def _hours(start, end):
    """Hours between two ISO timestamps, or 0 if either is missing."""
    if not (start and end):
        return 0.0
    dt = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(
        start.replace("Z", "+00:00")
    )
    return max(dt.total_seconds(), 0) / 3600


def run_jobs(run_id, repo, token, api="https://api.github.com"):
    """Fetch the jobs (with per-job runner labels/timing) for one run."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    # per_page=100, not the API's default of 30: a matrix wider than 30 jobs
    # was silently truncated, and this now also feeds the confidence ratio.
    jobs, page = [], 1
    while page <= _MAX_PAGES:
        r = requests.get(
            f"{api}/repos/{repo}/actions/runs/{run_id}/jobs",
            params={"per_page": 100, "page": page},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json().get("jobs", [])
        jobs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return jobs


# Pagination backstop. Generous — a repo doing more than this in 30 days is
# unusual — but a cap that silently drops data would read as a quiet month
# rather than a truncated query, so hitting it is always reported.
_MAX_PAGES = 20


def _warn_if_truncated(kind, fetched, total, hit_page_cap):
    """A silent shortfall understates the badge and looks like good news.

    Two different ceilings can cause it and they need different advice: our own
    page cap, which raising _MAX_PAGES fixes, and GitHub's hard 1000-result
    limit on a listing, which it does not.
    """
    if total is None or fetched >= total:
        return
    cause = (
        f"hit the local {_MAX_PAGES}-page cap, so raising _MAX_PAGES would help"
        if hit_page_cap
        else "GitHub caps this listing at 1000 results; a narrower window is the "
        "only way to see the rest"
    )
    print(
        f"carbon-badge: only read {fetched} of {total} {kind}(s) — {cause}. "
        "The figure is an undercount.",
        file=sys.stderr,
    )


def _list_runs(repo, token, api, since):
    """Every run in the window — cheap, 100 per request."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    runs, page, total = [], 1, None
    while page <= _MAX_PAGES:
        r = requests.get(
            f"{api}/repos/{repo}/actions/runs",
            params={"per_page": 100, "page": page, "created": f">={since}"},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
        total = payload.get("total_count", total)
        batch = payload.get("workflow_runs", [])
        runs.extend(batch)
        # A short page is the last page — stop rather than spend a request
        # confirming the next one is empty.
        if len(batch) < 100:
            break
        page += 1
    _warn_if_truncated("run", len(runs), total, page > _MAX_PAGES)
    return runs


def _run_kwh(run, repo, token, api, runner_watts, undeclared):
    """Exact kWh for one run: sum its jobs, each at its own runner's draw.

    Costs one API call. Jobs on a runner this cannot price — self-hosted, or
    any custom label — are charged at the baseline and recorded in
    `undeclared`, rather than skipped. Skipping scored them as zero, which made
    moving a build onto the biggest machine you own *improve* the badge.
    """
    kwh, jobs, guessed_kwh = 0.0, 0, 0.0
    for job in run_jobs(run["id"], repo, token, api):
        labels = job.get("labels", [])
        start, end = job.get("started_at"), job.get("completed_at")
        # Skipped only when the job never ran (queued/in-progress). A genuine
        # zero-duration job still gets its runner recorded, so an unrecognised
        # one is reported even if that particular job was instant.
        if not (start and end):
            continue
        hours = _hours(start, end)
        watts = runner_power_w(labels, runner_watts)
        guessed = watts is None
        if guessed:
            watts = DEFAULT_RUNNER_POWER_W
            key = ",".join(labels) or "(no labels)"
            undeclared[key] = undeclared.get(key, 0) + 1
        job_kwh = hours * watts / 1000
        kwh += job_kwh
        # Tracked in energy, not job count: one long job on an unknown runner
        # undermines the total far more than a dozen short known ones.
        guessed_kwh += job_kwh if guessed else 0.0
        jobs += 1
    return kwh, jobs, guessed_kwh


def ci_kwh_last_30d(
    repo, token, api="https://api.github.com", runner_watts=None, use_artifacts=True
):
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
    undeclared = {}

    # Runs whose jobs reported themselves are already measured — exactly, and
    # for free. Only the rest need a request each. Instrumentation therefore
    # pays off from the very first workflow file rather than at some threshold:
    # every job you add removes a request from next week's refresh, and the
    # answer stays correct the whole way through.
    by_run = {}
    if use_artifacts:
        by_run, _ = artifact_kwh_by_run(repo, token, api, runner_watts)
    # How many markers a fully instrumented run of each workflow should have.
    expected = _expected_markers(runs, by_run, repo, token, api) if by_run else {}

    kwh, total_jobs, used_markers = 0.0, 0, 0
    measured_kwh, guessed_kwh = 0.0, 0.0
    for run in runs:
        entry = by_run.get(run.get("id"))
        want = expected.get(run.get("workflow_id"), 0)
        # Trust a run only when it reported every job. A short count means
        # instrumentation is still landing on that workflow, so the markers are
        # discarded and the API priced for the whole run — adding them would
        # double-count the jobs that did report.
        if entry is not None and entry[1] >= want:
            kwh += entry[0]
            measured_kwh += entry[0]
            used_markers += entry[1]
            total_jobs += entry[1]
        else:
            run_kwh, run_jobs_seen, run_guessed = _run_kwh(
                run, repo, token, api, runner_watts, undeclared
            )
            kwh += run_kwh
            guessed_kwh += run_guessed
            total_jobs += run_jobs_seen
    if by_run:
        # Counted after the completeness check, not before: a run with a marker
        # is not necessarily a run we could use, and saying otherwise reported
        # full coverage on a half-instrumented workflow.
        trusted = sum(
            1
            for r in runs
            if (e := by_run.get(r.get("id"))) and e[1] >= expected.get(r.get("workflow_id"), 0)
        )
        print(
            f"carbon-badge: {trusted}/{len(runs)} run(s) fully self-reported; "
            f"{len(runs) - trusted} priced from the API. Instrument the remaining "
            "jobs to shrink that.",
            file=sys.stderr,
        )
    _warn_undeclared_runners(undeclared)
    # used_markers, not every marker read: markers from runs outside the window,
    # or from runs that turned out to be partial, are not part of this figure.
    return CiUsage(kwh, used_markers, total_jobs, measured_kwh, guessed_kwh)


# Jobs self-report into an artifact *name*, which the artifacts API returns in
# its listing — so reading a month of exact per-job measurements costs one
# request per 100 artifacts and downloads nothing.
#
#   carbon.v1.<seconds>.<vcpu>.<memMB>.<platform>.<slug>
#
# <platform> is one of the RUNNER_POWER_W keys, because CPU and memory alone do
# not determine draw: the same 4 vCPU / 16 GiB reading means a very different
# wattage on Apple silicon than on a shared x86 VM.
#
# Versioned because the name is the wire format. Artifact names may not contain
# " : < > | * ? \ /, which is why the separator is a dot and the job slug is
# sanitised at the source.
_ARTIFACT_RE = re.compile(r"^carbon\.v1\.(\d+)\.(\d+)\.(\d+)\.([a-z]+)\.")

# Linear model for a self-reported machine, anchored on the same Eco-CI curve:
# a 4-vCPU / 16 GiB GitHub runner at 8.18 W machine draw x PUE ~= 9.4 W.
#   1.2 + 1.6*4 + 0.11*16 = 9.36
# Crude — real draw swings 1.76-8.18 W with utilisation, which we cannot see —
# but applied to the machine's *actual* vCPU count and memory rather than to a
# guess scraped from a label.
#
# It was previously fitted to "2 vCPU / 7 GB = 12.5 W", which was wrong twice
# over: public repos have had 4-vCPU/16 GiB runners since Dec 2023, and 12.5 W
# was itself ~1.5x the measured full-load figure. That combination priced a
# real 4-vCPU runner at 24.3 W, about 3x too high.
WATTS_BASE = 1.2
WATTS_PER_VCPU = 1.6
WATTS_PER_GB = 0.11


def watts_from_specs(vcpu, mem_mb, platform="ubuntu"):
    """Power draw from a machine's actual CPU count, memory and platform.

    The linear model is fitted to x86 shared VMs and does not transfer. A
    Mac mini M1 reporting 3 vCPU / 7 GiB comes out at 6.8 W through it, against
    a measured 15.53 W — so the self-reported path would have been 2.6x *worse*
    than the label lookup it replaces. Apple silicon is dedicated hardware whose
    draw does not track a vCPU slice, so it uses the measured figure directly;
    ARM scales its own baseline rather than borrowing the x86 one.
    """
    if platform == "macos":
        return RUNNER_POWER_W["macos"]
    if platform == "arm":
        return RUNNER_POWER_W["arm"] * (vcpu / BASELINE_VCPU)
    return WATTS_BASE + WATTS_PER_VCPU * vcpu + WATTS_PER_GB * (mem_mb / 1024)


def parse_carbon_artifact(name):
    """ "carbon.v1.142.4.16384.ubuntu.build" -> (142.0 s, 4 vcpu, 16384 MB, "ubuntu").

    None for anything that is not one of ours, so a repo's normal build
    artifacts sitting in the same listing are simply ignored.
    """
    match = _ARTIFACT_RE.match(name or "")
    if not match:
        return None
    seconds, vcpu, mem_mb, platform = match.groups()
    return (float(seconds), int(vcpu), int(mem_mb), platform)


def list_artifacts(repo, token, api="https://api.github.com"):
    """Every non-expired artifact, 100 per request."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    artifacts, page, total = [], 1, None
    while page <= _MAX_PAGES:
        r = requests.get(
            f"{api}/repos/{repo}/actions/artifacts",
            params={"per_page": 100, "page": page},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        payload = r.json()
        total = payload.get("total_count", total)
        batch = payload.get("artifacts", [])
        artifacts.extend(batch)
        if len(batch) < 100:  # short page = last page
            break
        page += 1
    _warn_if_truncated("artifact", len(artifacts), total, page > _MAX_PAGES)
    return artifacts


def _expected_markers(runs, by_run, repo, token, api):
    """True job count per workflow, sampled from one run of each.

    Needed because a marker only proves *a* job reported, not that all of them
    did. Without a denominator, a run where one job of ten is instrumented
    looks complete and the other nine count as zero energy. One API call per
    instrumented workflow buys the denominator; in steady state that is a
    handful of calls against the hundreds it saves.
    """
    expected, seen = {}, set()
    for run in runs:
        wf = run.get("workflow_id")
        if wf in seen or run.get("id") not in by_run:
            continue
        seen.add(wf)
        try:
            expected[wf] = len(run_jobs(run["id"], repo, token, api))
        except Exception:
            # Unreachable sample: fall back to the most complete run observed,
            # which is still better than trusting a single marker.
            expected[wf] = max(n for _, n in by_run.values())
    return expected


def artifact_kwh_by_run(repo, token, api="https://api.github.com", runner_watts=None):
    """{run_id: (kWh, marker_count)} for every run whose jobs recorded themselves.

    Keyed by run because instrumentation arrives one workflow file at a time,
    so the answer is almost never "all" or "none" — it is "these runs, not
    those". The artifacts listing carries workflow_run.id already, so knowing
    *which* runs are covered is free.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    blanket = (runner_watts or {}).get(ANY_RUNNER)
    by_run, jobs = {}, 0
    for artifact in list_artifacts(repo, token, api):
        if artifact.get("expired"):
            continue
        parsed = parse_carbon_artifact(artifact.get("name"))
        if not parsed:
            continue
        created = artifact.get("created_at")
        if created and datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff:
            continue
        run_id = (artifact.get("workflow_run") or {}).get("id")
        if run_id is None:
            continue
        seconds, vcpu, mem_mb, platform = parsed
        # A declared figure still wins: someone who knows their hardware's real
        # draw beats a linear model of it.
        watts = blanket if blanket else watts_from_specs(vcpu, mem_mb, platform)
        kwh, count = by_run.get(run_id, (0.0, 0))
        by_run[run_id] = (kwh + (seconds / 3600) * watts / 1000, count + 1)
        jobs += 1
    return by_run, jobs


def _warn_undeclared_runners(undeclared):
    """Name the labels, not just a count — the label *is* the fix, since it's
    what the user passes back in --runner-watts."""
    for labels, count in sorted(undeclared.items(), key=lambda kv: -kv[1]):
        # Only suggest a label we can actually build a flag from. The
        # placeholders used when a job has none ("(no labels)") produced
        # `--runner-watts '(self-managed=<watts>'`, which parse_runner_watts
        # rejects — a fix-it message that hands over a broken command is worse
        # than one that admits it cannot.
        first = labels.split(",")[0]
        usable = first and not first.startswith("(")
        remedy = (
            f"pass --runner-watts '{first}=<watts>' to price it"
            if usable
            else "give it a label, or set a blanket --runner-watts <watts>"
        )
        print(
            f"carbon-badge: {count} job(s) on unrecognised runner "
            f"'{labels}' charged at the {DEFAULT_RUNNER_POWER_W} W baseline; "
            f"{remedy}",
            file=sys.stderr,
        )


def grams_co2e(minutes, grid_intensity=DEFAULT_GRID_INTENSITY, watts=DEFAULT_RUNNER_POWER_W):
    """Convert CI runner-minutes to gCO2e for the given grid intensity.

    `watts` honours a declared blanket --runner-watts: without it, an offline
    --minutes estimate silently used the 12.5 W baseline however big the
    runners were actually declared to be.
    """
    return minutes * (watts / 1000 / 60) * grid_intensity


def grams_co2e_kwh(kwh, grid_intensity=DEFAULT_GRID_INTENSITY):
    """Convert kWh to gCO2e for the given grid intensity."""
    return kwh * grid_intensity


def gitlab_kwh_last_30d(project, token, api="https://gitlab.com/api/v4", runner_watts=None):
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
    kwh, undeclared, page = 0.0, {}, 1
    guessed_kwh, total_jobs = 0.0, 0
    while page <= _MAX_PAGES:
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
            if not created or datetime.fromisoformat(created.replace("Z", "+00:00")) < since:
                continue
            duration = job.get("duration")
            if not duration:
                continue
            runner = job.get("runner") or {}
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
            if guessed:
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
        print(
            f"carbon-badge: read {_MAX_PAGES} pages of jobs and stopped at the "
            "cap; the figure may be an undercount.",
            file=sys.stderr,
        )
    _warn_undeclared_runners(undeclared)
    # record/ is a GitHub Action, so nothing self-reports on GitLab and measured
    # is always zero — but the guessed share still separates "estimated" from
    # "rough", which is the distinction a GitLab user needs most.
    return CiUsage(kwh, 0, total_jobs, 0.0, guessed_kwh)


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


# Share of energy that must be directly measured before the figure is called
# measured rather than estimated. Not 1.0: a cancelled job never reaches its
# recording step, so a fully instrumented repo still sits just under parity.
MEASURED_THRESHOLD = 0.95
# Share of energy priced at the fallback wattage — an unrecognised runner whose
# real draw nobody declared — above which the total is only a rough figure. A
# quarter of the energy resting on a guess is enough to move a badge a colour.
ROUGH_THRESHOLD = 0.25


def confidence(usage):
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


def endpoint_json(grams, usage=None):
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
        "color": badge_color(grams),
    }


def estimate(args, token):
    """Run one carbon estimate for the parsed CLI args. Returns (endpoint_json, detail)."""
    grid_intensity = args.grid_intensity
    if args.grid_region:
        em_token = args.electricitymaps_token or os.environ.get("ELECTRICITYMAPS_TOKEN")
        grid_intensity = live_grid_intensity(args.grid_region, token=em_token)
        print(
            f"carbon-badge: live grid intensity for {args.grid_region} = {grid_intensity:.0f} gCO2e/kWh",
            file=sys.stderr,
        )
    runner_watts = parse_runner_watts(getattr(args, "runner_watts", None))
    usage = None
    if args.minutes is not None:
        watts = runner_watts.get(ANY_RUNNER, DEFAULT_RUNNER_POWER_W)
        grams = grams_co2e(args.minutes, grid_intensity, watts)
        detail = f"{args.minutes:.0f} CI min/30d at {watts:g} W"
    elif args.provider == "gitlab":
        usage = gitlab_kwh_last_30d(
            args.repo,
            token,
            api=args.api or "https://gitlab.com/api/v4",
            runner_watts=runner_watts,
        )
        grams = grams_co2e_kwh(usage.kwh, grid_intensity)
        detail = f"{usage.kwh:.3f} kWh/30d, confidence: {confidence(usage)}"
    else:
        usage = ci_kwh_last_30d(
            args.repo,
            token,
            api=args.api or "https://api.github.com",
            runner_watts=runner_watts,
            use_artifacts=not getattr(args, "ignore_self_reported", False),
        )
        grams = grams_co2e_kwh(usage.kwh, grid_intensity)
        level = confidence(usage)
        guessed_pct = 100 * usage.guessed_kwh / usage.kwh if usage.kwh else 0
        detail = (
            f"{usage.kwh:.3f} kWh/30d, confidence: {level} "
            f"({usage.measured_jobs}/{usage.total_jobs} job(s) self-reported, "
            f"{guessed_pct:.0f}% of energy on unrecognised runners)"
        )
    return endpoint_json(grams, usage), detail


def badge_handler(compute, ttl=300):
    """Build a request handler serving /badge.json from compute(), cached for ttl seconds."""
    # `t=None` means "never computed yet" — not 0.0, since time.monotonic()'s
    # epoch is arbitrary (e.g. near-zero shortly after a container boots), so
    # `now - 0.0 > ttl` can be false on the very first request too, leaving
    # cache["body"] permanently empty.
    cache = {"t": None, "body": b""}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/badge.json"):
                self.send_response(404)
                self.end_headers()
                return
            now = time.monotonic()
            if cache["t"] is None or now - cache["t"] > ttl:
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
    http.server.HTTPServer(("0.0.0.0", port), badge_handler(compute, ttl)).serve_forever()


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
    p.add_argument(
        "--ignore-self-reported",
        action="store_true",
        help=(
            "query the API for every run, ignoring what jobs recorded about "
            "themselves. Slower and no more accurate, so this exists for one "
            "job: reconciling the two paths against each other to see what the "
            "self-reported figure is missing (see docs/getting-started.md)"
        ),
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

    # Validated up front so a typo fails the command rather than surfacing as a
    # traceback from inside estimate() — or, under --serve, on every request.
    try:
        parse_runner_watts(args.runner_watts)
    except ValueError as exc:
        p.error(f"--runner-watts: {exc}")

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
