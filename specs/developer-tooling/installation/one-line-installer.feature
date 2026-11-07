@domain:developer-tooling @area:installation @from:carbon-badge/install
Feature: Installing carbon-badge with the one-line installer
  Lets an operator get the carbon-badge command onto a machine with a single piped shell command, without cloning the repository.

  Background:
    Given the operator runs the one-line installer from the carbon-badge README

  @id:developer-tooling.installation.001 @rule:install.installer.prefers-pipx-falls-back-to-pip-user @inferred @source:install.sh#install.sh
  Scenario Outline: The installer uses pipx when present and otherwise a per-user pip install
    Given pipx <pipx_state> on the operator's PATH
    When the operator runs the installer
    Then carbon-badge is installed with <install_method>
    And the operator sees "carbon-badge installed. Run: carbon-badge --help"

    Examples:
      | pipx_state       | install_method                                                  |
      | is available     | pipx, into its own isolated environment                         |
      | is not available | pip into the operator's user site-packages (pip install --user) |

  @id:developer-tooling.installation.002 @rule:install.installer.failure-aborts-without-success-message @inferred @source:install.sh#install.sh
  Scenario: A failed install stops the installer and does not claim success
    Given the chosen installer tool cannot install carbon-badge, for example because pip refuses an externally managed Python or the network is unreachable
    When the operator runs the installer
    Then the installer exits with a non-zero status
    And the operator does not see "carbon-badge installed. Run: carbon-badge --help"

  @id:developer-tooling.installation.003 @rule:install.installer.installs-unpinned-default-branch @inferred @source:install.sh#install.sh @questionable
  Scenario: The installer installs whatever is currently on the default branch, not a released version
    Given the carbon-badge repository's default branch contains unreleased changes
    When the operator runs the installer
    Then the installed carbon-badge is built from the current default-branch source
    And the operator is offered no way to choose a released version
