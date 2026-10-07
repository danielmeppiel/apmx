"""Pure layout helpers for the real resolved contract graph.

These functions derive a left-to-right, level-by-level layout from
``resolution.Graph`` so the widget can render forks and joins without
inventing dependencies the graph does not declare. No execution state is
produced here; cards start "pending" until genuine engine events (milestone 3
of docs/textual-design.md) report otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..contracts.models import LeafContract
from ..contracts.resolution import Graph


@dataclass(frozen=True)
class ContractNode:
    """One card's static, declared identity -- never a claimed execution state."""

    identity: str
    needs: tuple[str, ...]
    produces: tuple[str, ...]
    checks: tuple[str, ...]
    path: Path


def contract_identity(root: Path, contract: LeafContract, catalog: tuple[LeafContract, ...]) -> str:
    """A unique basename, or a root-relative path when names collide."""
    basenames = [item.path.name for item in catalog]
    if basenames.count(contract.path.name) > 1:
        return contract.path.relative_to(root).as_posix()
    return contract.path.name


def build_nodes(graph: Graph) -> tuple[ContractNode, ...]:
    """One node per catalog contract, in the graph's admitted topological order."""
    return tuple(
        ContractNode(
            identity=contract_identity(graph.root, contract, graph.catalog),
            needs=contract.needs,
            produces=contract.outputs,
            checks=tuple(check.name for check in contract.checks),
            path=contract.path,
        )
        for contract in graph.order
    )


def levels(graph: Graph) -> tuple[tuple[ContractNode, ...], ...]:
    """Group nodes into left-to-right levels from real producer/consumer edges.

    A node's level is one past the deepest level of any contract that produces
    one of its declared ``needs``. Independent roots share level 0; forks and
    joins fall out of the max() over real edges, not an assumed single chain.
    """
    nodes = build_nodes(graph)
    by_path = {contract.path: node for contract, node in zip(graph.order, nodes, strict=True)}
    producers_of: dict[Path, list[Path]] = {}
    for edge in graph.edges:
        producers_of.setdefault(edge.consumer, []).append(edge.producer)
    depth: dict[Path, int] = {}

    def depth_of(path: Path) -> int:
        if path in depth:
            return depth[path]
        producers = producers_of.get(path, [])
        value = 0 if not producers else max(depth_of(producer) for producer in producers) + 1
        depth[path] = value
        return value

    grouped: dict[int, list[ContractNode]] = {}
    for contract in graph.order:
        grouped.setdefault(depth_of(contract.path), []).append(by_path[contract.path])
    return tuple(tuple(grouped[level]) for level in sorted(grouped))
