"""Reading the carbon artifacts a run leaves behind."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import requests

from .base import Getter, Json, RunnerWatts, _ARTIFACT_RE, log
from .ci import _get_pages, _warn_if_truncated, run_jobs
from .grid import _ci_api_snapshot, live_region_factor
from .power import ANY_RUNNER, RunTotals, _ran, grid_factor_for, watts_from_specs

class RegionFactors:
    """Memoised region -> factor, so each distinct region costs one request.

    A month of runs touches a handful of regions, so this is a few calls per
    refresh, not one per job. `factor_for` is a bound method, so it goes
    wherever the plain function it replaced did.
    """

    def __init__(self, eia_key: str | None = None, override: float | None = None, get: Getter = requests.get) -> None:
        self._eia_key = eia_key
        self._override = override
        self._get = get
        self._cache: dict[str, float] = {}
        # One-slot memo: [] means "not fetched", [None] means "tried and failed".
        self._snapshot: list[Json | None] = []

    def _ci_api(self) -> Json | None:
        if not self._snapshot:
            try:
                self._snapshot.append(_ci_api_snapshot(get=self._get))
            except Exception as exc:
                log.warning("ci-api snapshot failed (%s)", exc)
                self._snapshot.append(None)
        return self._snapshot[0]

    def factor_for(self, region: str) -> float:
        if self._override is not None:
            return self._override
        if region not in self._cache:
            live = live_region_factor(region, eia_key=self._eia_key, get=self._get, ci_api=self._ci_api)
            if live is not None:
                log.info(
                    "%s live at %.0f gCO2e/kWh (annual average is %.0f)",
                    region,
                    live,
                    grid_factor_for(region),
                )
            self._cache[region] = live if live is not None else grid_factor_for(region)
        return self._cache[region]




# Linear model for a self-reported machine, anchored on the same Eco-CI curve:
# a 4-vCPU / 16 GiB GitHub runner at 8.18 W machine draw x PUE ~= 9.4 W.
#   1.2 + 1.6*4 + 0.1125*16 = 9.4 exactly
# Crude — real draw swings 1.76-8.18 W with utilisation, which we cannot see —
# but applied to the machine's *actual* vCPU count and memory rather than to a
# guess scraped from a label.
#
# The per-GB term is tuned so a standard runner comes out at exactly
# RUNNER_POWER_W["ubuntu"]. Without that the two paths disagreed by 0.4% for
# the same machine, so a repo's figure stepped very slightly as it instrumented
# — a change with no cause, which is the kind of thing that gets read as signal.
#
# It was previously fitted to "2 vCPU / 7 GB = 12.5 W", which was wrong twice
# over: public repos have had 4-vCPU/16 GiB runners since Dec 2023, and 12.5 W
# was itself ~1.5x the measured full-load figure. That combination priced a
# real 4-vCPU runner at 24.3 W, about 3x too high.
#
# Neither of the two constants below is measured or published — say so plainly,
# because they read like coefficients and they are not. They are the two free
# parameters of a fit whose only constraint is that the model reproduce the
# Eco-CI table exactly at 4 vCPU / 16 GiB; per_vcpu is then solved for, which
# is why it is derived in code rather than written down. Any (base, per-GB)
# pair summing to 3.0 W at 16 GiB would satisfy that constraint equally well.
#
# In particular WATTS_PER_GB is nothing like Cloud Carbon Footprint's 0.392
# W/GB for memory, which greenlint's numbers descend from — and must not be:
# CCF meters memory separately from compute, whereas the Eco-CI curve is
# whole-machine draw and already has the memory in it. Raising this term to
# CCF's would double-count. It is a shape parameter, not a memory coefficient.

def parse_carbon_artifact(name: str) -> tuple[float, int, int, str, str] | None:
    """ "carbon.v1.142.4.16384.ubuntu.build" -> (142.0 s, 4 vcpu, 16384 MB, "ubuntu").

    None for anything that is not one of ours, so a repo's normal build
    artifacts sitting in the same listing are simply ignored.
    """
    match = _ARTIFACT_RE.match(name or "")
    if not match:
        return None
    seconds, vcpu, mem_mb, platform, region = match.groups()
    return (float(seconds), int(vcpu), int(mem_mb), platform, region)


def list_artifacts(repo: str, token: str | None, api: str = "https://api.github.com") -> list[Json]:
    """Every non-expired artifact, 100 per request."""
    artifacts, total, hit_cap = _get_pages(f"{api}/repos/{repo}/actions/artifacts", token, "artifacts")
    _warn_if_truncated("artifact", len(artifacts), total, hit_cap)
    return artifacts


def _expected_markers(
    runs: list[Json], by_run: dict[int, RunTotals], repo: str, token: str | None, api: str
) -> dict[int, int]:
    """True job count per workflow, sampled from one run of each.

    Needed because a marker only proves *a* job reported, not that all of them
    did. Without a denominator, a run where one job of ten is instrumented
    looks complete and the other nine count as zero energy. One API call per
    instrumented workflow buys the denominator; in steady state that is a
    handful of calls against the hundreds it saves.

    Two sources, and the larger wins. The API sample is ground truth for the
    run it looked at — but it is one run, applied to a whole month, so a
    workflow whose newest run was unusually small (conditional jobs that did
    not fire, a narrower matrix) would set the bar too low and let genuinely
    partial runs through. A marker can never outnumber its run's jobs, so the
    highest marker count seen for the workflow is itself a valid lower bound,
    and it costs nothing.

    Erring high means a legitimately small run gets priced from the API: a
    wasted request, never a wrong number.
    """
    observed: dict[int, int] = {}
    sampled: dict[int, int] = {}
    seen: set[int] = set()
    for run in runs:
        workflow_id = int(run.get("workflow_id", 0))
        entry = by_run.get(int(run.get("id", 0)))
        if entry is None:
            continue
        observed[workflow_id] = max(observed.get(workflow_id, 0), entry.markers)
        if workflow_id not in seen:
            seen.add(workflow_id)
            try:
                sampled[workflow_id] = sum(1 for j in run_jobs(run["id"], repo, token, api) if _ran(j))
            except Exception:
                sampled[workflow_id] = 0  # unreachable sample; the observed bound stands
    return {workflow_id: max(observed.get(workflow_id, 0), sampled.get(workflow_id, 0)) for workflow_id in observed}


def artifact_kwh_by_run(
    repo: str,
    token: str | None,
    api: str = "https://api.github.com",
    runner_watts: RunnerWatts | None = None,
    factor_for: Callable[[str], float] | None = None,
) -> tuple[dict[int, RunTotals], int]:
    """{run_id: RunTotals} for runs whose jobs recorded themselves.

    Keyed by run because instrumentation arrives one workflow file at a time,
    so the answer is almost never "all" or "none" — it is "these runs, not
    those". The artifacts listing carries workflow_run.id already, so knowing
    *which* runs are covered is free.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    blanket = (runner_watts or {}).get(ANY_RUNNER)
    by_run: dict[int, RunTotals] = {}
    jobs = 0
    for artifact in list_artifacts(repo, token, api):
        if artifact.get("expired"):
            continue
        parsed = parse_carbon_artifact(str(artifact.get("name") or ""))
        if not parsed:
            continue
        created: str | None = artifact.get("created_at")
        if created and datetime.fromisoformat(created.replace("Z", "+00:00")) < cutoff:
            continue
        workflow_run: Json = artifact.get("workflow_run") or {}
        if workflow_run.get("id") is None:
            continue
        run_id = int(workflow_run["id"])
        seconds, vcpu, mem_mb, platform, region = parsed
        # A declared figure still wins: someone who knows their hardware's real
        # draw beats a linear model of it.
        watts = blanket if blanket else watts_from_specs(vcpu, mem_mb, platform)
        job_kwh = (seconds / 3600) * watts / 1000
        # Each job priced at the grid it actually ran on. GitHub allocates
        # runners across regions whose factors differ by ~25x, so this is the
        # largest correction available and it costs nothing — the region came
        # in on the marker.
        job_g = job_kwh * (factor_for(region) if factor_for else grid_factor_for(region))
        so_far = by_run.get(run_id, RunTotals(0.0, 0.0, 0))
        by_run[run_id] = RunTotals(so_far.kwh + job_kwh, so_far.grams + job_g, so_far.markers + 1)
        jobs += 1
    return by_run, jobs
