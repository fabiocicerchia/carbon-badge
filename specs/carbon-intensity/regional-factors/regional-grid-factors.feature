@domain:carbon-intensity @area:regional-factors @from:carbon-badge/power
Feature: Choosing a grid factor for the region a job ran in
  Gives operators a grid intensity per job that honours their explicit figure first and falls back to static Azure region factors and then the world average.

  @id:carbon-intensity.regional-factors.001 @rule:carbon.regional-factors.explicit-then-region-then-world @verified @source:carbon_badge/power.py#grid_factor_for @test:tests/test_carbon_badge.py#test_unknown_region_falls_back_to_the_annual_average @test:tests/test_carbon_badge.py#test_resolver_caches_per_region_and_honours_an_explicit_figure
  Scenario Outline: An explicit grid figure wins, then the region's static factor, then the world average
    Given a recorded job reported running in region <region>
    And no live grid reading is available
    And the operator passed grid intensity <explicit>
    When the operator runs an estimate
    Then the job's energy is priced at <factor> gCO2e/kWh

    Examples:
      | region        | explicit | factor |
      | swedencentral | none     | 35     |
      | polandcentral | none     | 589    |
      | moonbase1     | none     | 480    |
      | unreported    | none     | 480    |
      | swedencentral | 333      | 333    |
