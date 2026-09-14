"""Keep the README's contract example runnable with its supplied checker."""

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


@pytest.mark.parametrize("omit_source", [False, True])
def test_readme_contract_uses_the_complete_example_checker(
    tmp_path: Path, omit_source: bool,
) -> None:
    """Exercise the documented command, including its required positional arguments."""
    snippets = re.findall(r"```markdown\n(.*?)\n```", (ROOT / "README.md").read_text(), re.S)
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
        [sys.executable, *command[1:]], cwd=caller, capture_output=True, text=True,
        timeout=10, check=False,
    )
    assert result.returncode == (1 if omit_source else 0), result.stdout + result.stderr
    assert (caller / "notes.md").read_bytes() == (EXAMPLE / "notes.md").read_bytes()
