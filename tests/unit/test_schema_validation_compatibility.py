"""Compatibility guards for schema errors and subprocess option forwarding."""

import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from apmx.contracts import process_windows
from apmx.contracts.imports import _package
from apmx.contracts.models import ContractError, ProcessRequest
from apmx.models import apm_package
from apmx.models.apm_package import APMPackage
from apmx.models.dependency.reference import DependencyReference
from apmx.models.dependency.subsets import parse_skill_subset, parse_target_subset
from apmx.utils import subprocess_env

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "parser,field", [(parse_skill_subset, "skills"), (parse_target_subset, "targets")]
)
def test_subset_schema_errors_keep_distinct_value_errors(
    parser: Callable[[object], list[str]], field: str
) -> None:
    with pytest.raises(ValueError, match=rf"'{field}' field must be a list"):
        parser("not-a-list")
    with pytest.raises(ValueError, match=rf"{field}: must contain at least one"):
        parser([])


@pytest.mark.parametrize("key", ["dependencies", "devDependencies"])
@pytest.mark.parametrize(
    "value,suffix", [(42, "must be a mapping."), ({"apm": "not-a-list"}, ".apm must be a list.")]
)
def test_manifest_schema_errors_keep_value_error(key: str, value: object, suffix: str) -> None:
    message = f"{key}{suffix}" if suffix.startswith(".") else f"{key} {suffix}"
    with pytest.raises(ValueError, match=re.escape(message)):
        APMPackage.from_mapping(
            {"name": "fixture", "version": "1.0.0", key: value},
            package_path=Path("fixture"),
        )


def test_import_manifest_wraps_schema_value_error() -> None:
    with pytest.raises(ContractError) as caught:
        _package(b"[]", Path("fixture"))
    assert caught.value.code == "invalid_manifest"
    assert type(caught.value.__cause__) is ValueError


def test_dependency_schema_error_and_reexport_remain_compatible() -> None:
    assert apm_package.DependencyReference is DependencyReference
    with pytest.raises(ValueError, match="'allow_insecure' field must be a boolean"):
        DependencyReference.parse_from_dict(
            {"git": "https://github.com/example/package.git", "allow_insecure": "yes"}
        )


@pytest.mark.parametrize(
    "argv,env,message",
    [
        ((42,), {}, "Native argv must contain non-NUL strings."),
        (("agent.exe",), {"KEY": 42}, "Invalid native environment entry."),
        (("agent.exe",), {42: "value"}, "Invalid native environment entry."),
    ],
)
def test_windows_command_schema_errors_keep_value_error(
    monkeypatch: pytest.MonkeyPatch, argv: object, env: object, message: str
) -> None:
    monkeypatch.setattr(subprocess_env.sys, "frozen", False, raising=False)
    monkeypatch.setattr(process_windows.shutil, "which", Mock(return_value=r"C:\tools\agent.exe"))
    valid = ProcessRequest(("agent.exe",), Path("fixture"), 1, {})
    request = replace(valid, argv=argv, env=env)
    with pytest.raises(ValueError, match=re.escape(message)):
        process_windows._command(request)


@pytest.mark.parametrize(
    "options,expected", [({}, False), ({"check": False}, False), ({"check": True}, True)]
)
def test_external_probe_forwards_check_without_changing_default(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, bool], expected: bool
) -> None:
    monkeypatch.setattr(subprocess_env.sys, "frozen", False, raising=False)
    runner = Mock()
    monkeypatch.setattr(subprocess_env.subprocess, "run", runner)
    result = subprocess_env.run_external(["tool"], env={}, **options)
    assert result is runner.return_value
    assert runner.call_args.kwargs["check"] is expected
    assert runner.call_args.kwargs["env"] == {}
