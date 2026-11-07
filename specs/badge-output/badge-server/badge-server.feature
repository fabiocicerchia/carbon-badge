@domain:badge-output @area:badge-server @from:carbon-badge/server
Feature: Serving the badge over HTTP
  Lets an operator run carbon-badge as a long-lived endpoint that Shields.io can poll, so a README badge stays current without a CI job republishing JSON.

  Background:
    Given the operator has started carbon-badge with "--serve 8080" for the repository "acme/widgets"

  @id:badge-output.badge-server.001 @rule:badge.server.serves-endpoint-json @verified @source:carbon_badge/server.py#BadgeHandler @test:tests/test_docs.py#test_documented_serve_command_over_real_http @test:tests/test_carbon_badge.py#test_serve_badge_endpoint_caches_and_404s
  Scenario Outline: The badge endpoint answers with Shields.io endpoint JSON any site may fetch
    Given the current estimate for "acme/widgets" is a badge labelled "CI carbon"
    When Shields.io requests <path>
    Then the response status is 200
    And the Content-Type is "application/json"
    And the Access-Control-Allow-Origin header is "*"
    And the body is the badge JSON with label "CI carbon" and the estimate as its message

    Examples:
      | path        |
      | /badge.json |
      | /           |

  @id:badge-output.badge-server.002 @rule:badge.server.unknown-path-not-found @verified @source:carbon_badge/server.py#BadgeHandler @test:tests/test_carbon_badge.py#test_serve_badge_endpoint_caches_and_404s
  Scenario: Any other path is answered with Not Found
    When a client requests "/nope"
    Then the response status is 404
    And the response carries no badge

  @id:badge-output.badge-server.003 @rule:badge.server.estimate-refresh-interval @verified @source:carbon_badge/server.py#serve @test:tests/test_carbon_badge.py#test_serve_badge_endpoint_caches_and_404s @test:tests/test_docs.py#test_documented_serve_command_over_real_http
  Scenario: The badge is re-estimated at most once every five minutes
    Given the served badge was last estimated less than 5 minutes ago
    When Shields.io requests "/badge.json" again
    Then the body is identical to the previous badge
    And no new request is made to the CI provider
    And once more than 5 minutes have passed since that estimate, the next request returns a freshly computed badge

  @id:badge-output.badge-server.004 @rule:badge.server.listen-interface @inferred @source:carbon_badge/server.py#serve
  Scenario Outline: The badge server listens on every interface unless the operator narrows it
    Given the operator started the server with <bind option>
    When a client on <client location> requests "/badge.json" on port 8080
    Then the client <outcome>

    Examples:
      | bind option      | client location             | outcome            |
      | no --bind        | another host on the network | receives the badge |
      | --bind 127.0.0.1 | the same machine            | receives the badge |
      | --bind 127.0.0.1 | another host on the network | cannot connect     |

  @id:badge-output.badge-server.005 @rule:badge.server.failed-estimate-no-badge @inferred @source:carbon_badge/server.py#BadgeHandler @questionable
  Scenario: When the estimate fails, the request gets no answer and the next request tries again
    Given the CI provider API is unreachable
    When Shields.io requests "/badge.json"
    Then the connection closes without any HTTP response
    And the server keeps running
    And the next request attempts a fresh estimate
