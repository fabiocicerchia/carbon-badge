# CI Platforms

What it shows: refreshing the badge from the sixteen CI/CD systems that aren't
GitHub Actions. One file per platform, each one a drop-in.

Two different things are going on, and it is worth separating them:

- **Where the badge is computed** — any of these platforms. The tool is a CLI;
  it needs Python and a token, nothing else.
- **What it measures** — the repository you point it at. `--provider github`
  reads the GitHub Actions API, `--provider gitlab` reads GitLab's jobs API.
  Running it from Jenkins does not measure Jenkins.

So a Jenkins job refreshing the badge for a GitHub repo is measuring that
repo's *GitHub Actions*, not the Jenkins build that ran the command. GitLab is
the one platform here that can measure itself, and
[`gitlab-ci.yml`](gitlab-ci.yml) does exactly that.

```sh
docker run --rm -e GITHUB_TOKEN ghcr.io/fabiocicerchia/carbon-badge:0.2.1 \
  OWNER/REPO --grid-intensity 56 > badge.json
```

Every file here uses the **published image**, `ghcr.io/fabiocicerchia/carbon-badge`,
rather than installing from source — nothing clones the repository or pip-installs
at run time. The image's entrypoint *is* `carbon-badge`, so the repo is just an
argument. Pin the tag (`0.2.1` above), not `latest`.

Two consequences worth knowing before reading the files:

- **The entrypoint has to be cleared** wherever the platform wants to run its
  own shell in the container (`entrypoint: [""]` on GitLab, `entrypoint:
  /bin/sh` on CircleCI and the Buildkite docker plugin, `--entrypoint=` on
  Jenkins). Where the platform supplies its own shell anyway — Harness `Run`
  steps, Cloud Build, Tekton, Argo — it does not.
- **The image runs as uid 10001**, so anything writing into a runner-owned
  checkout needs the container as root (`docker: {user: root}`,
  `user: root`, `run-as-user: 0`) or a `/tmp` path instead.

> [!NOTE]
> The image is published by the release pipeline, so a tag exists only once
> that release has run. If a pinned tag 404s, either publish it — the **Publish
> Image** workflow takes a tag via `workflow_dispatch` — or pin to the newest
> tag the registry actually has.

## Files

| Platform            | File                                                 | Copy it to                                  |
| ------------------- | ---------------------------------------------------- | ------------------------------------------- |
| GitLab CI           | [`gitlab-ci.yml`](gitlab-ci.yml)                     | `.gitlab-ci.yml`                            |
| CircleCI            | [`circleci-config.yml`](circleci-config.yml)         | `.circleci/config.yml`                      |
| Travis CI           | [`travis.yml`](travis.yml)                           | `.travis.yml`                               |
| Azure DevOps        | [`azure-pipelines.yml`](azure-pipelines.yml)         | `azure-pipelines.yml`                       |
| AWS CodePipeline    | [`buildspec.yml`](buildspec.yml)                     | `buildspec.yml` (CodeBuild stage)           |
| Devtron             | [`devtron-task.sh`](devtron-task.sh)                 | a Job, or a Post-Deployment task            |
| Northflank          | [`northflank-service.json`](northflank-service.json) | `northflank create service deployment -f …` |
| Spacelift           | [`spacelift-config.yml`](spacelift-config.yml)       | `.spacelift/config.yml`                     |
| Jenkins             | [`Jenkinsfile`](Jenkinsfile)                         | `Jenkinsfile`                               |
| Bitbucket Pipelines | [`bitbucket-pipelines.yml`](bitbucket-pipelines.yml) | `bitbucket-pipelines.yml`                   |
| Google Cloud Build  | [`cloudbuild.yaml`](cloudbuild.yaml)                 | `cloudbuild.yaml`                           |
| Tekton              | [`tekton.yaml`](tekton.yaml)                         | `kubectl apply -f`                          |
| Argo Workflows      | [`argo-workflow.yaml`](argo-workflow.yaml)           | `kubectl apply -f` (a CronWorkflow)         |
| Harness             | [`harness-pipeline.yml`](harness-pipeline.yml)       | the pipeline's YAML editor                  |
| Buildkite           | [`buildkite-pipeline.yml`](buildkite-pipeline.yml)   | `.buildkite/pipeline.yml`                   |
| Drone / Woodpecker  | [`drone.yml`](drone.yml)                             | `.drone.yml` / `.woodpecker.yml`            |

For GitHub Actions use the action itself — see
[`../../.github-workflow-example/carbon-badge.yml`](../../.github-workflow-example/carbon-badge.yml).

## The token

On GitHub Actions the workflow gets `GITHUB_TOKEN` for free. Nowhere else does,
so this is the one credential every file needs:

| provider | token                           | scope                             |
| -------- | ------------------------------- | --------------------------------- |
| `github` | a PAT (classic or fine-grained) | `actions:read` on the target repo |
| `gitlab` | a project or group access token | `read_api`                        |

`$CI_JOB_TOKEN` on GitLab cannot read the jobs API — it has to be a real token.

## Scheduling

The badge is a 30-day rolling figure, so it wants a cron, not a push trigger.
Every platform spells that differently, and several keep the schedule outside
the config file entirely:

| Platform           | Where the cron lives                                                                                  |
| ------------------ | ----------------------------------------------------------------------------------------------------- |
| GitLab CI          | Build → Pipeline schedules (the job filters on `$CI_PIPELINE_SOURCE == "schedule"`)                   |
| CircleCI           | Project Settings → Triggers → Scheduled pipelines (the workflow filters on `pipeline.trigger_source`) |
| Travis CI          | Settings → Cron Jobs (the script filters on `$TRAVIS_EVENT_TYPE`)                                     |
| Azure DevOps       | `schedules:` in the file — note `always: true`, or a quiet repo never refreshes                       |
| AWS CodePipeline   | an EventBridge rule targeting the pipeline or the CodeBuild project                                   |
| Devtron            | a Job with a cron trigger                                                                             |
| Northflank         | not a cron at all — see below                                                                         |
| Spacelift          | a scheduled task on the stack (the file's hook refreshes after each apply)                            |
| Jenkins            | `triggers { cron('H 2 * * *') }` in the file                                                          |
| Bitbucket          | Repository settings → Pipelines → Schedules, pointed at the `custom:` pipeline                        |
| Google Cloud Build | Cloud Scheduler → a trigger; Cloud Build itself is event-driven                                       |
| Tekton             | no native cron — a Kubernetes CronJob creates the TaskRun                                             |
| Argo Workflows     | a `CronWorkflow`, which is native                                                                     |
| Harness            | a Scheduled trigger on the pipeline (a separate resource from this YAML)                              |
| Buildkite          | Pipeline Settings → Schedules                                                                         |
| Drone              | Settings → Cron Jobs in the UI, matched by `trigger: {cron: [nightly]}`                               |

Nightly is plenty. The figure covers 30 days, so consecutive runs mostly
republish a number that has barely moved — and each run costs API calls.

## Northflank runs it as a service, not a job

`--serve PORT` turns the CLI into an HTTP server that answers `/badge.json`,
recomputing at most once every 5 minutes. Northflank's primitive for that is a
deployment service with a public port, which is what
[`northflank-service.json`](northflank-service.json) creates — no cron, no
bucket, no publishing step. The public URL *is* the badge endpoint:

```markdown
![CI carbon](https://img.shields.io/endpoint?url=https://YOUR-SERVICE.code.run/badge.json)
```

The tradeoff is that it holds a token in a running service and recomputes on
demand. shields.io caches, but the endpoint is public — keep an eye on your API
budget, and prefer a cron elsewhere if that is a concern.

## Publishing

The tool prints Shields.io endpoint JSON to stdout and stops there. That JSON
has to end up somewhere **shields.io can fetch over public HTTPS on every view
of your README** — which build artifacts, on most of these platforms, are not:

| Where                 | Notes                                                                              |
| --------------------- | ---------------------------------------------------------------------------------- |
| GitLab Pages          | the `pages` job in [`gitlab-ci.yml`](gitlab-ci.yml); the Pages site must be public |
| S3 / GCS / Azure Blob | what the AWS, Cloud Build and Azure files do — a bucket policy beats an ACL        |
| a `gh-pages` branch   | what the GitHub Action does; any platform can push to it with a token              |
| build artifacts       | fine for a log, useless for a badge on every platform here except Pages            |

## Runners it does not recognise

Off GitHub-hosted runners, the wattage cannot be inferred — the API exposes no
CPU or memory for any runner, and labels are arbitrary text. An unrecognised
runner falls back to a generic figure and the badge says `(rough)`.

`--runner-watts 180` declares one figure for every job;
`--runner-watts LABEL=WATTS`, repeatable, is for mixed fleets. GitLab matches on
tags and the runner description. See
[`docs/assumptions.md`](../../docs/assumptions.md) for what the numbers rest on,
and the README's "How a number is arrived at" for what *measured*, *declared*
and *guessed* mean on the badge.

## Platform notes

**GitLab CI** — the one that measures itself: `--provider gitlab --api
"$CI_API_V4_URL" "$CI_PROJECT_PATH"` reads this project's own jobs. The job is
named `pages`, so its `public/` artifact is published by GitLab Pages.

**CircleCI** — `when: {equal: [scheduled_pipeline, << pipeline.trigger_source >>]}`
is what keeps the workflow off every push.

**Travis CI** — runs jobs on a VM rather than an image you pick, so the image
is pulled and run rather than installed into. Cron builds run the same script
as pushes, so it exits early unless `$TRAVIS_EVENT_TYPE` is `cron`.

**Azure DevOps** — `always: true` matters: without it a scheduled run is skipped
when the branch hasn't changed, and the badge goes stale on quiet repos. The
image is run with `docker run` rather than as a `container:` job, so the tasks
keep the Node runtime that container jobs require.

**AWS CodePipeline** — CodePipeline has no schedule; EventBridge provides it.
The token comes from Secrets Manager via `env/secrets-manager`. `docker run`
keeps the managed image's AWS CLI available for the S3 upload, at the cost of
the project's **privileged mode** being on; setting the project's own image to
`carbon-badge` instead drops the buildspec to two lines but takes the CLI with
it.

**Devtron** — a Job with a cron trigger is the closer fit than a deployment
stage, since nothing here gates a release. A Container-image task pointed at
the published image needs no script at all: the entrypoint is the CLI, so the
arguments go in the task's Args. The shell script in this folder is the
fallback for a node with Docker but no per-task image.

**Spacelift** — the one platform here where the published image cannot be the
job image: hooks run inside the stack's *runner* image, which already has to
carry your IaC tooling. Layer the CLI into a runner image built once (the file
shows the Dockerfile) rather than installing on every run. The hook also
swallows its own errors (`|| true`): a badge is a report, and a report must
never fail a run.

**Jenkins** — `cron('H 2 * * *')`: the `H` spreads load across the hour instead
of every job on the controller firing at :00.

**Bitbucket Pipelines** — schedules attach to `custom:` pipelines, which never
run on a push. That is why the step lives under `custom:`. `run-as-user: 0`
because the image's uid does not own the clone.

**Google Cloud Build** — the image's entrypoint is overridden with `sh` only
because a step cannot redirect stdout to a file on its own.

**Tekton** — no native cron; the second document is a plain Kubernetes CronJob
that creates a TaskRun. Its service account needs `create` on `taskruns` and
nothing else.

**Argo Workflows** — a `CronWorkflow` is native, which makes this the shortest
file of the sixteen.

**Harness** — `cloneCodebase: false`: nothing is read from the repository, so
skipping the clone saves the whole checkout.

**Buildkite** — Buildkite interpolates `$VAR` at pipeline *upload* time, so
anything the shell must expand at *run* time is written `$$VAR`. Listing
`GITHUB_TOKEN` with no value in the plugin's `environment` passes it through
from the agent rather than baking it into the file.

**Drone / Woodpecker** — the cron is created in the UI and matched by name in
`trigger.cron`. The image's entrypoint is overridden because `command:` needs a
shell to redirect.
