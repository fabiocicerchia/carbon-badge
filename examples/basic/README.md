# Basic Example

What it shows: generating a Shields.io endpoint badge for a repo's CI carbon
footprint and previewing it locally.

## Run

```sh
# from the repo root, after `pipx install .` (or `make dev`)
export GITHUB_TOKEN=ghp_...            # token with actions:read on the target repo
carbon-badge fabiocicerchia/carbon-badge --token "$GITHUB_TOKEN" > badge.json
cat badge.json
```

`badge.json` is Shields.io endpoint JSON. Host it publicly and embed:

```markdown
![CI carbon](https://img.shields.io/endpoint?url=https://example.com/badge.json)
```
