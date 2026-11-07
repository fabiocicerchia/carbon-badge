@domain:badge-output @area:command-line @from:carbon-badge/cli
Feature: Running a carbon estimate from the command line
  Lets a repository maintainer or CI operator turn the last 30 days of CI usage into a Shields.io badge value, a reconciliation report or a badge server, with one command.

  Background:
    Given the operator has the carbon-badge command installed

  @id:badge-output.command-line.001 @rule:badge.cli.prints-endpoint-json @verified @source:carbon_badge/cli.py#main @test:tests/test_carbon_badge.py#test_cli_prints_the_badge_for_a_github_repo
  Scenario: A one-off run prints only the badge JSON on standard output
    Given the GitHub repository "acme/widgets" ran one hour of jobs on "ubuntu-latest" in the last 30 days
    When the operator runs carbon-badge for "acme/widgets" with --grid-intensity 480
    Then standard output holds Shields.io endpoint JSON with schemaVersion 1 and label "CI carbon"
    And the badge message is the estimated grams followed by "(estimated)"
    And a one-line summary of the usage and the grams appears on standard error, not standard output
    And the command exits with status 0

  @id:badge-output.command-line.002 @rule:badge.cli.token-from-flag-or-provider-env @verified @source:carbon_badge/cli.py#main @test:tests/test_carbon_badge.py#test_cli_prints_the_badge_for_a_github_repo
  Scenario Outline: The API token comes from --token, otherwise from the environment variable of the chosen provider
    Given the environment variable <env_var> holds "t0ken"
    And no --token flag is given
    When the operator runs carbon-badge for "acme/widgets" with --provider <provider>
    Then the CI API is called with the token "t0ken"

    Examples:
      | provider | env_var      |
      | github   | GITHUB_TOKEN |
      | gitlab   | GITLAB_TOKEN |

  @id:badge-output.command-line.003 @rule:badge.cli.offline-minutes-skip-api @verified @source:carbon_badge/report.py#estimate @test:tests/test_carbon_badge.py#test_cli_offline_minutes_never_touch_the_network
  Scenario: Declaring CI minutes produces a badge without contacting the CI provider
    Given the CI provider cannot be reached
    When the operator runs carbon-badge for "acme/widgets" with --minutes 1000 and --grid-intensity 480
    Then no request is made to the CI provider
    And the badge message is the grams for 1000 minutes at 480 gCO2e/kWh on the default runner power
    And the command exits with status 0

  @id:badge-output.command-line.004 @rule:badge.cli.malformed-estimate-flags-refused @inferred @source:carbon_badge/cli.py#main
  Scenario Outline: A malformed load factor or runner wattage stops the command before any estimate
    When the operator runs carbon-badge for "acme/widgets" with <flag> <value>
    Then the command exits with a usage error (status 2)
    And standard error shows "<message>"
    And no badge JSON is printed

    Examples:
      | flag           | value | message                                                          |
      | --load-factor  | 1.5   | --load-factor must be between 0 and 1                            |
      | --load-factor  | -0.1  | --load-factor must be between 0 and 1                            |
      | --runner-watts | fast  | --runner-watts: 'fast' is not a number, in 'fast'                |
      | --runner-watts | =180  | --runner-watts: expected LABEL=WATTS or a bare number, in '=180' |
      | --runner-watts | 0     | --runner-watts: watts must be positive, got 0.0, in '0'          |

  @id:badge-output.command-line.005 @rule:badge.cli.reconcile-prints-report @inferred @source:carbon_badge/cli.py#main @questionable
  Scenario: Reconciliation prints a readable report instead of a badge
    Given the GitHub repository "acme/widgets" has runs in the last 30 days
    When the operator runs carbon-badge for "acme/widgets" with --reconcile
    Then standard output holds the reconciliation report comparing the API estimate with the recorded measurements
    And no badge JSON is printed
    And the command exits with status 0

  @id:badge-output.command-line.006 @rule:badge.cli.reconcile-refuses-unsupported-combinations @inferred @source:carbon_badge/cli.py#main
  Scenario Outline: Reconciliation is refused for GitLab projects and for declared minutes
    When the operator runs carbon-badge for "acme/widgets" with --reconcile and <extra>
    Then the command exits with a usage error (status 2)
    And standard error shows "<message>"

    Examples:
      | extra             | message                                                                 |
      | --provider gitlab | --reconcile needs the GitHub artifact markers; not available for GitLab |
      | --minutes 100     | --reconcile compares two API paths; --minutes uses neither              |

  @id:badge-output.command-line.007 @rule:badge.cli.serve-instead-of-print @verified @source:carbon_badge/cli.py#main @test:tests/test_docs.py#test_documented_serve_command_over_real_http @questionable
  Scenario: With a port, the command serves the badge over HTTP instead of printing it
    Given the GitHub repository "acme/widgets" ran one hour of jobs on "ubuntu-latest" in the last 30 days
    When the operator runs carbon-badge for "acme/widgets" with --serve 8080 --bind 127.0.0.1 and --grid-intensity 480
    Then http://127.0.0.1:8080/badge.json answers 200 with Content-Type application/json
    And the served badge has label "CI carbon" and the estimated grams as its message
    And without --bind the server listens on 0.0.0.0
