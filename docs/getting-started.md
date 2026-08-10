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

Add one step to a job and it records its own duration and the machine's real
CPU and memory, so the refresh reads a month of exact measurements instead of
querying every run:

```yaml
jobs:
  build:
    steps:
      - uses: fabiocicerchia/carbon-badge/record@v1
      # ... the job's real steps ...
```

One line, at the top of the job. Its `post:` step runs when the job ends and
uploads a one-line artifact whose *name* carries the measurement:

```
carbon.v1.142.4.16384.ubuntu.eastus.build-a1b2c3d4   # 142 s, 4 vCPU, 16384 MB, x86 Linux, US-MIDA-PJM
```

The artifacts API returns names in its listing, 100 per request, so nothing is
downloaded. On a repo with ~400 runs a month that is roughly **20 requests
instead of ~426**: the markers are themselves artifacts to page through, the
run list is still needed, and one run per workflow is sampled to learn how many
jobs a complete run has. Around 40% of the hourly token budget down to 2%.

The numbers are also measured rather than inferred, so `runner-watts` becomes
unnecessary — though a declared value still wins if you set one.

No extra permissions are needed: `upload-artifact` authenticates with
`ACTIONS_RUNTIME_TOKEN`, not `GITHUB_TOKEN`, so your jobs keep `contents: read`.

#### Adopting it: whole workflow files, busiest first

A run is priced from its markers only when **every job that ran** reported one.
One job short and the whole run falls back to the API — correctly, since
trusting a partial run would score its uninstrumented jobs as zero energy.

Three consequences worth knowing before you start:

- **Instrument a whole workflow file at a time.** Adding the step to some jobs
  in `ci.yml` and not others buys nothing at all: every run of that workflow
  still costs an API call. Finishing the file converts all of its runs at once.
- **Different workflows are independent.** `ci.yml` fully instrumented pays off
  immediately even if `security.yml` is untouched. The badge reads `partial`
  and the log tells you how far along you are.
- **Start with the busiest workflow.** It has both the most runs and usually
  the most jobs per run, so it converts the most volume per file edited.

Conditional jobs are not a problem: a skipped job never runs a step, so it
cannot report, and it is excluded from the count on both sides. A workflow
where two of three jobs are skipped by their `if:` still reaches completeness
on the one that ran.

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

A run is only trusted when it reported *every* job — the marker count is
compared against the real job count, sampled once per workflow. A run that
reported none, or only some, is priced from the API instead, so a half-adopted
workflow cannot quietly drop the jobs that are not instrumented yet. The
remaining exposure is a job that vanished inside an otherwise complete run,
which needs the runner itself to die. Coverage is printed to stderr each run,
so a drift shows up in the workflow log.

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
  charged at the 9.4 W baseline; pass --runner-watts 'self-hosted=<watts>' to price it
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
