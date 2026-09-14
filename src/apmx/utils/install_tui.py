"""Canonical animation eligibility without an installer."""

import os

from .console import _get_console


def should_animate() -> bool:
    """Return True iff the install pipeline should paint a Live region.

    Resolution order (first match wins):

    1. ``APM_PROGRESS=never`` or ``=quiet`` -- never animate.
    2. ``APM_PROGRESS=always`` -- always animate (intended for local
       debugging; CI MUST NOT set this).
    3. ``APM_PROGRESS=auto`` (default) -- animate iff the console is
       an interactive TTY AND ``TERM`` is not ``""`` / ``"dumb"`` AND
       ``CI`` is not truthy.

    The function intentionally does NOT consult ``--quiet`` itself;
    the CLI front-end is responsible for setting ``APM_PROGRESS=quiet``
    (or never instantiating ``InstallTui``) in that case.
    """
    mode = os.environ.get("APM_PROGRESS", "auto").strip().lower()
    if mode in ("never", "quiet", "off", "0", "false", "no"):
        return False
    if mode in ("always", "on", "1", "true", "yes"):
        return True
    # mode == "auto" (or unrecognized -- treat as auto)
    if os.environ.get("CI", "").strip().lower() in ("1", "true", "yes"):
        return False
    if os.environ.get("TERM", "").strip().lower() in ("", "dumb"):
        return False
    c = _get_console()
    if c is None:
        return False
    return bool(getattr(c, "is_terminal", False)) and bool(getattr(c, "is_interactive", False))
