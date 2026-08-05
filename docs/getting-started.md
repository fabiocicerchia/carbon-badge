# Getting Started

## Prerequisites

- Python 3.10+
- A GitHub token with read access to the target repo's Actions
  (`GITHUB_TOKEN` in CI, or a PAT with `actions:read`).

## Install

```sh
pipx install .        # or: pip install .
```

## Run

```sh
carbon-badge fabiocicerchia/carbon-badge --token "$GITHUB_TOKEN" > badge.json
```

Serve `badge.json` anywhere public (gist, S3, GitHub Pages) and embed it:

```markdown
![CI carbon](https://img.shields.io/endpoint?url=https://example.com/badge.json)
```

Override the grid intensity for your region, e.g. Sweden:

```sh
carbon-badge OWNER/REPO --grid-intensity 56 > badge.json
```

To refresh the badge nightly in CI, copy
[`.github-workflow-example/carbon-badge.yml`](../.github-workflow-example/carbon-badge.yml)
into the target repo.

## Development

`make setup` (git hooks) then `make dev`, and `make test` / `make lint`.

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

See all inputs/outputs in [`action.yml`](../action.yml); full example workflow
(with the cron schedule) at
[`.github-workflow-example/carbon-badge.yml`](../.github-workflow-example/carbon-badge.yml).

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
