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

### Self-reported machines

When a job reports its own CPU and memory (see
[`record/`](../record/)), the wattage comes from a linear model anchored on the
same curve:

```
watts = 1.2 + 1.6 × vCPU + 0.11 × GiB

  4 vCPU, 16 GiB  ->  1.2 + 6.4 + 1.76  =  9.4 W   (matches the table above)
```

## Grid intensity

`480 gCO2e/kWh`, roughly the world average. Override with `--grid-intensity`,
or use `--grid-region` for a live Electricity Maps figure for your datacentre's
zone — which is the single highest-leverage correction available, since real
grids range from under 50 to over 700 gCO2e/kWh.

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
