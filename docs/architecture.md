# Architecture

carbon-badge is a single module (`carbon_badge.py`) with a small CLI.

## Overview

```
runs (30d) → per-job seconds (self-reported, else API) → kWh (per-runner W × PUE)
  → gCO2e (grid factor) → badge JSON
```

## Components

- **GitHub Actions client** — pages the workflow-runs API for the last 30 days
  and sums runner-minutes.
- **Estimate model** — `RUNNER_POWER_W` per runner type × `PUE`, or
  `watts_from_specs()` for a job that reported its own CPU and memory.
  Every figure and its source is in [assumptions.md](assumptions.md)
  times a grid-intensity factor (default 480 gCO2e/kWh, world average;
  override with `--grid-intensity`).
- **Badge renderer** — maps the monthly gCO2e total to a color band and emits
  Shields.io endpoint JSON (or an SVG).

## Decisions

- Only runtime dependency is `requests`; everything else is stdlib.
- The estimate is deliberately simple — the value is trend and awareness, not
  accounting. Assumptions are documented constants and overridable via flags.
- Color bands: <100 g brightgreen · <500 g green · <2 kg yellow · <10 kg
  orange · above red.

## How the estimate works

```
runs (30d) → per-job seconds (self-reported, else API) → kWh (per-runner W × PUE)
  → gCO2e (grid factor)
```

Defaults: world-average grid intensity (480 gCO2e/kWh); override with
`--grid-intensity` (e.g. `--grid-intensity 56` for Sweden), or use a live
figure with `--grid-region SE` (an [Electricity
Maps](https://www.electricitymaps.com/) zone; needs
`--electricitymaps-token`/`ELECTRICITYMAPS_TOKEN`) — this takes priority over
`--grid-intensity` when set. It's an *estimate* — the value is trend and
awareness, not accounting.

Color bands: <100 g bright green · <500 g green · <2 kg yellow · <10 kg
orange · above red.
