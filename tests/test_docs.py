"""docs/assumptions.md quotes the constants it explains; these keep it honest.

The page is the only place a reader can check the working behind a badge, so a
figure there that has drifted from the code is worse than no figure at all.
"""

import re
from pathlib import Path

import pytest

from carbon_badge import runner_power_w
from carbon_badge.base import DEFAULT_GRID_INTENSITY, PUE
from carbon_badge.power import RUNNER_POWER_ESTIMATED, RUNNER_POWER_W

ASSUMPTIONS = (Path(__file__).parent.parent / "docs" / "assumptions.md").read_text(encoding="utf-8")


def _table_after(heading: str) -> list[list[str]]:
    """The body rows of the first Markdown table under `heading`, cells stripped."""
    table = re.search(r"(?:^\|.*\|\n)+", ASSUMPTIONS.split(heading, 1)[1], re.MULTILINE)
    assert table, f"no table under {heading!r}"
    rows = table.group().splitlines()[2:]  # past the header and the |---| rule
    return [[cell.strip() for cell in row.strip("|").split("|")] for row in rows]


def _watts(cell: str) -> float:
    return float(re.findall(r"\d+(?:\.\d+)?", cell)[0])


def test_resulting_table_matches_runner_power_w() -> None:
    """Every row of 'The resulting table' is the code's figure, and no class is missing."""
    rows = {row[0].strip("`"): row for row in _table_after("### The resulting table")}
    assert set(rows) == set(RUNNER_POWER_W)
    for label, (_, _, watts) in rows.items():
        assert _watts(watts) == RUNNER_POWER_W[label], label
        assert ("estimate" in watts) == (label in RUNNER_POWER_ESTIMATED), label


@pytest.mark.parametrize(
    ("cores", "row"),
    [(int(row[0].split("-")[0]), row) for row in _table_after("**Affine, not proportional.**")],
)
def test_affine_table_matches_core_scaling(cores: int, row: list[str]) -> None:
    """The affine column is what a core-counted ubuntu label actually prices at."""
    assert runner_power_w([f"ubuntu-latest-{cores}-cores"]) == pytest.approx(_watts(row[2]), abs=0.05)


def test_quoted_constants_match_code() -> None:
    assert f"`PUE = {PUE:g}`" in ASSUMPTIONS
    assert f"`{DEFAULT_GRID_INTENSITY:g} gCO2e/kWh` by default" in ASSUMPTIONS
