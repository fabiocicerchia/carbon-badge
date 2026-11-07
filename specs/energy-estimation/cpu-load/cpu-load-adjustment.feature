@domain:energy-estimation @area:cpu-load @from:carbon-badge/power
Feature: Adjusting power draw for CPU load
  Lets maintainers price mostly-waiting jobs below full load without pretending an idle machine draws nothing.

  Background:
    Given a job ran on ubuntu-latest, rated 9.4 W at full load

  @id:energy-estimation.cpu-load.001 @rule:energy.cpu-load.only-variable-draw-scales @inferred @source:carbon_badge/power.py#apply_load_factor
  Scenario Outline: A load factor scales only the variable part of the draw, keeping the idle floor
    Given the operator passed --load-factor <factor>
    When the operator runs an estimate
    Then the job is priced at <watts> W

    Examples:
      | factor | watts |
      | 0      | 2.02  |
      | 0.25   | 3.87  |
      | 0.5    | 5.71  |
      | 1      | 9.4   |

  @id:energy-estimation.cpu-load.002 @rule:energy.cpu-load.declared-load-beats-measured @inferred @source:carbon_badge/power.py#apply_load_factor
  Scenario Outline: A declared load factor overrides a job's measured utilisation, and no measurement means full load
    Given a job measured its own CPU utilisation as <measured>
    And the operator passed --load-factor <declared>
    When the operator runs an estimate
    Then the job is priced at load <used>

    Examples:
      | measured                   | declared  | used      |
      | none (priced from the API) | not given | full load |
      | 0.25                       | not given | 0.25      |
      | 0.25                       | 1         | full load |
      | 0.25                       | 0.5       | 0.5       |
