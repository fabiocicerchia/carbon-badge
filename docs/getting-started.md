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
