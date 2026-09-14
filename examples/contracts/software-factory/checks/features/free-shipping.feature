Feature: Delivery at checkout
  Both pricing and checkout honor the same delivery charge in integer cents.

  Scenario: Free delivery at 5000 cents
    Given a subtotal of 5000
    When I request pricing and checkout
    Then delivery is 0 cents and the total is 5000 cents

  Scenario Outline: <case>
    Given a subtotal of <subtotal>
    When I request pricing and checkout
    Then delivery is <delivery> cents and the total is <total> cents

    Examples:
      | case      | subtotal      | delivery | total         |
      | below     | 4999          | 500      | 5499          |
      | above     | 5001          | 0        | 5001          |
      | zero      | 0             | 500      | 500           |
      | large     | 1000000000000 | 0        | 1000000000000 |

  Scenario Outline: <case>
    Given a subtotal of <subtotal>
    When I request pricing and checkout
    Then both interfaces reject the subtotal with <error>

    Examples:
      | case           | subtotal | error      |
      | negative       | -1       | ValueError |
      | negative-large | -5000    | ValueError |
      | true           | true     | TypeError  |
      | false          | false    | TypeError  |
      | float          | 5000.0   | TypeError  |
      | string         | "5000"   | TypeError  |
      | null           | null     | TypeError  |
      | list           | []       | TypeError  |
      | object         | {}       | TypeError  |
