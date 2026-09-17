"""Work-first consent, using the real presenter without model or package actions."""

import io
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from test_logger_presentation import _terminal
from test_terminal_pty import _read

from apmx.contracts.models import CheckSpec, LeafContract
from apmx.contracts.resolution import Edge, Graph
from apmx.core.contract_logger import ContractLogger

pytestmark = pytest.mark.component


def consent_graph(root):
    contracts = tuple(
        LeafContract(
            root / f"{name}.contract.md",
            "sha",
            "PRIVATE_PROMPT",
            needs,
            outputs,
            tuple(CheckSpec(check, f"check-{check}") for check in checks),
        )
        for name, needs, outputs, checks in (
            ("planning", ("request.md",), "plan.md", ("plan-sections",)),
            (
                "specification",
                ("plan.md",),
                "spec.md",
                ("specification-sections",),
            ),
            (
                "build",
                ("spec.md",),
                ("changes.diff", "implementation.md"),
                (
                    "shipping-examples",
                    "checkout-regression",
                    "implementation-report-sections",
                ),
            ),
            ("review", ("implementation.md",), "review.md", ("review-sections",)),
        )
    )
    edges = tuple(
        Edge(consumer.path, name, producer.path)
        for consumer in contracts
        for name in consumer.needs
        for producer in contracts
        if name in producer.outputs
    )
    return Graph(root, (contracts[-1].path,), contracts, contracts, edges)


@pytest.mark.parametrize("width", (40, 80, 120))
@pytest.mark.parametrize("mode", ("styled", "no_color", "dumb", "pipe"))
@pytest.mark.parametrize("verbose", (False, True))
@pytest.mark.parametrize("answer", ("y\n", "n\n"))
def test_work_precedes_single_disclosure_and_default_no(
    tmp_path, monkeypatch, width, mode, verbose, answer
):
    monkeypatch.chdir(tmp_path)
    graph = consent_graph(tmp_path / "feature-factory")
    logger = ContractLogger(verbose=verbose)
    monkeypatch.setattr(sys, "stdin", io.StringIO(answer))
    with _terminal(monkeypatch, width=width, mode=mode) as capture:
        logger.select_factory_root(graph.root)
        logger.render_factory_work(graph)
        assert logger.confirm_factory() is (answer == "y\n")
        logger.execution_context()
    raw = capture.text
    text = re.sub(r"\x1b\[[0-9;]*m", "", raw)
    words = " ".join(text.split())
    expected = [
        "Factory: feature-factory",
        "4 contracts / 5 artifacts / 6 planned checks",
        "Contract 1/4: planning",
        "Produces: plan.md",
        "Checks: plan-sections",
        "Contract 2/4: specification",
        "Produces: spec.md",
        "Contract 3/4: build",
        "Produces: changes.diff, implementation.md",
        "Checks: shipping-examples, checkout-regression, implementation-report-sections",
        "Contract 4/4: review",
        "Produces: review.md",
        "Evidence: will be saved under feature-factory/.apm/",
        "Required checks must pass before dependent work starts.",
        "Execution: local (not sandboxed)",
        "Agents and checks can access host files, network and available logins.",
        "Package dependencies may be installed; model usage may cost money.",
        "Run only contracts you trust.",
        "Run these 4 contracts with Copilot? [y/N]",
    ]
    position = 0
    for fragment in expected:
        position = words.index(fragment, position) + len(fragment)
    assert words.count("Execution:") == 1
    assert words.count("model usage may cost money") == 1
    assert ".contract.md" in words if verbose else ".contract.md" not in words
    assert ("check-shipping-examples" in words) is verbose
    for name in ("plan.md", "spec.md", "implementation.md"):
        assert (f"Input: {name} (from an earlier step)" in words) is verbose
        assert f"Input: {name} (starting file)" not in words
    assert ("Input: request.md (starting file)" in words) is verbose
    for forbidden in ("Final outputs:", "PASS", "COMPLETE", "PRIVATE_PROMPT"):
        assert forbidden not in words
    assert raw.isascii()
    assert not list(tmp_path.iterdir())
    if mode == "styled":
        assert "\x1b[1;36mContract 1/4: planning" in raw
        assert not re.search(r"\x1b\[[0-9;]*(?:32|33)m", raw)
    else:
        assert "\x1b" not in raw


@pytest.mark.parametrize("outputs", ("one.md", ("one.md", "two.md")))
def test_single_contract_grammar(tmp_path, monkeypatch, capsys, outputs):
    graph = consent_graph(tmp_path)
    contract = replace(graph.order[0], produces=outputs)
    graph = replace(graph, catalog=(contract,), order=(contract,), targets=(contract.path,))
    logger = ContractLogger()
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    logger.render_factory_work(graph)
    assert not logger.confirm_factory()
    output = capsys.readouterr().out
    count = 1 if isinstance(outputs, str) else 2
    assert f"1 contract / {count} artifact{'s' if count == 2 else ''} / 1 planned check" in output
    assert "Run this contract with Copilot? [y/N]" in output


def test_collision_names_match_execution(tmp_path, capsys):
    graph = consent_graph(tmp_path)
    contracts = tuple(
        replace(graph.order[0], path=tmp_path / folder / "planning.contract.md")
        for folder in ("first", "second")
    )
    graph = replace(graph, catalog=contracts, order=contracts)
    logger = ContractLogger()
    logger.select_factory_root(tmp_path)
    logger.render_factory_work(graph)
    preview = capsys.readouterr().out
    for index, contract in enumerate(contracts, 1):
        logger.chain_node(
            index, 2, contract.path, catalog=tuple(item.path for item in graph.catalog)
        )
    execution = capsys.readouterr().out
    headings = [line for line in preview.splitlines() if line.startswith("Contract ")]
    assert headings == [line for line in execution.splitlines() if line]
    assert headings == ["Contract 1/2: first/planning", "Contract 2/2: second/planning"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY fixture.")
@pytest.mark.parametrize("answer", (b"y\n", b"n\n"))
@pytest.mark.parametrize("width", (40, 80, 120))
def test_real_pty_confirmation_waits_for_answer(tmp_path, monkeypatch, answer, width):
    import fcntl
    import pty
    import struct
    import termios

    monkeypatch.setenv("APM_PROGRESS", "never")
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("CI", raising=False)
    actor = """
import sys
from pathlib import Path
sys.path[:0] = sys.argv[1:3]
from test_consent_presentation import consent_graph
from apmx.core.contract_logger import ContractLogger
logger = ContractLogger()
assert logger.can_confirm_factory()
logger.render_factory_work(consent_graph(Path.cwd() / "feature-factory"))
accepted = logger.confirm_factory()
logger.close()
raise SystemExit(0 if accepted else 21)
"""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, width, 0, 0))
    before = termios.tcgetattr(slave)
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            actor,
            str(Path(__file__).resolve().parents[3] / "src"),
            str(Path(__file__).resolve().parent),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
    )
    output = bytearray()
    try:
        deadline = time.monotonic() + 10
        while b"[y/N]" not in output and time.monotonic() < deadline:
            output.extend(_read(master))
        assert b"[y/N]" in output, output.decode("ascii")
        assert child.poll() is None
        text = re.sub(r"\x1b\[[0-9;]*m", "", output.decode("ascii"))
        assert text.index("Contract 4/4: review") < text.index("Execution: local")
        assert text.count("Execution:") == 1 and "Final outputs:" not in text
        os.write(master, answer)
        assert child.wait(timeout=10) == (0 if answer == b"y\n" else 21)
        assert termios.tcgetattr(slave) == before
        assert list(tmp_path.iterdir()) == []
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        os.close(master)
        os.close(slave)
