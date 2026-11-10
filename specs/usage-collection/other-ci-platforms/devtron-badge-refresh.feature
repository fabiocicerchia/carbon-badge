@domain:usage-collection @area:other-ci-platforms @from:examples/ci-platforms
Feature: Refreshing the carbon badge from a Devtron task
  Lets an operator who runs Devtron refresh a repo's CI carbon badge on a cron trigger or after each release, without installing the CLI on the node.

  Background:
    Given a Devtron node with Docker available
    And the Devtron Shell task runs the carbon-badge refresh script

  @id:usage-collection.other-ci-platforms.001 @rule:ci-platforms.devtron.target-repo-required @inferred @source:examples/ci-platforms/devtron-task.sh#TARGET_REPO
  Scenario: A refresh without a target repository is refused
    Given the task does not set TARGET_REPO
    When the Devtron task runs
    Then the task fails before any estimate is attempted
    And the operator sees "owner/repo (GitHub) or group/project (GitLab)" in the task log
    And no badge file is written

  @id:usage-collection.other-ci-platforms.002 @rule:ci-platforms.devtron.badge-written-and-printed @inferred @source:examples/ci-platforms/devtron-task.sh#docker run
  Scenario Outline: A refresh writes the badge to the output file and prints it in the task log
    Given TARGET_REPO is "<repo>"
    And PROVIDER is <provider>
    And GRID_INTENSITY is <grid>
    And BADGE_OUT is <out>
    When the Devtron task runs
    Then the estimate for "<repo>" is made against <effective_provider> at <effective_grid> gCO2e/kWh
    And the badge JSON is saved to <effective_out> and shown in the task log

    Examples:
      | repo               | provider | grid  | out                      | effective_provider | effective_grid | effective_out            |
      | acme/widgets       | unset    | unset | unset                    | github             | 480            | /tmp/badge.json          |
      | acme-group/widgets | gitlab   | 56    | /srv/badges/widgets.json | gitlab             | 56             | /srv/badges/widgets.json |

  @id:usage-collection.other-ci-platforms.003 @rule:ci-platforms.devtron.provider-token-forwarded @inferred @source:examples/ci-platforms/devtron-task.sh#docker run
  Scenario: The task's GitHub and GitLab tokens reach the estimate
    Given the task environment sets GITHUB_TOKEN or GITLAB_TOKEN
    When the Devtron task runs
    Then the estimate authenticates with the token matching the chosen provider
    And no other variables from the task environment are passed to the estimate

  @id:usage-collection.other-ci-platforms.004 @rule:ci-platforms.devtron.fixed-grid-intensity-always-applied @inferred @source:examples/ci-platforms/devtron-task.sh#GRID_INTENSITY @questionable
  Scenario: A Devtron refresh always prices every job at one fixed grid intensity
    Given GRID_INTENSITY is not set
    And the repo's jobs report their Azure regions
    When the Devtron task runs
    Then every job is priced at 480 gCO2e/kWh
    And per-region grid pricing is not used

  @id:usage-collection.other-ci-platforms.005 @rule:ci-platforms.devtron.failed-refresh-empties-badge @inferred @source:examples/ci-platforms/devtron-task.sh#docker run @questionable
  Scenario: A failed refresh replaces the previous badge with an empty file
    Given a badge from an earlier refresh is saved at the output path
    And the estimate fails, for example because the token is rejected
    When the Devtron task runs
    Then the task fails
    And the output file no longer holds the earlier badge and is empty
