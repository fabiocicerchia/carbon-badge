"""Asking each CI provider what it billed, and what the grid was doing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .artifacts import RegionFactors, _expected_markers, artifact_kwh_by_run
from .base import Getter, Json, RunnerWatts, log
from .ci import _MAX_PAGES, _PAGE_SIZE, _list_runs, _run_kwh
from .power import DEFAULT_RUNNER_POWER_W, CiUsage, grid_factor_for, runner_power_w
from .reconcile import _warn_undeclared_runners


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
