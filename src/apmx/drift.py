"""Direct Git/local lock replay; no registry or installer integration."""

from dataclasses import replace

from .utils.github_host import is_full_commit_sha


def detect_ref_change(dependency, locked, *, update_refs=False, logger=None):
    if update_refs or locked is None:
        return False
    return (
        (dependency.source or "git") != (locked.source or "git")
        or dependency.reference != locked.resolved_ref
        or bool(dependency.is_insecure) != bool(locked.is_insecure)
        or (dependency.ref_kind == "semver" and dependency.reference != locked.constraint)
    )


def build_download_ref(dependency, lock, *, update_refs, ref_changed, logger=None):
    if lock and not update_refs and not ref_changed:
        locked = lock.get_dependency(dependency.get_unique_key())
        if locked and is_full_commit_sha(locked.resolved_commit):
            return replace(dependency, reference=locked.resolved_commit)
    return dependency
