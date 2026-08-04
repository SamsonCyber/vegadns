Feature: vegadns core enumeration behaviors
  All steps drive the shipped vegadns binary / library path (no reimplemented DNS rules).

  Scenario: Expand wordlist labels into FQDNs
    Given a temporary wordlist with lines
      | line   |
      | www    |
      | # skip |
      | mail   |
      |        |
    And the base domain is "example.com"
    When I run vegadns expand
    Then the expand output should contain exactly
      | fqdn              |
      | www.example.com   |
      | mail.example.com  |

  Scenario: Mock enum finds live hosts and reports NXDOMAIN as absent
    Given the fixed mock zone fixture
    And a wordlist with labels "www", "nope", "mail"
    When I run vegadns enum against the mock zone with known-true
    Then the primary names should include "www.bench.test"
    And the primary names should include "mail.bench.test"
    And the primary names should not include "nope.bench.test"
    And recall should be 1.0 for the subset true names present in the wordlist path

  Scenario: Wildcard catch-all false positives are rejected
    Given the fixed mock zone fixture
    And a wordlist with labels "www", "foo.wild", "bar.wild", "api"
    When I run vegadns enum against the mock zone with known-true
    Then the primary names should include "www.bench.test"
    And the primary names should include "api.bench.test"
    And the primary names should not include any name ending with ".wild.bench.test"
    And precision should be 1.0 against fixtures known_true

  Scenario: Full fixture recall and precision on bench wordlist
    Given the fixed mock zone fixture
    And the bench wordlist fixture
    When I run vegadns enum against the mock zone with known-true
    Then recall should be 1.0 against fixtures known_true
    And precision should be 1.0 against fixtures known_true
    And found count should equal known_true count
