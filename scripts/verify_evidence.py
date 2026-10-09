"""Thin wrapper over the single receipt verifier owned by ``src/apmx/audit/verifier.py``.

The verifier is loaded by file path, never as part of the ``apmx`` package, so
this script still validates a receipt without importing APMX. A copy placed
next to this script as ``receipt_verifier.py`` (as the demo kit does) wins
over the source checkout. ``apmx audit`` uses the same module.
"""

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SOURCE = next(
    candidate
    for candidate in (
        _HERE / "receipt_verifier.py",
        _HERE.parent / "src/apmx/audit/verifier.py",
    )
    if candidate.is_file()
)
_SPEC = importlib.util.spec_from_file_location("receipt_verifier", _SOURCE)
_VERIFIER = importlib.util.module_from_spec(_SPEC)
sys.modules["receipt_verifier"] = _VERIFIER
_SPEC.loader.exec_module(_VERIFIER)
globals().update(
    {name: value for name, value in vars(_VERIFIER).items() if not name.startswith("__")}
)

if __name__ == "__main__":
    raise SystemExit(_VERIFIER.main())
