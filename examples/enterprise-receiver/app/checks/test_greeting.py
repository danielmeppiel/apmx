"""Receiver-required check for the enterprise-receiver fixture app.

Loaded by file path so it is independent of the working directory or an
installed package; this mirrors how a real receiver check validates a
candidate without trusting its import machinery.
"""

import importlib.util
from pathlib import Path


def _load_greeting():
    path = Path(__file__).resolve().parents[1] / "greeting.py"
    spec = importlib.util.spec_from_file_location("greeting", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_greet_uses_full_salutation() -> None:
    greeting = _load_greeting()
    assert greeting.greet("World") == "Hello, World!"
