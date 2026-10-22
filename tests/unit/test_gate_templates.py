"""The shipped gate workflows run from the base branch and never execute PR code."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ["examples/accept-factory-prs/audit.yml", "examples/factory-platform/audit.yml"]
HEAD = "${{ github.event.pull_request.head.sha }}"


def _load(name: str) -> dict:
    workflow = yaml.safe_load((ROOT / name).read_text())
    workflow["on"] = workflow.pop(True, workflow.get("on"))  # YAML 1.1 reads `on` as true
    return workflow


@pytest.mark.parametrize("name", TEMPLATES)
def test_the_gate_runs_the_base_branch_copy_of_itself(name) -> None:
    # pull_request would run the PR's own copy, which the PR can edit to `exit 0`.
    workflow = _load(name)
    assert workflow["on"] == {
        "pull_request_target": {"types": ["opened", "synchronize", "reopened"]}
    }
    assert workflow["permissions"] == {"contents": "read", "attestations": "read"}
    (job,) = workflow["jobs"].values()
    assert "if" not in job, "a skipped required job counts as passed"
    assert "permissions" not in job


@pytest.mark.parametrize("name", TEMPLATES)
def test_the_gate_checks_out_pr_bytes_and_executes_only_apmx(name) -> None:
    (job,) = _load(name)["jobs"].values()
    steps = job["steps"]
    checkouts = [
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    (checkout,) = checkouts
    assert checkout["with"] == {"ref": HEAD, "fetch-depth": 0, "persist-credentials": False}
    runs = [step["run"] for step in steps if "run" in step]
    assert runs == ["apmx audit"], "nothing else may run in the PR's checkout"
    assert all("working-directory" not in step for step in steps)
    actions = [str(step["uses"]).split("@")[0] for step in steps if "uses" in step]
    assert set(actions) <= {
        "actions/checkout",
        "actions/create-github-app-token",
        "danielmeppiel/apmx/.github/actions/setup",
    }
    assert actions.index("actions/checkout") < actions.index(
        "danielmeppiel/apmx/.github/actions/setup"
    )
