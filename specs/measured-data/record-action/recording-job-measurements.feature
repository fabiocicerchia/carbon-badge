@domain:measured-data @area:record-action @from:record/src
Feature: Recording measured job data from inside a GitHub Actions job
  Lets repository owners add one `uses:` line so each job leaves a carbon artifact holding its real duration, machine size, CPU utilisation and region. The badge then rests on measurements rather than API guesses.

  Background:
    Given the repository's workflow job uses the carbon-badge record action

  @id:measured-data.record-action.001 @rule:measured-data.record.artifact-name-encodes-measurement @verified @source:record/src/post.js#recordJob @test:record/src/action.test.js#main then post names the artifact after the job's measurement @test:record/src/action.test.js#post uploads and finalises the artifact when the backend accepts it
  Scenario Outline: The carbon artifact's name carries the job's measurement
    Given a job with job id "<job>" ran for <seconds> seconds on a <platform> runner with <vcpu> vCPU and <mem> MB of memory in region "westeurope"
    And its average CPU utilisation was <utilisation>
    When the job ends
    Then an artifact named "<name>" is published for the run
    And the name ends with the job id, with characters outside letters, digits, "-" and "_" replaced by "-", followed by 8 random hex characters

    Examples:
      | job         | seconds | platform | vcpu | mem   | utilisation     | name                                                          |
      | build/linux | 87      | ubuntu   | 4    | 16008 | measured as 23% | carbon.v2.87.4.16008.ubuntu.westeurope.23.build-linux-<8 hex> |
      | test        | 53      | arm      | 2    | 7940  | not measurable  | carbon.v1.53.2.7940.arm.westeurope.test-<8 hex>               |

  @id:measured-data.record-action.002 @rule:measured-data.record.hosted-duration-includes-setup @verified @source:record/src/duration.js#jobSeconds @test:record/src/duration.test.js#self-hosted records step time only @test:record/src/duration.test.js#hosted records the VM lifetime, which includes setup @test:record/src/duration.test.js#hosted falls back when uptime is not measuring this job @test:record/src/duration.test.js#hosted falls back when uptime is below the elapsed time
  Scenario Outline: A GitHub-hosted job's duration includes runner setup unless that setup is implausible
    Given the job ran on a <runner> runner
    And its own steps took 53 seconds
    And the machine had been up for <uptime> seconds when the job ended
    When the job ends
    Then the recorded duration is <recorded> seconds

    Examples:
      | runner        | uptime | recorded |
      | GitHub-hosted | 87     | 87       |
      | GitHub-hosted | 653    | 653      |
      | GitHub-hosted | 654    | 53       |
      | GitHub-hosted | 10     | 53       |
      | self-hosted   | 950400 | 53       |

  @id:measured-data.record-action.003 @rule:measured-data.record.cpu-utilisation-only-when-trustworthy @verified @source:record/src/load.js#cpuLoadPercent @test:record/src/load.test.js#a hosted runner reads the counters since boot @test:record/src/load.test.js#elsewhere it reads the interval since the snapshot @test:record/src/load.test.js#no snapshot and no uptime reading is no figure @test:record/src/load.test.js#counters that did not move or went backwards are refused @test:record/src/action.test.js#post uploads and finalises the artifact when the backend accepts it
  Scenario Outline: CPU utilisation is recorded only when the reading can be trusted
    Given <interval>
    And over that interval the CPU counters show <counters>
    When the job ends
    Then the recorded CPU utilisation is <recorded>

    Examples:
      | interval                                                                                                  | counters                                         | recorded                                      |
      | the job ran on a GitHub-hosted runner and its duration is the VM's uptime, so the interval starts at boot | 25 of 100 time units busy                        | 25%                                           |
      | the job's duration is its step time, so the interval starts at the start stamp                            | 100 of 400 time units busy                       | 25%                                           |
      | no CPU snapshot was taken at the start of the job and the duration is step time                           | any reading                                      | unmeasured, and the artifact uses the v1 name |
      | the job's duration is its step time                                                                       | no movement, or more busy time than total time   | unmeasured, and the artifact uses the v1 name |

  @id:measured-data.record-action.004 @rule:measured-data.record.region-declared-or-detected @verified @source:record/src/post.js#recordJob @test:record/src/action.test.js#main then post names the artifact after the job's measurement @questionable
  Scenario Outline: A declared region overrides the detected one, and a region that cannot be found reads as unknown
    Given the region input is <declared>
    And the cloud metadata service <metadata>
    When the job ends
    Then the artifact name records region "<region>"

    Examples:
      | declared      | metadata                        | region         |
      | "West Europe" | reports northcentralus          | westeurope     |
      | not set       | reports northcentralus          | northcentralus |
      | not set       | does not answer within a second | unknown        |
      | "!!!"         | reports northcentralus          | unknown        |

  @id:measured-data.record-action.005 @rule:measured-data.record.never-fails-the-job @verified @source:record/src/post.js#recordJob @test:record/src/action.test.js#main then post names the artifact after the job's measurement
  Scenario Outline: Recording never fails the job
    Given <situation>
    When the job ends
    Then the job's outcome is unchanged
    And the log shows the warning "<warning>"
    And no carbon artifact is published

    Examples:
      | situation                                          | warning                                            |
      | the artifact service refuses the upload            | carbon-badge: could not record this job (<reason>) |
      | the action's start step never stamped a start time | carbon-badge: no start state recorded; skipping.   |

  @id:measured-data.record-action.006 @rule:measured-data.record.retention-days @verified @source:record/src/post.js#recordJob @test:record/src/action.test.js#post uploads and finalises the artifact when the backend accepts it
  Scenario Outline: The carbon artifact expires after the configured retention period
    Given the retention-days input is <input>
    When the job ends
    Then the published artifact expires after <days> days

    Examples:
      | input   | days |
      | not set | 35   |
      | 7       | 7    |

  @id:measured-data.record-action.007 @rule:measured-data.record.cancelled-jobs-recorded @inferred @source:record/action.yml#runs
  Scenario Outline: Failed and cancelled jobs are still recorded
    Given the job <outcome> after the action's start step ran
    When the job ends
    Then a carbon artifact is published for the job

    Examples:
      | outcome       |
      | failed        |
      | was cancelled |
