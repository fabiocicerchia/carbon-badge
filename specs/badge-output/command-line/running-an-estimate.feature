@domain:badge-output @area:command-line @from:carbon-badge/report
Feature: Running an estimate from the command line
  Operators choose where the CI usage and grid factor come from, including an offline figure when they cannot or need not call a CI API.

  @id:badge-output.command-line.008 @rule:badge-output.command-line.offline-minutes-estimate @verified @source:carbon_badge/report.py#estimate @test:tests/test_carbon_badge.py#test_cli_offline_minutes_never_touch_the_network @test:tests/test_carbon_badge.py#test_offline_estimate_honours_declared_runner_watts
  Scenario Outline: An offline estimate from declared CI minutes uses the blanket runner wattage and carries no qualifier
    Given the operator declares 1000 CI minutes for the last 30 days
    And the operator declares a grid intensity of 480 gCO2e/kWh
    And the operator declares <watts> as the runner wattage for every runner
    When the operator runs the estimate
    Then no CI provider is contacted
    And the badge message is "<message>"

    Examples:
      | watts                     | message       |
      | no figure (default 9.4 W) | 75 gCO2e/mo   |
      | 180                       | 1.4 kgCO2e/mo |

  @id:badge-output.command-line.009 @rule:badge-output.command-line.grid-region-overrides-intensity @inferred @source:carbon_badge/report.py#estimate @questionable
  Scenario: A named grid region replaces any declared grid intensity with the live figure
    Given the operator declares a grid intensity of 480 gCO2e/kWh
    And the operator names the grid region DE
    When the operator runs the estimate
    Then every job is priced at the live 24-hour mean intensity for DE
    And the declared 480 gCO2e/kWh is not used
