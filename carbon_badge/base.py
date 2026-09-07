"""Constants and types the rest of the package is written in, where the
numbers come from, and the artifact naming everything agrees on."""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

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


class Response(Protocol):
    """An HTTP response, as this tool uses one: a status check and a body.

    A Protocol so the tests' stand-ins are checked against the same two calls
    the real `requests.Response` is used for, rather than against `Any`.
    """

    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...  # noqa: ANN401 — requests.Response.json() is Any


class Getter(Protocol):
    """`requests.get`, as this tool calls it.

    Written out rather than `Callable[..., Any]`: the ellipsis form accepts a
    fake with any signature at all, which is how a test keeps passing while
    the call it stands in for has moved on.
    """

    def __call__(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> Response: ...


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



def carbon_artifact_slug(name: str) -> str | None:
    """The job slug a marker carries, or None.

    Kept separate from parse_carbon_artifact rather than widening its tuple:
    every caller of that unpacks five values, and the slug is only ever wanted
    here.
    """
    head = _ARTIFACT_RE.match(name or "")
    if not head:
        return None
    # The regex ends at the dot after the region; the slug is the remainder.
    rest = name[head.end() :]
    return rest or None


def job_slug(name: str) -> str | None:
    """An API job name reduced the way the reporting action reduces it.

    Best effort, and deliberately so: the sanitising happens in the action that
    writes the marker, not here, so this can only approximate it. A slug that
    does not match falls back to per-run comparison rather than being dropped —
    the run-level answer is still correct, it is just coarser.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or None

