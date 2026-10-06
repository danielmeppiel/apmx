"""Checker claims bind actual retained files, never producer prose or output-set hashes."""

import hashlib
import json
from pathlib import Path

import pytest

from apmx.contracts import check_subjects
from apmx.contracts.events import EventEmitter
from apmx.contracts.models import ContractError
from apmx.contracts.stream import ContractStreamDecoder
from apmx.core.contract_logger import ContractLogger


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def document_report() -> dict:
    return {
        "schema": "software-factory-document/2",
        "document": "planning",
        "sha256": digest(b"document"),
        "references": {},
        "status": "passed",
        "scope": "Format and reference consistency only.",
    }


def test_only_exact_checker_attribution_can_supply_a_report() -> None:
    report = document_report()
    raw = json.dumps(report)
    transcript = (
        f"  Copilot (untrusted) > {raw}\n"
        f"  Check similar (untrusted) > {raw}\n"
        f"  Check exact stderr (untrusted) > {raw}\n"
        f"  Check exact (untrusted) > {raw}\n"
    )
    assert check_subjects.read_report(transcript, "exact") == report
    assert check_subjects.read_report(transcript, "missing") is None


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("chunk_size", [1, 7, 4096])
def test_checker_report_survives_native_line_framing(
    tmp_path: Path, ending: bytes, chunk_size: int
) -> None:
    report = document_report()
    logger = ContractLogger()
    logger.attach_run("fixture", tmp_path)
    decoder = ContractStreamDecoder(
        EventEmitter("fixture", logger.on_event),
        source="checker",
        label="exact",
        json_stdout=False,
    )
    wire = json.dumps(report).encode("ascii") + ending
    for start in range(0, len(wire), chunk_size):
        decoder.feed("stdout", wire[start : start + chunk_size])
    decoder.finish()
    logger.close()
    transcript = (tmp_path / "transcript.log").read_text(encoding="utf-8")
    assert check_subjects.read_report(transcript, "exact") == report


@pytest.mark.parametrize("fault", ["duplicate", "malformed", "unknown-schema", "duplicate-key"])
def test_recognized_report_corruption_is_not_a_generic_success(fault: str) -> None:
    raw = json.dumps(document_report())
    if fault == "duplicate":
        raw = raw + "\n  Check exact (untrusted) > " + raw
    elif fault == "malformed":
        raw = raw[:-5]
    elif fault == "unknown-schema":
        raw = raw.replace("software-factory-document/2", "software-factory-document/999")
    else:
        raw = raw[:-1] + ', "status": "failed"}'
    with pytest.raises(ContractError):
        check_subjects.read_report("  Check exact (untrusted) > " + raw, "exact")


def test_document_subject_is_actual_file_not_other_output(tmp_path: Path) -> None:
    (tmp_path / "planning.md").write_bytes(b"document")
    (tmp_path / "implementation.md").write_bytes(b"different")
    assert check_subjects.document_subject(
        document_report(), tmp_path, ("planning.md", "implementation.md")
    ) == ("planning.md",)


@pytest.mark.parametrize("fault", ["missing", "ambiguous", "reference"])
def test_document_subject_requires_unambiguous_bytes_and_available_references(
    tmp_path: Path, fault: str
) -> None:
    (tmp_path / "planning.md").write_bytes(b"wrong" if fault == "missing" else b"document")
    (tmp_path / "other.md").write_bytes(b"document" if fault == "ambiguous" else b"other")
    report = document_report()
    if fault == "reference":
        report["references"] = {"missing.txt": {"sha256": digest(b"absent"), "size": 6}}
    with pytest.raises(ContractError):
        check_subjects.document_subject(report, tmp_path, ("planning.md", "other.md"))


def test_candidate_is_reconstructed_without_executing_code(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "src").mkdir()
    before = b'raise RuntimeError("must never execute")\n'
    after = b'raise RuntimeError("must still never execute")\n'
    (source / "src/app.py").write_bytes(before)
    patch = (
        b"diff --git a/src/app.py b/src/app.py\n"
        b"--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n"
        b'-raise RuntimeError("must never execute")\n'
        b'+raise RuntimeError("must still never execute")\n'
    )
    (source / "changes.diff").write_bytes(patch)
    base = {"src/app.py": {"sha256": digest(before), "size": len(before)}}
    candidate = {"src/app.py": {"sha256": digest(after), "size": len(after)}}
    report = {
        "base_files": base,
        "base": check_subjects.inventory_digest(base),
        "candidate_files": candidate,
        "candidate": check_subjects.inventory_digest(candidate),
        "patch": digest(patch),
    }
    target = tmp_path / "candidate"
    check_subjects.materialize_candidate(report, source, target)
    assert (target / "src/app.py").read_bytes() == after
    assert (source / "src/app.py").read_bytes() == before
    assert not (target / ".git").exists()


@pytest.mark.parametrize("name", ["../escape", "/absolute", ".git/config", "a/../../escape"])
def test_report_inventories_refuse_unsafe_paths(tmp_path: Path, name: str) -> None:
    with pytest.raises(ContractError):
        check_subjects.verify_inventory(tmp_path, {name: {"sha256": digest(b"x"), "size": 1}})
