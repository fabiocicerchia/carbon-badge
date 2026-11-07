@domain:measured-data @area:reconciliation @from:carbon-badge/reconcile
Feature: Reconciling the estimate against self-reported measurements
  Lets a maintainer see why the API-based estimate and the self-reported carbon markers disagree, split into setup time, the watts model and the grid factor, so each cause can be judged and fixed separately.

  Background:
    Given a GitHub repository whose workflow runs in the last 30 days are visible to the operator

  @id:measured-data.reconciliation.006 @rule:measured-data.reconciliation.refused-without-github-markers @verified @source:carbon_badge/cli.py#main @test:tests/test_carbon_badge.py#test_reconcile_refuses_the_combinations_that_cannot_work
  Scenario Outline: Reconciliation is refused when there are no two GitHub paths to compare
    Given the operator asks for a reconciliation report
    When the operator runs carbon-badge with --reconcile and <other flag>
    Then the command exits with a usage error
    And stderr says "<message>"
    And no report and no badge JSON are printed

    Examples:
      | other flag        | message                                                                 |
      | --provider gitlab | --reconcile needs the GitHub artifact markers; not available for GitLab |
      | --minutes 100     | --reconcile compares two API paths; --minutes uses neither              |

  @id:measured-data.reconciliation.007 @rule:measured-data.reconciliation.only-fully-instrumented-runs @verified @source:carbon_badge/reconcile.py#reconcile_last_30d @test:tests/test_carbon_badge.py#test_a_partly_instrumented_run_is_not_compared
  Scenario: Only runs where every job self-reported are compared
    Given run 1 has two jobs, "build" and "test", and only "build" left a carbon marker
    When the operator runs carbon-badge with --reconcile
    Then run 1 is not compared
    And the report says "nothing to reconcile — 1 run(s) in the window, none of them fully self-reported"

  @id:measured-data.reconciliation.008 @rule:measured-data.reconciliation.setup-time-is-unseen-seconds @verified @source:carbon_badge/reconcile.py#reconcile_last_30d @test:tests/test_carbon_badge.py#test_a_run_the_marker_and_the_api_agree_on_shows_no_setup_gap @test:tests/test_carbon_badge.py#test_the_setup_gap_is_the_seconds_the_marker_never_saw
  Scenario Outline: Setup time is the job time the marker never saw
    Given a fully self-reported run whose marker timed <marker seconds> s
    And the API bills the same job for <api minutes> minutes
    When the operator runs carbon-badge with --reconcile
    Then the report shows a setup-time gap of <gap> s
    And the setup-time energy is <setup share> of the API path's energy for the run

    Examples:
      | marker seconds | api minutes | gap | setup share |
      | 600            | 10          | 0   | none        |
      | 600            | 15          | 300 | one third   |

  @id:measured-data.reconciliation.009 @rule:measured-data.reconciliation.watts-model-term @verified @source:carbon_badge/reconcile.py#reconcile_last_30d @test:tests/test_carbon_badge.py#test_a_watts_disagreement_is_not_reported_as_setup_time
  Scenario: A wattage disagreement on the same seconds is reported as the watts model, not setup time
    Given a fully self-reported run whose marker and API agree on 600 s
    And the operator declares ubuntu-latest runners at 500 W
    When the operator runs carbon-badge with --reconcile
    Then the setup-time term is 0 kWh
    And the watts-model term equals the whole kWh divergence and is positive

  @id:measured-data.reconciliation.010 @rule:measured-data.reconciliation.grid-factor-term @verified @source:carbon_badge/reconcile.py#reconcile_last_30d @test:tests/test_carbon_badge.py#test_the_grid_gap_is_separated_from_the_energy_gap @test:tests/test_carbon_badge.py#test_an_explicit_grid_figure_removes_the_grid_term
  Scenario Outline: The grid term is the grams gap that the energy gap does not explain
    Given a fully self-reported run that ran in eastus
    And the grid intensity is <grid declaration>
    When the operator runs carbon-badge with --reconcile
    Then the grid-factor term is <grid term>
    And setup time, watts model and grid factor add up to the whole gCO2e divergence

    Examples:
      | grid declaration                                     | grid term |
      | not declared, so the API path uses the world average | non-zero  |
      | declared as 400 gCO2e/kWh with --grid-intensity      | 0 gCO2e   |

  @id:measured-data.reconciliation.011 @rule:measured-data.reconciliation.jobs-matched-by-name @verified @source:carbon_badge/reconcile.py#_marker_seconds_by_run @test:tests/test_carbon_badge.py#test_jobs_are_matched_by_name_between_the_two_paths @test:tests/test_carbon_badge.py#test_an_unmatched_job_still_counts_in_its_run
  Scenario Outline: Markers are matched to jobs by name, and an unmatched job still counts in its run
    Given a fully self-reported run with jobs <job names>
    And its markers are named after <marker jobs>
    When the operator runs carbon-badge with --reconcile
    Then the report says "<matched>/<jobs> job(s) matched by name between the two paths"
    And the run's marker and API totals still include every job

    Examples:
      | job names             | marker jobs                         | matched | jobs |
      | "build", "Unit Tests" | build-a1b2c3d4, unit_tests-0f9e8d7c | 2       | 2    |
      | "build"               | mystery-slug                        | 0       | 1    |

  @id:measured-data.reconciliation.012 @rule:measured-data.reconciliation.report-explains-divergence @verified @source:carbon_badge/reconcile.py#format_reconciliation @test:tests/test_carbon_badge.py#test_the_report_names_all_three_terms
  Scenario: The reconciliation report lists diverging runs and names the three causes
    Given a fully self-reported run where the API bills 15 minutes and the marker timed 600 s
    When the operator runs carbon-badge with --reconcile
    Then stdout shows a per-run table of up to 15 runs, largest absolute gCO2e gap first, leaving out runs where both paths agree
    And stdout shows an AGGREGATE section with self-reported, API and divergence kWh and gCO2e
    And stdout shows a WHERE IT COMES FROM section naming setup time, watts model and grid factor
    And the report says setup time is the only one of the three that is a BIAS
    And no badge JSON is printed

  @id:measured-data.reconciliation.013 @rule:estimate.runners.undeclared-runner-named-with-fix @verified @source:carbon_badge/reconcile.py#_warn_undeclared_runners @test:tests/test_carbon_badge.py#test_undeclared_warning_never_suggests_a_command_that_will_not_parse
  Scenario Outline: Jobs on unrecognised runners are named with a fix that will parse
    Given <count> job(s) ran on runners labelled <labels> that have no declared wattage
    When the operator runs an estimate or a reconciliation
    Then stderr warns "<count> job(s) on unrecognised runner '<labels>' charged at the 9.4 W baseline"
    And the warning ends with "<remedy>"

    Examples:
      | count | labels           | remedy                                                   |
      | 2     | my-builder,linux | pass --runner-watts 'my-builder=<watts>' to price it     |
      | 3     | (no labels)      | give it a label, or set a blanket --runner-watts <watts> |
