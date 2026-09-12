"""Read-only declaration/lock drift check; native APM owns resolution and replay."""


def detect_ref_change(dependency, locked, *, update_refs=False, logger=None):
    if update_refs or locked is None:
        return False
    return (
        (dependency.source or "git") != (locked.source or "git")
        or dependency.reference != locked.resolved_ref
        or bool(dependency.is_insecure) != bool(locked.is_insecure)
    )
