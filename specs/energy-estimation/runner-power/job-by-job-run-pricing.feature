@domain:energy-estimation @area:runner-power @from:carbon-badge/ci
Feature: Pricing a CI run job by job
  Gives a repository owner an energy figure where each job is charged for its own runtime on its own runner, so moving work between machines changes the badge the way it should.

  Background:
    Given the repository "acme/widgets" has one workflow run in the window that is priced from the GitHub API

  @id:energy-estimation.runner-power.001 @rule:energy-estimation.runner-power.job-charged-at-own-runner-draw @verified @source:carbon_badge/ci.py#_api_run_detail @test:tests/test_carbon_badge.py#test_a_partly_instrumented_run_does_not_lose_its_other_jobs @test:tests/test_carbon_badge.py#test_github_client_over_real_http
  Scenario: Each job is charged its own runtime at its own runner's power draw
    Given the run has a 1-hour job and a 10-hour job, both on "ubuntu-latest"
    When the operator estimates the footprint of "acme/widgets"
    Then the run's energy is 11 hours times the ubuntu runner's draw, in kWh
    And both jobs count toward the job total

  @id:energy-estimation.runner-power.002 @rule:energy-estimation.runner-power.unknown-runner-charged-at-baseline @verified @source:carbon_badge/ci.py#_api_run_detail @test:tests/test_carbon_badge.py#test_unknown_runners_are_charged_not_skipped @test:tests/test_carbon_badge.py#test_declaring_an_unknown_runner_clears_the_guess
  Scenario: A job on an unrecognised runner is charged at the baseline, counted as guessed, and named in a warning
    Given the run has a job on a runner labelled "self-hosted,linux,x64"
    And no wattage has been declared for that runner
    When the operator estimates the footprint of "acme/widgets"
    Then that job is charged at the baseline runner draw instead of being left out
    And its energy counts as guessed, which lowers the badge's confidence
    And a warning names the runner "self-hosted,linux,x64" and suggests --runner-watts

  @id:energy-estimation.runner-power.003 @rule:energy-estimation.job-duration.unrun-jobs-not-charged @verified @source:carbon_badge/ci.py#_api_run_detail @test:tests/test_carbon_badge.py#test_skipped_jobs_contribute_no_energy
  Scenario Outline: Jobs that did not run, or report no start or finish time, add no energy and no job
    Given the run has a 1-hour job on "ubuntu-latest" that succeeded
    And it also has a job that <state>
    When the operator estimates the footprint of "acme/widgets"
    Then the run's energy is 1 hour times the ubuntu runner's draw
    And the run counts 1 job

    Examples:
      | state                                                      |
      | was skipped, even though it reports start and finish times |
      | has no finish time                                         |
