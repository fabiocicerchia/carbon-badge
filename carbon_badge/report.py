"""Turning the usage into the figure a badge shows: grams, confidence, JSON."""

from __future__ import annotations

import argparse
import os

from .base import GRAMS_PER_KILOGRAM, Json, RunnerWatts, log
from .power import (
    _ESTIMATED_USED,
    ANY_RUNNER,
    BADGE_COLOR,
    DEFAULT_RUNNER_POWER_W,
    CiUsage,
    _warn_estimated_classes,
    grid_factor_for,
    parse_runner_watts,
)
from .providers import ci_kwh_last_30d, gitlab_kwh_last_30d, live_grid_intensity
from .reconcile import grams_co2e


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
