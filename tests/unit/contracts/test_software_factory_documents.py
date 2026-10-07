"""Document gates test format/references and real code-plus-docs candidates."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_software_factory_support import (
    authored_documentation_patch,
    authored_patch,
    documentation_text,
    factory,
    invoke,
    outputs,
    seed,
)

__all__ = ["factory"]
pytestmark = pytest.mark.component
BDD = pytest.mark.skipif(
    importlib.util.find_spec("behave") is None,
    reason="Optional example integration: install apmx[factory] (Behave==1.3.3).",
)


@pytest.fixture
def documents(tmp_path: Path) -> Path:
    root = seed(tmp_path / "document factory")
    for name, raw in outputs(tmp_path).items():
        (root / name).write_bytes(raw)
    return root


def replace_metadata(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    text = path.read_text(encoding="ascii")
    header, body = text.split("```\n", 1)
    metadata = json.loads(header.removeprefix("```json\n"))
    change(metadata)
    path.write_text(f"```json\n{json.dumps(metadata)}\n```{body}", encoding="ascii")


@pytest.mark.parametrize(
    "kind,name",
    [
        ("planning", "plan.md"),
        ("specification", "specification.md"),
        ("implementation", "implementation.md"),
        ("documentation", "documentation.md"),
        ("review", "review.md"),
    ],
)
def test_document_profile_accepts_valid_structure_and_references(
    documents: Path, kind: str, name: str
) -> None:
    code, report = invoke(documents, "documents.py", kind, name)
    assert code == 0, report
    assert report["schema"] == "software-factory-document/2"
    assert "semantic" in report["scope"]
    assert report["sha256"] == hashlib.sha256((documents / name).read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "fault",
    [
        "metadata",
        "duplicate-heading",
        "unknown-case",
        "missing-case",
        "duplicate-case",
        "interface",
    ],
)
def test_specification_format_and_reference_failures_are_rejected(
    documents: Path, fault: str
) -> None:
    path = documents / "specification.md"
    if fault == "metadata":
        path.write_text(path.read_text().split("```\n", 1)[1], encoding="ascii")
    elif fault == "duplicate-heading":
        path.write_text(path.read_text() + "\n## Behavior\nDuplicate.\n", encoding="ascii")
    else:

        def mutate(data: dict) -> None:
            if fault == "unknown-case":
                data["acceptance"][0] = "not-a-supplied-case"
            elif fault == "missing-case":
                data["acceptance"].pop()
            elif fault == "duplicate-case":
                data["acceptance"].append(data["acceptance"][0])
            else:
                data["interfaces"][0] = "src/pricing.py:missing"

        replace_metadata(path, mutate)
    code, report = invoke(documents, "documents.py", "specification", path.name)
    assert code == 1 and report["status"] == "failed", report


def test_specification_checks_do_not_require_one_prose_wording(documents: Path) -> None:
    path = documents / "specification.md"
    text = path.read_text().replace(
        "Free from 5000 cents; fee 500 below.", "A completely different explanation."
    )
    path.write_text(text, encoding="ascii")
    assert invoke(documents, "documents.py", "specification", path.name)[0] == 0


def test_planning_distinguishes_new_and_existing_targets(documents: Path) -> None:
    path = documents / "plan.md"
    replace_metadata(path, lambda data: data["targets"][0].update(state="new"))
    code, report = invoke(documents, "documents.py", "planning", path.name)
    assert code == 1 and report["status"] == "failed", report


def test_review_finding_must_reference_an_actual_artifact_line(documents: Path) -> None:
    path = documents / "review.md"
    replace_metadata(
        path,
        lambda data: data.update(
            findings=[{"artifact": "changes.diff", "line": 100000, "detail": "Inspect this."}]
        ),
    )
    code, report = invoke(documents, "documents.py", "review", path.name)
    assert code == 1 and report["status"] == "failed", report


@pytest.mark.parametrize(
    "checker", ["documentation.py", pytest.param("documented_checkout.py", marks=BDD)]
)
def test_documentation_patch_is_checked_with_unchanged_code(documents: Path, checker: str) -> None:
    before = {
        path.relative_to(documents): path.read_bytes()
        for path in documents.rglob("*")
        if path.is_file()
    }
    code, report = invoke(documents, checker, "changes.diff", "documentation.diff")
    assert code == 0 and report["status"] == "passed", report
    subject = report["subject"]
    assert subject["patch"] == hashlib.sha256(before[Path("changes.diff")]).hexdigest()
    assert (
        subject["documentation_patch"]
        == hashlib.sha256(before[Path("documentation.diff")]).hexdigest()
    )
    assert subject["candidate"] != subject["code_candidate"]
    assert "docs/checkout.md" in subject["candidate_files"]
    after = {
        path.relative_to(documents): path.read_bytes()
        for path in documents.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    "fault",
    [
        "link",
        "anchor",
        "example",
        "missing-example",
        "duplicate-section",
        "reference-link",
        "malformed-link",
        "space-link",
        "escape-link",
    ],
)
def test_documentation_content_failures_reject_the_delivery(
    documents: Path, tmp_path: Path, fault: str
) -> None:
    text = documentation_text()
    if fault == "link":
        text = text.replace("../src/pricing.py", "../src/missing.py")
    elif fault == "anchor":
        text = text.replace("#examples", "#missing")
    elif fault == "example":
        text = text.replace("| 5000 | 0 | 5000 |", "| 5000 | 500 | 5500 |")
    elif fault == "missing-example":
        text = (
            "\n".join(line for line in text.splitlines() if not line.startswith("| zero |")) + "\n"
        )
    elif fault == "duplicate-section":
        text += "\n## Delivery policy\nDuplicate.\n"
    elif fault == "reference-link":
        text += "\n[Missing page][missing]\n\n[missing]: missing.md\n"
    elif fault == "malformed-link":
        text += "\n[Broken](missing.md\n"
    elif fault == "space-link":
        text += "\n[Broken](missing file.md)\n"
    else:
        text += "\n[Escape](%2e%2e/%2e%2e/outside.md)\n"
    (documents / "documentation.diff").write_bytes(authored_documentation_patch(tmp_path, text))
    code, report = invoke(documents, "documentation.py", "changes.diff", "documentation.diff")
    assert code == 1 and report["status"] == "failed", report


def test_documentation_cannot_edit_the_accepted_application(documents: Path) -> None:
    patch = documents / "documentation.diff"
    patch.write_bytes(patch.read_bytes().replace(b"docs/checkout.md", b"src/checkout.py"))
    code, report = invoke(documents, "documentation.py", "changes.diff", patch.name)
    assert code == 2 and report["status"] == "error", report


@pytest.mark.parametrize(
    "checker", ["documentation.py", pytest.param("documented_checkout.py", marks=BDD)]
)
def test_documented_examples_really_call_the_candidate_apis(
    documents: Path, tmp_path: Path, checker: str
) -> None:
    (documents / "changes.diff").write_bytes(authored_patch(tmp_path, "boundary"))
    code, report = invoke(documents, checker, "changes.diff", "documentation.diff")
    assert code == 1 and report["status"] == "failed", report
    assert "Free delivery at 5000 cents" in json.dumps(report["observed"])


@pytest.mark.parametrize(
    "name", ["docs/checkout.md", "documentation.diff", "changes.diff", "checks/document-formats.md"]
)
def test_documentation_baselines_patches_and_formats_remain_bound_during_checks(
    documents: Path,
    factory: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    def mutate(root: Path, _candidate: Path, _area: Path) -> tuple[int, list]:
        target = root / name
        target.write_bytes(target.read_bytes() + b"\nchanged\n")
        return 0, []

    monkeypatch.chdir(documents)
    code = factory.support.execute(
        "documentation-mutation",
        (),
        mutate,
        ["changes.diff", "documentation.diff"],
        documentation=True,
    )
    assert code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_generated_document_fences_are_not_executed(documents: Path, tmp_path: Path) -> None:
    text = documentation_text() + (
        "\n```python\nfrom pathlib import Path\n"
        "Path('document-code-executed').write_text('wrong')\n"
        "## Examples\n```\n"
    )
    (documents / "documentation.diff").write_bytes(authored_documentation_patch(tmp_path, text))
    code, report = invoke(documents, "documentation.py", "changes.diff", "documentation.diff")
    assert code == 0, report
    assert not (documents / "document-code-executed").exists()


@pytest.mark.parametrize("fault", ["duplicate-json-key", "wrong-type", "stale-profile", "empty"])
def test_document_metadata_is_typed_versioned_and_unambiguous(documents: Path, fault: str) -> None:
    path = documents / "review.md"
    if fault == "duplicate-json-key":
        text = path.read_text().replace('"findings": []', '"findings": [], "findings": []')
        path.write_text(text, encoding="ascii")
    elif fault == "empty":
        path.write_text(
            path.read_text().replace(
                "## Findings\nNo additional concerns found by inspection.", "## Findings\n"
            ),
            encoding="ascii",
        )
    else:
        replace_metadata(
            path,
            lambda data: data.update(
                {"findings": "none"}
                if fault == "wrong-type"
                else {"schema": "software-factory-document/1"}
            ),
        )
    code, report = invoke(documents, "documents.py", "review", path.name)
    assert code == 1 and report["status"] == "failed", report


def test_review_accepts_a_real_artifact_line_reference(documents: Path) -> None:
    path = documents / "review.md"
    replace_metadata(
        path,
        lambda data: data.update(
            findings=[{"artifact": "changes.diff", "line": 1, "detail": "Review this scope."}]
        ),
    )
    code, report = invoke(documents, "documents.py", "review", path.name)
    assert code == 0, report
    assert (
        report["references"]["changes.diff"]["sha256"]
        == hashlib.sha256((documents / "changes.diff").read_bytes()).hexdigest()
    )


def test_combined_candidate_missing_behave_is_operational(documents: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(documents / "checks/documented_checkout.py"),
            "changes.diff",
            "documentation.diff",
        ],
        cwd=documents,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 2, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "error" and "Optional Behave is missing" in report["detail"]
