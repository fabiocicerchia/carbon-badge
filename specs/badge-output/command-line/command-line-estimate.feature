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
