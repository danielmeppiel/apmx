"""Unit tests for pure DAG-leveling logic feeding the Textual preview.

Scope: apmx.tui.graph never executes anything or computes outcomes; these
tests exercise only identity/level derivation from a real resolved Graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apmx.contracts.resolution import resolve_factory
from apmx.tui.graph import build_nodes, contract_identity, levels

pytestmark = pytest.mark.unit

EXAMPLE_FACTORY = Path(__file__).resolve().parents[3] / "examples/contracts/branched-demo"


@pytest.fixture
def branched_graph(tmp_path):
    caller = tmp_path / "branched-demo"
    import shutil

    shutil.copytree(EXAMPLE_FACTORY, caller)
    return resolve_factory(caller)


def test_build_nodes_covers_every_catalog_entry(branched_graph):
    nodes = build_nodes(branched_graph)
    assert {node.identity for node in nodes} == {
        contract_identity(branched_graph.root, item, branched_graph.catalog)
        for item in branched_graph.catalog
    }


def test_levels_place_fork_siblings_together(branched_graph):
    grouped = levels(branched_graph)
    identities = [[node.identity for node in level] for level in grouped]
    assert identities[0] == ["plan.contract.md"]
    assert set(identities[1]) == {"design.contract.md", "spec.contract.md"}
    assert identities[2] == ["build.contract.md"]
    assert set(identities[3]) == {"docs.contract.md", "tests.contract.md"}
    assert identities[4] == ["review.contract.md"]


def test_levels_use_max_depth_across_all_producers_for_a_join(branched_graph):
    """build depends on both spec and design (level 1); it must sit one level
    below the deepest producer, not the first one encountered."""
    grouped = levels(branched_graph)
    build_level = next(
        i
        for i, level in enumerate(grouped)
        if any(n.identity == "build.contract.md" for n in level)
    )
    design_level = next(
        i
        for i, level in enumerate(grouped)
        if any(n.identity == "design.contract.md" for n in level)
    )
    spec_level = next(
        i for i, level in enumerate(grouped) if any(n.identity == "spec.contract.md" for n in level)
    )
    assert build_level == max(design_level, spec_level) + 1


def test_node_needs_and_produces_reflect_declared_graph(branched_graph):
    nodes = {node.identity: node for node in build_nodes(branched_graph)}
    build = nodes["build.contract.md"]
    assert set(build.needs) == {"specification.md", "design.md"}
    assert build.produces == ("changes.diff",)
    assert build.checks == ("build-shape",)
