"""Trusted steps call both candidate interfaces with the supplied examples."""

import json
from typing import Any

from behave import given, then, when


@given("a subtotal of {value}")
def subtotal(context: Any, value: str) -> None:
    context.subtotal = json.loads(value)


@when("I request pricing and checkout")
def request_checkout(context: Any) -> None:
    context.results = []
    for function in (context.pricing, context.checkout):
        try:
            context.results.append(("return", function(context.subtotal)))
        except (TypeError, ValueError) as exc:
            context.results.append(("raise", type(exc).__name__))


@then("delivery is {delivery:d} cents and the total is {total:d} cents")
def valid_quote(context: Any, delivery: int, total: int) -> None:
    fee, quote = context.results
    assert fee == ("return", delivery)
    assert type(fee[1]) is int
    assert quote[0] == "return"
    assert type(quote[1]) is dict
    assert quote[1] == {
        "subtotal_cents": context.subtotal,
        "delivery_cents": delivery,
        "total_cents": total,
    }
    assert all(type(value) is int for value in quote[1].values())


@then("both interfaces reject the subtotal with {error}")
def invalid_quote(context: Any, error: str) -> None:
    assert context.results == [("raise", error), ("raise", error)]
