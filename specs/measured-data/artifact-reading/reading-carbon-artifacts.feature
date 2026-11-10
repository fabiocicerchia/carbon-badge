@domain:measured-data @area:artifact-reading @from:carbon-badge/artifacts
Feature: Pricing CI runs from the carbon artifacts their jobs recorded
  Lets maintainers who instrument their workflows see a badge priced from each job's recorded duration, hardware, load and region instead of the API estimate.

  Background:
    Given the repository's workflows publish carbon artifacts with the recording action
    And carbon-badge reads the repository's artifact listing over the last 30 days

  @id:measured-data.artifact-reading.002 @rule:measured-data.artifacts.thirty-day-window @verified @source:carbon_badge/artifacts.py#artifact_kwh_by_run @test:tests/test_carbon_badge.py#test_expired_and_out_of_window_markers_are_ignored
  Scenario: Expired or month-old markers do not count toward the badge
    Given a carbon marker for run 1 created today
    And a carbon marker for run 2 created 40 days ago
    And an expired carbon marker for run 3 created today
    When the operator refreshes the badge
    Then only run 1 is counted as recorded
    And 1 recorded job is reported

  @id:measured-data.artifact-reading.003 @rule:measured-data.artifacts.energy-from-recorded-hardware @verified @source:carbon_badge/artifacts.py#artifact_kwh_by_run @test:tests/test_carbon_badge.py#test_artifact_kwh_by_run_keys_by_run_and_downloads_nothing
  Scenario: A recorded job's energy comes from its own duration and hardware, summed per run
    Given run 11 recorded a 3600-second job and a 1800-second job, each on 2 vCPU with 7168 MB on ubuntu
    And run 22 recorded a 3600-second job on the same hardware
    When the operator refreshes the badge
    Then run 11 is charged 1.5 hours at the wattage of a 2 vCPU / 7168 MB ubuntu machine, with 2 recorded jobs
    And run 22 is charged 1 hour at that wattage, with 1 recorded job
    And no job listing or artifact download is requested for either run

  @id:measured-data.artifact-reading.005 @rule:measured-data.artifacts.declared-blanket-watts-win @verified @source:carbon_badge/artifacts.py#artifact_kwh_by_run @test:tests/test_carbon_badge.py#test_declared_watts_still_beat_the_model @questionable
  Scenario: A wattage declared for every runner overrides the recorded hardware
    Given run 1 recorded a 1-hour job on 2 vCPU / 7168 MB ubuntu
    And the operator declared 200 W for every runner with --runner-watts '*=200'
    When the operator refreshes the badge
    Then run 1 is charged 0.2 kWh

  @id:measured-data.artifact-reading.006 @rule:measured-data.artifacts.priced-at-recorded-region @verified @source:carbon_badge/artifacts.py#RegionFactors @test:tests/test_carbon_badge.py#test_resolver_caches_per_region_and_honours_an_explicit_figure @test:tests/test_carbon_badge.py#test_ci_api_failure_leaves_the_annual_average_standing @test:tests/test_carbon_badge.py#test_unknown_region_falls_back_to_the_annual_average
  Scenario Outline: Each recorded job is priced at the grid of the region it ran in
    Given a recorded job ran in region "<region>"
    And <grid condition>
    When the operator refreshes the badge
    Then that job's emissions use <factor>

    Examples:
      | region    | grid condition                                    | factor                                                               |
      | uksouth   | the live UK grid reads 100 gCO2e/kWh              | 100 gCO2e/kWh                                                        |
      | uksouth   | the operator set a grid override of 333 gCO2e/kWh | 333 gCO2e/kWh                                                        |
      | japaneast | the live grid provider is unreachable             | the region's annual average, with a warning that the snapshot failed |
      | moonbase1 | no grid figure is known for the region            | the 480 gCO2e/kWh world average                                      |

  @id:measured-data.artifact-reading.007 @rule:measured-data.artifacts.partial-runs-priced-from-api @verified @source:carbon_badge/artifacts.py#_expected_markers @test:tests/test_carbon_badge.py#test_a_partly_instrumented_run_does_not_lose_its_other_jobs @test:tests/test_carbon_badge.py#test_markers_are_not_double_counted_when_a_run_is_topped_up @test:tests/test_carbon_badge.py#test_an_unusually_small_newest_run_cannot_lower_the_bar @test:tests/test_carbon_badge.py#test_skipped_jobs_do_not_make_completeness_unreachable
  Scenario: A run where not every job recorded itself is priced entirely from the API
    Given the newest run of the workflow ran a single job and recorded its marker
    And an older run of the workflow recorded markers for all 3 of its jobs
    And run 3 ran 3 jobs, skipped jobs excluded, but recorded markers for only 2 of them
    When the operator refreshes the badge
    Then run 3 is priced from its job listing, not from its 2 markers
    And each of run 3's jobs is counted exactly once
    And run 3 does not count toward the measured share of the badge

  @id:measured-data.artifact-reading.008 @rule:measured-data.artifacts.truncated-listing-warned @inferred @source:carbon_badge/artifacts.py#list_artifacts
  Scenario: The operator is warned when not every artifact could be read
    Given GitHub reports more artifacts than carbon-badge could read
    When the operator refreshes the badge
    Then a warning says how many of GitHub's reported artifacts were read and that the figure is an undercount
    And the warning names the cause: either the local page cap or GitHub's 1000-result limit
