"""Talking to the CI provider: paging the API, listing runs and jobs, and
asking about one run in detail."""

from __future__ import annotations

from typing import Any

import requests

from .base import Json, RunnerWatts, job_slug, log
from .power import DEFAULT_RUNNER_POWER_W, _hours, _ran, runner_power_w

_MAX_PAGES = 20

# Requested per page, and therefore what a short page means: fewer than this
# many results is the last page. The two readings have to stay the same number,
# which is why it is one name and not two literals.
# 100, not the API's default of 30: a matrix wider than 30 jobs was silently
# truncated, and this now also feeds the confidence ratio.
_PAGE_SIZE = 100


def _get_pages(
    url: str, token: str | None, key: str, params: dict[str, str] | None = None
) -> tuple[list[Json], int | None, bool]:
    """Every item under `key` across pages -> (items, total_count, hit_page_cap).

    The three GitHub listings this tool reads page identically, and the pieces
    that have to agree — the requested page size and the short-page test that
    ends the loop — are the ones that would silently disagree if each caller
    kept its own copy. `total_count` is None for a listing that omits it.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    items: list[Json] = []
    page = 1
    total: int | None = None
    while page <= _MAX_PAGES:
        query: dict[str, Any] = {**(params or {}), "per_page": _PAGE_SIZE, "page": page}
        response = requests.get(
            url,
            params=query,
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload: Json = response.json()
        total = payload.get("total_count", total)
        batch: list[Json] = payload.get(key, [])
        items.extend(batch)
        # A short page is the last page — stop rather than spend a request
        # confirming the next one is empty.
        if len(batch) < _PAGE_SIZE:
            break
        page += 1
    return items, total, page > _MAX_PAGES


def run_jobs(run_id: int | str, repo: str, token: str | None, api: str = "https://api.github.com") -> list[Json]:
    """Fetch the jobs (with per-job runner labels/timing) for one run."""
    jobs, _, _ = _get_pages(f"{api}/repos/{repo}/actions/runs/{run_id}/jobs", token, "jobs")
    return jobs


def _warn_if_truncated(kind: str, fetched: int, total: int | None, hit_page_cap: bool) -> None:
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
        else "GitHub caps this listing at 1000 results; a narrower window is the only way to see the rest"
    )
    log.warning(
        "only read %d of %d %s(s) — %s. The figure is an undercount.",
        fetched,
        total,
        kind,
        cause,
    )


def _list_runs(repo: str, token: str | None, api: str, since: str) -> list[Json]:
    """Every run in the window — cheap, 100 per request."""
    runs, total, hit_cap = _get_pages(
        f"{api}/repos/{repo}/actions/runs",
        token,
        "workflow_runs",
        {"created": f">={since}"},
    )
    _warn_if_truncated("run", len(runs), total, hit_cap)
    return runs


def _run_kwh(  # noqa: PLR0913,PLR0917 — the run, where to fetch it, and the two tables it charges against
    run: Json, repo: str, token: str | None, api: str, runner_watts: RunnerWatts | None, undeclared: dict[str, int]
) -> tuple[float, int, float]:
    """Exact kWh for one run: sum its jobs, each at its own runner's draw.

    Costs one API call. Jobs on a runner this cannot price — self-hosted, or
    any custom label — are charged at the baseline and recorded in
    `undeclared`, rather than skipped. Skipping scored them as zero, which made
    moving a build onto the biggest machine you own *improve* the badge.
    """
    # The body lives in _api_run_detail, which the reconciliation also needs;
    # this returns the three values the badge path has always used. Skipped
    # jobs and jobs with no timestamps are dropped there — counting them would
    # inflate both the denominator and the "N/M measured" ratio, and a skipped
    # job can never write a marker.
    kwh, _seconds, jobs, guessed_kwh, _per_job, found = _api_run_detail(run, repo, token, api, runner_watts)
    # Tracked in energy, not job count: one long job on an unknown runner
    # undermines the total far more than a dozen short known ones.
    for key, count in found.items():
        undeclared[key] = undeclared.get(key, 0) + count
    return kwh, jobs, guessed_kwh




def _api_run_detail(
    run: Json, repo: str, token: str | None, api: str, runner_watts: RunnerWatts | None
) -> tuple[float, float, int, float, dict[str, Any], dict[str, int]]:
    """(kwh, seconds, jobs, guessed_kwh, per_job) for one run, from the API.

    Same arithmetic as _run_kwh — which now calls this — plus the seconds and
    the per-job breakdown that only the reconciliation needs.
    """
    kwh = seconds = guessed_kwh = 0.0
    jobs = 0
    per_job: dict[str, tuple[float, float]] = {}
    undeclared: dict[str, int] = {}
    for job in run_jobs(run["id"], repo, token, api):
        if not _ran(job):
            continue
        start, end = job.get("started_at"), job.get("completed_at")
        if not (start and end):
            continue
        labels = job.get("labels", [])
        hours = _hours(start, end)
        watts = runner_power_w(labels, runner_watts)
        guessed = watts is None
        if guessed:
            watts = DEFAULT_RUNNER_POWER_W
            key = ",".join(labels) or "(no labels)"
            undeclared[key] = undeclared.get(key, 0) + 1
        assert watts is not None  # noqa: S101 — set on both branches above
        job_kwh = hours * watts / 1000
        kwh += job_kwh
        seconds += hours * 3600
        guessed_kwh += job_kwh if guessed else 0.0
        jobs += 1
        slug = job_slug(str(job.get("name") or ""))
        if slug:
            # A matrix leg and its parent can slugify alike; sum rather than
            # overwrite, so the comparison is never quietly missing a job.
            prev = per_job.get(slug, (0.0, 0.0))
            per_job[slug] = (prev[0] + job_kwh, prev[1] + hours * 3600)
    return kwh, seconds, jobs, guessed_kwh, per_job, undeclared

