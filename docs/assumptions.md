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

`PUE = 1.15`. GitHub's hosted runners are Azure VMs, so the hyperscale end is
the right one: Cloud Carbon Footprint publishes **1.125 for Azure**, 1.135 for
AWS, 1.1 for GCP
([methodology](https://www.cloudcarbonfootprint.org/docs/methodology/)). 1.15
sits just above that band rather than on Azure's own figure — a 2% difference,
far inside the error on everything it multiplies, and rounding up is the
direction that does not flatter the badge.

The *industry-wide* average is 1.56 and has been flat for five years
([Uptime Institute, Global Data Center Survey 2024](https://uptimeinstitute.com/resources/research-and-reports/uptime-institute-global-data-center-survey-results-2024)).
That figure does not apply here, because we know where these jobs ran.

Kept as its own named constant rather than folded into the wattages, so it is
visible and can be changed on its own.

### The resulting table

| label | working | watts |
|---|---|---|
| `ubuntu` | 8.18 × 1.15 | **9.4** |
| `windows` | 8.18 × 1.15 | **9.4** |
| `macos` | 15.53 × 1.15 | **17.9** |
| `arm` | 8.18 × 0.6 × 1.15 | **5.6** |
| `gpu` | (8.18 + 70) × 1.15 | **89.9** |

The `gpu` row is a 4-vCPU host slice — the same one `ubuntu` describes — plus
one T4-class accelerator at its 70 W board TDP.

**Windows is not 2× Linux and macOS is not 10×.** GitHub bills them at those
multipliers, but billing is a pricing decision. Windows runs on the same Azure
hardware as Linux and draws the same; macOS runs on Mac minis, which are
efficient. Using billing multipliers as energy factors would be wrong by a wide
margin — in the macOS case, by about 4×.

The two weak entries are `arm` and `gpu`: no measured curve exists for either,
so `arm` is taken as ~40% below x86 and `gpu` as a T4-class accelerator plus
host. A TDP is a ceiling, not a mean, so the `gpu` row overstates anything but
a saturated card. If you run either seriously, declare your own with
`--runner-watts arm=… gpu=…`.

Note the ARM figure deliberately avoids the "3–4× more efficient" claim that
circulates. AWS's own published figure is *up to 60% less energy for equal
work* ([Graviton](https://aws.amazon.com/ec2/graviton/)), and independent
benchmarks land nearer 45–50%, so 40% is the conservative end. greenlint's
GL016 uses the same 40%.

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

**The 1.2 and the 0.1125 are not measurements.** They are the two free
parameters of a fit whose only constraint is reproducing the Eco-CI table at
4 vCPU / 16 GiB; `per_vcpu` is then solved for. Any pair summing to 3.0 W at
16 GiB satisfies that constraint equally well.

In particular `0.1125 W/GiB` is nothing like Cloud Carbon Footprint's
0.392 W/GB for memory — and must not be. CCF meters memory separately from
compute; the Eco-CI curve is whole-machine draw and already contains it.
Raising this term to CCF's would double-count. It is a shape parameter, not a
memory coefficient.

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

`480 gCO2e/kWh` by default. That is the world-average power-sector intensity
for 2023: *"CO2 intensity reached a new record low of 480 gCO2/kWh, down 1.2%
from 486 gCO2/kWh in 2022"* — Ember, Global Electricity Review 2024, in the
["Electricity transition in 2023"](https://ember-energy.org/latest-insights/global-electricity-review-2024/electricity-transition-in-2023/)
chapter. It drifts a few percent a year (486 in 2022, 480 in 2023, 473 in
2024), which is far inside the error bars on the wattages it multiplies.
greenlint pins the same figure.

It is also the single highest-leverage thing to correct, since real grids run
from under 30 to over 700. Two ways to do better:

```sh
carbon-badge OWNER/REPO --grid-intensity 56        # a fixed figure you trust
carbon-badge OWNER/REPO --grid-region SE           # live, from Electricity Maps
```

`--grid-region` is also an action input (`grid-region`), with
`electricitymaps-token` for the API key.

### The per-region table

A job that recorded itself also records the Azure region it landed in, and
`AZURE_REGION_GRID` prices it there instead of at the world average. That is
the largest correction available and it costs nothing — the spread across the
table is ~25× end to end.

The country-level rows are **Ember / Energy Institute (via OWID) annual
operational intensity**, data years 2024–2025, taken from the fleet's own
[carbon-intensity-api](https://github.com/fabiocicerchia/carbon-intensity-api)
dataset. That is the same Ember series the 480 comes from, so a region factor
and the world average are the same kind of number rather than two unrelated
guesses. Values are quoted as published, not rounded, so any row can be checked
against the source; the approximation is *"this region is in that country"*,
not the figure itself.

**The US and Canadian rows are the exception, and the weakest here.** Both
countries span several grids differing by ~5×, so the national average (384 and
191) would hide exactly the variation the table exists to capture — but Ember
publishes nothing sub-national. Those rows are hand-transcribed for the
Electricity Maps zone named against each in the source, undated, and are the
ones to distrust first. Check one at
`https://app.electricitymaps.com/zone/<ZONE>`.

## Reconciling with greenlint

[greenlint](https://github.com/fabiocicerchia/greenlint) states its own carbon
figures against a different anchor: **15 W for one busy physical core**. This
table works out at ~4.7 W — 9.4 W for a 4-vCPU runner, which is two
hyperthreaded cores. Two sibling tools disagreeing 3× on the same physical
quantity looks like a bug, so: it is not, and neither constant belongs in the
other tool.

- **The published coefficients already differ by 1.7×.** greenlint starts from
  Cloud Carbon Footprint's 3.5 W per vCPU at 100% CPU, a cross-fleet average.
  This starts from Eco-CI's 8.18 W for a 4-vCPU slice — 2.05 W per vCPU —
  modelled for the one machine GitHub actually runs jobs on.
- **greenlint then rounds up on purpose** (7 W of silicon × PUE is 8–11 W; it
  quotes 15) and carries the industry-average PUE of 1.56, because it is
  linting code destined for infrastructure nobody has described. This tool
  knows the infrastructure is Azure, so it takes the hyperscale PUE and no
  safety margin.

greenlint is the generous end of plausible for an unknown machine; this is the
modelled figure for a known one.

Everything the two tools *do* share is pinned to the same value: the 480
gCO2e/kWh grid factor, and the 40% ARM efficiency figure (greenlint's GL016).

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

## Live grid factors

The table above is an annual average. Real grids move several times over inside
a day — Germany measured 146 to 634 gCO2eq/kWh across one day — so a live
figure is worth far more than any refinement to the wattages. Where a free
source exists for a region, it is used automatically.

| provider | key | resolution | covers |
|---|---|---|---|
| [energy-charts.info](https://api.energy-charts.info) (Fraunhofer ISE) | **none** | 15 min | de fr it pl es nl at cz gr hu no |
| [carbonintensity.org.uk](https://carbonintensity.org.uk) (NESO) | **none** | 30 min | Great Britain |
| [ci-api](https://ci-api.fabiocicerchia.it) | **none** | hourly | Europe, US balancing authorities, AU and CA zones — the rest fall back |
| [EIA](https://www.eia.gov/opendata/) | free, register | hourly | United States |
| Electricity Maps | paid | 5 min | everywhere |

Precedence: an explicit `--grid-intensity` or `--grid-region` beats everything,
then a live provider for the job's region, then the annual average. One request
per distinct region per refresh, memoised — not one per job. A provider that
fails is reported and the annual average used; a grid lookup must never fail a
badge refresh.

### ci-api: one request for the whole world

Two properties of that API decide how it is used here, and getting either wrong
would be a silent bug rather than a visible failure.

**It allows 1 request per 10s per IP**, enforced as a CDN rule that answers
`429`. A refresh resolves several regions, so a per-region lookup would fail on
everything after the first — and fail *quietly*, since a failed grid lookup
degrades to the annual average by design. So this reads `/v1/latest.json`
instead: every country and zone in one object, fetched once per refresh and
memoised, which costs one request no matter how many regions a month of runs
touched.

**It publishes no freshness flag.** Its responses are static objects served
with nothing in the request path, so nothing evaluates staleness when you ask —
the client has to. A snapshot whose `generated_at` is over 65 minutes old (the
hourly pipeline having missed a run) is refused, and so is any reading whose
`basis` is not `measured`. That second check is the one that matters: an
`annual-average` reading is the API's fallback for a grid with no live feed,
which is *the same kind of number the table above already holds*. Accepting it
would log "live at 513" for a figure no more live than the table's.

Readings are taken as **`consumption_lifecycle`** — upstream emissions plus the
trade adjustment, the most complete of the four figures published and the one
the API tells clients to use. Its zone readings carry no consumption figures at
all, since the import adjustment is a national number that does not describe a
single bidding zone, so those fall back to `lifecycle` — still the same
lifecycle scope as the IPCC factors below and the annual table.

US regions map to their EIA balancing authority (`US/ERCO`, `US/CISO`) rather
than to `US`, for the reason the table already gives: a national average blurs
grids that differ by ~5x. Canada maps to `CA/ON` for the same reason — Ontario
is the province with a live feed, and it is the one Azure's central Canadian
region draws from.

### The US number is a model, not a measurement

EIA publishes **generation by fuel type**, not carbon intensity, so for US
regions this tool computes it: generation-weighted
[IPCC AR5 Annex III](https://www.ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf)
lifecycle medians over the most recent hour, for the region's balancing
authority. Lifecycle rather than combustion-only, to match how the other
sources express themselves — mixing the two would understate renewables.

That makes it the one figure here derived rather than sourced. The fuel factors
are cited and the arithmetic is a weighted mean, but the choice of factors is
ours.

### Why Electricity Maps is not in the free chain

Its free tier is **one zone, 50 requests/hour, non-commercial**. GitHub places
runners in a different region run to run, so a single zone cannot serve them,
and most repos this measures are commercial. Their newer Carbon Intensity Level
API is free for all zones but returns `high`/`moderate`/`low` rather than a
number. `--grid-region` still uses Electricity Maps for anyone on a paid plan.

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

## The flat wattage, and what to do about it

Real draw swings 1.76–8.18 W with CPU load on the same 4-vCPU slice. The
Actions API exposes no utilisation, so the wattage has to come from somewhere
else. Three options were on the table:

1. **Keep full load and state the bias.** Simple and honest, but the bias is
   large: a job that averages 25% CPU is overstated by roughly 3x, and a
   CI suite that is mostly `npm install` and network waiting is exactly that
   job.
2. **A job-class heuristic** — guess utilisation from the workflow name, the
   step names, the duration. Cheap to implement and impossible to defend: it
   would produce a different number for the same machine doing the same work
   depending on what someone called the file.
3. **An explicit `--load-factor`.** The caller states the average utilisation
   they know or have measured; the default changes nothing.

**Option 3, with option 1 as the default.** The number is only as good as the
utilisation figure behind it, and the tool does not have one — so it prices at
full load, says so, and gives anyone who *does* have one a way to say it:

```sh
carbon-badge OWNER/REPO --load-factor 0.25   # measured average CPU utilisation
```

Only the variable part scales. Eco-CI's 1.76 W at idle against 8.18 W at full
load means **21.5% of the draw is there whatever the job does**, so
`--load-factor 0` is 2.02 W, not zero, and `--load-factor 0.25` is 3.87 W
rather than the 2.35 W a plain multiply would give. A flat multiply would have
replaced an overstatement with an understatement of about the same size.

**The remaining error, stated plainly:** at the default the figure is a
*ceiling*. For a CPU-bound build it is close to right; for an I/O-bound or
mostly-waiting job it is high by up to about 3x. It is never low on this axis.
That direction is deliberate — a carbon figure that flatters the caller is
worth less than one that does not.

## Known limits

- **A flat wattage cannot be right.** The default prices at full load and so
  overstates an I/O-bound job; `--load-factor` is the lever, and the section
  above is the reasoning.
- **Shared hardware is an attribution, not a measurement.** A 4-vCPU slice of a
  many-core host has no single true wattage; the Cloud Energy model apportions
  it.
- **`measured` is not `accurate`.** Even fully instrumented, the watts model and
  the grid factor carry their own error. The confidence score in the
  [README](README.md#how-a-number-is-arrived-at) describes how the *inputs*
  were obtained, not how close the answer is.

Treat the output as an order of magnitude and a trend line, not a figure to put
in a report.

## Changelog of these assumptions

**2026-08 (b)** — sourced the numbers that had no source, and reconciled
against greenlint. Nothing about the method changed; three sets of figures
moved:

- **The region table was re-derived from Ember**, replacing values of mixed and
  undocumented vintage. Most rows move under 20%, but a few were badly stale:
  `francecentral` 85 → 41, `northeurope` 350 → 257, `westeurope` 330 → 254,
  `southafricanorth` 900 → 699, `polandcentral` 700 → 589, `australiaeast`
  600 → 525. `eastus`/`eastus2` went the other way, 350 → 390. Only repos with
  self-reported jobs see this; an uninstrumented repo prices at 480 as before.
- **`gpu` 149.5 W → 89.9 W.** The old figure was a flat 130 W of machine draw
  with no working behind it, implying a ~60 W host — 7× what the same tool
  charges a 4-vCPU slice everywhere else. It is now the `ubuntu` host slice
  plus a 70 W T4 board TDP.
- **PUE, 480, and the ARM factor kept their values** and gained their citations
  (Cloud Carbon Footprint / Uptime Institute, Ember GER 2024, AWS Graviton).

If you are reading a trend line across this release, that is a measurement
change, not a reduction.


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
