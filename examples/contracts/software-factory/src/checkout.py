"""Checkout totals for the current fixed-fee pricing policy."""

from .pricing import DELIVERY_FEE_CENTS, delivery_fee


def checkout(subtotal_cents: int) -> dict[str, int]:
    """Return a subtotal, delivery charge and payable total in cents."""
    fee = delivery_fee(subtotal_cents)
    return {
        "subtotal_cents": subtotal_cents,
        "delivery_cents": fee,
        "total_cents": subtotal_cents + DELIVERY_FEE_CENTS,
    }
