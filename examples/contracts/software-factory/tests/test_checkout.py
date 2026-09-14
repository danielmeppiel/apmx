"""Existing behavior that the feature must preserve."""

import unittest

from src.checkout import checkout
from src.pricing import delivery_fee


class CheckoutTests(unittest.TestCase):
    def test_small_order(self) -> None:
        self.assertEqual(delivery_fee(1000), 500)
        self.assertEqual(
            checkout(1000),
            {"subtotal_cents": 1000, "delivery_cents": 500, "total_cents": 1500},
        )

    def test_zero(self) -> None:
        self.assertEqual(checkout(0)["total_cents"], 500)

    def test_negative(self) -> None:
        for function in (delivery_fee, checkout):
            with self.assertRaises(ValueError):
                function(-1)

    def test_invalid_types(self) -> None:
        for value in (True, False, 1000.0, "1000", None, [], {}):
            for function in (delivery_fee, checkout):
                with (
                    self.subTest(value=value, function=function.__name__),
                    self.assertRaises(TypeError),
                ):
                    function(value)
