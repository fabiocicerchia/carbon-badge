# carbon-badge

[![CI](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/ci.yml/badge.svg)](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/ci.yml)
[![Security](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/security.yml/badge.svg)](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/security.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/fabiocicerchia/carbon-badge/badge)](https://securityscorecards.dev/viewer/?uri=github.com/fabiocicerchia/carbon-badge)
[![CI carbon](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/fabiocicerchia/carbon-badge/gh-pages/badge.json)](.github/workflows/carbon-badge.yml)
[![Release](https://img.shields.io/github/v/release/fabiocicerchia/carbon-badge)](https://github.com/fabiocicerchia/carbon-badge/releases)

A **Shields-style README badge for your repo's CI carbon footprint**. Sums 30
days of per-job runtime, prices each job at its runner's power draw, and emits
Shields.io endpoint JSON — with every assumption documented and the badge
itself saying how the figure was arrived at. The same pattern as Badge Poser —
for sustainability.

![example](https://img.shields.io/badge/CI%20carbon-1.2%20kgCO2e%2Fmo-green)

## Install

```sh
pipx install git+https://github.com/fabiocicerchia/carbon-badge
```

Or with pip:

```sh
pip install git+https://github.com/fabiocicerchia/carbon-badge
```

Or the one-line installer:

```sh
curl -fsSL https://raw.githubusercontent.com/fabiocicerchia/carbon-badge/main/install.sh | bash
```

## Usage

```sh
carbon-badge fabiocicerchia/carbon-badge --token "$GITHUB_TOKEN" > badge.json
```

More in [`docs/getting-started.md`](docs/getting-started.md).

## How a number is arrived at

Every job's footprint is **duration × watts**. Duration is always known well.
The watts are where the uncertainty lives, and there are exactly three ways one
is obtained. These three words mean the same thing everywhere — in the code, in
the log, and on the badge.

| | what it means | how you get there |
|---|---|---|
| **measured** | the job reported its own duration and the machine's real CPU and memory | add [`carbon-badge/record`](record/) to the job |
| **declared** | you told us the wattage for that runner | `runner-watts: "my-builder=180"` |
| **guessed** | the runner was not recognised, so a generic fallback was used | nothing — this is what happens by default |

A recognised GitHub-hosted label (`ubuntu-latest`, `macos-14`, `…-8-cores`,
`…-arm`, `…-gpu`) counts as **declared**: the figure comes from a published
spec rather than a fallback.

**Declared is believed, not verified.** We trust you over any inference — you
know your hardware and the API does not expose it. But nothing checks the
number, so a wrong declaration produces a confidently wrong badge. Only
*measured* closes that gap.

### Why guessed is dangerous

Nine two-minute `ubuntu-latest` jobs plus one ten-hour job on an unrecognised
`my-builder`:

```
                nine short jobs       the long one              total
guessed    0.3 h total × 9.4 W  +  10 h × 9.4 W  =   97 Wh  ->   46 gCO2e/mo  (brightgreen)
declared   0.3 h total × 9.4 W  +  10 h × 180 W  = 1803 Wh  ->  865 gCO2e/mo  (yellow)
```

Same CI, 14× apart, **two colour bands** apart. The fallback is not a small
inaccuracy — it silently prices a big machine as a small one. That is why an
unrecognised runner is named in the log with the exact flag to fix it, rather
than quietly absorbed.

### What the badge says

The badge reports the share of **energy** — not the share of jobs, since one
long job outweighs a dozen short ones — that came from each category.

| badge | meaning |
|---|---|
| `113 gCO2e/mo` | measured: essentially all of it self-reported |
| `113 gCO2e/mo (~18/24 measured)` | partial: some self-reported, the rest from the API |
| `113 gCO2e/mo (estimated)` | none self-reported, but every runner was recognised or declared — durations real, wattages modelled |
| `113 gCO2e/mo (rough)` | a quarter or more of the energy rests on a guess |

The grams always cover **all** CI, measured or not. A badge that shrank while
instrumentation lagged would reward not instrumenting.

This score is about instrumentation only. Even `measured` still assumes the
linear watts model and a grid-intensity factor, which carry their own
uncertainty — it means "we measured what was measurable", not "accurate to 5%".

### The wattages

| runner | working | watts |
|---|---|---|
| `ubuntu` / `windows` | 8.18 W × 1.15 PUE | 9.4 |
| `macos` | 15.53 W × 1.15 | 17.9 |
| `arm` | 8.18 W × 0.6 × 1.15 | 5.6 |
| `gpu` | 130 W × 1.15 | 149.5 |

The base figures are [Eco-CI's](https://github.com/green-coding-solutions/eco-ci-energy-estimation/tree/main/machine-power-data)
measured power curves at full CPU, for the exact machines GitHub runs jobs on —
a shared 4-core EPYC 7763, and a Mac mini M1. Public repos have had **4-vCPU /
16 GiB** runners since December 2023, so larger runners scale off 4, not 2.

Windows is *not* 2× Linux and macOS is *not* 10×: those are GitHub's billing
multipliers, not power ones. Same hardware draws the same watts.

Full derivation, sources, and the known limits are in
[`docs/assumptions.md`](docs/assumptions.md).

## How this compares to Eco-CI

[Eco-CI](https://github.com/green-coding-solutions/eco-ci-energy-estimation) by
Green Coding Solutions solves a neighbouring problem, and the power curves this
tool is calibrated against are theirs. They are worth knowing about before you
pick one — for some questions they are simply the better answer.

| | Eco-CI | carbon-badge |
|---|---|---|
| **measures** | CPU utilisation sampled during the job | job duration, after the fact |
| **wattage** | integrated across the load curve | the full-load figure, flat |
| **instrumentation** | required — `start` / `get` / `display` in every job | optional; adds accuracy, not required for a number |
| **granularity** | per step and per run | 30-day repo total |
| **badge** | via `metrics.green-coding.io` (`send-data` defaults to true) | your own `gh-pages`, no third party |
| **CI systems** | GitHub, GitLab, Jenkins | GitHub, GitLab |
| **platforms** | Linux and macOS (macOS needs GNU `date`) | ubuntu, windows, macos, arm, gpu, or declared |
| **grid intensity** | Electricity Maps | Electricity Maps, or a fixed factor |

### The difference that matters

**Eco-CI knows how hard the CPU was working; this tool does not.** It samples
utilisation into a time series and integrates it against the curve, so a job
idling at 25% is priced near 4 W and a busy one near 8 W.

carbon-badge only ever uses the full-load end of that same curve, because the
Actions API exposes no utilisation figure — a retrospective tool cannot recover
what nobody recorded. On mixed CI, where much of a job is waiting on the
network, that plausibly **over-reports by 1.5–2×**. It is the largest known
error in this tool and it is not fixable from the outside.

### So which

- **What did this build cost, and which step is the expensive one?** Eco-CI.
  It measures the thing that actually varies, and reports per step.
- **What has this repo's CI cost over the last month, across every workflow,
  without touching any of them?** carbon-badge. It works on a repo you have not
  instrumented, publishes to infrastructure you own, and states on the badge
  how much of the figure was measured rather than modelled.

They also compose: nothing stops a repo running Eco-CI on the workflow it cares
about and carbon-badge for the fleet-wide rolling number.

## Documentation

Full docs live in [`docs/`](docs/). Runnable examples live in [`examples/`](examples/).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Found a vulnerability? See [SECURITY.md](SECURITY.md) — please don't open a public issue.

## Support

Need help implementing this? [Get in touch](https://fabiocicerchia.it/contact).

## License

Apache 2.0 — see [LICENSE](LICENSE).
