"""Independent fixture checker: compare candidate identity with captured input."""

import json
import sys
from pathlib import Path


def main():
    expected = json.loads(Path("notes.md").read_text(encoding="utf-8"))
    candidates = [
        json.loads(Path(name).read_text(encoding="utf-8"))
        for name in (sys.argv[1:] or ["handoff.json"])
    ]
    valid = all(
        isinstance(candidate, dict) and set(candidate) == {"source", "value"}
        and candidate == expected
        for candidate in candidates
    )
    print("Independent fixture check: " + ("pass" if valid else "reject"))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
