"""Static ownership guard: the Textual prototype renders a declared graph and
reactive status it is handed; it must never import the modules that execute
work or decide outcomes (apmx.contracts.chain/engine/records/process*), and
must never own color/env capability policy that already belongs to
utils.console (see docs/textual-design.md, "Canonical engine and
ContractLogger own semantics").
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

ROOT = Path(__file__).resolve().parents[3] / "src/apmx"
TUI = ROOT / "tui"

FORBIDDEN_MODULE_PREFIXES = (
    "apmx.contracts.chain",
    "apmx.contracts.engine",
    "apmx.contracts.records",
    "apmx.contracts.process",
    "apmx.contracts.workspace",
)
ALLOWED_CONTRACTS_IMPORTS = {
    "apmx.contracts.models",
    "apmx.contracts.resolution",
}


def _import_module(node: ast.ImportFrom) -> str:
    """Resolve a relative import inside apmx/tui/<file>.py (fixed depth 2)."""
    if not node.level:
        return node.module or ""
    # level=1 ("from .x") -> apmx.tui; level=2 ("from ..x") -> apmx
    package = ["apmx", "tui"][: 3 - node.level]
    return ".".join([*package, *(node.module or "").split(".")]).rstrip(".")


def _imported_modules(tree: ast.Module) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _import_module(node)
            modules.append(base)
            modules.extend(f"{base}.{item.name}" for item in node.names)
    return modules


@pytest.mark.parametrize("path", sorted(TUI.glob("*.py")), ids=lambda p: p.name)
def test_tui_module_never_imports_execution_or_outcome_owners(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = _imported_modules(tree)
    violations = [
        name
        for name in modules
        for prefix in FORBIDDEN_MODULE_PREFIXES
        if name == prefix or name.startswith(prefix + ".")
    ]
    assert not violations, (
        f"{path.name} imports execution/outcome-owning modules {violations}; the TUI must "
        "only read declared structure (resolution.Graph) and reactive status it is handed, "
        "never execute work or decide outcomes itself."
    )


@pytest.mark.parametrize("path", sorted(TUI.glob("*.py")), ids=lambda p: p.name)
def test_tui_module_restricted_to_allowed_contracts_surface(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    contracts_imports = {
        name
        for name in _imported_modules(tree)
        if name.startswith("apmx.contracts.") and name.count(".") == 2
    }
    assert contracts_imports <= ALLOWED_CONTRACTS_IMPORTS, (
        f"{path.name} imports {contracts_imports - ALLOWED_CONTRACTS_IMPORTS} from "
        "apmx.contracts beyond the declared-structure/types surface it is allowed to read."
    )


def test_tui_module_never_hardcodes_capability_or_color_env_policy() -> None:
    """NO_COLOR/CI/TERM detection and ANSI/color literals belong to
    utils.console, not duplicated inline in the TUI (docs/textual-design.md
    "existing redaction/privacy" + fallback requirements)."""
    capability_keys = {"NO_COLOR", "CI", "TERM", "APM_PROGRESS"}
    for path in sorted(TUI.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"getenv", "get"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in capability_keys
            ):
                pytest.fail(
                    f"{path.name}:{node.lineno}: reads {node.args[0].value!r} directly; "
                    "use apmx.utils.console's existing capability detection instead."
                )
