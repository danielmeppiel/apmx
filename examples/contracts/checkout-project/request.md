# Free delivery at checkout

Our tiny checkout currently charges a fixed 500-cent delivery fee. Make delivery
free for subtotals of **5000 cents or more**, keeping the existing fee below
that threshold. All amounts are integer cents; there is no rounding.

## Public interface

- `src.pricing.delivery_fee(subtotal_cents)` returns integer delivery cents.
- `src.checkout.checkout(subtotal_cents)` returns exactly
  `{"subtotal_cents": ..., "delivery_cents": ..., "total_cents": ...}`.
  Total is subtotal plus the actual delivery fee.
- Both functions accept nonnegative integers, including zero. Reject negative
  integers with `ValueError`. Reject all other types, including `bool`, with
  `TypeError`, before comparing or adding values.
- There is no upper subtotal limit. Preserve these function names, arguments
  and return shapes.

## Delivery

Edit `src/pricing.py` and `src/checkout.py`, and add
`tests/test_free_shipping.py` using Python's standard `unittest` framework.
Keep the existing tests. Export the actual edits as `changes.diff` and describe
the change in `implementation.md`. Source edits are working files; the patch
and report are the published artifacts.

The supplied acceptance checks exercise 4999, 5000, 5001, zero, negative
subtotals and invalid types, including both booleans. Generated tests supplement
these checks; neither generated tests nor a report can replace acceptance.
This example chooses Gherkin; other APMX contracts can use ordinary check tools.

Do not alter the request, supplied checks or existing tests. Do not install
packages, invoke a shell, or run checks during production. APMX runs verification
separately. Keep all generated files ASCII.
