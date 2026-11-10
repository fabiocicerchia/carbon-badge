@domain:measured-data @area:artifact-reading @from:carbon-badge/base
Feature: Recognising carbon markers by artifact name
  Serves repo owners who instrument jobs with the recording action: the artifact name is the wire format between the recorder and the badge, so which names count as measurements decides what the badge reports as measured.

  Background:
    Given the recording action names each job's artifact carbon.v1.<seconds>.<vcpu>.<memMB>.<platform>.<region>.<slug> or carbon.v2.<seconds>.<vcpu>.<memMB>.<platform>.<region>.<cpu%>.<slug>

  @id:measured-data.artifact-reading.009 @rule:measured.markers.v1-and-v2-recognised @verified @source:carbon_badge/base.py#_match_marker @test:tests/test_carbon_badge.py#test_parse_carbon_artifact @test:tests/test_carbon_badge.py#test_v2_markers_carry_a_measured_load @test:tests/test_carbon_badge.py#test_the_slug_is_read_off_the_marker_name
  Scenario Outline: A v1 or v2 carbon marker is read as a measured job, and only v2 carries a CPU load
    Given a run left an artifact named "<name>"
    When the operator runs carbon-badge
    Then the job is counted as measured at <seconds> s on <vcpu> vCPU and <mem> MB on platform "<platform>" in region "<region>"
    And its measured CPU load is <load>
    And its job slug is "<slug>"

    Examples:
      | name                                                  | seconds | vcpu | mem   | platform | region | load                             | slug           |
      | carbon.v1.142.4.16384.ubuntu.eastus.build-a1b2c3d4    | 142     | 4    | 16384 | ubuntu   | eastus | not recorded (full load assumed) | build-a1b2c3d4 |
      | carbon.v2.142.4.16384.ubuntu.eastus.37.build-a1b2c3d4 | 142     | 4    | 16384 | ubuntu   | eastus | 37%                              | build-a1b2c3d4 |
      | carbon.v2.142.4.16384.ubuntu.eastus.100.build         | 142     | 4    | 16384 | ubuntu   | eastus | 100%                             | build          |

  @id:measured-data.artifact-reading.010 @rule:measured.markers.foreign-names-ignored @verified @source:carbon_badge/base.py#_match_marker @test:tests/test_carbon_badge.py#test_parse_carbon_artifact @test:tests/test_carbon_badge.py#test_the_slug_is_read_off_the_marker_name
  Scenario Outline: Artifacts that are not complete carbon markers are not counted as measurements
    Given a run left an artifact named "<name>"
    When the operator runs carbon-badge
    Then that artifact is not counted as a measured job
    And the run's energy is estimated from the GitHub Actions API instead

    Examples:
      | name                                        | why                        |
      | gitleaks-results.sarif                      | an ordinary build artifact |
      | github-pages                                | an ordinary build artifact |
      | carbon.v1.142.4.16384.build                 | no platform or region      |
      | carbon.v1.142.4.16384.ubuntu.build          | no region                  |
      | carbon.v2.142.2.7168.build                  | malformed v2               |
      | carbon.v3.142.2.7168.ubuntu.eastus.37.build | a future format version    |
      |                                             | no name                    |

  @id:measured-data.artifact-reading.011 @rule:measured.markers.impossible-cpu-load-rejected @verified @source:carbon_badge/base.py#_match_marker @test:tests/test_carbon_badge.py#test_v2_markers_carry_a_measured_load
  Scenario: A v2 marker claiming more than 100% CPU is not a measurement at all
    Given a run left an artifact named "carbon.v2.142.4.16384.ubuntu.eastus.250.build"
    When the operator runs carbon-badge
    Then that artifact is not counted as a measured job
    And it is not priced at full load
    And it carries no job slug for per-job reconciliation
