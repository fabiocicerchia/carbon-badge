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

### Letting jobs measure themselves (recommended)

Add two steps to a job and it records its own duration and the machine's real
CPU and memory, so the weekly refresh reads a month of exact measurements
without one API call per run:

```yaml
jobs:
  build:
    steps:
      - uses: fabiocicerchia/carbon-badge/record-start@v1
      # ... the job's real steps ...
      - uses: fabiocicerchia/carbon-badge/record-end@v1
        if: always()
```

`record-end` uploads a one-line artifact whose *name* carries the measurement:

```
carbon.v1.142.2.7168.build      # 142 s, 2 vCPU, 7168 MB
```

The artifacts API returns names in its listing, 100 per request, so nothing is
downloaded. On a repo with ~400 runs a month that is **1 request instead of
~421** — and the numbers are measured rather than inferred, so `runner-watts`
becomes unnecessary (a declared value still wins if you set one).

No extra permissions are needed: `upload-artifact` authenticates with
`ACTIONS_RUNTIME_TOKEN`, not `GITHUB_TOKEN`, so your jobs keep `contents: read`.

#### How jobs that end badly are handled

Measured, not assumed. Every termination path was run against a live repo
([cron-translate#26](https://github.com/fabiocicerchia/cron-translate/pull/26)):

| how the job ended | marker written? |
|---|---|
| succeeded | yes |
| failed (`exit 1`, and by extension an OOM-killed step) | **yes** |
| cancelled mid-run | **yes** |
| killed by `timeout-minutes` | **yes** |

This is why `record/` is a JavaScript action with a `post:` step rather than a
composite step with `if: always()`. An `if: always()` step does **not** run on
cancellation; a `post:` step does, and GitHub treats a `timeout-minutes` kill as
a cancellation too, so both are covered.

What is still not covered is the runner itself dying — an infrastructure
failure that takes the VM with it before teardown. Nothing inside the job can
survive that, and it is rare.

A run that recorded nothing isn't treated as self-reported at all, so it gets
queried like any uninstrumented run. The exposure is therefore limited to a
*job* that vanished inside a run whose siblings did report. Every run's coverage
is printed to stderr, so a drift shows up in the workflow log.

#### Known limitation: the job's own setup time is invisible

The recorder starts its clock when its step executes — after runner
provisioning, "Set up job", and downloading the action. That time is real
energy and cannot be seen from inside the job. Measured across six jobs on the
trial repo it was a near-constant **~5.7 s/job**, so the error scales inversely
with job length: ~39% on ten-second jobs, ~1% on a ten-minute build. It biases
the self-reported figure **downward**. See `TODO.md` for the candidate fix.

**Before trusting the self-reported figure, reconcile it once.** Run both paths
against the same repo and compare:

```sh
carbon-badge owner/repo --token "$GITHUB_TOKEN"                        # normal
carbon-badge owner/repo --token "$GITHUB_TOKEN" --ignore-self-reported # API only
```

That is the only reason `--ignore-self-reported` exists — it is slower and no
more accurate for day-to-day use. A persistent gap between the two figures is
the cancelled/killed population. If that matters for your repo, keep the flag
on permanently and pay the requests.

### Telling it how big your runners are

Standard GitHub-hosted runners are priced from a built-in table. Anything else
— self-hosted, larger runners, GPU, custom labels — has to be declared, because
**the API exposes no CPU or memory for any runner**. Labels are the only signal
there is, and they're arbitrary text, so size can't be inferred reliably.

Set it once, when you add the workflow:

```yaml
      - uses: fabiocicerchia/carbon-badge@main
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
          runner-watts: "180"     # average draw of your runners, in watts
```

That single number covers every job and is all most repos need. Only if you
genuinely mix runner types, give one `LABEL=WATTS` per line — a bare number can
sit alongside them as the fallback:

```yaml
          runner-watts: |
            180
            gpu=320
            macos-14-xlarge=90
```

Matching is exact label first, then substring (so `gpu=320` covers a family),
then the bare number, then the built-in table.

Undeclared runners are charged at the ubuntu baseline and named in the log, so
you can see what to declare:

```
carbon-badge: 42 job(s) on unrecognised runner 'self-hosted,linux,x64'
  charged at the 12.5 W baseline; pass --runner-watts 'self-hosted=<watts>'
  to price it
```

They're charged rather than skipped on purpose. Skipping scored them as zero,
which meant moving a build onto the biggest machine you own *improved* your
badge. A visible undercount beats a silent free pass.

GitLab CI works the same way with `--provider gitlab`, matching `runner-watts`
against job tags and the runner description:

```sh
carbon-badge mygroup/myproject --provider gitlab --token $GITLAB_TOKEN > badge.json
```

For a badge that always reflects the last 5 minutes without a refresh
workflow, run it as a tiny local endpoint instead of a one-shot:

```sh
carbon-badge fabiocicerchia/nginx-lua --token $GITHUB_TOKEN --serve 8080
# -> http://localhost:8080/badge.json, recomputed at most once per 5 min
```

Put a reverse proxy (or an SSH tunnel) in front of it for anything public —
this is a bare `http.server`, not a hardened production service.
