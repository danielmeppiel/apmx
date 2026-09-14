"""Check document structure only; supplied acceptance governs application behavior."""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from software_factory_support import Invalid, digest, read_file

SECTIONS = {
    "planning": ("Goal", "Changes", "Validation", "Risks"),
    "specification": ("Behavior", "Interface", "Acceptance"),
    "implementation": ("Changes", "Validation", "Limitations"),
    "review": ("Assessment", "Findings", "Limitations"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", choices=SECTIONS)
    parser.add_argument("artifact")
    args = parser.parse_args(argv)
    result = {"schema": "software-factory-document/1", "document": args.document}
    try:
        raw = read_file(Path.cwd(), args.artifact, 16 * 1024)
        text = raw.decode("ascii")
        sections = re.split(r"(?m)^## ([^\n]+)\n", text)
        found = {sections[i].strip(): sections[i + 1].strip() for i in range(1, len(sections), 2)}
        valid = all(found.get(heading) for heading in SECTIONS[args.document])
        result.update(
            status="passed" if valid else "failed",
            sha256=digest(raw),
            scope="Document sections only; no semantic correctness or execution claim.",
        )
        code = int(not valid)
    except (Invalid, OSError, UnicodeError) as exc:
        result.update(status="error", detail=str(exc)[:600])
        code = 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
