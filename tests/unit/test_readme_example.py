"""Keep the first-contract walkthrough runnable with its supplied checker."""

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples/contracts/first-contract"


def test_readme_opens_with_plan_run_audit() -> None:
    """The first commands preview, run and audit a factory, with nothing else required."""
    snippets = re.findall(
        r"```(?:sh|bash)\n(.*?)\n```", (ROOT / "README.md").read_text(), re.DOTALL
    )
    assert snippets
    commands = [shlex.split(line, comments=True) for line in snippets[0].splitlines()]
    assert commands == [
        ["apmx", "./feature-factory", "--plan"],
        ["apmx", "./feature-factory"],
        ["apmx", "audit", "<receipt>"],
    ]
    assert Path(commands[1][1]).parts == ("feature-factory",)


def test_readme_teaches_the_real_planning_contract_before_running() -> None:
    """Introduce one ordinary artifact before showing a factory run."""
    readme = (ROOT / "README.md").read_text()
    snippets = re.findall(r"```(?:markdown|yaml)\n---\n(.*?)\n---", readme, re.DOTALL)
    assert len(snippets) == 1
    documented = yaml.safe_load(snippets[0])
    planning = ROOT / "examples/contracts/software-factory/contracts/planning.contract.md"
    actual = yaml.safe_load(planning.read_text().split("---", 2)[1])
    assert documented == actual
    assert readme.index(snippets[0]) < readme.index("$ apmx ./feature-factory")


def test_readme_multiple_artifacts_match_the_implementation_contract() -> None:
    """Document delivered files, never a workspace write-frame substitute."""
    readme = (ROOT / "README.md").read_text()
    snippets = re.findall(r"```yaml\n(.*?)\n```", readme, re.DOTALL)
    declarations = [yaml.safe_load(snippet) for snippet in snippets]
    outputs = [item["produces"] for item in declarations if "produces" in item]
    build = ROOT / "examples/contracts/software-factory/contracts/build.contract.md"
    actual = yaml.safe_load(build.read_text().split("---", 2)[1])
    assert outputs == [actual["produces"]]
    assert set(outputs[0]) == {"changes.diff", "implementation.md"}


def test_readme_quotes_a_real_optional_gherkin_scenario() -> None:
    """The optional behavior example must exist in the actual acceptance suite."""
    readme = (ROOT / "README.md").read_text()
    snippets = re.findall(r"```gherkin\n(.*?)\n```", readme, re.DOTALL)
    assert len(snippets) == 1
    quoted = "\n".join(line.strip() for line in snippets[0].strip().splitlines())
    feature = ROOT / "examples/contracts/software-factory/checks/features/free-shipping.feature"
    actual = "\n".join(line.strip() for line in feature.read_text().splitlines())
    assert quoted in actual
    assert re.search(r"Gherkin[^\n.]*optional|[Oo]ptional[^\n.]*Gherkin", readme)


@pytest.mark.parametrize("omit_source", [False, True])
def test_readme_contract_uses_the_complete_example_checker(
    tmp_path: Path,
    omit_source: bool,
) -> None:
    """Exercise the documented command, including its required positional arguments."""
    snippets = re.findall(
        r"```markdown\n(.*?)\n```", (EXAMPLE / "README.md").read_text(), re.DOTALL
    )
    assert len(snippets) == 1 and snippets[0].startswith("---\n")
    documented = yaml.safe_load(snippets[0].split("---", 2)[1])
    actual = yaml.safe_load((EXAMPLE / "handoff.contract.md").read_text().split("---", 2)[1])
    assert documented == actual
    caller = tmp_path / "caller with spaces"
    shutil.copytree(EXAMPLE, caller)
    entries = [
        {"source_id": source, "summary": "A fixture summary.", "caution": "A fixture limitation."}
        for source in ("restore", "files", "scripts")
    ]
    if omit_source:
        entries.pop()
    (caller / documented["produces"]).write_text(json.dumps(entries), encoding="utf-8")
    command = shlex.split(documented["verify"]["handoff"])
    assert command[0] == "python3"
    result = subprocess.run(
        [sys.executable, *command[1:]],
        cwd=caller,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (1 if omit_source else 0), result.stdout + result.stderr
    assert (caller / "notes.md").read_bytes() == (EXAMPLE / "notes.md").read_bytes()


def test_accept_factory_prs_example_is_a_valid_policy_and_one_step_gate() -> None:
    """The documented policy parses with apmx's own reader; the gate is one audit step."""
    from apmx.audit import policy

    example = ROOT / "examples/accept-factory-prs"
    keys = policy.document_keys((example / "apm-policy.yml").read_text(), "example")
    assert keys["require_signed"] and keys["signers"][0].repo == "acme/factories"
    readme = (ROOT / "README.md").read_text()
    section = readme[readme.index("## Accept factory PRs by proof") :]
    snippet = re.findall(r"```yaml\n(.*?)\n```", section, re.DOTALL)[0]
    documented = policy.document_keys(snippet.replace("<definition digest>", "0" * 64), "readme")
    assert documented["signers"] == keys["signers"]
    workflow = yaml.safe_load((example / "audit.yml").read_text())
    (job,) = workflow["jobs"].values()
    assert workflow["permissions"] == {"contents": "read", "attestations": "read"}
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    assert [step.get("run") for step in job["steps"]][-1] == "apmx audit"
