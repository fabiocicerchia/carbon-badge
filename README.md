# carbon-badge

[![CI](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/ci.yml/badge.svg)](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/ci.yml)
[![Security](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/security.yml/badge.svg)](https://github.com/fabiocicerchia/carbon-badge/actions/workflows/security.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/fabiocicerchia/carbon-badge/badge)](https://securityscorecards.dev/viewer/?uri=github.com/fabiocicerchia/carbon-badge)
[![CI carbon](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/fabiocicerchia/carbon-badge/gh-pages/badge.json)](.github/workflows/carbon-badge.yml)
[![Release](https://img.shields.io/github/v/release/fabiocicerchia/carbon-badge)](https://github.com/fabiocicerchia/carbon-badge/releases)

A **Shields-style README badge for your repo's CI carbon footprint**. Reads
30 days of GitHub Actions runs, converts runner-minutes to gCO2e with
documented assumptions, and emits Shields.io endpoint JSON. The same pattern
as Badge Poser — for sustainability.

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
