@domain:usage-collection @area:gitlab-pipelines @from:carbon-badge/providers
Feature: Estimating GitLab CI usage
  Lets GitLab projects get a 30-day CI energy and carbon figure from the job list, with runner wattages they can declare.

  Background:
    Given the operator estimates a GitLab project such as group/project

  @id:usage-collection.gitlab-pipelines.001 @rule:gitlab.jobs.only-recent-jobs-with-duration-charged @inferred @source:carbon_badge/providers.py#gitlab_kwh_last_30d
  Scenario: Only jobs created in the last 30 days that report a duration are charged
    Given a job created today that ran 3600 seconds
    And a job created 40 days ago that ran 3600 seconds
    And a job created today with no recorded duration
    When the operator runs an estimate for the project
    Then only the first job is charged
    And the job count is 1

  @id:usage-collection.gitlab-pipelines.002 @rule:gitlab.runners.undeclared-runner-charged-at-baseline @verified @source:carbon_badge/providers.py#gitlab_kwh_last_30d @test:tests/test_carbon_badge.py#test_gitlab_kwh_charges_self_managed_runners @test:tests/test_carbon_badge.py#test_gitlab_takes_an_injected_getter @questionable
  Scenario: A job on an undeclared runner is charged at the baseline wattage and lowers confidence
    Given two 1-hour jobs today, one on a shared runner and one on a self-managed runner
    And no runner wattage is declared
    When the operator runs an estimate for the project
    Then both jobs are charged at the default runner wattage
    And the undeclared-runner warning names the untagged self-managed runner and suggests giving it a label or a blanket --runner-watts figure
    And the confidence is "rough"

  @id:usage-collection.gitlab-pipelines.003 @rule:gitlab.runners.declared-watts-match-tags-or-description @verified @source:carbon_badge/providers.py#gitlab_kwh_last_30d @test:tests/test_carbon_badge.py#test_gitlab_runner_watts_matches_on_tags @test:tests/test_carbon_badge.py#test_gitlab_honours_a_blanket_override_on_untagged_jobs
  Scenario Outline: Declared runner wattages apply by tag, runner description or blanket figure
    Given a 1-hour job created today tagged <tags>
    And the operator declared runner wattage <declared>
    When the operator runs an estimate for the project
    Then the job is charged at <watts> W
    And the confidence is "estimated"

    Examples:
      | tags      | declared      | watts |
      | big-metal | big-metal=200 | 200   |
      | (none)    | *=180         | 180   |

  @id:usage-collection.gitlab-pipelines.004 @rule:gitlab.jobs.page-cap-warns-of-undercount @inferred @source:carbon_badge/providers.py#gitlab_kwh_last_30d @questionable
  Scenario: Reaching the page limit on GitLab jobs warns that the figure may be an undercount
    Given the project's job list fills every page up to the page limit
    When the operator runs an estimate for the project
    Then the warning "read 20 pages of jobs and stopped at the cap; the figure may be an undercount." is shown
    And the estimate is still produced from the jobs that were read
