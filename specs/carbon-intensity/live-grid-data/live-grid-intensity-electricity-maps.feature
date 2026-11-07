@domain:carbon-intensity @area:live-grid-data @from:carbon-badge/providers
Feature: Pricing the grid from Electricity Maps
  Gives operators who name a grid region a carbon factor that reflects the past day, not whatever moment the scheduled refresh happens to run.

  Background:
    Given the operator names the grid region SE

  @id:carbon-intensity.live-grid-data.009 @rule:grid.live.uses-24h-mean @verified @source:carbon_badge/providers.py#live_grid_intensity @test:tests/test_carbon_badge.py#test_grid_factor_averages_a_day_not_an_instant @test:tests/test_carbon_badge.py#test_grid_factor_ignores_gaps_in_the_history
  Scenario Outline: The grid factor is the mean of the past 24 hours, skipping hours with no data
    Given Electricity Maps reports hourly readings of <readings> gCO2e/kWh for SE
    When the operator runs an estimate
    Then the grid factor is <factor> gCO2e/kWh
    And the log reads "grid factor for SE = <factor> gCO2e/kWh (24h mean)"

    Examples:
      | readings            | factor |
      | 100, 200, 300, 400  | 250    |
      | 100, (no data), 300 | 200    |

  @id:carbon-intensity.live-grid-data.010 @rule:grid.live.falls-back-to-instant-reading @verified @source:carbon_badge/providers.py#live_grid_intensity @test:tests/test_carbon_badge.py#test_grid_factor_falls_back_to_the_instant_reading @test:tests/test_carbon_badge.py#test_live_grid_intensity_injected_client @questionable
  Scenario: Without usable history, the current reading is used and the operator is warned
    Given Electricity Maps returns no usable history for SE
    And its current reading for SE is 412 gCO2e/kWh
    When the operator runs an estimate
    Then the grid factor is 412 gCO2e/kWh
    And a warning says the 24h grid history is unavailable and the instantaneous reading prices a month at one moment
