# carbon-badge

[![CI](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/ci.yml/badge.svg)](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/ci.yml)
[![Security](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/security.yml/badge.svg)](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/security.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/fabiocicerchia/carbon-badge/badge)](https://securityscorecards.dev/viewer/?uri=github.com/fabiocicerchia/carbon-badge)
[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Ffabiocicerchia%2Fcarbon-badge.svg?type=shield)](https://app.fossa.com/projects/git%2Bgithub.com%2Ffabiocicerchia%2Fcarbon-badge?ref=badge_shield)
[![Release](https://img.shields.io/github/v/release/fabiocicerchia/carbon-badge)](https://github.com/fabiocicerchia/carbon-badge/releases)

A **Shields-style README badge for your repo's CI carbon footprint**. Reads
30 days of GitHub Actions runs, converts runner-minutes to gCO2e with
documented assumptions, and emits Shields.io endpoint JSON. The same pattern
as Badge Poser — for sustainability.

![example](https://img.shields.io/badge/CI%20carbon-1.2%20kgCO2e%2Fmo-green)

## How the estimate works

```
runs (30d) → runner-minutes → kWh (12.5 W, PUE incl.) → gCO2e (grid factor)
```

Defaults: world-average grid intensity (480 gCO2e/kWh); override with
`--grid-intensity` (e.g. `--grid-intensity 56` for Sweden). It's an
*estimate* — the value is trend and awareness, not accounting.

Color bands: <100 g bright green · <500 g green · <2 kg yellow · <10 kg
orange · above red.

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
pipx install .
carbon-badge fabiocicerchia/nginx-lua --token $GITHUB_TOKEN > badge.json
```

Serve `badge.json` anywhere public (gist, S3, gh-pages) and embed:

```markdown
![CI carbon](https://img.shields.io/endpoint?url=https://example.com/badge.json)
```

Nightly refresh workflow: see
[`.github-workflow-example/carbon-badge.yml`](.github-workflow-example/carbon-badge.yml).

## Roadmap

- [x] Per-runner-type power model (macOS/Windows/larger runners)
- [ ] GitLab CI + self-hosted runner support
- [ ] Live regional grid intensity via Electricity Maps API
- [ ] Hosted endpoint mode (badge-poser style service)

## Documentation

Full docs live in [`docs/`](docs/). Runnable examples live in [`examples/`](examples/).

## Development

`make setup` (git hooks) then `make dev`, and `make test` / `make lint`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Found a vulnerability? See [SECURITY.md](SECURITY.md) — please don't open a public issue.

## License

Apache 2.0 — see [LICENSE](LICENSE).
