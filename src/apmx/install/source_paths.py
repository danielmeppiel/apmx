"""Same-repository import expansion without an APM dependency graph."""

import posixpath
from dataclasses import replace
from pathlib import PureWindowsPath

from ..models.dependency.reference import DependencyReference
from ..utils.path_security import validate_path_segments


def expand_remote_import(
    parent: DependencyReference, child: DependencyReference
) -> DependencyReference:
    if parent.is_local or parent.source == "registry":
        raise ValueError("Same-repository imports require a Git parent.")
    if child.is_parent_repo_inheritance:
        path = child.virtual_path
        reference = child.reference if child.reference is not None else parent.reference
    else:
        raw = child.local_path or ""
        if not raw or PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive or raw.startswith(("/", "~")):
            raise ValueError("Remote packages cannot import absolute local paths.")
        path = posixpath.normpath(posixpath.join(parent.virtual_path or "", raw.replace("\\", "/")))
        if path == ".." or path.startswith("../"):
            raise ValueError("Remote import escapes its repository.")
        reference = parent.reference
    if path in ("", "."):
        path = None
    if path:
        validate_path_segments(path, context="same-repository import")
    return replace(
        parent, virtual_path=path, is_virtual=bool(path), reference=reference,
        alias=child.alias, target_subset=child.target_subset, skill_subset=child.skill_subset,
        is_parent_repo_inheritance=False, is_local=False, local_path=None,
    )
