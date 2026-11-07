@domain:carbon-intensity @area:live-grid-data @from:carbon-badge/grid
Feature: Pricing jobs at their region's live grid intensity
  Serves repo owners who want the badge to reflect what the grid their CI ran on was actually emitting, not a yearly average, without ever failing a refresh when a grid provider misbehaves.

  Background:
    Given each job is priced at the grid of the Azure region it ran on
    And the operator has not declared an explicit grid intensity

  @id:carbon-intensity.live-grid-data.001 @rule:carbon-intensity.live-grid.provider-chosen-by-region @verified @source:carbon_badge/grid.py#live_region_factor @test:tests/test_carbon_badge.py#test_resolver_caches_per_region_and_honours_an_explicit_figure @test:tests/test_carbon_badge.py#test_ci_api_snapshot_is_fetched_once_for_many_regions
  Scenario Outline: Each region's live intensity comes from the provider that covers it
    Given a job ran in Azure region <region>
    And <provider> currently reports <reading> gCO2e/kWh for that grid
    When the operator refreshes the badge
    Then that job is priced at <reading> gCO2e/kWh
    And the log reports the region as live at <reading>, rounded to whole grams, alongside its annual average

    Examples:
      | region                                                   | provider                                 | reading |
      | uksouth                                                  | the GB Carbon Intensity API (NESO)       | 133     |
      | germanywestcentral                                       | Energy-Charts (country de)               | 411.5   |
      | japaneast                                                | the ci-api world snapshot (JP)           | 567     |
      | southcentralus                                           | the ci-api world snapshot (US/ERCO)      | 425     |
      | eastus with an EIA API key and no trusted ci-api reading | EIA fuel mix for balancing authority PJM | 415.5   |

  @id:carbon-intensity.live-grid-data.002 @rule:carbon-intensity.live-grid.gb-forecast-until-settled @verified @source:carbon_badge/grid.py#_uk_factor @test:tests/test_carbon_badge.py#test_uk_falls_back_to_forecast_within_the_current_half_hour
  Scenario Outline: Great Britain uses the forecast until the current half-hour is settled
    Given a job ran in Azure region uksouth
    And the GB grid reports a forecast of <forecast> and an actual of <actual> gCO2e/kWh for the current half-hour
    When the operator refreshes the badge
    Then that job is priced at <priced> gCO2e/kWh

    Examples:
      | forecast | actual            | priced |
      | 127      | not yet published | 127    |
      | 127      | 133               | 133    |

  @id:carbon-intensity.live-grid-data.003 @rule:carbon-intensity.live-grid.country-consumption-zone-lifecycle @verified @source:carbon_badge/grid.py#_ci_api_factor @test:tests/test_carbon_badge.py#test_ci_api_reports_consumption_lifecycle_for_a_country @test:tests/test_carbon_badge.py#test_ci_api_zone_falls_back_to_lifecycle
  Scenario Outline: A country is priced on its consumption-based lifecycle figure, a sub-national zone on its lifecycle figure
    Given a job ran in Azure region <region>
    And the ci-api snapshot is under an hour old and reports a measured reading for <grid> with direct <direct>, lifecycle <lifecycle> and consumption lifecycle <consumption>
    When the operator refreshes the badge
    Then that job is priced at <priced> gCO2e/kWh

    Examples:
      | region         | grid    | direct | lifecycle | consumption   | priced |
      | japaneast      | JP      | 477    | 522       | 567           | 567    |
      | southcentralus | US/ERCO | 380    | 425       | not published | 425    |

  @id:carbon-intensity.live-grid-data.004 @rule:carbon-intensity.live-grid.annual-average-never-reported-as-live @verified @source:carbon_badge/grid.py#_measured_reading @test:tests/test_carbon_badge.py#test_ci_api_refuses_an_annual_average
  Scenario: A provider's annual-average figure is never passed off as a live reading
    Given a job ran in Azure region uaenorth
    And the ci-api snapshot reports AE only as an annual average of 513 gCO2e/kWh
    When the operator refreshes the badge
    Then that job is priced at the badge's own annual average for uaenorth
    And the log does not report uaenorth as live

  @id:carbon-intensity.live-grid-data.005 @rule:carbon-intensity.live-grid.stale-snapshot-refused @verified @source:carbon_badge/grid.py#_measured_reading @test:tests/test_carbon_badge.py#test_ci_api_refuses_a_missed_pipeline_run
  Scenario Outline: A grid snapshot more than 65 minutes old is not used
    Given a job ran in Azure region japaneast
    And the ci-api snapshot was generated <age> minutes ago with JP at 567 gCO2e/kWh
    When the operator refreshes the badge
    Then that job is priced at <priced>

    Examples:
      | age | priced                                       |
      | 60  | 567 gCO2e/kWh                                |
      | 70  | the badge's own annual average for japaneast |

  @id:carbon-intensity.live-grid-data.006 @rule:carbon-intensity.live-grid.us-fuel-mix-newest-hour @verified @source:carbon_badge/grid.py#_eia_factor @test:tests/test_carbon_badge.py#test_eia_weights_the_fuel_mix_of_the_newest_hour_only @test:tests/test_carbon_badge.py#test_eia_ignores_negative_generation
  Scenario Outline: A US grid priced from EIA uses only the newest hour's generation mix
    Given a job ran in Azure region eastus and the operator supplied an EIA API key
    And no trusted ci-api reading exists for that grid
    And EIA reports for PJM <newest_hour_mix> in the newest hour and 10000 MWh of coal in the hour before
    When the operator refreshes the badge
    Then that job is priced at <priced> gCO2e/kWh

    Examples:
      | newest_hour_mix                       | priced |
      | 100 MWh coal and 100 MWh wind         | 415.5  |
      | 100 MWh natural gas and -50 MWh other | 490    |

  @id:carbon-intensity.live-grid-data.007 @rule:carbon-intensity.live-grid.no-live-reading-uses-annual-average @verified @source:carbon_badge/grid.py#live_region_factor @test:tests/test_carbon_badge.py#test_us_regions_need_a_key_and_degrade_without_one @test:tests/test_carbon_badge.py#test_unknown_region_falls_back_to_the_annual_average @questionable
  Scenario Outline: A region with no usable live reading is priced at its annual average
    Given a job ran in Azure region <region>
    And <situation>
    When the operator refreshes the badge
    Then that job is priced at the badge's own annual average for <region>
    And <other_providers>

    Examples:
      | region             | situation                                                    | other_providers                                  |
      | northcentralus     | no EIA API key is supplied and ci-api has no trusted reading | EIA is not contacted                             |
      | moonbase1          | no live provider covers the region                           | no grid provider is contacted                    |
      | germanywestcentral | Energy-Charts returns no non-empty slot                      | ci-api is not consulted even though it covers DE |

  @id:carbon-intensity.live-grid-data.008 @rule:carbon-intensity.live-grid.provider-failure-never-fails-refresh @verified @source:carbon_badge/grid.py#live_region_factor @test:tests/test_carbon_badge.py#test_a_failing_provider_never_breaks_the_refresh @test:tests/test_carbon_badge.py#test_ci_api_failure_leaves_the_annual_average_standing
  Scenario Outline: A failing grid provider is reported and never fails the badge refresh
    Given a job ran in Azure region <region>
    And <provider> is unreachable or returns an error
    When the operator refreshes the badge
    Then the refresh still completes
    And that job is priced at the badge's own annual average for <region>
    And a warning reads "<warning>"

    Examples:
      | region    | provider                    | warning                                                                             |
      | uksouth   | the GB Carbon Intensity API | live grid lookup failed for uksouth (...); using the annual average for that region |
      | japaneast | the ci-api world snapshot   | ci-api snapshot failed (...)                                                        |
