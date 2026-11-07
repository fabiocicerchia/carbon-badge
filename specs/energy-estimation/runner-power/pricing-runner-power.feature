@domain:energy-estimation @area:runner-power @from:carbon-badge/power
Feature: Pricing a CI runner's power draw
  Gives repository maintainers a defensible wattage for every job's runner, from GitHub-hosted defaults or their own declared figures, so the badge's gCO2e figure rests on stated, overridable assumptions.

  Background:
    Given no --load-factor is given

  @id:energy-estimation.runner-power.004 @rule:energy.runner-power.hosted-label-defaults @verified @source:carbon_badge/power.py#runner_power_w @test:tests/test_carbon_badge.py#test_runner_power_by_type @test:tests/test_carbon_badge.py#test_arm_and_gpu_are_priced_before_their_os
  Scenario Outline: A GitHub-hosted runner is priced from its label at the built-in wattage
    Given a job ran on a runner labelled <label>
    And no --runner-watts is declared
    When the operator runs an estimate
    Then the job is priced at <watts> W

    Examples:
      | label                     | watts                            |
      | ubuntu-latest             | 9.4                              |
      | windows-latest            | 9.4                              |
      | macos-latest              | 17.9                             |
      | ubuntu-22.04-arm          | 5.6                              |
      | ubuntu-latest-4-cores-gpu | 89.9                             |
      | some-custom-label         | not priced by the built-in table |

  @id:energy-estimation.runner-power.005 @rule:energy.runner-power.core-count-scales-affinely @verified @source:carbon_badge/power.py#_table_watts @test:tests/test_carbon_badge.py#test_core_count_parsed_from_every_common_label_shape @test:tests/test_carbon_badge.py#test_gpu_is_not_core_scaled_but_arm_is
  Scenario Outline: A core count in the runner label raises the wattage by a per-core share, not proportionally
    Given a job ran on a runner labelled <label>
    And no --runner-watts is declared
    When the operator runs an estimate
    Then the job is priced at <watts> W

    Examples:
      | label                      | watts |
      | ubuntu-latest-16-cores     | 34.0  |
      | ubuntu-latest-16-core      | 34.0  |
      | ubuntu-24.04-8vcpu         | 17.6  |
      | ubuntu-24.04-arm-8-cores   | 10.0  |
      | ubuntu-latest-16-cores-gpu | 89.9  |

  @id:energy-estimation.runner-power.006 @rule:energy.runner-power.declared-watts-take-precedence @verified @source:carbon_badge/power.py#_declared_watts @test:tests/test_carbon_badge.py#test_bare_number_prices_every_runner @test:tests/test_carbon_badge.py#test_specific_label_still_beats_the_blanket_figure @test:tests/test_carbon_badge.py#test_runner_watts_overrides_price_custom_and_self_hosted_labels @test:tests/test_carbon_badge.py#test_arm_and_gpu_defaults_are_overridable
  Scenario Outline: A declared runner wattage beats the built-in figure, most specific declaration first
    Given a job ran on a runner labelled <label>
    And the operator declared --runner-watts <declared>
    When the operator runs an estimate
    Then the job is priced at <watts> W

    Examples:
      | label                     | declared        | watts |
      | my-builder                | my-builder=180  | 180   |
      | ubuntu-latest             | ubuntu-latest=9 | 9     |
      | ubuntu-latest-4-cores-gpu | 180 and gpu=320 | 320   |
      | ubuntu-latest             | 180 and gpu=320 | 180   |
      | self-hosted, linux        | 180             | 180   |
      | ubuntu-latest-16-cores    | 180             | 180   |
      | ubuntu-22.04-arm          | arm=6           | 6     |

  @id:energy-estimation.runner-power.007 @rule:energy.runner-power.malformed-declaration-refused @verified @source:carbon_badge/power.py#parse_runner_watts @test:tests/test_carbon_badge.py#test_parse_runner_watts_rejects_malformed_input
  Scenario Outline: A malformed runner wattage declaration is refused and names the offending entry
    Given the operator declared --runner-watts <entry>
    When the operator runs an estimate
    Then the command stops with a usage error before any estimate is made
    And the error names <entry> and says <problem>

    Examples:
      | entry     | problem                               |
      | no-equals | 'no-equals' is not a number           |
      | =100      | expected LABEL=WATTS or a bare number |
      | label=    | '' is not a number                    |
      | label=abc | 'abc' is not a number                 |
      | label=0   | watts must be positive                |
      | label=-5  | watts must be positive                |

  @id:energy-estimation.runner-power.008 @rule:energy.runner-power.self-reported-specs-match-label-pricing @verified @source:carbon_badge/power.py#watts_from_specs @test:tests/test_carbon_badge.py#test_watts_from_specs_is_platform_aware @test:tests/test_carbon_badge.py#test_watts_from_specs_reproduces_the_known_runner
  Scenario Outline: A job that reported its own hardware is priced the same as that machine recognised by its label
    Given a recorded job reported <vcpu> vCPU and <mem> GiB on <platform>
    When the operator runs an estimate
    Then the job is priced at <watts> W

    Examples:
      | vcpu | mem | platform | watts |
      | 4    | 16  | ubuntu   | 9.4   |
      | 4    | 16  | arm      | 5.6   |
      | 8    | 32  | arm      | 10.0  |
      | 3    | 7   | macos    | 17.9  |

  @id:energy-estimation.runner-power.009 @rule:energy.runner-power.estimated-classes-disclosed @verified @source:carbon_badge/power.py#_warn_estimated_classes @test:tests/test_carbon_badge.py#test_the_warning_names_the_class_the_figure_and_the_replacement @test:tests/test_carbon_badge.py#test_a_declared_figure_is_not_reported_as_an_estimate @test:tests/test_carbon_badge.py#test_pricing_a_job_on_an_estimated_class_says_so_and_names_the_flag @test:tests/test_carbon_badge.py#test_the_self_reported_route_admits_to_the_same_estimate
  Scenario Outline: An estimate that relies on an unmeasured runner class says so and names the flag that replaces it
    Given jobs were priced on the built-in <class> figure
    When the operator runs an estimate
    Then stderr says the jobs were priced on the '<class>' class at <watts> W, which is an estimate
    And stderr says to pass "--runner-watts <class>=" followed by the operator's own wattage, and points to docs/assumptions.md
    And no such line appears for that class when the operator declared --runner-watts <class>=...

    Examples:
      | class | watts |
      | arm   | 5.6   |
      | gpu   | 89.9  |

  @id:energy-estimation.runner-power.010 @rule:energy.runner-power.estimated-class-job-count @inferred @source:carbon_badge/power.py#_table_watts @questionable
  Scenario: The unmeasured-class warning counts a core-scaled job twice
    Given one job ran on a runner labelled ubuntu-24.04-arm-8-cores
    And no --runner-watts is declared
    When the operator runs an estimate
    Then stderr reports 2 job(s) priced on the 'arm' class
