"""Prevent accidental runtime coupling to any installed canonical APM."""

import importlib.abc
import sys


class _NoApm(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "apm_cli" or fullname.startswith("apm_cli."):
            raise AssertionError("Standalone apmx attempted to import installed APM.")


sys.meta_path.insert(0, _NoApm())
