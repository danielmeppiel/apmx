"""Read-only subject mapping for the versioned software-factory checker reports.

These reports describe checker observations, not another acceptance authority.
Candidate reconstruction applies retained patches but never executes their code.
"""

import hashlib
import json
import re
import shutil
from pathlib import Path

from .models import ContractError, ContractLimits
from .process import local_git
from .workspace import _path, _read

REPORT_SCHEMAS = frozenset({"software-factory-document/2", "software-factory-check/1"})
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _refuse(message: str) -> ContractError:
    return ContractError(message, code="check_subject_unavailable")


def _object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise _refuse("The retained checker report contains a duplicate field.")
        value[key] = item
    return value


def read_report(transcript: str, check_name: str) -> dict | None:
    """Only the logger's exact checker/stdout attribution can supply this profile."""
    prefix = f"  Check {check_name} (untrusted) > "
    reports = []
    for line in transcript.splitlines():
        if not line.startswith(prefix):
            continue
        text = line[len(prefix) :]
        if not text.startswith("{") or "software-factory-" not in text:
            continue
        try:
            report = json.loads(text, object_pairs_hook=_object)
        except (ValueError, RecursionError) as exc:
            if isinstance(exc, ContractError):
                raise
            raise _refuse("The retained checker report is malformed or truncated.") from exc
        if report.get("schema") not in REPORT_SCHEMAS:
            raise _refuse("The retained checker report version is unsupported.")
        reports.append(report)
    if len(reports) > 1:
        raise _refuse("Multiple reports cannot identify one checker invocation unambiguously.")
    return reports[0] if reports else None


def inventory_digest(files: dict) -> str:
    """software-factory-check/1: SHA-256 of compact sorted ASCII JSON, without LF."""
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _inventory(files: dict) -> None:
    limits = ContractLimits()
    if not isinstance(files, dict) or len(files) > limits.baseline_files:
        raise _refuse("The checker subject inventory is invalid or oversized.")
    names = set()
    size = 0
    for name, item in files.items():
        if (
            not isinstance(name, str)
            or not name.isascii()
            or any(part.casefold() == ".git" for part in Path(name).parts)
            or name.casefold() in names
            or not isinstance(item, dict)
            or set(item) != {"sha256", "size"}
            or not isinstance(item["sha256"], str)
            or _SHA256.fullmatch(item["sha256"]) is None
            or type(item["size"]) is not int
            or not 0 <= item["size"] <= limits.file_bytes
        ):
            raise _refuse("The checker subject contains an invalid file identity.")
        _path(Path.cwd(), name)
        names.add(name.casefold())
        size += item["size"]
    if size > limits.baseline_bytes:
        raise _refuse("The checker subject exceeds the captured byte limit.")


def verify_inventory(root: Path, files: dict) -> None:
    """Match each reported file against bounded retained bytes."""
    _inventory(files)
    for name, expected in files.items():
        try:
            _, observed = _read(root, name, expected["size"])
        except OSError as exc:
            raise _refuse("A checker subject reference is unavailable.") from exc
        if observed.sha256 != expected["sha256"] or observed.size != expected["size"]:
            raise _refuse("The checker subject does not match retained bytes.")


def document_subject(report: dict, source: Path, outputs: tuple[str, ...]) -> tuple[str, ...]:
    """Select the exact assessed document; matching an aggregate output hash is invalid."""
    matches = []
    for name in outputs:
        _, observed = _read(source, name, ContractLimits().output_bytes)
        if observed.sha256 == report.get("sha256"):
            matches.append(name)
    if len(matches) != 1:
        raise _refuse("The document report does not identify exactly one retained artifact.")
    if report.get("status") == "passed":
        if "references" not in report:
            raise _refuse("The passed document report omitted its reference inventory.")
        verify_inventory(source, report["references"])
    return tuple(matches)


def _patch(source: Path, name: str, expected: str) -> Path:
    _, actual = _read(source, name, ContractLimits().output_bytes)
    if actual.sha256 != expected:
        raise _refuse("The checker patch does not match retained bytes.")
    return _path(source, name)


def _tree(root: Path) -> dict:
    """Inventory only a bounded owned reconstruction, including unexpected files."""
    entries = {}
    pending = [root]
    count = 0
    limits = ContractLimits()
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            count += 1
            if count > limits.baseline_files * 2:
                raise _refuse("Reconstructed candidate exceeds the path limit.")
            if path == root / ".git":
                continue
            if path.is_symlink():
                raise _refuse("A reconstructed candidate contains a symlink.")
            if path.is_dir():
                pending.append(path)
            else:
                name = path.relative_to(root).as_posix()
                _, entry = _read(root, name, limits.file_bytes)
                entries[name] = {"sha256": entry.sha256, "size": entry.size}
    _inventory(entries)
    return entries


def materialize_candidate(subject: dict, source: Path, target: Path) -> None:
    """Reconstruct code or combined code/docs from captured inputs, without running checks.

    The target must be absent and caller-owned. No live checkout or original
    package is consulted. Git uses the shared local-only supervised boundary.
    """
    try:
        base = subject["base_files"]
        candidate = subject["candidate_files"]
        verify_inventory(source, base)
        _inventory(candidate)
        if (
            not base
            or not candidate
            or inventory_digest(base) != subject["base"]
            or inventory_digest(candidate) != subject["candidate"]
        ):
            raise _refuse("The checker candidate/base inventory digest is inconsistent.")
        patches = [_patch(source, "changes.diff", subject["patch"])]
        if "documentation_patch" in subject:
            patches.append(_patch(source, "documentation.diff", subject["documentation_patch"]))
        target.mkdir()
        for name, identity in base.items():
            raw, _ = _read(source, name, identity["size"])
            path = _path(target, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        git_root = target / ".git"
        try:
            local_git(target, "init", "--quiet")
            code_files = None
            for index, patch in enumerate(patches):
                local_git(target, "apply", "--check", "--whitespace=error-all", str(patch))
                local_git(target, "apply", "--whitespace=error-all", str(patch))
                if index == 0 and len(patches) == 2:
                    code_files = _tree(target)
            files = _tree(target)
            if files != candidate:
                raise _refuse("Reconstructed bytes differ from the checker's actual subject.")
            if code_files is not None:
                changed = {name for name, item in files.items() if code_files.get(name) != item}
                code_only = {name: item for name, item in code_files.items() if name not in changed}
                if inventory_digest(code_only) != subject.get("code_candidate"):
                    raise _refuse("Combined candidate does not preserve its code-only identity.")
        finally:
            if git_root.is_dir() and not git_root.is_symlink():
                shutil.rmtree(git_root)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise _refuse("The retained candidate cannot be reconstructed.") from exc
