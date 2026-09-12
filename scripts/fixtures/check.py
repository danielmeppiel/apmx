"""Independent fixture checker: compare candidate identity with captured input."""

import json
from pathlib import Path


def main():
    expected = json.loads(Path("notes.md").read_text(encoding="utf-8"))
    candidate = json.loads(Path("handoff.json").read_text(encoding="utf-8"))
    valid = (
        isinstance(candidate, dict)
        and set(candidate) == {"source", "value"}
        and candidate == expected
    )
    print("Independent fixture check: " + ("pass" if valid else "reject"))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
