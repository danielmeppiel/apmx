"""Five ordinary contracts exercised through native APMX chaining, not a wrapper."""

import json
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from apmx.cli import main
from apmx.contracts import resolution, workspace
from test_chain import caller as caller, producer
from test_chain_sources import private_preparation as private_preparation
from test_software_factory_support import EXAMPLE, Modules, outputs
from test_software_factory_support import factory as factory

pytestmark = pytest.mark.component


@pytest.mark.parametrize("interactive", (False, True))
def test_factory_native_preview_and_five_real_leaf_handoffs(
    caller: Path,
    factory: Modules,
    monkeypatch: pytest.MonkeyPatch,
    private_preparation: None,
    interactive: bool,
) -> None:
    from apmx.core.contract_logger import ContractLogger

    source = caller
    shutil.copytree(EXAMPLE / "contracts", source / "contracts")
    shutil.copytree(EXAMPLE / "checks", source / "checks")
    shutil.copyfile(EXAMPLE / "request.json", caller / "request.json")
    monkeypatch.chdir(caller.parent)
    monkeypatch.setattr(ContractLogger, "can_confirm_factory", lambda self: interactive)
    expected = outputs(factory)
    original = {
        p.relative_to(caller).as_posix(): p.read_bytes() for p in caller.rglob("*") if p.is_file()
    }
    calls = producer(
        monkeypatch,
        body=lambda plan, *_: (
            "from pathlib import Path\n"
            f"Path({plan.contract.produces!r}).write_bytes({expected[plan.contract.produces]!r})"
        ),
    )
    preview = CliRunner().invoke(
        main,
        [str(source), "--on", "copilot", "--plan"],
    )
    assert preview.exit_code == 0 and "5 contracts" in preview.output, preview.output
    assert calls == [] and not (caller / ".apm").exists()
    result = CliRunner().invoke(
        main,
        [
            str(source),
            "--on",
            "copilot",
            *([] if interactive else ["--allow-host-access", "--allow-unproven-inputs"]),
        ],
        input="y\n",
    )
    assert result.exit_code == 21 and len(calls) == 5, result.output
    aggregate = next((caller / ".apm/chains").glob("*/record.json"))
    data = json.loads(aggregate.read_bytes())
    assert data["complete"] is True and data["result"]["outcome"]["name"] == "UNPROVEN"
    assert data["consent_source"] == ("interactive" if interactive else "flag")
    assert Path.cwd() == caller.parent
    assert not (caller.parent / ".apm").exists()
    previous = {}
    for plan, snapshot, directory in calls:
        raw = (directory / "artifacts" / plan.contract.produces).read_bytes()
        assert raw == expected[plan.contract.produces]
        for name in plan.contract.needs:
            if name in previous:
                assert (snapshot.root / name).read_bytes() == previous[name]
        previous[plan.contract.produces] = raw
    review = json.loads(previous["review.json"])
    assert review["recommendation"] == "follow_up"
    assert not (caller / "evidence.json").exists()
    assert all(not (caller / name).exists() for name in expected)
    assert original == {name: (caller / name).read_bytes() for name in original}
    view = Path(data["artifacts"]["root"])
    assert view == aggregate.parent / "artifacts"
    assert {item["relative_path"] for item in data["artifacts"]["files"]} == {
        *expected,
        "request.json",
        "checks/verify.py",
    }
    for item in data["artifacts"]["files"]:
        raw = (view / item["relative_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        assert len(raw) == item["size"]
    for captured in data["artifacts"]["sources"]:
        origin = Path(captured["source_root"]) / captured["source_relative_path"]
        assert origin.read_bytes() == (view / captured["entry"]["relative_path"]).read_bytes()
        assert origin.is_relative_to(caller / ".apm/runs")
    for phase in ("quote", "test"):
        checked = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(view / "checks/verify.py"),
                phase,
                "--directory",
                str(view),
                *(["--weight", "1001"] if phase == "quote" else []),
            ],
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert checked.returncode == 0, checked.stdout
        if phase == "quote":
            assert json.loads(checked.stdout) == {"returns": 700}


def test_factory_graph_follows_artifact_edges_after_renaming(caller: Path) -> None:
    shutil.copytree(EXAMPLE / "contracts", caller / "contracts")
    shutil.copytree(EXAMPLE / "checks", caller / "checks")
    shutil.copyfile(EXAMPLE / "request.json", caller / "request.json")
    names = ["planning", "specification", "build", "test", "review"]
    for index, name in enumerate(names):
        (caller / f"contracts/{name}.contract.md").rename(
            caller / f"contracts/{9 - index}.contract.md"
        )
    graph = resolution.resolve_factory(caller)
    assert [item.produces for item in graph.order] == [
        "plan.json",
        "spec.json",
        "shipping.py",
        "tests.json",
        "review.json",
    ]
    plan = resolution.preflight(graph, caller, harness="copilot")
    assert all(workspace.inspect_workspace(node.plan) == node.inventory for node in plan.nodes)
    assert not (EXAMPLE / "run.py").exists() and not (EXAMPLE / "evidence.py").exists()
