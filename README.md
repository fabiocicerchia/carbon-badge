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
`--grid-intensity` (e.g. `--grid-intensity 56` for Sweden), or use a live
figure with `--grid-region SE` (an [Electricity
Maps](https://www.electricitymaps.com/) zone; needs
`--electricitymaps-token`/`ELECTRICITYMAPS_TOKEN`) — this takes priority over
`--grid-intensity` when set. It's an *estimate* — the value is trend and
awareness, not accounting.

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

Or drop it in as a GitHub Action instead of installing the CLI yourself —
it refreshes `badge.json` and publishes it to `gh-pages`:

```yaml
permissions:
  contents: write
jobs:
  badge:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: fabiocicerchia/carbon-badge@main
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
```

See all inputs/outputs in [`action.yml`](action.yml); full example workflow
(with the cron schedule) at
[`.github-workflow-example/carbon-badge.yml`](.github-workflow-example/carbon-badge.yml).

GitLab CI works the same way with `--provider gitlab`:

```sh
carbon-badge mygroup/myproject --provider gitlab --token $GITLAB_TOKEN > badge.json
```

Jobs on project/group (non-shared) runners are skipped either way — their
power draw is unknown, so they'd skew the estimate; a stderr line reports
how many were excluded.

For a badge that always reflects the last 5 minutes without a refresh
workflow, run it as a tiny local endpoint instead of a one-shot:

```sh
carbon-badge fabiocicerchia/nginx-lua --token $GITHUB_TOKEN --serve 8080
# -> http://localhost:8080/badge.json, recomputed at most once per 5 min
```

Put a reverse proxy (or an SSH tunnel) in front of it for anything public —
this is a bare `http.server`, not a hardened production service.

## Documentation

Full docs live in [`docs/`](docs/). Runnable examples live in [`examples/`](examples/).

## Development

`make setup` (git hooks) then `make dev`, and `make test` / `make lint`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Security

Found a vulnerability? See [SECURITY.md](SECURITY.md) — please don't open a public issue.

## Support

Need help implementing this? [Get in touch](https://fabiocicerchia.it/contact).

## License

Apache 2.0 — see [LICENSE](LICENSE).
