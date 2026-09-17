"""Four ordinary contracts with real leaf handoffs/checks; only production is replaced."""

import hashlib
import importlib.util
import json
import shlex
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_chain import caller, producer
from test_chain_sources import private_preparation
from test_software_factory_support import EXAMPLE, invoke, outputs, seed

from apmx.cli import main
from apmx.contracts import resolution, workspace
from apmx.utils.yaml_io import load_frontmatter_document

__all__ = ["caller", "private_preparation"]
pytestmark = pytest.mark.component

DELIVERIES = {
    "planning.contract.md": ("plan.md",),
    "specification.contract.md": ("specification.md",),
    "build.contract.md": ("changes.diff", "implementation.md"),
    "review.contract.md": ("review.md",),
}


def prepare_example(root: Path, renamed: bool = False) -> dict[str, tuple[str, ...]]:
    seed(root)
    deliveries = {}
    for index, (name, files) in enumerate(DELIVERIES.items()):
        path = root / "contracts" / name
        document = load_frontmatter_document(path)
        document.metadata["verify"] = {
            key: shlex.join([sys.executable, *shlex.split(command)[1:]])
            for key, command in document.metadata["verify"].items()
        }
        path.write_text(
            f"---\n{json.dumps(document.metadata)}\n---\n{document.body}",
            encoding="ascii",
            newline="\n",
        )
        if renamed:
            path = path.rename(path.with_name(f"stage-{9 - index}.contract.md"))
        deliveries[path.name] = files
    return deliveries


@pytest.mark.parametrize(
    "executable",
    [
        r"C:\Program Files\Python\python.exe",
        r"C:\Users\D'Angelo\Python\python.exe",
        "/opt/Python tools/python3",
        "/opt/D'Angelo/python3",
    ],
)
def test_factory_checks_preserve_shell_arguments_and_contract_body(
    tmp_path, monkeypatch, executable
):
    monkeypatch.setattr(sys, "executable", executable)
    root = tmp_path / "factory"
    prepare_example(root)
    for name in DELIVERIES:
        original = load_frontmatter_document(EXAMPLE / "contracts" / name)
        prepared = load_frontmatter_document(root / "contracts" / name)
        assert prepared.body == original.body
        assert prepared.metadata.keys() == original.metadata.keys()
        for key in original.metadata.keys() - {"verify"}:
            assert prepared.metadata[key] == original.metadata[key]
        assert prepared.metadata["verify"].keys() == original.metadata["verify"].keys()
        for key, command in original.metadata["verify"].items():
            assert shlex.split(prepared.metadata["verify"][key]) == [
                executable,
                *shlex.split(command)[1:],
            ]


@pytest.mark.skipif(
    importlib.util.find_spec("behave") is None,
    reason="Optional example integration: install apmx[factory] (Behave==1.3.3).",
)
@pytest.mark.parametrize("interactive,renamed", [(False, False), (True, False), (False, True)])
def test_factory_native_preview_and_four_real_leaf_handoffs(
    caller: Path,
    monkeypatch: pytest.MonkeyPatch,
    private_preparation: None,
    interactive: bool,
    renamed: bool,
) -> None:
    from apmx.core.contract_logger import ContractLogger

    deliveries = prepare_example(caller, renamed)
    expected = outputs(caller.parent)
    original = {
        path.relative_to(caller).as_posix(): path.read_bytes()
        for path in caller.rglob("*")
        if path.is_file()
    }
    monkeypatch.chdir(caller.parent)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: interactive)

    def produce(plan, *_):
        files = plan.contract.outputs
        assert files == deliveries[plan.contract.path.name]
        writes = "\n".join(f"Path({name!r}).write_bytes({expected[name]!r})" for name in files)
        scratch = ""
        if "changes.diff" in files:
            scratch = (
                "Path('src/pricing.py').write_text('# private working copy, not a delivery\\n')\n"
                "Path('src/checkout.py').write_text('# private working copy, not a delivery\\n')\n"
                "Path('private-scratch.txt').write_text('not a published artifact')\n"
            )
        return "from pathlib import Path\n" + scratch + writes

    calls = producer(monkeypatch, body=produce)
    preview = CliRunner().invoke(main, [str(caller), "--on", "copilot", "--plan"])
    assert preview.exit_code == 0 and "4 contracts" in preview.output, preview.output
    assert calls == [] and not (caller / ".apm").exists()
    result = CliRunner().invoke(
        main,
        [
            str(caller),
            "--on",
            "copilot",
            *([] if interactive else ["--allow-host-access", "--allow-unproven-inputs"]),
        ],
        input="y\n",
    )
    assert result.exit_code == 0 and len(calls) == 4, result.output
    aggregate = next((caller / ".apm/chains").glob("*/record.json"))
    data = json.loads(aggregate.read_bytes())
    assert data["complete"] is True and data["result"]["outcome"]["name"] == "COMPLETE"
    assert data["consent_source"] == ("interactive" if interactive else "flag")
    assert Path.cwd() == caller.parent and not (caller.parent / ".apm").exists()
    previous = {}
    for plan, snapshot, directory in calls:
        published = {
            path.relative_to(directory / "artifacts").as_posix(): path.read_bytes()
            for path in (directory / "artifacts").rglob("*")
            if path.is_file()
        }
        assert published == {name: expected[name] for name in plan.contract.outputs}
        for name in plan.contract.needs:
            if name in previous:
                assert (snapshot.root / name).read_bytes() == previous[name]
        previous.update(published)
    assert previous == expected
    assert original == {name: (caller / name).read_bytes() for name in original}
    assert all(not (caller / name).exists() for name in expected)
    assert not (caller / "private-scratch.txt").exists()
    view = Path(data["artifacts"]["root"])
    assert view == aggregate.parent / "artifacts"
    initial = {
        "request.md",
        "src/__init__.py",
        "src/pricing.py",
        "src/checkout.py",
        "tests/test_checkout.py",
        *(name for name in original if name.startswith("checks/")),
    }
    assert {item["relative_path"] for item in data["artifacts"]["files"]} == {*expected, *initial}
    for item in data["artifacts"]["files"]:
        raw = (view / item["relative_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        assert len(raw) == item["size"]
    for captured in data["artifacts"]["sources"]:
        origin = Path(captured["source_root"]) / captured["source_relative_path"]
        assert origin.read_bytes() == (view / captured["entry"]["relative_path"]).read_bytes()
        assert origin.is_relative_to(caller / ".apm/runs")
    for checker in ("acceptance.py", "regression.py"):
        code, report = invoke(view, checker, "changes.diff")
        assert (
            code == 0
            and report["subject"]["patch"] == hashlib.sha256(expected["changes.diff"]).hexdigest()
        ), report
    assert (view / "src/pricing.py").read_bytes() == original["src/pricing.py"]


def test_factory_graph_follows_exact_artifact_edges_after_renaming(caller: Path) -> None:
    deliveries = prepare_example(caller, renamed=True)
    graph = resolution.resolve_factory(caller)
    assert [item.path.name for item in graph.order] == list(deliveries)
    assert [item.outputs for item in graph.order] == list(deliveries.values())
    assert {(edge.name, edge.producer.name, edge.consumer.name) for edge in graph.edges} == {
        ("plan.md", "stage-9.contract.md", "stage-8.contract.md"),
        ("specification.md", "stage-8.contract.md", "stage-7.contract.md"),
        ("specification.md", "stage-8.contract.md", "stage-6.contract.md"),
        ("changes.diff", "stage-7.contract.md", "stage-6.contract.md"),
        ("implementation.md", "stage-7.contract.md", "stage-6.contract.md"),
    }
    prepared = resolution.preflight(graph, caller, harness="copilot")
    assert all(workspace.inspect_workspace(node.plan) == node.inventory for node in prepared.nodes)
    assert not (EXAMPLE / "run.py").exists() and not (EXAMPLE / "evidence.py").exists()
