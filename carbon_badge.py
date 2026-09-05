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
import functools
import http.server
import json
import logging
import os
import re
import sys
import time
from collections import namedtuple
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

# One logger for the process. Diagnostics go here; the badge JSON — the tool's
# actual result — stays on stdout. Configured once in main(); a caller using
# this module as a library gets logging's own default behaviour.
log = logging.getLogger("carbon-badge")

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

# Datacentre overhead, applied to machine draw to get wall power.
#
# CHECKED AGAINST THE SOURCE CURVES, because applying this on top of a figure
# that already contained it would put every number here ~15% high. It does not:
#
#   - Cloud Energy, which produces the curves Eco-CI ships, describes its
#     output as "the estimation of the current power draw of the whole machine
#     in Watts" — the machine, not the facility. PUE, cooling and distribution
#     losses appear nowhere in that project.
#     https://github.com/green-coding-solutions/cloud-energy
#   - It is trained on SPECpower_ssj2008, which requires the power analyser to
#     sit between the AC line source and the system under test, with no active
#     component in between. That boundary is the server's own AC inlet, which
#     is precisely the denominator of PUE (facility power / IT equipment
#     power), so the two do not overlap.
#     https://www.spec.org/power_ssj2008/
#   - Eco-CI itself never applies one: the string "PUE" does not occur anywhere
#     in green-coding-solutions/eco-ci-energy-estimation, so it is neither
#     baked into the curves nor added by the action.
#
# Cloud Energy's own caveats say SPECpower machines "tend to be rather tuned
# and do not necessarily represent the reality of current datacenter
# configurations. So you are likely to get a too small value than a too high
# value" — so the base figure errs low, and multiplying it by PUE is not
# recovering an overhead it already had.
#
# GitHub's hosted runners are Azure VMs, so the hyperscale end is the right
# one: Cloud Carbon Footprint publishes 1.125 for Azure, 1.135 for AWS, 1.1
# for GCP. https://www.cloudcarbonfootprint.org/docs/methodology/
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

# What a standard runner has, and therefore what the table entries describe.
# Larger GitHub runners keep this ratio, so a core count implies a memory size.
BASELINE_MEM_GB = 16
MEM_PER_VCPU_MB = 1024 * BASELINE_MEM_GB // BASELINE_VCPU

# Published. World-average power-sector intensity for 2023: "CO2 intensity
# reached a new record low of 480 gCO2/kWh, down 1.2% from 486 gCO2/kWh in
# 2022" — Ember, Global Electricity Review 2024. It is in the "Electricity
# transition in 2023" chapter, not on the report landing page:
# https://ember-energy.org/latest-insights/global-electricity-review-2024/electricity-transition-in-2023/
# The figure drifts a few percent a year (486 in 2022, 480 in 2023, 473 in
# 2024), which is far inside the error bars on the wattages it multiplies.
# greenlint pins the same figure, and the two agreeing matters more than
# either tracking the latest annual revision.
# GitHub's and GitLab's JSON, as requests hands it over.
Json = dict[str, Any]
# The HTTP getter every network reader takes, so a test can pass its own.
Getter = Callable[..., Any]
# --runner-watts: a runner label, lowercased, to the wattage declared for it.
RunnerWatts = dict[str, float]

# Below this the two reconciliation paths agree to the last gram, and the row
# says nothing worth printing.
RECONCILE_EPSILON_G = 1e-9
# Above a kilogram the badge reads in kg, not g.
GRAMS_PER_KILOGRAM = 1000

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


def grid_factor_for(region: str, override: float | None = None) -> float:
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
    # The HARDWARE here is sourced; the DRAW is not, and that is the whole of
    # what makes this an estimate (see RUNNER_POWER_ESTIMATED).
    #
    # GitHub's GPU runner is `gpu-t4-4-core`: 4 vCPU, 28 GB RAM and one NVIDIA
    # Tesla T4 with 16 GB, which is Azure's Standard_NC4as_T4_v3 (NCasT4_v3
    # series: T4 GPUs on AMD EPYC 7V12 hosts).
    # https://github.blog/changelog/
    #   2024-07-08-github-actions-gpu-hosted-runners-are-now-generally-available/
    # https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/
    #   gpu-accelerated/ncast4v3-series
    #
    # So: a 4-vCPU host slice, as for "ubuntu", plus the T4's 70 W board power
    # limit — the card takes no supplemental power connector precisely because
    # 70 W is its ceiling (NVIDIA T4 product brief PB-09256-001).
    # https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/tesla-t4/
    #   t4-tensor-core-product-brief.pdf
    #
    # 8.18 + 70 = 78.2 W. What is NOT sourced is that a job draws the ceiling:
    # a board limit is not a mean, so this overstates anything short of a
    # saturated card and badly overstates a job that merely has a GPU attached.
    # Declare your own with --runner-watts gpu=<watts>. Two smaller caveats:
    # the host is an EPYC 7V12 (Rome) rather than the 7763 (Milan) the curve
    # was modelled on, and the SKU carries 28 GiB rather than the 16 GiB the
    # standard vCPU:memory ratio would imply.
    #
    # Flat, not core-scaled: the accelerator dominates and does not grow with
    # vCPUs. This was a flat 130 W with no working behind it, which implied a
    # ~60 W host — 7x what the same tool charges a 4-vCPU slice everywhere else.
    "gpu": round((8.18 + 70.0) * PUE, 1),
    # ARM: no measured curve, and none available to take. Eco-CI ships no Arm
    # entry at all — its machine-power-data holds EPYC 7763, EPYC 7B12, Xeon
    # 6246 and a Mac mini M1, and nothing else — so this is extrapolated from
    # the x86 baseline at ~40% less energy for equal work.
    #
    # Deliberately not the "3-4x more efficient" claim that circulates. AWS's
    # published figure is up to 60% less energy for equal work
    # (https://aws.amazon.com/ec2/graviton/) and independent benchmarks land
    # nearer 45-50%. greenlint's GL016 uses the same 40%.
    #
    # WORTH RE-CHECKING: GitHub's arm64 runners are Azure Cobalt 100, not
    # Graviton, and Microsoft reports ~30% lower power for web-server and
    # database workloads on Cobalt 100 against x86 — a figure for the actual
    # silicon, and a less flattering one than 40%. If it holds, the right
    # constant is 0.7 rather than 0.6 (6.6 W rather than 5.6 W) and this is a
    # one-line change. It is left at 0.6 because that source could not be
    # verified first-hand from here, not because it was dismissed.
    # https://azure.microsoft.com/en-us/blog/
    #   how-azure-cobalt-100-vms-are-powering-real-world-solutions-delivering-
    #   performance-and-efficiency-results/
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

# Which classes rest on a measured power curve and which are composed here.
#
# The distinction is not cosmetic: a reader cannot tell 9.4 W from 89.9 W apart
# by looking, and presenting an extrapolation next to a measurement without
# saying so is how an estimate acquires a precision it never had. Any run that
# prices a job on one of these says so on stderr and names the flag that
# replaces it.
RUNNER_POWER_ESTIMATED = {
    "gpu": ("composed from a 4-vCPU host slice plus one T4 at its 70 W board limit — a ceiling, not a measured mean"),
    "arm": (
        "extrapolated from the x86 curve at 40% less energy for equal work — "
        "a vendor claim, not a measurement; no Arm curve is published"
    ),
}

# Classes this run actually priced something on, so the warning fires only when
# an estimate is load-bearing for the figure being printed. Reset per estimate()
# rather than per process, so --serve reports each request on its own terms.
_ESTIMATED_USED = {}


def _note_runner_class(key: str) -> str:
    """Record that a figure came from a class with no measured curve behind it."""
    if key in RUNNER_POWER_ESTIMATED:
        _ESTIMATED_USED[key] = _ESTIMATED_USED.get(key, 0) + 1
    return key


def _warn_estimated_classes() -> None:
    """Say which of this run's numbers are estimates, and what replaces them."""
    for key, count in sorted(_ESTIMATED_USED.items(), key=lambda kv: -kv[1]):
        print(  # noqa: T201 — the tool's output
            f"carbon-badge: {count} job(s) priced on the '{key}' class at "
            f"{RUNNER_POWER_W[key]:g} W, which is an estimate: "
            f"{RUNNER_POWER_ESTIMATED[key]}. "
            f"Pass --runner-watts {key}=<watts> to price yours. "
            "See docs/assumptions.md",
            file=sys.stderr,
        )


DEFAULT_RUNNER_POWER_W = RUNNER_POWER_W["ubuntu"]

# What a pass measured, and how much of it came from jobs that reported
# themselves. The badge shows the ratio so a reader can tell a fully measured
# figure from a partly inferred one.
CiUsage = namedtuple("CiUsage", "kwh grams measured_jobs total_jobs measured_kwh guessed_kwh")

# What one run's markers add up to. `markers` is a count of jobs that reported
# themselves, not energy — it is the numerator of the completeness check, and
# reading it as a third float is the mistake the positional tuple invited.
RunTotals = namedtuple("RunTotals", "kwh grams markers")

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


def parse_runner_watts(pairs: list[str]) -> RunnerWatts:
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
    for raw in pairs or []:
        pair = raw.strip()
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


def _declared_watts(labels_lower: list[str], overrides: RunnerWatts | None) -> float | None:
    """A --runner-watts figure for these labels, or None if none was declared.

    Exact label first, then a substring one (so `gpu=320` prices a family), then
    the blanket entry: a declared blanket figure beats the built-in guess table,
    because the developer knows what their runners are and the API does not.
    """
    for label in labels_lower:
        if label in overrides:
            return overrides[label]
    for key, watts in overrides.items():
        if key != ANY_RUNNER and any(key in label for label in labels_lower):
            return watts
    return overrides.get(ANY_RUNNER)


def _table_watts(labels_lower: list[str]) -> float | None:
    """The built-in GitHub-hosted figure for these labels, or None.

    Checked in RUNNER_POWER_W's own order, first substring match wins, and
    scaled by any core count the label carries.
    """
    for key, watts in RUNNER_POWER_W.items():
        if not any(key in label for label in labels_lower):
            continue
        _note_runner_class(key)
        if key in _NO_CORE_SCALING:
            return apply_load_factor(watts)
        cores = next(
            (int(match.group(1)) for label in labels_lower if (match := _CORES_RE.search(label))),
            None,
        )
        if not cores:
            return apply_load_factor(watts)
        # Through the same law the self-reported path uses, assuming the
        # standard memory-per-vCPU ratio, so the two agree for any size
        # rather than only at the calibration point.
        return watts_from_specs(cores, cores * MEM_PER_VCPU_MB, key)
    return None


def runner_power_w(labels: list[str], overrides: RunnerWatts | None = None) -> float:
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
    declared = _declared_watts(labels_lower, overrides or {})
    if declared is not None:
        return apply_load_factor(declared)
    return _table_watts(labels_lower)


def _ran(job: Json) -> bool:
    """Did this job actually execute, and so could it have recorded itself?

    A skipped job still appears in the jobs API with both timestamps set — and
    occasionally with completed_at *before* started_at — but none of its steps
    run, so it can never write a marker. Counting one in the denominator means
    a workflow with any conditional job can never reach completeness: it would
    be priced from the API on every run, however thoroughly instrumented.
    """
    return job.get("conclusion") != "skipped"


def _hours(start: str, end: str) -> float:
    """Hours between two ISO timestamps, or 0 if either is missing."""
    if not (start and end):
        return 0.0
    dt = datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))
    return max(dt.total_seconds(), 0) / 3600


# Pagination backstop. Generous — a repo doing more than this in 30 days is
# unusual — but a cap that silently drops data would read as a quiet month
# rather than a truncated query, so hitting it is always reported.
_MAX_PAGES = 20

# Requested per page, and therefore what a short page means: fewer than this
# many results is the last page. The two readings have to stay the same number,
# which is why it is one name and not two literals.
# 100, not the API's default of 30: a matrix wider than 30 jobs was silently
# truncated, and this now also feeds the confidence ratio.
_PAGE_SIZE = 100


def _get_pages(
    url: str, token: str | None, key: str, params: dict[str, str] | None = None
) -> tuple[list[Json], int, bool]:
    """Every item under `key` across pages -> (items, total_count, hit_page_cap).

    The three GitHub listings this tool reads page identically, and the pieces
    that have to agree — the requested page size and the short-page test that
    ends the loop — are the ones that would silently disagree if each caller
    kept its own copy. `total_count` is None for a listing that omits it.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    items, page, total = [], 1, None
    while page <= _MAX_PAGES:
        response = requests.get(
            url,
            params={**(params or {}), "per_page": _PAGE_SIZE, "page": page},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        total = payload.get("total_count", total)
        batch = payload.get(key, [])
        items.extend(batch)
        # A short page is the last page — stop rather than spend a request
        # confirming the next one is empty.
        if len(batch) < _PAGE_SIZE:
            break
        page += 1
    return items, total, page > _MAX_PAGES


def run_jobs(run_id: str, repo: str, token: str | None, api: str = "https://api.github.com") -> list[Json]:
    """Fetch the jobs (with per-job runner labels/timing) for one run."""
    jobs, _, _ = _get_pages(f"{api}/repos/{repo}/actions/runs/{run_id}/jobs", token, "jobs")
    return jobs


def _warn_if_truncated(kind: str, fetched: int, total: int, hit_page_cap: bool) -> None:
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
    undeclared = {}
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
            if (entry := by_run.get(run.get("id"))) and entry.markers >= expected.get(run.get("workflow_id"), 0)
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


def _energy_charts_factor(country: str, get: Getter = requests.get) -> float | None:
    """Latest absolute gCO2eq/kWh from Fraunhofer ISE. No key, 15-minute data."""
    response = get(
        "https://api.energy-charts.info/co2eq",
        params={"country": country},
        timeout=20,
    )
    response.raise_for_status()
    values = [v for v in (response.json().get("co2eq") or []) if v is not None]
    return float(values[-1]) if values else None


def _uk_factor(get: Getter = requests.get) -> float | None:
    """Great Britain, from NESO. No key, half-hourly."""
    response = get("https://api.carbonintensity.org.uk/intensity", timeout=20)
    response.raise_for_status()
    entry = (response.json().get("data") or [{}])[0].get("intensity", {})
    value = entry.get("actual")
    if value is None:
        value = entry.get("forecast")  # the current half-hour is not settled yet
    return float(value) if value is not None else None


def _to_float(value: Any) -> float | None:
    """A number from a JSON field, or 0.0 when the field is not one.

    EIA sends its figures as JSON strings and occasionally as null; a row that
    does not parse is dropped rather than failing the whole hour.
    """
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _eia_factor(balancing_authority: str, api_key: str, get: Getter = requests.get) -> float | None:
    """US, computed from the balancing authority's fuel mix.

    EIA publishes generation by fuel type, not carbon intensity, so this is the
    one provider where the number is ours: generation-weighted IPCC lifecycle
    factors over the most recent hour. Documented in docs/assumptions.md,
    because it is a model rather than a measurement.
    """
    response = get(
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
    response.raise_for_status()
    rows = response.json().get("response", {}).get("data", [])
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


def _ci_api_snapshot(get: Getter = requests.get) -> Json | None:
    """The whole world in one request, from https://ci-api.fabiocicerchia.it.

    Per-region lookups would be the obvious shape and are the wrong one: the
    API is rate-limited to 1 request per 10s per IP as a CDN rule, and a badge
    refresh resolves several regions back to back, so every lookup after the
    first would collect a 429 instead of a number. `/v1/latest.json` is every
    country and every zone in a single object, which makes N regions cost one
    request no matter how many N is.
    """
    response = get(f"{CI_API_BASE}/v1/latest.json", timeout=25)
    response.raise_for_status()
    return response.json()


def _measured_reading(key: str, snapshot: Json | None, now: float | None = None) -> Json | None:
    """The `_REGION_CI_API` reading for `key`, or None if it cannot be trusted.

    Three ways it cannot: the snapshot is not an object, it is older than
    CI_API_MAX_AGE_S (the pipeline runs hourly, so a stale one describes an
    hour that has passed), or the reading is not a measurement. A zone key
    ("<COUNTRY>/<ZONE>") is published under `zones`, a country under
    `countries`.
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
    bucket = "zones" if "/" in key else "countries"
    reading = (snapshot.get(bucket) or {}).get(key)
    if not isinstance(reading, dict) or reading.get("basis") != "measured":
        return None
    return reading


def _ci_api_factor(key: str, snapshot: Json | None, now: float | None = None) -> float | None:
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
    reading = _measured_reading(key, snapshot, now)
    if reading is None:
        return None
    for name in ("consumption_lifecycle", "lifecycle", "direct"):
        value = reading.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def live_region_factor(
    region: str, eia_key: str | None = None, get: Getter = requests.get, ci_api: Json | None = None
) -> float:
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
    except Exception as exc:
        log.warning(
            "live grid lookup failed for %s (%s); using the annual average for that region",
            region,
            exc,
        )
    return None


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
        self._cache = {}
        # One-slot memo: [] means "not fetched", [None] means "tried and failed".
        self._snapshot = []

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


def apply_load_factor(watts: float, load_factor: float | None = None) -> float:
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


def watts_from_specs(vcpu: int, mem_mb: int, platform: str = "ubuntu") -> float:
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
    # Both routes price the same machine the same way, so both have to admit to
    # the same estimate — a self-reported arm job is no better founded than a
    # label-matched one.
    if platform in RUNNER_POWER_W:
        _note_runner_class(platform)
    baseline_w = RUNNER_POWER_W.get(platform, DEFAULT_RUNNER_POWER_W)
    per_vcpu = (baseline_w - WATTS_BASE - WATTS_PER_GB * BASELINE_MEM_GB) / BASELINE_VCPU
    # Rounded so the two routes compare equal rather than differing in float
    # noise, and so the log prints a sane number.
    return apply_load_factor(round(WATTS_BASE + per_vcpu * vcpu + WATTS_PER_GB * (mem_mb / 1024), 2))


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
    runs: list[Json], by_run: dict[str, Any], repo: str, token: str | None, api: str
) -> dict[str, int]:
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
        workflow_id = run.get("workflow_id")
        entry = by_run.get(run.get("id"))
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
) -> tuple[dict[str, Any], int]:
    """{run_id: RunTotals} for runs whose jobs recorded themselves.

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
        so_far = by_run.get(run_id, RunTotals(0.0, 0.0, 0))
        by_run[run_id] = RunTotals(so_far.kwh + job_kwh, so_far.grams + job_g, so_far.markers + 1)
        jobs += 1
    return by_run, jobs


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

Divergence = namedtuple(
    "Divergence",
    "run_id workflow_id jobs marker_seconds api_seconds marker_kwh api_kwh marker_grams api_grams matched_jobs",
)

Reconciliation = namedtuple(
    "Reconciliation",
    "rows runs_total runs_compared marker_kwh api_kwh marker_grams api_grams "
    "marker_seconds api_seconds jobs setup_kwh model_kwh grid_grams api_factor",
)


def carbon_artifact_slug(name: str) -> str | None:
    """The job slug a marker carries, or None.

    Kept separate from parse_carbon_artifact rather than widening its tuple:
    every caller of that unpacks five values, and the slug is only ever wanted
    here.
    """
    if not _ARTIFACT_RE.match(name or ""):
        return None
    # The regex ends at the dot after the region; the slug is the remainder.
    rest = name[_ARTIFACT_RE.match(name).end() :]
    return rest or None


def job_slug(name: str) -> str:
    """An API job name reduced the way the reporting action reduces it.

    Best effort, and deliberately so: the sanitising happens in the action that
    writes the marker, not here, so this can only approximate it. A slug that
    does not match falls back to per-run comparison rather than being dropped —
    the run-level answer is still correct, it is just coarser.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or None


def _api_run_detail(
    run: Json, repo: str, token: str | None, api: str, runner_watts: RunnerWatts | None
) -> tuple[float, float, int, float, dict[str, Any], dict[str, int]]:
    """(kwh, seconds, jobs, guessed_kwh, per_job) for one run, from the API.

    Same arithmetic as _run_kwh — which now calls this — plus the seconds and
    the per-job breakdown that only the reconciliation needs.
    """
    kwh = seconds = guessed_kwh = 0.0
    jobs = 0
    per_job = {}
    undeclared = {}
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
        job_kwh = hours * watts / 1000
        kwh += job_kwh
        seconds += hours * 3600
        guessed_kwh += job_kwh if guessed else 0.0
        jobs += 1
        slug = job_slug(job.get("name"))
        if slug:
            # A matrix leg and its parent can slugify alike; sum rather than
            # overwrite, so the comparison is never quietly missing a job.
            prev = per_job.get(slug, (0.0, 0.0))
            per_job[slug] = (prev[0] + job_kwh, prev[1] + hours * 3600)
    return kwh, seconds, jobs, guessed_kwh, per_job, undeclared


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
    expected = _expected_markers(runs, by_run, repo, token, api) if by_run else {}
    api_factor = grid_factor_for(None, grid_override)

    marker_seconds_by_run = _marker_seconds_by_run(repo, token, api)

    rows = []
    undeclared = {}
    for run in runs:
        entry = by_run.get(run.get("id"))
        want = expected.get(run.get("workflow_id"), 0)
        # Only fully instrumented runs are comparable. A partly instrumented
        # one would show a divergence that is just the missing jobs, which is
        # not what any of the three terms above is about.
        if entry is None or entry[2] < want:
            continue
        a_kwh, a_seconds, a_jobs, _, a_per_job, a_undeclared = _api_run_detail(run, repo, token, api, runner_watts)
        for k, v in a_undeclared.items():
            undeclared[k] = undeclared.get(k, 0) + v
        m_slugs = marker_seconds_by_run.get(run.get("id"), {})
        matched = sum(1 for slug in m_slugs if slug in a_per_job)
        rows.append(
            Divergence(
                run_id=run.get("id"),
                workflow_id=run.get("workflow_id"),
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


def _marker_seconds_by_run(repo: str, token: str | None, api: str = "https://api.github.com") -> dict[str, float]:
    """{run_id: {slug: seconds}} — the raw durations the markers carry.

    Separate from artifact_kwh_by_run because that one has already priced them,
    and the seconds are what isolates setup time from the wattage models.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    out = {}
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
        slug = carbon_artifact_slug(artifact.get("name")) or f"?{len(out)}"
        out.setdefault(run_id, {})
        out[run_id][slug] = out[run_id].get(slug, 0.0) + parsed[0]
    return out


def _pct(part: float, whole: float) -> float:
    return (100 * part / whole) if whole else 0.0


def format_reconciliation(rec: Json, limit: int = 15) -> str:
    """The report. Per run, then the aggregate, then the decomposition."""
    if not rec.rows:
        return (
            f"carbon-badge: nothing to reconcile — {rec.runs_total} run(s) in the "
            f"window, none of them fully self-reported.\n"
            "Both paths need the same runs to compare, so a repo with no "
            "instrumented workflow has nothing to say here yet."
        )
    out = []
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


def rows_by_gap(rows: list[Any]) -> list[Any]:
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
    kwh, undeclared, page = 0.0, {}, 1
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


def confidence(usage: Json | None) -> str:
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


def endpoint_json(grams: float, usage: Json | None = None) -> Json:
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
    args: argparse.Namespace, token: str | None, runner_watts: RunnerWatts | None, grid_intensity: float
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
    args: argparse.Namespace, token: str | None, runner_watts: RunnerWatts | None, grid_intensity: float
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


def estimate(args: argparse.Namespace, token: str | None) -> tuple[CiUsage, float, str]:
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
    runner_watts = parse_runner_watts(getattr(args, "runner_watts", None))
    usage = None
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


class BadgeHandler(http.server.BaseHTTPRequestHandler):
    """Serve /badge.json from `compute()`, re-computing at most once per `ttl`.

    `cache` is owned by the caller and shared across every request: http.server
    builds a fresh handler per connection, so anything kept on `self` would be
    a cache of one request.
    """

    def __init__(
        self,
        compute: Callable[[], Json],
        ttl: int,
        cache: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._compute = compute
        self._ttl = ttl
        self._cache = cache
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path not in ("/", "/badge.json"):
            self.send_response(404)
            self.end_headers()
            return
        now = time.monotonic()
        if self._cache["t"] is None or now - self._cache["t"] > self._ttl:
            self._cache["body"] = json.dumps(self._compute()).encode()
            self._cache["t"] = now
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(self._cache["body"])

    def log_message(self, *args: Any) -> None:
        pass


def badge_handler(compute: Callable[[], Json], ttl: int = 300) -> type[http.server.BaseHTTPRequestHandler]:
    """A BadgeHandler bound to one compute() and one shared cache."""
    # `t=None` means "never computed yet" — not 0.0, since time.monotonic()'s
    # epoch is arbitrary (e.g. near-zero shortly after a container boots), so
    # `now - 0.0 > ttl` can be false on the very first request too, leaving
    # cache["body"] permanently empty.
    return functools.partial(BadgeHandler, compute, ttl, {"t": None, "body": b""})


# The default has to be every interface: the documented --serve deployment is a
# container with a published port (see examples/ci-platforms/), and 127.0.0.1
# inside one is unreachable from outside it. That does mean a process holding a
# CI token is listening on every interface of whatever host runs it, so --bind
# exists for anyone running it directly on a machine that has others.
DEFAULT_BIND = "0.0.0.0"  # noqa: S104 — deliberate, see above


def _logged_estimate(args: argparse.Namespace, token: str | None) -> tuple[CiUsage, float, str]:
    """One estimate, with the same one-line summary the CLI prints."""
    badge, detail = estimate(args, token)
    log.info("%s ≈ %s", detail, badge["message"])
    return badge


def serve(port: int, args: argparse.Namespace, token: str | None, ttl: int = 300, bind: str = DEFAULT_BIND) -> None:
    """Serve the badge JSON at /badge.json on bind:port.

    Recomputes at most once every `ttl` seconds (default 5 min) so repeated
    hits (Shields refreshes the endpoint on every badge view) don't hammer
    the CI/grid APIs.

    Binds every interface by default so the container deployment works; pass
    `bind` (--bind) to narrow it. The endpoint is unauthenticated and the
    process holds a CI token, so on a shared host that is worth doing.
    """

    log.info("serving /badge.json on %s:%d (ttl %ds)", bind, port, ttl)
    handler = badge_handler(lambda: _logged_estimate(args, token), ttl)
    # 0.0.0.0 by default because the usual deployment is a container whose
    # port is published; --bind exists for anyone who wants loopback.
    http.server.HTTPServer((bind, port), handler).serve_forever()


def _build_parser() -> argparse.ArgumentParser:
    """The CLI surface: every flag, its default and its help text."""
    parser = argparse.ArgumentParser(
        prog="carbon-badge",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("repo", help="owner/repo (GitHub) or group/project (GitLab)")
    parser.add_argument(
        "--provider",
        choices=["github", "gitlab"],
        default="github",
        help="CI provider to query (default %(default)s)",
    )
    parser.add_argument(
        "--api",
        default=None,
        help="override API base URL (GitHub Enterprise / self-hosted GitLab)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="API token (or GITHUB_TOKEN / GITLAB_TOKEN env, matching --provider)",
    )
    parser.add_argument(
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
    parser.add_argument(
        "--grid-region",
        default=None,
        metavar="ZONE",
        help="Electricity Maps zone (e.g. SE, US-CAL-CISO) for live grid intensity",
    )
    parser.add_argument(
        "--electricitymaps-token",
        default=None,
        help="Electricity Maps API token (or ELECTRICITYMAPS_TOKEN env)",
    )
    parser.add_argument(
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
    parser.add_argument(
        "--eia-key",
        default=None,
        help=(
            "EIA API key (or EIA_API_KEY env) to price US regions from their "
            "balancing authority's live fuel mix. Free from "
            "eia.gov/opendata. European and UK regions need no key at all"
        ),
    )
    parser.add_argument(
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
    parser.add_argument(
        "--ignore-self-reported",
        action="store_true",
        help=(
            "query the API for every run, ignoring what jobs recorded about "
            "themselves. Slower and no more accurate, so this exists for one "
            "job: reconciling the two paths against each other to see what the "
            "self-reported figure is missing (see docs/getting-started.md)"
        ),
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help=(
            "run BOTH paths over the same runs and report where they disagree, "
            "split into setup time, the watts model and the grid factor. A "
            "diagnostic, not a badge: it prices every run from the API as well "
            "as reading every marker, which is the work the normal path exists "
            "to avoid. See docs/assumptions.md"
        ),
    )
    parser.add_argument(
        "--minutes",
        type=float,
        default=None,
        help="skip the API and use this many CI minutes (testing/offline)",
    )
    parser.add_argument(
        "--serve",
        type=int,
        default=None,
        metavar="PORT",
        help="serve /badge.json on this port instead of printing once and exiting",
    )
    parser.add_argument(
        "--bind",
        default=DEFAULT_BIND,
        metavar="ADDR",
        help=(
            f"interface for --serve (default: {DEFAULT_BIND}, which a container needs; use 127.0.0.1 on a shared host)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse args, estimate emissions, emit badge JSON."""
    # stderr, and the tool's name in the format rather than in every message.
    logging.basicConfig(level=logging.INFO, format="carbon-badge: %(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not 0 <= args.load_factor <= 1:
        parser.error("--load-factor must be between 0 and 1")
    # The load factor is a property of the run, read by functions several
    # layers down that take no config object. Threading one through purely to
    # carry a single float would be a larger change than the rule prevents.
    global LOAD_FACTOR  # noqa: PLW0603
    LOAD_FACTOR = args.load_factor

    # Validated up front so a typo fails the command rather than surfacing as a
    # traceback from inside estimate() — or, under --serve, on every request.
    try:
        parse_runner_watts(args.runner_watts)
    except ValueError as exc:
        parser.error(f"--runner-watts: {exc}")

    env_var = "GITLAB_TOKEN" if args.provider == "gitlab" else "GITHUB_TOKEN"
    token = args.token or os.environ.get(env_var)

    if args.serve:
        serve(args.serve, args, token, bind=args.bind)
        return 0

    if args.reconcile:
        # Prints to stdout and emits no badge JSON: this is a report to read,
        # not a value to pipe into Shields.
        if args.provider != "github":
            parser.error("--reconcile needs the GitHub artifact markers; not available for GitLab")
        if args.minutes is not None:
            parser.error("--reconcile compares two API paths; --minutes uses neither")
        grid = args.grid_intensity
        if args.grid_region:
            em_token = args.electricitymaps_token or os.environ.get("ELECTRICITYMAPS_TOKEN")
            grid = live_grid_intensity(args.grid_region, token=em_token)
        rec = reconcile_last_30d(
            args.repo,
            token,
            api=args.api or "https://api.github.com",
            runner_watts=parse_runner_watts(getattr(args, "runner_watts", None)),
            grid_override=grid,
            eia_key=getattr(args, "eia_key", None) or os.environ.get("EIA_API_KEY"),
        )
        print(format_reconciliation(rec))  # noqa: T201 — the tool's output
        return 0

    badge, detail = estimate(args, token)
    json.dump(badge, sys.stdout, indent=2)
    # Not a diagnostic: a blank line separating the JSON from the summary.
    print(file=sys.stderr)  # noqa: T201 — the tool's output
    log.info("%s ≈ %s", detail, badge["message"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
