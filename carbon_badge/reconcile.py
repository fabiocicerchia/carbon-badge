"""Comparing the estimate against what the artifacts actually measured."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from .artifacts import RegionFactors, _expected_markers, artifact_kwh_by_run, list_artifacts, parse_carbon_artifact
from .base import DEFAULT_GRID_INTENSITY, RECONCILE_EPSILON_G, Json, RunnerWatts, carbon_artifact_slug, log
from .ci import _api_run_detail, _list_runs
from .power import DEFAULT_RUNNER_POWER_W, grid_factor_for

# ------------------------------------------------------- reconciliation ---
#
# docs/getting-started.md has always told people to compare the default path
# against --ignore-self-reported, and left them to eyeball two totals. Two
# totals cannot say WHY they differ, and there are three separate reasons they
# can, pulling in different directions:
#
#   1. SETUP TIME. A marker times the instrumented step. The API bills the
#      whole job — runner provisioning, checkout, tool caches, upload. That gap
#      is real energy the self-reported path does not see, and it is the one
#      known remaining bias.
#   2. THE WATTS MODEL. A marker prices itself from its own vCPU/memory through
#      a linear model; the API path prices from the runner label through a
#      lookup table. The same job can get two different wattages.
#   3. THE GRID FACTOR. A marker carries the region it ran in and is priced at
#      that grid; a run priced from the API has no region and takes the world
#      average. GitHub's regions differ by ~25x, so this is the largest single
#      term and it is not an error in either direction — the per-region figure
#      is the better one.
#
# Reporting one number hides all three. This decomposes the divergence so each
# can be judged separately, which is the difference between "the two paths
# differ by 12%" and "setup time is 8% of the total and the grid factor
# accounts for the rest".


class Divergence(NamedTuple):
    run_id: int
    workflow_id: int
    jobs: int
    marker_seconds: float
    api_seconds: float
    marker_kwh: float
    api_kwh: float
    marker_grams: float
    api_grams: float
    matched_jobs: int


class Reconciliation(NamedTuple):
    rows: list[Divergence]
    runs_total: int
    runs_compared: int
    marker_kwh: float
    api_kwh: float
    marker_grams: float
    api_grams: float
    marker_seconds: float
    api_seconds: float
    jobs: int
    setup_kwh: float
    model_kwh: float
    grid_grams: float
    api_factor: float


def reconcile_last_30d(  # noqa: PLR0913 — the repo/token/api trio plus independent optional knobs
    repo: str,
    token: str | None,
    *,
    api: str = "https://api.github.com",
    runner_watts: RunnerWatts | None = None,
    grid_override: float | None = None,
    eia_key: str | None = None,
) -> Reconciliation:
    """Run BOTH paths over the same runs and decompose where they disagree.

    Expensive on purpose: it prices every run from the API *and* reads every
    marker, which is the one thing the normal path exists to avoid. This is a
    diagnostic, not something to put in a badge refresh.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    runs = _list_runs(repo, token, api, since)
    factor_for = RegionFactors(eia_key=eia_key, override=grid_override).factor_for
    by_run, _ = artifact_kwh_by_run(repo, token, api, runner_watts, factor_for)
    expected: dict[int, int] = _expected_markers(runs, by_run, repo, token, api) if by_run else {}
    api_factor = grid_factor_for(None, grid_override)

    marker_seconds_by_run = _marker_seconds_by_run(repo, token, api)

    rows: list[Divergence] = []
    undeclared: dict[str, int] = {}
    for run in runs:
        entry = by_run.get(int(run.get("id", 0)))
        want: int = expected.get(int(run.get("workflow_id", 0)), 0)
        # Only fully instrumented runs are comparable. A partly instrumented
        # one would show a divergence that is just the missing jobs, which is
        # not what any of the three terms above is about.
        if entry is None or entry[2] < want:
            continue
        a_kwh, a_seconds, a_jobs, _, a_per_job, a_undeclared = _api_run_detail(run, repo, token, api, runner_watts)
        for k, v in a_undeclared.items():
            undeclared[k] = undeclared.get(k, 0) + v
        m_slugs: dict[str, float] = marker_seconds_by_run.get(int(run.get("id", 0)), {})
        matched = sum(1 for slug in m_slugs if slug in a_per_job)
        rows.append(
            Divergence(
                run_id=int(run.get("id", 0)),
                workflow_id=int(run.get("workflow_id", 0)),
                jobs=a_jobs,
                marker_seconds=sum(m_slugs.values()),
                api_seconds=a_seconds,
                marker_kwh=entry[0],
                api_kwh=a_kwh,
                marker_grams=entry[1],
                api_grams=a_kwh * api_factor,
                matched_jobs=matched,
            )
        )
    _warn_undeclared_runners(undeclared)

    m_kwh = sum(r.marker_kwh for r in rows)
    a_kwh = sum(r.api_kwh for r in rows)
    m_g = sum(r.marker_grams for r in rows)
    a_g = sum(r.api_grams for r in rows)
    m_s = sum(r.marker_seconds for r in rows)
    a_s = sum(r.api_seconds for r in rows)
    jobs = sum(r.jobs for r in rows)

    # The decomposition. Setup time is priced at the API path's own mean draw
    # over the compared runs, so it is the share of the kWh gap that the extra
    # SECONDS explain; whatever is left over is the two models disagreeing
    # about wattage on the same seconds.
    mean_watts = (a_kwh * 1000 * 3600 / a_s) if a_s else 0.0
    setup_kwh = (a_s - m_s) / 3600 * mean_watts / 1000
    model_kwh = (a_kwh - m_kwh) - setup_kwh
    # And the grams gap that is NOT explained by the kWh gap is the grid: one
    # side priced per region, the other at the world average.
    grid_grams = (a_g - m_g) - (a_kwh - m_kwh) * api_factor

    return Reconciliation(
        rows=rows,
        runs_total=len(runs),
        runs_compared=len(rows),
        marker_kwh=m_kwh,
        api_kwh=a_kwh,
        marker_grams=m_g,
        api_grams=a_g,
        marker_seconds=m_s,
        api_seconds=a_s,
        jobs=jobs,
        setup_kwh=setup_kwh,
        model_kwh=model_kwh,
        grid_grams=grid_grams,
        api_factor=api_factor,
    )


def _marker_seconds_by_run(
    repo: str, token: str | None, api: str = "https://api.github.com"
) -> dict[int, dict[str, float]]:
    """{run_id: {slug: seconds}} — the raw durations the markers carry.

    Separate from artifact_kwh_by_run because that one has already priced them,
    and the seconds are what isolates setup time from the wattage models.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    out: dict[int, dict[str, float]] = {}
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
        slug = carbon_artifact_slug(str(artifact.get("name") or "")) or f"?{len(out)}"
        out.setdefault(run_id, {})
        out[run_id][slug] = out[run_id].get(slug, 0.0) + parsed[0]
    return out


def _pct(part: float, whole: float) -> float:
    return (100 * part / whole) if whole else 0.0


def format_reconciliation(rec: Reconciliation, limit: int = 15) -> str:
    """The report. Per run, then the aggregate, then the decomposition."""
    if not rec.rows:
        return (
            f"carbon-badge: nothing to reconcile — {rec.runs_total} run(s) in the "
            f"window, none of them fully self-reported.\n"
            "Both paths need the same runs to compare, so a repo with no "
            "instrumented workflow has nothing to say here yet."
        )
    out: list[str] = []
    out.append(
        f"Reconciliation over {rec.runs_compared} fully self-reported run(s) "
        f"of {rec.runs_total} in the last 30 days ({rec.jobs} job(s)).\n"
    )
    out.append(
        f"{'run':>12}  {'jobs':>4}  {'marker s':>9}  {'api s':>9}  "
        f"{'setup s/job':>11}  {'marker g':>9}  {'api g':>9}  {'delta':>7}"
    )
    out.append("-" * 84)
    worst = sorted(rows_by_gap(rec.rows), key=lambda r: -abs(r.api_grams - r.marker_grams))
    for r in worst[:limit]:
        per_job = (r.api_seconds - r.marker_seconds) / r.jobs if r.jobs else 0.0
        out.append(
            f"{r.run_id:>12}  {r.jobs:>4}  {r.marker_seconds:>9.0f}  "
            f"{r.api_seconds:>9.0f}  {per_job:>11.0f}  {r.marker_grams:>9.1f}  "
            f"{r.api_grams:>9.1f}  {_pct(r.api_grams - r.marker_grams, r.marker_grams):>6.0f}%"
        )
    if len(worst) > limit:
        out.append(f"... and {len(worst) - limit} more, ordered by absolute gCO2e gap")

    setup_per_job = (rec.api_seconds - rec.marker_seconds) / rec.jobs if rec.jobs else 0.0
    matched = sum(r.matched_jobs for r in rec.rows)
    out.append("")
    out.append("AGGREGATE")
    out.append(f"  self-reported   {rec.marker_kwh:>10.4f} kWh   {rec.marker_grams:>10.1f} gCO2e")
    out.append(f"  API            {rec.api_kwh:>10.4f} kWh   {rec.api_grams:>10.1f} gCO2e")
    out.append(
        f"  divergence     {rec.api_kwh - rec.marker_kwh:>+10.4f} kWh   "
        f"{rec.api_grams - rec.marker_grams:>+10.1f} gCO2e   "
        f"({_pct(rec.api_grams - rec.marker_grams, rec.marker_grams):+.1f}%)"
    )
    out.append("")
    out.append("WHERE IT COMES FROM")
    out.append(
        f"  setup time     {rec.setup_kwh:>+10.4f} kWh   "
        f"({_pct(rec.setup_kwh, rec.marker_kwh):+.1f}% of the self-reported total)\n"
        f"                 {rec.api_seconds - rec.marker_seconds:.0f} s across {rec.jobs} job(s) "
        f"= {setup_per_job:.0f} s/job the marker never sees"
    )
    out.append(
        f"  watts model    {rec.model_kwh:>+10.4f} kWh   "
        f"({_pct(rec.model_kwh, rec.marker_kwh):+.1f}%) — the same seconds priced two ways"
    )
    out.append(
        f"  grid factor    {rec.grid_grams:>+10.1f} gCO2e   "
        f"({_pct(rec.grid_grams, rec.marker_grams):+.1f}%) — per-region markers vs the "
        f"{rec.api_factor:.0f} gCO2e/kWh world average"
    )
    out.append("")
    out.append(
        f"  {matched}/{rec.jobs} job(s) matched by name between the two paths. An "
        f"unmatched job\n  still counts in its run's totals; only the per-job "
        f"attribution needs the name."
    )
    out.append(
        "\n  Setup time is the only one of the three that is a BIAS: it is energy "
        "really\n  spent that the self-reported path cannot see, and it is always "
        "one-directional.\n  The other two are the two paths knowing different things, "
        "and on both counts\n  the self-reported side is the better informed one."
    )
    return "\n".join(out)


def rows_by_gap(rows: list[Divergence]) -> list[Divergence]:
    """Rows worth printing: a run where both paths agree exactly says nothing."""
    return [r for r in rows if abs(r.api_grams - r.marker_grams) > RECONCILE_EPSILON_G]


def _warn_undeclared_runners(undeclared: dict[str, int]) -> None:
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
        log.warning(
            "%d job(s) on unrecognised runner '%s' charged at the %s W baseline; %s",
            count,
            labels,
            DEFAULT_RUNNER_POWER_W,
            remedy,
        )


def grams_co2e(
    minutes: float, grid_intensity: float = DEFAULT_GRID_INTENSITY, watts: float = DEFAULT_RUNNER_POWER_W
) -> float:
    """Convert CI runner-minutes to gCO2e for the given grid intensity.

    `watts` honours a declared blanket --runner-watts: without it, an offline
    --minutes estimate silently used the DEFAULT_RUNNER_POWER_W baseline
    however big the runners were actually declared to be.

    Sanity anchor, the same shape as greenlint's core_seconds_per_gram: at 480
    gCO2e/kWh a gram is 7.5 kJ, so one 9.4 W standard runner earns a gram every
    ~13 minutes. A badge reading 100 gCO2e/mo is therefore claiming about 22
    runner-hours a month — if that does not match the repo, the error is here
    and not in the grid factor.
    """
    return minutes * (watts / 1000 / 60) * grid_intensity


def grams_co2e_kwh(kwh: float, grid_intensity: float = DEFAULT_GRID_INTENSITY) -> float:
    """Convert kWh to gCO2e for the given grid intensity."""
    return kwh * grid_intensity


