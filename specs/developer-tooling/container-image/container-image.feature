@domain:developer-tooling @area:container-image @from:carbon-badge/dockerfile
Feature: Running carbon-badge from the container image
  Lets an operator produce a repository's carbon badge with a single docker run, without installing Python or the package locally.

  Background:
    Given the carbon-badge container image has been built

  @id:developer-tooling.container-image.001 @rule:distribution.container-image.entrypoint-is-cli @inferred @source:Dockerfile#ENTRYPOINT
  Scenario: Arguments given to the container are passed to the carbon-badge command
    Given a GitHub token is available in the operator's environment as GITHUB_TOKEN
    When the operator runs the image with the argument "acme/widgets" and the GITHUB_TOKEN variable passed through
    Then the carbon-badge command runs the estimate for "acme/widgets"
    And the badge output is written to standard output, ready to redirect to badge.json

  @id:developer-tooling.container-image.002 @rule:distribution.container-image.runs-as-non-root @inferred @source:Dockerfile#USER
  Scenario: The container runs carbon-badge as an unprivileged user
    When the operator starts a container from the image
    Then the carbon-badge process runs as the user "app" with uid 10001, not as root
