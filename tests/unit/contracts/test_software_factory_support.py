"""Authored example fixtures, not a second factory scheduler or evidence owner."""

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from apmx.utils.path_security import safe_rmtree

EXAMPLE = Path(__file__).resolve().parents[3] / "examples/contracts/software-factory"
pytestmark = pytest.mark.component

GENERATED_TESTS = '''"""Regression tests for the delivery threshold."""
import unittest

from src.checkout import checkout
from src.pricing import delivery_fee


class FreeShippingTests(unittest.TestCase):
    def test_threshold(self) -> None:
        self.assertEqual(delivery_fee(5000), 0)
        self.assertEqual(checkout(5000)["total_cents"], 5000)

    def test_above(self) -> None:
        self.assertEqual(checkout(5001)["delivery_cents"], 0)
'''


@pytest.fixture
def factory(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.syspath_prepend(str(EXAMPLE / "checks"))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    modules = {}
    for name in (
        "software_factory_support",
        "software_factory_cases",
        "software_factory_bdd",
        "documents",
        "regression",
    ):
        modules[name.rsplit("_", 1)[-1]] = importlib.import_module(name)
    return SimpleNamespace(**modules)


def seed(root: Path) -> Path:
    shutil.copytree(EXAMPLE, root, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    return root


def _write_patch_file(path: Path, raw: bytes, *, crlf: bool) -> None:
    if crlf:
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    path.write_bytes(raw)


def authored_patch(
    root: Path, variant: str = "good", tests: str = GENERATED_TESTS, *, crlf: bool = False
) -> bytes:
    """Export deterministic authored edits with Git; no model-composed patch hunks."""
    working = root / f"software-factory-patch-{variant}"
    working.mkdir()
    for name in ("src/__init__.py", "src/pricing.py", "src/checkout.py", "tests/test_checkout.py"):
        path = working / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (EXAMPLE / name).read_bytes()
        _write_patch_file(path, raw, crlf=crlf)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*arguments: str) -> bytes:
        return subprocess.run(
            ["git", "-c", "core.autocrlf=false", *arguments],
            cwd=working,
            env=environment,
            capture_output=True,
            check=True,
            timeout=15,
        ).stdout

    git("init", "--quiet")
    git("add", "--", "src", "tests")
    pricing = (working / "src/pricing.py").read_text()
    checkout = (working / "src/checkout.py").read_text()
    if variant == "baseline":
        pricing += "\n# Proposed change not implemented.\n"
        checkout += "\n# Proposed change not implemented.\n"
    else:
        operator = ">" if variant == "boundary" else ">="
        pricing = pricing.replace(
            "return DELIVERY_FEE_CENTS",
            f"return 0 if subtotal_cents {operator} 5000 else DELIVERY_FEE_CENTS",
        )
        if variant == "small-order":
            pricing = pricing.replace(
                "DELIVERY_FEE_CENTS = 500",
                "DELIVERY_FEE_CENTS = 500\nSMALL_ORDER_FEE_CENTS = 501",
            ).replace(
                "    return 0 if",
                "    if 0 < subtotal_cents < 2000:\n"
                "        return SMALL_ORDER_FEE_CENTS\n"
                "    return 0 if",
            )
        checkout = checkout.replace("current fixed-fee", "subtotal-based")
        checkout = checkout.replace(
            "import DELIVERY_FEE_CENTS, delivery_fee", "import delivery_fee"
        )
        checkout = checkout.replace("subtotal_cents + DELIVERY_FEE_CENTS", "subtotal_cents + fee")
    for name, content in (
        ("src/pricing.py", pricing),
        ("src/checkout.py", checkout),
        ("tests/test_free_shipping.py", tests),
    ):
        raw = content.encode("ascii")
        _write_patch_file(working / name, raw, crlf=crlf)
    git("add", "--intent-to-add", "--", "tests/test_free_shipping.py")
    raw = git("diff", "--no-ext-diff", "--no-textconv", "--no-renames")
    safe_rmtree(working, root)
    return raw


def outputs(root: Path, variant: str = "good") -> dict[str, bytes]:
    return {
        "plan.md": (
            "# Checkout plan\n\n## Goal\nFree delivery from 5000 cents.\n\n"
            "## Changes\nUpdate pricing and checkout; add threshold tests.\n\n"
            "## Validation\nIndependent acceptance and regression checks.\n\n"
            "## Risks\nCheck inclusive boundaries and reject booleans.\n"
        ).encode("ascii"),
        "specification.md": (
            "# Checkout specification\n\n## Behavior\nFree from 5000 cents; fee 500 below.\n\n"
            "## Interface\nPreserve delivery_fee and checkout; reject invalid input types.\n\n"
            "## Acceptance\n4999, 5000, 5001, zero, negatives and invalid types.\n"
        ).encode("ascii"),
        "changes.diff": authored_patch(root, variant),
        "implementation.md": (
            "# Implementation\n\n## Changes\nPricing and totals use the requested delivery fee.\n\n"
            "## Validation\nSupplied acceptance and regression commands run separately.\n\n"
            "## Limitations\nNo checks were run during artifact production.\n"
        ).encode("ascii"),
        "review.md": (
            "# Advisory review\n\n## Assessment\nThe supplied patch addresses the specification.\n\n"
            "## Findings\nNo additional concerns found by inspection.\n\n"
            "## Limitations\nNo execution claims or production certification.\n"
        ).encode("ascii"),
    }


def invoke(
    root: Path, checker: str, *args: str, env: dict[str, str] | None = None
) -> tuple[int, Any]:
    completed = subprocess.run(
        [sys.executable, "-I", "-B", str(root / "checks" / checker), *args],
        cwd=root,
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.stdout, completed.stderr.decode(errors="replace")
    return completed.returncode, json.loads(completed.stdout)
