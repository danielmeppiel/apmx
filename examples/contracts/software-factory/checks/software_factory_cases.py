"""Supplied acceptance examples, not an implementation of the pricing rule."""

CASES = (
    ("below", 4999, 500, 5499),
    ("Free delivery at 5000 cents", 5000, 0, 5000),
    ("above", 5001, 0, 5001),
    ("zero", 0, 500, 500),
    ("large", 10**12, 0, 10**12),
    ("negative", -1, "ValueError", None),
    ("negative-large", -5000, "ValueError", None),
    ("true", True, "TypeError", None),
    ("false", False, "TypeError", None),
    ("float", 5000.0, "TypeError", None),
    ("string", "5000", "TypeError", None),
    ("null", None, "TypeError", None),
    ("list", [], "TypeError", None),
    ("object", {}, "TypeError", None),
)
REQUIRED = tuple(case[0] for case in CASES)
