# Architecture

carbon-badge is a single module (`carbon_badge.py`) with a small CLI.

## Overview

```
runs (30d) → runner-minutes → kWh (12.5 W, PUE incl.) → gCO2e (grid factor) → badge JSON
```

## Components

- **GitHub Actions client** — pages the workflow-runs API for the last 30 days
  and sums runner-minutes.
- **Estimate model** — `KWH_PER_RUNNER_MINUTE` (12.5 W average draw incl. PUE)
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
