@domain:measured-data @area:reconciliation @from:carbon-badge/providers
Feature: Combining self-reported and API-priced GitHub runs
  Serves repo maintainers who are adding carbon instrumentation gradually, so the monthly badge stays accurate and never counts a job twice while coverage grows.

  Background:
    Given the repository has GitHub Actions workflow runs in the last 30 days

  @id:measured-data.reconciliation.001 @rule:measured.runs.complete-run-uses-its-own-measurement @verified @source:carbon_badge/providers.py#ci_kwh_last_30d @test:tests/test_carbon_badge.py#test_full_coverage_costs_one_sample_per_workflow @test:tests/test_carbon_badge.py#test_partial_instrumentation_tops_up_from_the_api
  Scenario: A run where every job self-reported is counted from its own measurements
    Given run 1 is the only run in the window and has 3 jobs
    And every job of run 1 published a carbon artifact
    When the operator runs an estimate for the repository
    Then run 1's energy is the sum of its self-reported measurements
    And its 3 jobs are counted as self-reported
    And the confidence counts read "3/3 job(s) self-reported"

  @id:measured-data.reconciliation.003 @rule:measured.runs.ignore-self-reported-prices-everything-from-api @verified @source:carbon_badge/providers.py#ci_kwh_last_30d @test:tests/test_carbon_badge.py#test_ignore_self_reported_does_not_consult_artifacts
  Scenario: Ignoring self-reported data prices every run from the API
    Given run 1 has a self-reported carbon artifact for its only job
    When the operator runs an estimate with self-reported data ignored
    Then run 1 is priced from its jobs as GitHub lists them
    And no carbon artifacts are read for the repository
    And no job counts as self-reported

  @id:measured-data.reconciliation.004 @rule:measured.runs.api-priced-runs-use-repo-grid-factor @inferred @source:carbon_badge/providers.py#ci_kwh_last_30d
  Scenario: Runs priced from the API use the repository-wide grid factor, not a regional one
    Given run 1 self-reported every job from region eastus
    And run 2 has no self-reported data
    And the operator declared no grid intensity
    When the operator runs an estimate for the repository
    Then run 1's grams use the grid factor for its reported region
    And run 2's grams use the 480 gCO2e/kWh world-average grid factor

  @id:measured-data.reconciliation.005 @rule:measured.runs.coverage-reported-to-operator @inferred @source:carbon_badge/providers.py#ci_kwh_last_30d
  Scenario: The operator is told how many runs fully self-reported
    Given 4 runs in the window, of which 2 fully self-reported
    When the operator runs an estimate for the repository
    Then the log reads "2/4 run(s) fully self-reported; 2 priced from the API. Instrument the remaining jobs to shrink that."
