"""Runner power: what a machine class draws, the grid factor for the region
it ran in, and turning declared specs into watts."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from typing import NamedTuple

from .base import BASELINE_MEM_GB, BASELINE_VCPU, DEFAULT_GRID_INTENSITY, MEM_PER_VCPU_MB, PUE, Json, RunnerWatts

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


def grid_factor_for(region: str | None, override: float | None = None) -> float:
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
_ESTIMATED_USED: dict[str, int] = {}


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
class CiUsage(NamedTuple):
    kwh: float
    grams: float
    measured_jobs: int
    total_jobs: int
    measured_kwh: float
    guessed_kwh: float


# What one run's markers add up to. `markers` is a count of jobs that reported
# themselves, not energy — it is the numerator of the completeness check, and
# reading it as a third float is the mistake the positional tuple invited.
class RunTotals(NamedTuple):
    kwh: float
    grams: float
    markers: int


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


class RunnerWattsError(ValueError):
    """A --runner-watts entry that could not be read.

    The message names the offending entry as well as what was wrong with it:
    the flag takes several entries at once, and "not a number" without the
    entry leaves the user guessing which one.
    """

    def __init__(self, entry: str, problem: str) -> None:
        super().__init__(f"{problem}, in {entry!r}")
        self.entry = entry


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
    table: RunnerWatts = {}
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
            raise RunnerWattsError(pair, "expected LABEL=WATTS or a bare number")
        try:
            watts = float(value)
        except ValueError:
            raise RunnerWattsError(pair, f"{value!r} is not a number") from None
        if watts <= 0:
            raise RunnerWattsError(pair, f"watts must be positive, got {watts}")
        table[label] = watts
    return table


def _declared_watts(labels_lower: list[str], overrides: RunnerWatts) -> float | None:
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


def runner_power_w(labels: list[str], overrides: RunnerWatts | None = None) -> float | None:
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
LOAD_FACTOR: float = 1.0


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


