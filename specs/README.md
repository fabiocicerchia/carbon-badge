# Specifications of carbon-badge

What this code does, one atomic Gherkin scenario per behaviour, read from the code by code-atlas.
Tags: `@verified` a test asserts it · `@inferred` read from the code only · `@gap` an entry point no test covers · `@human` written or amended by a person, never rewritten.

## Badge output

Presenting the footprint as a badge others can embed.

- **Badge content**: [Stating the CI footprint and its confidence on the badge](badge-output/badge-content/badge-message-and-confidence.feature) (5)
- **Badge server**: [Serving the badge over HTTP](badge-output/badge-server/badge-server.feature) (5)
- **Command line**: [Running a carbon estimate from the command line](badge-output/command-line/command-line-estimate.feature) (4), [Running an estimate from the command line](badge-output/command-line/running-an-estimate.feature) (2)

## Grid carbon intensity

Converting energy into CO2e using the carbon intensity of the region's electricity grid.

- **Live grid data**: [Pricing the grid from Electricity Maps](carbon-intensity/live-grid-data/live-grid-intensity-electricity-maps.feature) (2), [Pricing jobs at their region's live grid intensity](carbon-intensity/live-grid-data/live-regional-grid-intensity.feature) (8)
- **Regional grid factors**: [Choosing a grid factor for the region a job ran in](carbon-intensity/regional-factors/regional-grid-factors.feature) (1)

## Distribution and developer tooling

Installing, packaging, building and documenting carbon-badge.

- **Container image**: [Running carbon-badge from the container image](developer-tooling/container-image/container-image.feature) (2)
- **Installation**: [Installing carbon-badge with the one-line installer](developer-tooling/installation/one-line-installer.feature) (3)

## Energy estimation

Turning CI job time into kilowatt-hours using runner power models.

- **CPU load adjustment**: [Adjusting power draw for CPU load](energy-estimation/cpu-load/cpu-load-adjustment.feature) (2)
- **Runner power models**: [Pricing a CI run job by job](energy-estimation/runner-power/job-by-job-run-pricing.feature) (3), [Pricing a CI runner's power draw](energy-estimation/runner-power/pricing-runner-power.feature) (7)

## Measured job data

Recording real per-job measurements in CI and using them to check the estimate.

- **Carbon artifact reading**: [Recognising carbon markers by artifact name](measured-data/artifact-reading/carbon-marker-format.feature) (3), [Pricing CI runs from the carbon artifacts their jobs recorded](measured-data/artifact-reading/reading-carbon-artifacts.feature) (6)
- **Estimate reconciliation**: [Combining self-reported and API-priced GitHub runs](measured-data/reconciliation/combining-self-reported-and-api-priced-runs.feature) (4), [Reconciling the estimate against self-reported measurements](measured-data/reconciliation/estimate-reconciliation.feature) (8)
- **Recording action**: [Recording measured job data from inside a GitHub Actions job](measured-data/record-action/recording-job-measurements.feature) (7)

## CI usage collection

Gathering what a repository's CI actually ran from the CI provider's API.

- **GitHub Actions runs and jobs**: [Collecting GitHub Actions runs and jobs](usage-collection/github-actions/github-actions-collection.feature) (3)
- **GitLab pipelines**: [Estimating GitLab CI usage](usage-collection/gitlab-pipelines/gitlab-job-usage.feature) (4)
- **Other CI platforms**: [Refreshing the carbon badge from a Devtron task](usage-collection/other-ci-platforms/devtron-badge-refresh.feature) (5)
