"""Check the combined candidate's document structure, local links and declared examples."""

import json
import posixpath
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from documents import Rejected, markdown_sections, require_sections
from regression import validate_suite
from software_factory_cases import CASES
from software_factory_support import DOCUMENTATION_FILES, Invalid, execute, read_file, run_driver

REQUIRED = ("document-structure-links-examples",)


def validate_links(candidate: Path, name: str, text: str) -> None:
    """Resolve inline local file links and H2 fragments; never fetch external URLs."""
    if re.search(r"(?m)^ {0,3}\[[^\]\n]+\]:|!?\[[^\]\n]+\]\[[^\]\n]*\]", text):
        raise Rejected("Reference-style links are outside this example's inline-link format.")
    pattern = r"!?\[[^\]\n]*\]\(([^()\n]*)\)"
    if re.search(r"!?\[[^\]\n]*\]\(", re.sub(pattern, "", text)):
        raise Rejected("Malformed or unsupported inline documentation link.")
    for target in re.findall(pattern, text):
        if not target or any(character.isspace() for character in target):
            raise Rejected("Inline link targets must be nonempty URLs without literal spaces.")
        try:
            parsed = urlsplit(target)
        except ValueError as exc:
            raise Rejected("Malformed documentation link.") from exc
        if parsed.scheme in ("http", "https") and parsed.netloc:
            continue
        if parsed.scheme or parsed.netloc or parsed.query:
            raise Rejected("Expected an inline local link or an absolute HTTP(S) link.")
        path = (
            posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parsed.path)))
            if parsed.path
            else name
        )
        try:
            raw = read_file(candidate, path)
        except (Invalid, FileNotFoundError, IsADirectoryError) as exc:
            raise Rejected(f"Local documentation link is not a captured file: {target}") from exc
        if parsed.fragment:
            sections, _ = markdown_sections(raw.decode("ascii"))
            anchors = {
                re.sub(r"[^a-z0-9 _-]", "", heading.lower()).replace(" ", "-")
                for heading in sections
            }
            if unquote(parsed.fragment) not in anchors:
                raise Rejected(f"Local documentation H2 anchor does not exist: {target}")


def validate_examples(section: str) -> None:
    lines = [line.strip() for line in section.splitlines() if line.lstrip().startswith("|")]
    table = [tuple(part.strip() for part in line.strip("|").split("|")) for line in lines]
    if (
        len(table) != len(CASES) + 2
        or table[0] != ("Case", "Subtotal", "Delivery", "Total", "Error")
        or len(table[1]) != 5
        or any(re.fullmatch(r":?-{3,}:?", cell) is None for cell in table[1])
    ):
        raise Rejected("Expected the complete five-column checkout example table.")
    observed = {}
    for row in table[2:]:
        if len(row) != 5 or row[0] in observed:
            raise Rejected("Documentation example rows must have five cells and unique case IDs.")
        name, subtotal, fee, total, error = row
        try:
            if error == "-":
                case = (name, json.loads(subtotal), json.loads(fee), json.loads(total))
            elif fee == total == "-":
                case = (name, json.loads(subtotal), error, None)
            else:
                raise Rejected("An error example cannot also claim delivery and total values.")
        except (json.JSONDecodeError, RecursionError) as exc:
            raise Rejected("Example inputs and numeric results must use JSON literals.") from exc
        observed[name] = case
    expected = {case[0]: case for case in CASES}
    if json.dumps(observed, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise Rejected("Documented inputs/results must match every supplied checkout case exactly.")


def inspect(root: Path, candidate: Path, area: Path) -> tuple[int, list[dict[str, Any]]]:
    try:
        for name in DOCUMENTATION_FILES:
            text = read_file(candidate, name, 16 * 1024).decode("ascii")
            sections, _ = markdown_sections(text)
            require_sections(sections, ("Delivery policy", "Examples", "Input errors"))
            validate_links(candidate, name, text)
            validate_examples(sections["Examples"])
    except (Rejected, UnicodeError) as exc:
        return 1, [{"id": REQUIRED[0], "status": "failed", "detail": str(exc)[:600]}]
    returncode, report = run_driver(
        root, candidate, area, "software_factory_regression_driver.py", "original"
    )
    code, rows = validate_suite(report, "original", returncode)
    return code, [
        {"id": REQUIRED[0], "status": "passed" if code == 0 else "failed", "observed": rows}
    ]


if __name__ == "__main__":
    raise SystemExit(execute("documentation", REQUIRED, inspect, documentation=True))
