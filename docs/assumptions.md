# Assumptions — where every number comes from

carbon-badge multiplies **duration × watts × grid intensity**. Duration is
observed. The other two are assumptions, and this page shows the working for
each so you can check it, argue with it, or override it.

Every figure here is a constant in `carbon_badge.py` and overridable at runtime.

## The chain

```
job seconds  ×  watts  ÷ 3600 ÷ 1000  =  kWh   ×  gCO2e/kWh  =  gCO2e
```

## Watts

### The machine GitHub actually gives you

Public repositories have used **4-vCPU / 16 GiB** standard runners since
December 2023, not the 2-vCPU / 7 GiB machines most published estimates assume
([GitHub blog](https://github.blog/news-insights/product-news/github-hosted-runners-double-the-power-for-open-source/)).
Getting this wrong doubles everything downstream, so `BASELINE_VCPU = 4` and
larger runners scale off it.

### Measured power curves

Green Coding Solutions' [Eco-CI](https://github.com/green-coding-solutions/eco-ci-energy-estimation)
publishes power curves for exactly these machines, modelled by the Cloud Energy
project from SPECpower data
([machine-power-data](https://github.com/green-coding-solutions/eco-ci-energy-estimation/tree/main/machine-power-data)):

| CPU load | 4-core EPYC 7763 shared (Linux/Windows) | Mac mini M1 (macOS) |
|---|---|---|
| 0% | 1.76 W | 4.45 W |
| 50% | 5.16 W | 8.90 W |
| 100% | 8.18 W | 15.53 W |

These are **machine draw**, so datacentre overhead is applied separately.

### PUE

`PUE = 1.15`. Hyperscale operators publish 1.1–1.2; this is mid-range. Kept as
its own named constant rather than folded into the wattages, so it is visible
and can be changed on its own.

### The resulting table

| label | working | watts |
|---|---|---|
| `ubuntu` | 8.18 × 1.15 | **9.4** |
| `windows` | 8.18 × 1.15 | **9.4** |
| `macos` | 15.53 × 1.15 | **17.9** |
| `arm` | 8.18 × 0.6 × 1.15 | **5.6** |
| `gpu` | 130 × 1.15 | **149.5** |

**Windows is not 2× Linux and macOS is not 10×.** GitHub bills them at those
multipliers, but billing is a pricing decision. Windows runs on the same Azure
hardware as Linux and draws the same; macOS runs on Mac minis, which are
efficient. Using billing multipliers as energy factors would be wrong by a wide
margin — in the macOS case, by about 4×.

The two weak entries are `arm` and `gpu`: no measured curve exists for either,
so `arm` is taken as ~40% below x86 and `gpu` as a T4-class accelerator plus
host. If you run either seriously, declare your own with
`--runner-watts arm=… gpu=…`.

Note the ARM figure deliberately avoids the "3–4× more efficient" claim that
circulates. AWS's own published figure is *up to 60% less energy for equal
work*, and independent benchmarks land nearer 1.5–2.5×.

### One law, both routes

A runner recognised by its label and a job that reported its own hardware go
through the same function, so the same machine costs the same either way:

```
watts = 1.2 + per_vcpu × vCPU + 0.1125 × GiB

  per_vcpu is derived from the platform's table entry, so at the standard
  4 vCPU / 16 GiB shape the model reproduces the table exactly:

  ubuntu   1.2 + 1.6·4  + 0.1125·16 =  9.4 W
  arm      1.2 + 0.65·4 + 0.1125·16 =  5.6 W
```

**Affine, not proportional.** A machine has a fixed draw plus a per-core one —
Eco-CI measures 1.76 W at idle rising to 8.18 W at load — so doubling the cores
does not double the wattage:

| runner | proportional (wrong) | affine |
|---|---|---|
| 4-core | 9.4 W | 9.4 W |
| 8-core | 18.8 W | **17.6 W** |
| 16-core | 37.6 W | **34.0 W** |
| 64-core | 150.4 W | **132.4 W** |

The label path used to scale proportionally, agreeing with the model only at
the 4-vCPU point and drifting to 12% by 64 cores — so a repo's figure would
have moved as it instrumented, with nothing having changed.

## Grid factor

`480 gCO2e/kWh` by default, roughly the world average — and the single
highest-leverage thing to correct, since real grids run from under 50 to over
700. Two ways to do better:

```sh
carbon-badge OWNER/REPO --grid-intensity 56        # a fixed figure you trust
carbon-badge OWNER/REPO --grid-region SE           # live, from Electricity Maps
```

`--grid-region` is also an action input (`grid-region`), with
`electricitymaps-token` for the API key.

**It uses the mean of the past 24 hours, not the current reading.** A single
instant is a poor multiplier for a 30-day total: grids swing 2-3x across a day,
and the refresh runs on a fixed cron — 02:17 on a Monday for the fleet this was
built for — so the instantaneous value would price a whole month at an
overnight low, and the badge would move week to week on nothing but the clock.

A day's mean is still an approximation. Properly, each job would be priced at
the factor while it actually ran; that needs 30 days of history, which
Electricity Maps puts behind a paid tier. The 24-hour mean removes the
time-of-day bias, which was the part that moved the number for no reason. If
history is unavailable for your zone or plan, it falls back to the
instantaneous reading and says so in the log.

## Why there is no red-amber-green scale

The badge reports a value and passes no verdict, and that is deliberate.

A red-amber-green scale was tried and removed. It did not discriminate: across
the 40-repo fleet it was built for, every repo sat in the bottom band — 4 to 52
gCO2e against a 100 g threshold — so the colour was a constant that looked like
a signal. Re-cutting the thresholds only moves the window: CI footprints span
four or five orders of magnitude, from a hobby project to a monorepo, and no
published distribution exists to place a repo against.

The deeper problem is what the colour would claim. An absolute monthly total
mostly measures how *big* a project is. A small repo running a four-way matrix
on every push is genuinely wasteful and would score green; a large project with
well-managed CI would score amber. Grading size while implying virtue says
something the number cannot support.

[Eco-CI](https://github.com/green-coding-solutions/eco-ci-energy-estimation)
reaches the same conclusion — its badge reports a value with no colour banding.

What remains is the confidence marker, which describes how the figure was
obtained rather than whether it is good. That is a claim this tool can defend.

## Known limits

- **A flat wattage cannot be right.** Real draw swings 1.76–8.18 W with CPU
  load, and the API does not expose utilisation. The table uses the full-load
  figure, so an I/O-bound job is overstated.
- **Shared hardware is an attribution, not a measurement.** A 4-vCPU slice of a
  many-core host has no single true wattage; the Cloud Energy model apportions
  it.
- **`measured` is not `accurate`.** Even fully instrumented, the watts model and
  the grid factor carry their own error. The confidence score in the
  [README](../README.md#how-a-number-is-arrived-at) describes how the *inputs*
  were obtained, not how close the answer is.

Treat the output as an order of magnitude and a trend line, not a figure to put
in a report.

## Changelog of these assumptions

> **Every badge drops about 25% at this release, and nothing got cleaner.**
> The wattages were too high; correcting them moved every figure at once. If
> you are reading a trend line across this release, that step change is a
> measurement change, not a reduction — do not claim it as one.

**2026-08** — recalibrated against the Eco-CI curves. Previously `ubuntu` was
12.5 W (~1.5× the measured full-load figure, and describing the retired 2-vCPU
machine), `macos` 65 W (~4× high), `windows` 30 W (~3× high), and the
self-reported model was fitted to 2 vCPU / 7 GiB — which priced a real 4-vCPU
runner at 24.3 W, about 3× too high. Core scaling also divided by 2 rather than
by the 4-vCPU baseline, doubling every larger runner.
