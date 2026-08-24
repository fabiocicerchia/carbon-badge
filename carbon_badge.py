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
# Where these numbers come from. See docs/assumptions.md for the full working.
#
# Every constant below is either published, with a link, or derived from one
# that is — in which case the derivation is the whole of it. The sibling tool
# greenlint holds its constants to the same standard, and where the two price
# the same physical thing they are reconciled here rather than left to
# disagree quietly.
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
#
# Reconciling with greenlint. greenlint's anchor is 15 W for one busy physical
# core; the table below works out at ~4.7 W (9.4 W for a 4-vCPU runner, which
# is two hyperthreaded cores). That 3x gap is deliberate on both sides:
#
#   - The published coefficients differ by 1.7x before either tool touches
#     them. greenlint starts from Cloud Carbon Footprint's 3.5 W per vCPU at
#     100% CPU, a cross-fleet average; this starts from Eco-CI's 8.18 W for a
#     4-vCPU slice — 2.05 W per vCPU — modelled for the one machine GitHub
#     actually runs jobs on.
#   - greenlint then rounds up on purpose (7 W of silicon x PUE is 8-11 W; it
#     quotes 15) and carries the industry-average PUE of 1.56, because it is
#     linting code destined for infrastructure nobody has described. This tool
#     knows the infrastructure is Azure, so it takes the hyperscale PUE and no
#     safety margin.
#
# greenlint is the generous end of plausible for an unknown machine; this is
# the modelled figure for a known one. Neither constant belongs in the other
# tool.
# --------------------------------------------------------------------------

# Datacentre overhead, applied to machine draw to get wall power. GitHub's
# hosted runners are Azure VMs, so the hyperscale end is the right one: Cloud
# Carbon Footprint publishes 1.125 for Azure, 1.135 for AWS, 1.1 for GCP.
# https://www.cloudcarbonfootprint.org/docs/methodology/
# 1.15 sits just above that band rather than on Azure's own 1.125 — a 2%
# difference, far inside the error on everything it multiplies, and rounding
# up is the direction that does not flatter the badge. The industry-wide
# average is 1.56 and has been flat for five years (Uptime Institute, Global
# Data Center Survey 2024), which is what greenlint uses; it does not apply
# here, because we know these jobs ran in a hyperscale datacentre.
# https://uptimeinstitute.com/resources/research-and-reports/uptime-institute-global-data-center-survey-results-2024
PUE = 1.15

# Public repos have had 4-vCPU / 16 GiB standard runners since December 2023,
# not the 2-vCPU / 7 GiB machines older estimates assume. Larger runners scale
# off this, so it must match the baseline the wattages above describe.
# https://github.blog/news-insights/product-news/
#   github-hosted-runners-double-the-power-for-open-source/
BASELINE_VCPU = 4

# Published. World-average power-sector intensity for 2023: "CO2 intensity
# reached a new record low of 480 gCO2/kWh, down 1.2% from 486 gCO2/kWh in
# 2022" — Ember, Global Electricity Review 2024. It is in the "Electricity
# transition in 2023" chapter, not on the report landing page:
# https://ember-energy.org/latest-insights/global-electricity-review-2024/electricity-transition-in-2023/
# The figure drifts a few percent a year (486 in 2022, 480 in 2023, 473 in
# 2024), which is far inside the error bars on the wattages it multiplies.
# greenlint pins the same figure, and the two agreeing matters more than
# either tracking the latest annual revision.
DEFAULT_GRID_INTENSITY = 480.0  # gCO2e/kWh

# Annual-average grid carbon factor (gCO2e/kWh) for the grid each Azure region
# sits on, used when a job reported which region it ran in.
#
# The country-level rows are Ember / Energy Institute (via OWID) annual
# operational intensity, taken from the fleet's own carbon-intensity-api
# dataset (`src/datasets/countries.csv` there), data years 2024-2025. That is
# the same Ember series DEFAULT_GRID_INTENSITY comes from, so a region factor
# and the world average are the same kind of number rather than two unrelated
# guesses. Quoted as published rather than rounded, so any row can be checked
# against the source; the approximation is "this region is in that country",
# not the figure itself.
#
# The US and Canadian rows are the exception and the weakest here. Both
# countries span several grids differing by ~5x, so the national average (384
# and 191 respectively) would hide exactly the variation this table exists to
# capture — but Ember publishes nothing sub-national. Those rows are
# hand-transcribed for the Electricity Maps zone named against each, undated,
# and are the ones to distrust first. https://app.electricitymaps.com/zone/<ZONE>
#
# Not the live grid: `--grid-region` with an Electricity Maps token is strictly
# better where you have one. The point of this table is that it costs nothing
# and still beats a single world average by a wide margin — the spread below is
# roughly 25x end to end, which dwarfs every other correction in this tool.
#
# Regions absent here fall back to DEFAULT_GRID_INTENSITY, so an unmapped or
# newly launched region degrades rather than breaks.
AZURE_REGION_GRID = {
    # Nordics and hydro/nuclear-heavy Europe
    "norwayeast": 28.0,  # NO
    "norwaywest": 28.0,  # NO
    "swedencentral": 35.0,  # SE
    "switzerlandnorth": 39.0,  # CH
    "francecentral": 41.0,  # FR
    "francesouth": 41.0,  # FR
    # Rest of Europe
    "uksouth": 217.0,  # GB
    "ukwest": 217.0,  # GB
    "northeurope": 257.0,  # IE
    "westeurope": 254.0,  # NL
    "germanywestcentral": 330.0,  # DE
    "italynorth": 285.0,  # IT
    "spaincentral": 154.0,  # ES
    "polandcentral": 589.0,  # PL
    # North America — sub-national, hand-transcribed; see the caveat above.
    "canadaeast": 30.0,  # CA-QC, Quebec hydro
    "canadacentral": 130.0,  # CA-ON, Ontario nuclear + hydro
    "westus2": 90.0,  # US-NW-PACW, Washington hydro
    "westus": 250.0,  # US-CAL-CISO
    "eastus": 390.0,  # US-MIDA-PJM
    "eastus2": 390.0,  # US-MIDA-PJM
    "southcentralus": 400.0,  # US-TEX-ERCO
    "westus3": 400.0,  # US-SW-AZPS
    "centralus": 430.0,  # US-MIDW-MISO
    "northcentralus": 430.0,  # US-MIDW-MISO
    # South America
    "brazilsouth": 110.0,  # BR
    # Asia-Pacific
    "japaneast": 477.0,  # JP
    "japanwest": 477.0,  # JP
    "koreacentral": 417.0,  # KR
    "southeastasia": 497.0,  # SG
    "eastasia": 675.0,  # HK
    "centralindia": 670.0,  # IN
    "southindia": 670.0,  # IN
    "westindia": 670.0,  # IN
    "australiaeast": 525.0,  # AU
    "australiasoutheast": 525.0,  # AU
    # Middle East and Africa
    "uaenorth": 468.0,  # AE
    "southafricanorth": 699.0,  # ZA
}


def grid_factor_for(region, override=None):
    """gCO2e/kWh for a job, most specific source first.

    An explicit --grid-intensity or --grid-region wins outright: someone who
    named a figure knows something the table does not. Otherwise the region the
    job reported, then the world average.
    """
    if override is not None:
        return override
    return AZURE_REGION_GRID.get(region or "", DEFAULT_GRID_INTENSITY)


# Per-runner-type wall power for the standard BASELINE_VCPU machine (W, incl.
# PUE). "ubuntu" is the baseline; larger runners are not scaled from these
# directly but pushed through watts_from_specs(), which is affine, so a 2x core
# count is not a 2x wattage.
# Checked in order, first substring match wins, so the specific entries must
# come before the generic ones: "ubuntu-22.04-arm" contains "ubuntu", and a GPU
# runner's label normally names its OS too. Every figure here is a default —
# --runner-watts arm=... / gpu=... overrides any of them.
RUNNER_POWER_W = {
    # A 4-vCPU host slice, as for "ubuntu", plus one T4-class accelerator at its
    # 70 W board TDP: 8.18 + 70 = 78.2 W of machine draw. The weakest figure
    # here — no measured curve exists for GitHub's GPU runners and the TDP is a
    # ceiling, not a mean — so declare your own with --runner-watts gpu=<watts>
    # if you use them seriously. Flat, not core-scaled: the accelerator
    # dominates and does not grow with vCPUs.
    #
    # This was a flat 130 W with no working behind it, which implied a ~60 W
    # host — 7x what the same tool charges a 4-vCPU slice everywhere else.
    "gpu": round((8.18 + 70.0) * PUE, 1),
    # ARM (Cobalt/Graviton class): no measured curve either, taken as ~40% below
    # the x86 baseline. Deliberately not the "3-4x more efficient" claim that
    # circulates — AWS's own published figure is up to 60% less energy for equal
    # work (https://aws.amazon.com/ec2/graviton/) and independent benchmarks
    # land nearer 45-50%, so 40% is the conservative end. greenlint's GL016
    # uses the same 40%.
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
CiUsage = namedtuple("CiUsage", "kwh grams measured_jobs total_jobs measured_kwh guessed_kwh")

# One colour, always. The badge used to run a red-amber-green scale, which was
# wrong in two ways.
#
# It did not discriminate: all 40 repos in the fleet it was built for sat in the
# bottom band, 4-52 gCO2e against a 100 g threshold, so the colour was a
# constant that merely looked like a signal. Any other set of cut-offs just
# moves the window — CI footprints span four or five orders of magnitude and
# nobody has published a distribution to place them against.
#
# Worse, an absolute total mostly measures project *size*. A small repo running
# a four-way matrix on every push is genuinely wasteful and scored green; a
# large project with well-managed CI scored amber. Colour-coding size while
# implying virtue says something the number does not support.
#
# Eco-CI reaches the same conclusion — its badge reports a value and passes no
# verdict. The confidence marker stays, because that describes how the figure
# was obtained, which is a claim we can actually defend.
BADGE_COLOR = "informational"


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
            return apply_load_factor(overrides[label])
    for key, watts in overrides.items():
        if key != ANY_RUNNER and any(key in label for label in labels_lower):
            return apply_load_factor(watts)
    # A declared blanket figure beats the built-in guess table — the developer
    # knows what their runners are and the API does not.
    if ANY_RUNNER in overrides:
        return apply_load_factor(overrides[ANY_RUNNER])
    for key, watts in RUNNER_POWER_W.items():
        if any(key in label for label in labels_lower):
            if key in _NO_CORE_SCALING:
                return apply_load_factor(watts)
            cores = next(
                (int(m.group(1)) for label in labels_lower if (m := _CORES_RE.search(label))),
                None,
            )
            # Through the same law the self-reported path uses, assuming the
            # standard memory-per-vCPU ratio, so the two agree for any size
            # rather than only at the calibration point.
            if not cores:
                return apply_load_factor(watts)
            return watts_from_specs(cores, cores * MEM_PER_VCPU_MB, key)
    return None


def _ran(job):
    """Did this job actually execute, and so could it have recorded itself?

    A skipped job still appears in the jobs API with both timestamps set — and
    occasionally with completed_at *before* started_at — but none of its steps
    run, so it can never write a marker. Counting one in the denominator means
    a workflow with any conditional job can never reach completeness: it would
    be priced from the API on every run, however thoroughly instrumented.
    """
    return job.get("conclusion") != "skipped"


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
        # Skipped jobs burn nothing and cannot self-report; counting them would
        # inflate both the denominator and the "N/M measured" ratio.
        if not _ran(job):
            continue
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
    repo,
    token,
    api="https://api.github.com",
    runner_watts=None,
    use_artifacts=True,
    grid_override=None,
    eia_key=None,
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
    factor_for = region_factor_resolver(eia_key=eia_key, override=grid_override)

    # Runs whose jobs reported themselves are already measured — exactly, and
    # for free. Only the rest need a request each. Instrumentation therefore
    # pays off from the very first workflow file rather than at some threshold:
    # every job you add removes a request from next week's refresh, and the
    # answer stays correct the whole way through.
    by_run = {}
    if use_artifacts:
        by_run, _ = artifact_kwh_by_run(repo, token, api, runner_watts, factor_for)
    # How many markers a fully instrumented run of each workflow should have.
    expected = _expected_markers(runs, by_run, repo, token, api) if by_run else {}

    kwh, grams, total_jobs, used_markers = 0.0, 0.0, 0, 0
    measured_kwh, guessed_kwh = 0.0, 0.0
    # A run priced from the API reports no region, so it takes the repo-level
    # factor: no region to look up means no provider to ask. A partly
    # instrumented repo therefore mixes live-per-region and world-average
    # pricing, which is still strictly better than all-average.
    api_factor = grid_factor_for(None, grid_override)
    for run in runs:
        entry = by_run.get(run.get("id"))
        want = expected.get(run.get("workflow_id"), 0)
        # Trust a run only when it reported every job. A short count means
        # instrumentation is still landing on that workflow, so the markers are
        # discarded and the API priced for the whole run — adding them would
        # double-count the jobs that did report.
        if entry is not None and entry[2] >= want:
            kwh += entry[0]
            grams += entry[1]
            measured_kwh += entry[0]
            used_markers += entry[2]
            total_jobs += entry[2]
        else:
            run_kwh, run_jobs_seen, run_guessed = _run_kwh(
                run, repo, token, api, runner_watts, undeclared
            )
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
            for r in runs
            if (e := by_run.get(r.get("id"))) and e[2] >= expected.get(r.get("workflow_id"), 0)
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
    return CiUsage(kwh, grams, used_markers, total_jobs, measured_kwh, guessed_kwh)


# --------------------------------------------------------------------------
# Live grid factors.
#
# The static table above is an annual average. Real grids swing several times
# over within a day — Germany ran 146 to 634 gCO2eq/kWh in one measured day —
# so a live figure is worth far more than any refinement to the wattages.
#
# Four providers, tried in order of how much they cost the user:
#
#   energy-charts.info   no key at all, 15-min, absolute      much of Europe
#   carbonintensity.org.uk  no key at all, 30-min, absolute   Great Britain
#   ci-api               no key at all, hourly, absolute      everywhere else
#   EIA                  free key, hourly, fuel mix           United States
#
# ci-api covers every region in the table below, so EIA is now only reached
# when it is unavailable. It stays because its US numbers are computed from the
# balancing authority's fuel mix directly, with no third party in between.
#
# Electricity Maps is deliberately not in this chain. Its free tier is one zone
# at 50 requests/hour and non-commercial, which cannot serve CI that lands in a
# different region run to run; --grid-region still uses it for anyone on a paid
# plan, and an explicit --grid-intensity still overrides everything.
# --------------------------------------------------------------------------

# Azure region -> energy-charts country code. Only regions that provider covers
# appear; anything else falls through to the next provider or the table.
_REGION_ENERGY_CHARTS = {
    "germanywestcentral": "de",
    "francecentral": "fr",
    "francesouth": "fr",
    "italynorth": "it",
    "polandcentral": "pl",
    "spaincentral": "es",
    "westeurope": "nl",
    "norwayeast": "no",
    "norwaywest": "no",
}
_REGION_UK = {"uksouth", "ukwest"}

# Azure region -> key into the Carbon Intensity API's world snapshot: an ISO-2
# country, or "<COUNTRY>/<ZONE>" for a grid its operator publishes below
# national level. The US rows use the EIA balancing authority rather than the
# country, for the same reason _REGION_EIA_BA does: a national average blurs
# CAISO and ERCOT together, and those are different grids. Canada is CA/ON for
# the same reason: Ontario is the province with a live feed, and the one the
# central Canadian region draws from.
_REGION_CI_API = {
    "norwayeast": "NO",
    "norwaywest": "NO",
    "swedencentral": "SE",
    "switzerlandnorth": "CH",
    "francecentral": "FR",
    "francesouth": "FR",
    "uksouth": "GB",
    "ukwest": "GB",
    "northeurope": "IE",
    "westeurope": "NL",
    "germanywestcentral": "DE",
    "italynorth": "IT/NORD",
    "spaincentral": "ES",
    "polandcentral": "PL",
    "eastus": "US/PJM",
    "eastus2": "US/PJM",
    "northcentralus": "US/PJM",
    "centralus": "US/MISO",
    "southcentralus": "US/ERCO",
    "westus": "US/CISO",
    "westus2": "US/BPAT",
    "westus3": "US/AZPS",
    "canadacentral": "CA/ON",
    "brazilsouth": "BR",
    "japaneast": "JP",
    "japanwest": "JP",
    "koreacentral": "KR",
    "southeastasia": "SG",
    "eastasia": "HK",
    "centralindia": "IN",
    "southindia": "IN",
    "westindia": "IN",
    "australiaeast": "AU/NSW1",
    "australiasoutheast": "AU/VIC1",
    "uaenorth": "AE",
    "southafricanorth": "ZA",
}

CI_API_BASE = "https://ci-api.fabiocicerchia.it"

# The API publishes no freshness flag: responses are static objects served
# straight from a bucket, with nothing in the request path to evaluate one. Its
# pipeline runs hourly, so 65 minutes means a run was missed and the snapshot
# no longer describes the hour it claims.
CI_API_MAX_AGE_S = 65 * 60

# Azure region -> EIA balancing authority. The grid a datacentre draws from is
# the balancing authority for its location, not the state.
_REGION_EIA_BA = {
    "eastus": "PJM",
    "eastus2": "PJM",
    "northcentralus": "PJM",  # northern Illinois is ComEd, inside PJM
    "centralus": "MISO",  # Iowa
    "southcentralus": "ERCO",  # Texas
    "westus": "CISO",  # California
    "westus2": "BPAT",  # Washington, Bonneville
    "westus3": "AZPS",  # Arizona
}

# Lifecycle emission factors, gCO2e/kWh, IPCC AR5 Annex III medians. Lifecycle
# rather than combustion-only, to match how Electricity Maps and the static
# table above are expressed — mixing the two would understate renewables.
# https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf
IPCC_FUEL_G_PER_KWH = {
    "COL": 820.0,  # coal
    "NG": 490.0,  # natural gas, combined cycle
    "OIL": 650.0,  # not in AR5; between coal and gas, commonly cited
    "NUC": 12.0,
    "WAT": 24.0,  # hydro
    "WND": 11.0,  # onshore wind
    "SUN": 45.0,  # utility solar PV
    "GEO": 38.0,
    "BIO": 230.0,
    "OTH": 490.0,  # unknown mix; gas is the least-bad neutral guess
}


def _energy_charts_factor(country, get=requests.get):
    """Latest absolute gCO2eq/kWh from Fraunhofer ISE. No key, 15-minute data."""
    r = get(
        "https://api.energy-charts.info/co2eq",
        params={"country": country},
        timeout=20,
    )
    r.raise_for_status()
    values = [v for v in (r.json().get("co2eq") or []) if v is not None]
    return float(values[-1]) if values else None


def _uk_factor(get=requests.get):
    """Great Britain, from NESO. No key, half-hourly."""
    r = get("https://api.carbonintensity.org.uk/intensity", timeout=20)
    r.raise_for_status()
    entry = (r.json().get("data") or [{}])[0].get("intensity", {})
    value = entry.get("actual")
    if value is None:
        value = entry.get("forecast")  # the current half-hour is not settled yet
    return float(value) if value is not None else None


def _to_float(value):
    """A number from a JSON field, or 0.0 when the field is not one.

    EIA sends its figures as JSON strings and occasionally as null; a row that
    does not parse is dropped rather than failing the whole hour.
    """
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _eia_factor(balancing_authority, api_key, get=requests.get):
    """US, computed from the balancing authority's fuel mix.

    EIA publishes generation by fuel type, not carbon intensity, so this is the
    one provider where the number is ours: generation-weighted IPCC lifecycle
    factors over the most recent hour. Documented in docs/assumptions.md,
    because it is a model rather than a measurement.
    """
    r = get(
        "https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/",
        params={
            "api_key": api_key,
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": balancing_authority,
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 40,
        },
        timeout=25,
    )
    r.raise_for_status()
    rows = r.json().get("response", {}).get("data", [])
    if not rows:
        return None

    # Only the newest hour present, so a partially reported hour cannot mix
    # with the one before it.
    newest = rows[0].get("period")
    mwh, grams = 0.0, 0.0
    for row in rows:
        if row.get("period") != newest:
            continue
        value = _to_float(row.get("value"))
        if value <= 0:  # net-negative rows are storage discharge accounting
            continue
        factor = IPCC_FUEL_G_PER_KWH.get(row.get("fueltype"))
        if factor is None:
            continue
        mwh += value
        grams += value * factor
    return grams / mwh if mwh else None


def _ci_api_snapshot(get=requests.get):
    """The whole world in one request, from https://ci-api.fabiocicerchia.it.

    Per-region lookups would be the obvious shape and are the wrong one: the
    API is rate-limited to 1 request per 10s per IP as a CDN rule, and a badge
    refresh resolves several regions back to back, so every lookup after the
    first would collect a 429 instead of a number. `/v1/latest.json` is every
    country and every zone in a single object, which makes N regions cost one
    request no matter how many N is.
    """
    r = get(f"{CI_API_BASE}/v1/latest.json", timeout=25)
    r.raise_for_status()
    return r.json()


def _ci_api_factor(key, snapshot, now=None):
    """gCO2e/kWh for a `_REGION_CI_API` key, or None if the snapshot can't say.

    Reports `consumption_lifecycle`: upstream emissions plus the trade
    adjustment, the most complete of the four figures published and the one the
    API tells clients to use. Zone readings carry no consumption figures at all
    — the import adjustment is a national number and does not describe one
    bidding zone — so they report `lifecycle`, which is on the same lifecycle
    scope as IPCC_FUEL_G_PER_KWH and the static table.

    Returns None rather than a number for a reading that is not a measurement.
    `basis == "annual-average"` is the API's fallback for a grid with no live
    feed: a yearly constant, which is exactly what AZURE_REGION_GRID already
    holds. Taking it here would print "live at 477" for a figure no more live
    than the table's, which is the one failure mode worth refusing outright.
    """
    if not isinstance(snapshot, dict):
        return None

    stamp = snapshot.get("generated_at")
    try:
        generated = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    if (now - generated).total_seconds() > CI_API_MAX_AGE_S:
        return None

    if "/" in key:
        reading = (snapshot.get("zones") or {}).get(key)
    else:
        reading = (snapshot.get("countries") or {}).get(key)
    if not isinstance(reading, dict) or reading.get("basis") != "measured":
        return None

    for name in ("consumption_lifecycle", "lifecycle", "direct"):
        value = reading.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def live_region_factor(region, eia_key=None, get=requests.get, ci_api=None):
    """A live gCO2e/kWh for an Azure region, or None if nothing covers it.

    Never raises: a grid lookup failing must not fail a badge refresh. Every
    failure is reported, because silently falling back to an annual average
    while claiming live data would be worse than not trying.
    """
    if not region:
        return None
    try:
        if region in _REGION_UK:
            return _uk_factor(get=get)
        country = _REGION_ENERGY_CHARTS.get(region)
        if country:
            return _energy_charts_factor(country, get=get)
        key = _REGION_CI_API.get(region)
        if key and ci_api is not None:
            factor = _ci_api_factor(key, ci_api())
            if factor is not None:
                return factor
        ba = _REGION_EIA_BA.get(region)
        if ba and eia_key:
            return _eia_factor(ba, eia_key, get=get)
    except Exception as exc:  # noqa: BLE001 — injected `get`, any failure must fall back
        print(
            f"carbon-badge: live grid lookup failed for {region} ({exc}); "
            "using the annual average for that region",
            file=sys.stderr,
        )
    return None


def region_factor_resolver(eia_key=None, override=None, get=requests.get):
    """Memoised region -> factor, so each distinct region costs one request.

    A month of runs touches a handful of regions, so this is a few calls per
    refresh, not one per job.
    """
    cache = {}
    snapshot = []  # one-slot memo; [] means "not fetched", [None] means "tried"

    def ci_api():
        if not snapshot:
            try:
                snapshot.append(_ci_api_snapshot(get=get))
            except Exception as exc:  # noqa: BLE001 - a failed snapshot must not fail the refresh
                print(f"carbon-badge: ci-api snapshot failed ({exc})", file=sys.stderr)
                snapshot.append(None)
        return snapshot[0]

    def resolve(region):
        if override is not None:
            return override
        if region not in cache:
            live = live_region_factor(region, eia_key=eia_key, get=get, ci_api=ci_api)
            if live is not None:
                print(
                    f"carbon-badge: {region} live at {live:.0f} gCO2e/kWh "
                    f"(annual average is {grid_factor_for(region):.0f})",
                    file=sys.stderr,
                )
            cache[region] = live if live is not None else grid_factor_for(region)
        return cache[region]

    return resolve


# Jobs self-report into an artifact *name*, which the artifacts API returns in
# its listing — so reading a month of exact per-job measurements costs one
# request per 100 artifacts and downloads nothing.
#
#   carbon.v1.<seconds>.<vcpu>.<memMB>.<platform>.<region>.<slug>
#
# <platform> is one of the RUNNER_POWER_W keys, because CPU and memory alone do
# not determine draw: the same 4 vCPU / 16 GiB reading means a very different
# wattage on Apple silicon than on a shared x86 VM.
#
# Versioned because the name is the wire format. Artifact names may not contain
# " : < > | * ? \ /, which is why the separator is a dot and the job slug is
# sanitised at the source.
_ARTIFACT_RE = re.compile(r"^carbon\.v1\.(\d+)\.(\d+)\.(\d+)\.([a-z]+)\.([a-z0-9-]+)\.")

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
WATTS_BASE = 1.2
WATTS_PER_GB = 0.1125
# What a standard runner has, and therefore what the table entries describe.
# Larger GitHub runners keep this ratio, so a core count implies a memory size.
BASELINE_MEM_GB = 16
MEM_PER_VCPU_MB = 1024 * BASELINE_MEM_GB // BASELINE_VCPU


# Eco-CI measures the same 4-vCPU slice at 1.76 W idle and 8.18 W at full
# load, so 21.5% of the full-load figure is drawn whatever the job is doing and
# the remaining 78.5% scales with CPU utilisation.
#
# The API exposes no utilisation, so the default remains the full-load figure —
# the honest reading of "we do not know" for a tool whose output should not
# flatter the caller. --load-factor lets someone who *does* know say so: an
# I/O-bound job that averages 25% CPU is overstated by roughly 3x at the
# default, which is the single largest error left in the model.
IDLE_FRACTION = 1.76 / 8.18

# Set once from --load-factor. Module state rather than a parameter threaded
# through six call sites: this scales the last step of a calculation every
# route already shares, and the alternative was a wider diff for no more
# correctness.
LOAD_FACTOR = 1.0


def apply_load_factor(watts, load_factor=None):
    """Scale a full-load wattage to a stated average CPU utilisation.

    Only the variable part scales: a machine at 0% CPU still draws its idle
    power, so a load factor of 0 is not zero watts. That is why this is not a
    plain multiply, which would have made `--load-factor 0.25` understate by
    about the same margin the default overstates.
    """
    if load_factor is None:
        load_factor = LOAD_FACTOR
    if load_factor >= 1:
        return watts
    load_factor = max(0.0, float(load_factor))
    return round(watts * (IDLE_FRACTION + (1 - IDLE_FRACTION) * load_factor), 2)


def watts_from_specs(vcpu, mem_mb, platform="ubuntu"):
    """Power draw from a machine's CPU count, memory and platform.

    The single pricing law. Both routes into it — a runner recognised by its
    label, and a job that reported its own hardware — must produce the same
    answer for the same machine, or a repo's figure moves as it instruments
    without anything in the world changing.

    Affine, not proportional: a machine has a fixed draw plus a per-core one.
    Eco-CI measures 1.76 W at idle rising to 8.18 W at full load, so doubling
    the cores does not double the wattage. The label path used to scale the
    table value proportionally, which agreed with this only at the 4-vCPU
    calibration point and drifted to 12% by 64 cores.

    Per-core draw is derived from each platform's table entry rather than
    hardcoded, so the two stay tied together by construction: at the standard
    ratio the model reproduces the table exactly, for every platform.

    macOS is the exception — dedicated Apple hardware, not a slice of a shared
    host, so its draw does not track a vCPU count at all.
    """
    if platform == "macos":
        return apply_load_factor(RUNNER_POWER_W["macos"])
    baseline_w = RUNNER_POWER_W.get(platform, DEFAULT_RUNNER_POWER_W)
    per_vcpu = (baseline_w - WATTS_BASE - WATTS_PER_GB * BASELINE_MEM_GB) / BASELINE_VCPU
    # Rounded so the two routes compare equal rather than differing in float
    # noise, and so the log prints a sane number.
    return apply_load_factor(
        round(WATTS_BASE + per_vcpu * vcpu + WATTS_PER_GB * (mem_mb / 1024), 2)
    )


def parse_carbon_artifact(name):
    """ "carbon.v1.142.4.16384.ubuntu.build" -> (142.0 s, 4 vcpu, 16384 MB, "ubuntu").

    None for anything that is not one of ours, so a repo's normal build
    artifacts sitting in the same listing are simply ignored.
    """
    match = _ARTIFACT_RE.match(name or "")
    if not match:
        return None
    seconds, vcpu, mem_mb, platform, region = match.groups()
    return (float(seconds), int(vcpu), int(mem_mb), platform, region)


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
    observed, sampled, seen = {}, {}, set()
    for run in runs:
        wf = run.get("workflow_id")
        entry = by_run.get(run.get("id"))
        if entry is None:
            continue
        # entry is (kwh, grams, marker_count) — the count, not the energy.
        observed[wf] = max(observed.get(wf, 0), entry[2])
        if wf not in seen:
            seen.add(wf)
            try:
                sampled[wf] = sum(1 for j in run_jobs(run["id"], repo, token, api) if _ran(j))
            except Exception:  # noqa: BLE001 — injected `get`, any failure must fall back
                sampled[wf] = 0  # unreachable sample; the observed bound stands
    return {wf: max(observed.get(wf, 0), sampled.get(wf, 0)) for wf in observed}


def artifact_kwh_by_run(
    repo, token, api="https://api.github.com", runner_watts=None, factor_for=None
):
    """{run_id: (kWh, grams, marker_count)} for runs whose jobs recorded themselves.

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
        kwh, grams, count = by_run.get(run_id, (0.0, 0.0, 0))
        by_run[run_id] = (kwh + job_kwh, grams + job_g, count + 1)
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
    --minutes estimate silently used the DEFAULT_RUNNER_POWER_W baseline
    however big the runners were actually declared to be.

    Sanity anchor, the same shape as greenlint's core_seconds_per_gram: at 480
    gCO2e/kWh a gram is 7.5 kJ, so one 9.4 W standard runner earns a gram every
    ~13 minutes. A badge reading 100 gCO2e/mo is therefore claiming about 22
    runner-hours a month — if that does not match the repo, the error is here
    and not in the grid factor.
    """
    return minutes * (watts / 1000 / 60) * grid_intensity


def grams_co2e_kwh(kwh, grid_intensity=DEFAULT_GRID_INTENSITY):
    """Convert kWh to gCO2e for the given grid intensity."""
    return kwh * grid_intensity


def gitlab_kwh_last_30d(
    project,
    token,
    api="https://gitlab.com/api/v4",
    runner_watts=None,
    grid_override=None,
):
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
    factor = grid_factor_for(None, grid_override)
    return CiUsage(kwh, kwh * factor, 0, total_jobs, 0.0, guessed_kwh)


def live_grid_intensity(
    zone, token=None, api="https://api.electricitymap.org/v3", get=requests.get
):
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
        r = get(
            f"{api}/carbon-intensity/history",
            params={"zone": zone},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        readings = [
            float(h["carbonIntensity"])
            for h in r.json().get("history", [])
            if h.get("carbonIntensity") is not None
        ]
        if readings:
            return sum(readings) / len(readings)
        raise ValueError("history returned no usable readings")
    except Exception as exc:  # noqa: BLE001 — injected `get`, any failure must fall back
        print(
            f"carbon-badge: 24h grid history unavailable ({exc}); using the "
            "instantaneous reading, which prices a month at one moment",
            file=sys.stderr,
        )

    r = get(
        f"{api}/carbon-intensity/latest",
        params={"zone": zone},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    return float(r.json()["carbonIntensity"])


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
        "color": BADGE_COLOR,
    }


def estimate(args, token):
    """Run one carbon estimate for the parsed CLI args. Returns (endpoint_json, detail)."""
    # None means "nothing declared, use each job's own region where it reported
    # one". Any explicit figure overrides that for every job.
    grid_intensity = args.grid_intensity
    if args.grid_region:
        em_token = args.electricitymaps_token or os.environ.get("ELECTRICITYMAPS_TOKEN")
        grid_intensity = live_grid_intensity(args.grid_region, token=em_token)
        print(
            f"carbon-badge: grid factor for {args.grid_region} = "
            f"{grid_intensity:.0f} gCO2e/kWh (24h mean)",
            file=sys.stderr,
        )
    runner_watts = parse_runner_watts(getattr(args, "runner_watts", None))
    usage = None
    if args.minutes is not None:
        watts = runner_watts.get(ANY_RUNNER, DEFAULT_RUNNER_POWER_W)
        # No jobs, so no regions: the offline path takes one factor.
        grams = grams_co2e(args.minutes, grid_factor_for(None, grid_intensity), watts)
        detail = f"{args.minutes:.0f} CI min/30d at {watts:g} W"
    elif args.provider == "gitlab":
        usage = gitlab_kwh_last_30d(
            args.repo,
            token,
            api=args.api or "https://gitlab.com/api/v4",
            runner_watts=runner_watts,
            grid_override=grid_intensity,
        )
        grams = usage.grams
        detail = f"{usage.kwh:.3f} kWh/30d, confidence: {confidence(usage)}"
    else:
        usage = ci_kwh_last_30d(
            args.repo,
            token,
            api=args.api or "https://api.github.com",
            runner_watts=runner_watts,
            use_artifacts=not getattr(args, "ignore_self_reported", False),
            grid_override=grid_intensity,
            eia_key=getattr(args, "eia_key", None) or os.environ.get("EIA_API_KEY"),
        )
        grams = usage.grams
        level = confidence(usage)
        guessed_pct = 100 * usage.guessed_kwh / usage.kwh if usage.kwh else 0
        effective = usage.grams / usage.kwh if usage.kwh else 0
        detail = (
            f"{usage.kwh:.3f} kWh/30d at {effective:.0f} gCO2e/kWh effective, "
            f"confidence: {level} "
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
        default=None,
        metavar="G",
        help=(
            "gCO2e per kWh, applied to every job. Unset, each job that reported "
            "its Azure region is priced on that region's grid and the rest fall "
            f"back to {DEFAULT_GRID_INTENSITY:g} (world average). Ignored if "
            "--grid-region is set"
        ),
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
        "--eia-key",
        default=None,
        help=(
            "EIA API key (or EIA_API_KEY env) to price US regions from their "
            "balancing authority's live fuel mix. Free from "
            "eia.gov/opendata. European and UK regions need no key at all"
        ),
    )
    p.add_argument(
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

    if not 0 <= args.load_factor <= 1:
        p.error("--load-factor must be between 0 and 1")
    global LOAD_FACTOR
    LOAD_FACTOR = args.load_factor

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
