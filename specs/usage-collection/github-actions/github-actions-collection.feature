@domain:usage-collection @area:github-actions @from:carbon-badge/ci
Feature: Collecting GitHub Actions runs and jobs
  Gives a repository owner a footprint built from every workflow run and job in the window, and warns them when GitHub did not return all of them.

  Background:
    Given the repository "acme/widgets" runs its CI on GitHub Actions

  @id:usage-collection.github-actions.001 @rule:usage-collection.github-actions.truncation-warned @verified @source:carbon_badge/ci.py#_warn_if_truncated @test:tests/test_carbon_badge.py#test_pagination_cap_is_reported_not_silent @test:tests/test_carbon_badge.py#test_no_warning_when_everything_was_read @questionable
  Scenario Outline: An incomplete run listing is reported as an undercount, with the right remedy
    Given GitHub reports <total> workflow runs in the window
    And carbon-badge was able to read <fetched> of them, <reason>
    When the operator estimates the footprint of "acme/widgets"
    Then <warning>

    Examples:
      | total | fetched | reason                                       | warning                                                                                                                                                                      |
      | 9999  | 2000    | stopping at its own 20-page limit            | a warning says only 2000 of 9999 runs were read, that the local 20-page cap was hit, and that the figure is an undercount                                                    |
      | 1500  | 1000    | because GitHub stops listing at 1000 results | a warning says only 1000 of 1500 runs were read, that GitHub caps the listing at 1000 results so only a narrower window shows the rest, and that the figure is an undercount |
      | 1     | 1       | with nothing left out                        | no undercount warning is shown                                                                                                                                               |

  @id:usage-collection.github-actions.002 @rule:usage-collection.github-actions.every-job-of-a-run-counted @verified @source:carbon_badge/ci.py#run_jobs @test:tests/test_carbon_badge.py#test_run_jobs_pages_past_thirty @test:tests/test_carbon_badge.py#test_github_client_over_real_http
  Scenario: Every job of a wide matrix run is counted
    Given one workflow run in the window has 105 jobs
    When the operator estimates the footprint of "acme/widgets"
    Then all 105 jobs of that run are priced in the footprint

  @id:usage-collection.github-actions.003 @rule:usage-collection.github-actions.token-authenticates-requests @verified @source:carbon_badge/ci.py#_get_pages @test:tests/test_carbon_badge.py#test_github_client_over_real_http
  Scenario Outline: A supplied GitHub token authenticates every request; without one, requests go out anonymously
    Given the operator <token_state>
    When the operator estimates the footprint of "acme/widgets"
    Then every request to the GitHub API <auth>

    Examples:
      | token_state                | auth                                             |
      | supplies the token "t0ken" | carries the header "Authorization: Bearer t0ken" |
      | supplies no token          | carries no Authorization header                  |
