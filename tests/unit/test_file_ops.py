import errno
from unittest.mock import Mock

import pytest

from apmx.utils import file_ops, path_security


@pytest.mark.parametrize("failure", ["chmod", "remove"])
@pytest.mark.parametrize("ignore_errors", [False, True])
def test_cleanup_exhaustion_obeys_explicit_error_policy(
    tmp_path,
    monkeypatch,
    failure,
    ignore_errors,
):
    directory = tmp_path / "owned"
    directory.mkdir()
    error = OSError(errno.EBUSY, "fixture cleanup lock")
    remove = Mock(side_effect=error if failure == "remove" else None)
    chmod = Mock(side_effect=error if failure == "chmod" else None)
    attempts = []

    def fail_removal(path, *, onerror):
        attempts.append(path)
        onerror(remove, path, (OSError, error, None))

    monkeypatch.setattr(file_ops.shutil, "rmtree", fail_removal)
    monkeypatch.setattr(file_ops.os, "chmod", chmod)
    monkeypatch.setattr(file_ops.time, "sleep", lambda _: None)
    if ignore_errors:
        file_ops.robust_rmtree(directory, ignore_errors=True, max_retries=2)
    else:
        with pytest.raises(OSError) as caught:
            file_ops.robust_rmtree(directory, max_retries=2)
        assert caught.value is error
    assert len(attempts) == 3
    assert directory.is_dir()
    assert chmod.call_count == 3
    assert remove.call_count == (3 if failure == "remove" else 0)


def test_safe_cleanup_preserves_failure_and_owned_path_fence(tmp_path, monkeypatch):
    owned = tmp_path / "owned"
    owned.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    error = PermissionError("fixture denied removal")
    remove = Mock(side_effect=error)

    def fail_removal(path, *, onerror):
        onerror(remove, path, (PermissionError, error, None))

    monkeypatch.setattr(file_ops.shutil, "rmtree", fail_removal)
    monkeypatch.setattr(file_ops.os, "chmod", Mock())
    with pytest.raises(PermissionError) as caught:
        path_security.safe_rmtree(owned, tmp_path)
    assert caught.value is error
    with pytest.raises(path_security.PathTraversalError):
        path_security.safe_rmtree(outside, owned)
    assert remove.call_count == 1
    assert outside.is_dir()


def test_readonly_retry_refuses_link_components_before_chmod(tmp_path, monkeypatch):
    error = PermissionError("fixture link removal failure")
    chmod = Mock()
    remove = Mock()
    monkeypatch.setattr(path_security, "has_symlink_component", lambda *_: True)
    monkeypatch.setattr(file_ops.os, "chmod", chmod)
    with pytest.raises(PermissionError) as caught:
        file_ops._on_readonly_retry(remove, str(tmp_path), (PermissionError, error, None))
    assert caught.value is error
    chmod.assert_not_called()
    remove.assert_not_called()


def test_readonly_retry_removes_owned_regular_file(tmp_path):
    target = tmp_path / "readonly"
    target.write_bytes(b"owned")
    target.chmod(0o400)
    file_ops._on_readonly_retry(
        file_ops.os.unlink,
        str(target),
        (PermissionError, PermissionError("readonly"), None),
    )
    assert not target.exists()
