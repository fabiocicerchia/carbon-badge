@domain:badge-output @area:badge-content @from:carbon-badge/report
Feature: Stating the CI footprint and its confidence on the badge
  Repo maintainers and their readers get one Shields.io badge that shows the monthly CI footprint and says how far that figure can be trusted.

  Background:
    Given the operator publishes the carbon-badge output as a Shields.io endpoint badge

  @id:badge-output.badge-content.001 @rule:badge-output.badge-content.shields-endpoint-contract @verified @source:carbon_badge/report.py#endpoint_json @test:tests/test_carbon_badge.py#test_endpoint_schema @test:tests/test_carbon_badge.py#test_badge_passes_no_verdict @test:tests/test_carbon_badge.py#test_cli_prints_the_badge_for_a_github_repo
  Scenario Outline: The badge is a Shields.io endpoint labelled CI carbon in one neutral colour
    Given a repo whose CI emitted <grams> gCO2e over the last 30 days
    When the operator runs the estimate
    Then the output has schemaVersion 1
    And the output label is "CI carbon"
    And the output color is "informational"

    Examples:
      | grams    |
      | 1        |
      | 50       |
      | 1500     |
      | 10000000 |

  @id:badge-output.badge-content.002 @rule:badge-output.badge-content.grams-per-month-units @verified @source:carbon_badge/report.py#format_grams @test:tests/test_carbon_badge.py#test_format_switches_units
  Scenario Outline: The footprint is shown in grams per month, switching to kilograms from 1000 g
    Given a repo whose CI emitted <grams> gCO2e over the last 30 days
    When the operator runs the estimate
    Then the badge message begins with "<shown>"

    Examples:
      | grams | shown         |
      | 900   | 900 gCO2e/mo  |
      | 999.4 | 999 gCO2e/mo  |
      | 1000  | 1.0 kgCO2e/mo |
      | 2300  | 2.3 kgCO2e/mo |

  @id:badge-output.badge-content.003 @rule:badge-output.badge-content.confidence-qualifier @verified @source:carbon_badge/report.py#confidence @test:tests/test_carbon_badge.py#test_badge_states_its_own_confidence @test:tests/test_carbon_badge.py#test_confidence_levels @test:tests/test_carbon_badge.py#test_confidence_is_driven_by_energy_not_job_count
  Scenario Outline: The badge says whether its figure was measured, partly measured, estimated or rough
    Given a repo whose CI emitted 120 gCO2e over the last 30 days
    And it ran <total> CI jobs in that window, <self> of which reported their own measurements
    And <measured> of the CI energy came from self-reported jobs
    And <guessed> of the CI energy was priced on runners nobody declared
    When the operator runs the estimate
    Then the badge message is "<message>"

    Examples:
      | total | self | measured | guessed | message                        |
      | 24    | 24   | 100%     | 0%      | 120 gCO2e/mo                   |
      | 24    | 18   | 50%      | 0%      | 120 gCO2e/mo (~18/24 measured) |
      | 24    | 0    | 0%       | 0%      | 120 gCO2e/mo (estimated)       |
      | 24    | 0    | 0%       | 90%     | 120 gCO2e/mo (rough)           |
      | 10    | 9    | 70%      | 30%     | 120 gCO2e/mo (rough)           |
      | 10    | 9    | 99%      | 1%      | 120 gCO2e/mo                   |

  @id:badge-output.badge-content.004 @rule:badge-output.badge-content.grams-cover-all-ci @verified @source:carbon_badge/report.py#endpoint_json @test:tests/test_carbon_badge.py#test_badge_grams_always_cover_all_ci
  Scenario Outline: The footprint covers all CI however much of it was measured
    Given a repo whose CI emitted 1500 gCO2e over the last 30 days
    And <self> of its 50 jobs reported their own measurements
    When the operator runs the estimate
    Then the badge message begins with "1.5 kgCO2e/mo"

    Examples:
      | self |
      | 5    |
      | 50   |

  @id:badge-output.badge-content.005 @rule:badge-output.badge-content.no-ci-unqualified @inferred @source:carbon_badge/report.py#confidence @questionable
  Scenario: A repo with no CI energy in the window gets an unqualified zero
    Given a repo with no CI energy recorded in the last 30 days
    When the operator runs the estimate
    Then the badge message is "0 gCO2e/mo"
    And the badge message carries no confidence qualifier
