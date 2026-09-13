"""Native discovery destinations must preserve identity and reject unsafe names."""

import ast
from pathlib import Path

import pytest

from apmx.contracts.context_layout import context_directory, is_native_skill_path, native_skill_name
from apmx.contracts.frontend import plan_contract
from apmx.contracts.models import ContractError, ImportedSkill, LeafContract, LeafPlan
from apmx.contracts.workspace import _selected_names


@pytest.mark.parametrize("name", ["../escape", "A", "bad_name", "-bad", "bad--name", "a" * 65, ""])
def test_invalid_native_names_are_not_rewritten(name: str) -> None:
    with pytest.raises(ContractError, match="Native skill names"):
        native_skill_name(name)


def test_skill_identity_is_not_its_package_alias() -> None:
    context = ImportedSkill(
        "package-alias", Path("SKILL.md"), "", "digest", "lock", context_name="actual-skill",
    )
    assert context_directory(context, 3) == ".agents/skills/actual-skill"


def test_instruction_indices_stay_stable() -> None:
    context = ImportedSkill("rules", Path("rules.md"), "", "digest", "lock", kind="instruction")
    assert context_directory(context, 3) == "_apmx_context/import-3"


@pytest.mark.parametrize("root", [".agents/skills", ".github/skills", ".claude/skills"])
def test_discovery_roots_are_recognized_case_insensitively(root: str) -> None:
    assert is_native_skill_path(root.upper() + "/decoy/SKILL.md")
    assert not is_native_skill_path(root + "-other/file.txt")


@pytest.mark.parametrize("output", [
    ".agents", ".agents/skills/new/SKILL.md", ".GITHUB/skills/new/SKILL.md",
    ".claude/skills", "_apmx_context/new.md",
])
def test_outputs_cannot_create_activation_content(tmp_path: Path, output: str) -> None:
    source = tmp_path / "job.contract.md"
    source.write_text(
        f"---\nneeds: notes.md\nproduces: {output}\nverify:\n  shape: 'true'\n---\nWrite a report.\n"
    )
    (tmp_path / "notes.md").write_text("input")
    with pytest.raises(ContractError, match="Output overlaps"):
        plan_contract(source, tmp_path, harness="copilot")


def test_inputs_cannot_smuggle_a_discovery_tree(tmp_path: Path) -> None:
    contract = LeafContract(
        tmp_path / "job.contract.md", "digest", "Write output.",
        (".github/skills/decoy/SKILL.md",), "out.txt", (),
    )
    plan = LeafPlan(contract, tmp_path, Path("/native/copilot"))
    with pytest.raises(ContractError, match="Inputs cannot activate project skills"):
        _selected_names(plan)


def test_context_destinations_have_one_static_owner() -> None:
    root = Path(__file__).resolve().parents[3] / "src/apmx"
    for relative in ("contracts/workspace.py", "runtime/copilot_runtime.py"):
        tree = ast.parse((root / relative).read_text())
        nodes = list(ast.walk(tree))
        assert any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "context_directory" for node in nodes
        )
        assert not any(
            isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "_apmx_context/import-" in node.value for node in nodes
        )
