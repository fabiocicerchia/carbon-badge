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

from .artifacts import RegionFactors as RegionFactors
from .artifacts import artifact_kwh_by_run as artifact_kwh_by_run
from .artifacts import list_artifacts as list_artifacts
from .artifacts import parse_carbon_artifact as parse_carbon_artifact
from .base import BASELINE_MEM_GB as BASELINE_MEM_GB
from .base import BASELINE_VCPU as BASELINE_VCPU
from .base import DEFAULT_GRID_INTENSITY as DEFAULT_GRID_INTENSITY
from .base import GRAMS_PER_KILOGRAM as GRAMS_PER_KILOGRAM
from .base import MEM_PER_VCPU_MB as MEM_PER_VCPU_MB
from .base import PUE as PUE
from .base import RECONCILE_EPSILON_G as RECONCILE_EPSILON_G
from .base import Getter as Getter
from .base import Json as Json
from .base import Response as Response
from .base import RunnerWatts as RunnerWatts
from .base import carbon_artifact_slug as carbon_artifact_slug
from .base import job_slug as job_slug
from .base import log as log
from .ci import _MAX_PAGES as _MAX_PAGES
from .ci import _list_runs as _list_runs
from .ci import run_jobs as run_jobs
from .cli import main as main
from .grid import CI_API_BASE as CI_API_BASE
from .grid import CI_API_MAX_AGE_S as CI_API_MAX_AGE_S
from .grid import IPCC_FUEL_G_PER_KWH as IPCC_FUEL_G_PER_KWH
from .grid import _ci_api_factor as _ci_api_factor
from .grid import _eia_factor as _eia_factor
from .grid import _energy_charts_factor as _energy_charts_factor
from .grid import _uk_factor as _uk_factor
from .grid import live_region_factor as live_region_factor
from .power import ANY_RUNNER as ANY_RUNNER
from .power import AZURE_REGION_GRID as AZURE_REGION_GRID
from .power import BADGE_COLOR as BADGE_COLOR
from .power import DEFAULT_RUNNER_POWER_W as DEFAULT_RUNNER_POWER_W
from .power import IDLE_FRACTION as IDLE_FRACTION
from .power import RUNNER_POWER_ESTIMATED as RUNNER_POWER_ESTIMATED
from .power import RUNNER_POWER_W as RUNNER_POWER_W
from .power import WATTS_BASE as WATTS_BASE
from .power import WATTS_PER_GB as WATTS_PER_GB
from .power import CiUsage as CiUsage
from .power import RunnerWattsError as RunnerWattsError
from .power import RunTotals as RunTotals
from .power import _warn_estimated_classes as _warn_estimated_classes
from .power import apply_load_factor as apply_load_factor
from .power import grid_factor_for as grid_factor_for
from .power import parse_runner_watts as parse_runner_watts
from .power import runner_power_w as runner_power_w
from .power import watts_from_specs as watts_from_specs
from .providers import ci_kwh_last_30d as ci_kwh_last_30d
from .providers import gitlab_kwh_last_30d as gitlab_kwh_last_30d
from .providers import live_grid_intensity as live_grid_intensity
from .reconcile import Divergence as Divergence
from .reconcile import Reconciliation as Reconciliation
from .reconcile import _warn_undeclared_runners as _warn_undeclared_runners
from .reconcile import format_reconciliation as format_reconciliation
from .reconcile import grams_co2e as grams_co2e
from .reconcile import grams_co2e_kwh as grams_co2e_kwh
from .reconcile import reconcile_last_30d as reconcile_last_30d
from .reconcile import rows_by_gap as rows_by_gap
from .report import MEASURED_THRESHOLD as MEASURED_THRESHOLD
from .report import ROUGH_THRESHOLD as ROUGH_THRESHOLD
from .report import confidence as confidence
from .report import endpoint_json as endpoint_json
from .report import estimate as estimate
from .report import format_grams as format_grams
from .server import DEFAULT_BIND as DEFAULT_BIND
from .server import BadgeHandler as BadgeHandler
from .server import badge_handler as badge_handler
from .server import serve as serve

__all__ = [
    "ANY_RUNNER",
    "AZURE_REGION_GRID",
    "BADGE_COLOR",
    "BASELINE_MEM_GB",
    "BASELINE_VCPU",
    "CI_API_BASE",
    "CI_API_MAX_AGE_S",
    "DEFAULT_BIND",
    "DEFAULT_GRID_INTENSITY",
    "DEFAULT_RUNNER_POWER_W",
    "GRAMS_PER_KILOGRAM",
    "IDLE_FRACTION",
    "IPCC_FUEL_G_PER_KWH",
    "MEASURED_THRESHOLD",
    "MEM_PER_VCPU_MB",
    "PUE",
    "RECONCILE_EPSILON_G",
    "ROUGH_THRESHOLD",
    "RUNNER_POWER_ESTIMATED",
    "RUNNER_POWER_W",
    "WATTS_BASE",
    "WATTS_PER_GB",
    "_MAX_PAGES",
    "BadgeHandler",
    "CiUsage",
    "Divergence",
    "Getter",
    "Json",
    "Reconciliation",
    "RegionFactors",
    "Response",
    "RunTotals",
    "RunnerWatts",
    "RunnerWattsError",
    "_ci_api_factor",
    "_eia_factor",
    "_energy_charts_factor",
    "_list_runs",
    "_uk_factor",
    "_warn_estimated_classes",
    "_warn_undeclared_runners",
    "apply_load_factor",
    "artifact_kwh_by_run",
    "badge_handler",
    "carbon_artifact_slug",
    "ci_kwh_last_30d",
    "confidence",
    "endpoint_json",
    "estimate",
    "format_grams",
    "format_reconciliation",
    "gitlab_kwh_last_30d",
    "grams_co2e",
    "grams_co2e_kwh",
    "grid_factor_for",
    "job_slug",
    "list_artifacts",
    "live_grid_intensity",
    "live_region_factor",
    "log",
    "main",
    "parse_carbon_artifact",
    "parse_runner_watts",
    "reconcile_last_30d",
    "rows_by_gap",
    "run_jobs",
    "runner_power_w",
    "serve",
    "watts_from_specs",
]
