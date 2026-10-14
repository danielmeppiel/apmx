"""Prevent accidental runtime coupling to any installed canonical APM."""

import importlib.abc
import sys

import pytest


class _NoApm(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "apm_cli" or fullname.startswith("apm_cli."):
            raise AssertionError("Standalone apmx attempted to import installed APM.")


sys.meta_path.insert(0, _NoApm())


@pytest.fixture(autouse=True)
def _no_ambient_pull_request(monkeypatch) -> None:
    """`apmx audit` binds the PR of a GitHub event; CI's own PR must not leak into tests."""
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
