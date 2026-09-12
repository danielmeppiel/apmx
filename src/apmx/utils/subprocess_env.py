"""Environment helpers for spawning external processes from the frozen CLI.

When APM ships as a PyInstaller ``--onedir`` binary, the bootloader prepends
the bundle's ``_internal`` directory to ``LD_LIBRARY_PATH`` (Linux) and the
``DYLD_*`` variables (macOS) so that the main Python process can locate its
own shared libraries.  Child processes inherit this environment by default,
which causes system binaries -- ``git``, ``curl``, the install script, ... --
to resolve their dependencies against the bundled libraries.  When a bundled
library predates the system caller's ABI requirements, the child aborts with
a symbol lookup error.  This has produced two user-visible regressions:

* #462: ``apm`` → ``git`` → ``git-remote-https`` on Fedora 43
  (``OPENSSL_3.2.0 not found``).
* #894: ``apm update`` → ``install.sh`` → system ``curl`` on Debian trixie
  arm64 dev-containers (``OPENSSL_3.2.0 / OPENSSL_3.3.0 not found``).

PyInstaller saves each rewritten variable's pre-launch value under
``<NAME>_ORIG``.  The canonical mitigation, documented in PyInstaller's
runtime notes, is to restore those values on the child environment before
spawning -- not to blindly ``pop`` the variables, because a user may have
legitimately exported ``LD_LIBRARY_PATH`` themselves (CUDA, Nix, custom
toolchains).  This module centralises that restoration in one audited
helper so every subprocess call site gets identical, correct semantics.

Typical use::

    from apmx.utils.subprocess_env import external_process_env

    subprocess.run(cmd, env=external_process_env(), check=False)
"""

from __future__ import annotations

import os
import sys
import subprocess
import threading
from contextlib import contextmanager
from collections.abc import Mapping

# Runtime-library search-path variables that PyInstaller's bootloader
# rewrites at launch.  Each has a sibling ``<NAME>_ORIG`` holding the
# pre-launch value that we must restore before handing env to a child
# process.  The tuple is intentionally narrow: we do not touch ``PATH``
# or other inherited variables, only the ones PyInstaller itself manages.
_PYINSTALLER_MANAGED_LIBRARY_VARS: tuple[str, ...] = (
    "LD_LIBRARY_PATH",  # Linux and most Unixes
    "DYLD_LIBRARY_PATH",  # macOS dynamic library search path
    "DYLD_FRAMEWORK_PATH",  # macOS framework search path
)
_DLL_SEARCH_LOCK = threading.RLock()


@contextmanager
def external_dll_search():
    """Temporarily remove the frozen Windows DLL directory during child creation."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    with _DLL_SEARCH_LOCK:
        ctypes.set_last_error(0)
        size = kernel.GetDllDirectoryW(0, None)
        if not size and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        previous = ctypes.create_unicode_buffer(size + 1)
        if size:
            ctypes.set_last_error(0)
            copied = kernel.GetDllDirectoryW(len(previous), previous)
            if not copied and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not kernel.SetDllDirectoryW(previous.value or None):
                raise ctypes.WinError(ctypes.get_last_error())


def run_external(*args, **kwargs):
    """Run bounded authentication probes with external loader state."""
    kwargs["env"] = external_process_env(kwargs.get("env"))
    with external_dll_search():
        return subprocess.run(*args, **kwargs)


def popen_external(*args, **kwargs):
    """Spawn an external probe with shared frozen-loader state handling."""
    kwargs["env"] = external_process_env(kwargs.get("env"))
    with external_dll_search():
        return subprocess.Popen(*args, **kwargs)


def external_process_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment dict safe for spawning external system binaries.

    Args:
        base: Optional source mapping.  Defaults to ``os.environ``.  The
            returned dict is always an independent copy -- mutating it
            never touches the live process environment.

    Behaviour:
        * When **not** running as a PyInstaller-frozen binary the base env
          is returned as a fresh ``dict`` with no other modifications.
        * When frozen, every library-path variable listed in
          :data:`_PYINSTALLER_MANAGED_LIBRARY_VARS` is restored from its
          ``<NAME>_ORIG`` sibling (preserving the user's own exports); if
          no ``_ORIG`` sibling exists the variable is removed entirely so
          the child does not inherit the bundle's ``_internal`` path.  The
          ``_ORIG`` keys themselves are stripped so we do not leak
          PyInstaller internals to the child.

    This is the single source of truth for child-process environment
    sanitisation in the CLI; prefer it over per-call-site dict surgery.
    """
    env: dict[str, str] = dict(base if base is not None else os.environ)

    if not getattr(sys, "frozen", False):
        return env

    for key in _PYINSTALLER_MANAGED_LIBRARY_VARS:
        orig_key = f"{key}_ORIG"
        if orig_key in env:
            env[key] = env[orig_key]
            env.pop(orig_key)
        else:
            env.pop(key, None)
    return env
