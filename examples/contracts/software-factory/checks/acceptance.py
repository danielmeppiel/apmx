"""Apply changes.diff and execute every supplied Gherkin example."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from software_factory_bdd import inspect
from software_factory_cases import REQUIRED
from software_factory_support import execute

if __name__ == "__main__":
    raise SystemExit(execute("acceptance", REQUIRED, inspect))
