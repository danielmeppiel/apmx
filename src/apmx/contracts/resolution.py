"""Bounded file-derived contract closure and offline whole-graph admission."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from ..utils.path_security import ensure_path_within, has_symlink_component, is_link_or_reparse
from . import frontend, workspace
from .models import ContractError, ContractLimits, ContractSource, FileEntry, LeafContract, LeafPlan

EXCLUDED = frozenset(
    {
        ".git",
        ".apm",
        "apm_modules",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".agents",
        ".github",
        ".claude",
        ".copilot",
        ".codex",
        ".cursor",
        ".opencode",
        "vendor",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
    }
)


@dataclass(frozen=True)
class Edge:
    consumer: Path
    name: str
    producer: Path


@dataclass(frozen=True)
class Graph:
    root: Path
    targets: tuple[Path, ...]
    catalog: tuple[LeafContract, ...]
    order: tuple[LeafContract, ...]
    edges: tuple[Edge, ...]

    def inputs(self, contract: LeafContract) -> tuple[str, ...]:
        return tuple(edge.name for edge in self.edges if edge.consumer == contract.path)


@dataclass(frozen=True)
class Node:
    plan: LeafPlan
    inventory: tuple[FileEntry, ...]


@dataclass(frozen=True)
class ChainPlan:
    graph: Graph
    nodes: tuple[Node, ...]
    allow_unproven_inputs: bool = False


def discover(root: Path, limits: ContractLimits) -> tuple[LeafContract, ...]:
    """Read ordinary source contracts, never installed or harness-managed context."""
    pending = [root]
    names = []
    count = 0
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                count += 1
                if count > limits.baseline_files:
                    raise ContractError(
                        "Contract discovery exceeds the entry limit; narrow the source.",
                        code="graph_limit",
                    )
                if entry.name.casefold() in EXCLUDED:
                    continue
                path = Path(entry.path)
                if is_link_or_reparse(path):
                    raise ContractError(
                        "Contract discovery contains a symlink/reparse path.", code="source_escape"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.name.endswith(".contract.md"):
                    names.append(path)
                    if len(names) > limits.chain_contracts:
                        raise ContractError(
                            f"At most {limits.chain_contracts} source contracts may be discovered.",
                            code="graph_limit",
                        )
    names.sort(key=lambda path: path.relative_to(root).as_posix())
    if len({path.relative_to(root).as_posix().casefold() for path in names}) != len(names):
        raise ContractError("Case-colliding contract sources.", code="source_collision")
    return tuple(frontend.parse_contract(path, limits=limits) for path in names)


def _overlap(left: str, right: str) -> bool:
    left, right = left.casefold(), right.casefold()
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def resolve(
    target: Path,
    caller: Path,
    *,
    source: ContractSource | None = None,
    limits: ContractLimits | None = None,
) -> Graph:
    """Infer a backwards closure from declared file identities, not names or mtimes."""
    limits = limits or ContractLimits()
    root = source.root if source else caller.resolve()
    selected = target if target.is_absolute() else root / target
    try:
        ensure_path_within(selected, root)
    except ValueError as exc:
        raise ContractError(
            "Selected contract escapes the original source.", code="source_escape"
        ) from exc
    if has_symlink_component(root, selected):
        raise ContractError("Selected contract contains a symlink.", code="source_escape")
    catalog = discover(root, limits)
    return _resolve_catalog(root, caller, catalog, selected)


def select_factory_root(root: Path) -> Path:
    """Select the actual local caller root without changing process cwd."""
    root = root.expanduser().absolute()
    if has_symlink_component(Path(root.anchor), root) or not root.is_dir():
        raise ContractError(
            "Select a regular factory directory, without symlinks.", code="invalid_factory"
        )
    return root.resolve()


def resolve_factory(root: Path, *, limits: ContractLimits | None = None) -> Graph:
    """Resolve every local factory contract through the union of its derived sinks."""
    limits = limits or ContractLimits()
    root = select_factory_root(root)
    catalog = discover(root, limits)
    if not catalog:
        raise ContractError(
            "The factory contains no ordinary .contract.md files.", code="empty_factory"
        )
    return _resolve_catalog(root, root, catalog, None)


def _resolve_catalog(
    root: Path,
    caller: Path,
    catalog: tuple[LeafContract, ...],
    selected: Path | None,
) -> Graph:
    indexed = {item.path: item for item in catalog}
    if selected is not None and selected not in indexed:
        raise ContractError(
            "Selected contract is outside ordinary discoverable source.", code="missing_contract"
        )
    producers: dict[str, LeafContract] = {}
    for contract in catalog:
        for name in contract.outputs:
            key = name.casefold()
            if key in producers:
                raise ContractError(
                    f"Ambiguous producers for {name}: "
                    f"{producers[key].path.name}, {contract.path.name}. Select a unique source catalog.",
                    code="ambiguous_producer",
                )
            producers[key] = contract
    consumed = {
        producers[name.casefold()].path
        for contract in catalog
        for name in contract.needs
        if name.casefold() in producers
    }
    targets = (
        (selected,)
        if selected is not None
        else tuple(contract.path for contract in catalog if contract.path not in consumed)
    )
    active: set[Path] = set()
    visited: set[Path] = set()
    ordered = []
    edges = []

    def visit(contract: LeafContract) -> None:
        if contract.path in active:
            raise ContractError(
                f"Contract cycle reaches {contract.path.name}. Fix its needs.",
                code="contract_cycle",
            )
        if contract.path in visited:
            return
        active.add(contract.path)
        for name in sorted(contract.needs):
            producer = producers.get(name.casefold())
            if producer is not None:
                if name not in producer.outputs:
                    raise ContractError(
                        "Producer/input case differs; use one exact file spelling.",
                        code="source_collision",
                    )
                visit(producer)
                edges.append(Edge(contract.path, name, producer.path))
            else:
                frontend._regular(caller / name, caller, contract.locations["needs"])
        active.remove(contract.path)
        visited.add(contract.path)
        ordered.append(contract)

    for target in targets:
        visit(indexed[target])
    if selected is None:
        # A disconnected cyclic component has no sink; it must not disappear.
        for contract in catalog:
            visit(contract)
    outputs = [name for item in ordered for name in item.outputs]
    roots = {name for item in ordered for name in item.needs if name.casefold() not in producers}
    if len({name.casefold() for name in roots}) != len(roots):
        raise ContractError("Case-colliding caller input paths.", code="source_collision")
    protected = roots | {item.path.relative_to(root).as_posix() for item in catalog}
    if any(_overlap(a, b) for index, a in enumerate(outputs) for b in outputs[index + 1 :]):
        raise ContractError("Produced paths collide or overlap.", code="source_collision")
    if any(_overlap(output, name) for output in outputs for name in protected):
        raise ContractError(
            "Produced paths overlap original inputs or contract sources.", code="source_collision"
        )
    return Graph(root, targets, catalog, tuple(ordered), tuple(edges))


def preflight(
    graph: Graph,
    caller: Path,
    *,
    harness: str,
    model: str | None = None,
    source: ContractSource | None = None,
    limits: ContractLimits | None = None,
    imports_root: Path | None = None,
    apm_backend: Mapping[str, str] | None = None,
    allow_unproven_inputs: bool = False,
) -> ChainPlan:
    """Admit every selected leaf's known surface without making future-input files."""
    limits = limits or ContractLimits()
    nodes = []
    outputs = tuple(name for item in graph.order for name in item.outputs)
    for contract in graph.order:
        selected_source = (
            replace(
                source, contract_relative_path=contract.path.relative_to(source.root).as_posix()
            )
            if source
            else None
        )
        plan = frontend.plan_contract(
            contract.path,
            caller,
            harness=harness,
            model=model,
            limits=limits,
            source=selected_source,
            imports_root=imports_root,
            apm_backend=apm_backend,
            deferred_inputs=graph.inputs(contract),
            chain_outputs=outputs,
        )
        if plan.contract != contract:
            raise ContractError("Contract changed during graph admission.", code="plan_changed")
        from .check_command import check_argv

        for check in contract.checks:
            check_argv(check.command)
        nodes.append(Node(plan, workspace.inspect_workspace(plan)))
    if discover(graph.root, limits) != graph.catalog:
        raise ContractError("Contract catalog changed during admission.", code="plan_changed")
    return ChainPlan(graph, tuple(nodes), allow_unproven_inputs)
