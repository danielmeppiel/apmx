# Checkout delivery

## Delivery policy

All nonnegative integer subtotals currently have a fixed 500-cent delivery fee.
The checkout total is the subtotal plus this fee.
See [pricing](../src/pricing.py) and [checkout](../src/checkout.py).

## Examples

| Subtotal | Delivery | Total |
| --- | --- | --- |
| 0 | 500 | 500 |
| 4999 | 500 | 5499 |
| 5000 | 500 | 5500 |
| 5001 | 500 | 5501 |

## Input errors

Negative integers raise `ValueError`. Other types, including booleans, raise
`TypeError`. All prices are integer cents.
