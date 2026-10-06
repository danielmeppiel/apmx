"""Delivery pricing in integer cents."""

DELIVERY_FEE_CENTS = 500


def delivery_fee(subtotal_cents: int) -> int:
    """Return the delivery fee for a nonnegative integer subtotal."""
    if type(subtotal_cents) is not int:
        raise TypeError("subtotal_cents must be an integer")
    if subtotal_cents < 0:
        raise ValueError("subtotal_cents must be nonnegative")
    return DELIVERY_FEE_CENTS
