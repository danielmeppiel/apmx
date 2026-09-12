import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from apmx.contracts.models import ContractError
from apmx.contracts.workspace import _read
from apmx.utils.file_capture import open_readonly_nofollow
from apmx.utils.path_security import (
    PathTraversalError, has_symlink_component, is_link_or_reparse, validate_windows_segments,
)


def test_capture_preserves_binary_crlf_and_control_bytes(tmp_path):
    data = b"\x00\x1a\xfffirst\r\nsecond\r\n"
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    captured, entry = _read(tmp_path, "data.bin", 64)
    assert captured == data
    assert entry.size == len(data)
    with os.fdopen(open_readonly_nofollow(path), "rb") as stream:
        assert stream.read() == data


def test_capture_remains_bounded(tmp_path):
    (tmp_path / "large").write_bytes(b"x" * 20)
    with pytest.raises(ContractError):
        _read(tmp_path, "large", 19)


@pytest.mark.parametrize("path", ["con", "NUL.txt", "dir/com1", "out:stream", "out.", "out "])
def test_windows_device_stream_and_alias_paths_refuse(path):
    with pytest.raises(PathTraversalError):
        validate_windows_segments(path)


def test_windows_drive_and_spaces_are_supported():
    validate_windows_segments("C:/Program Files/work/notes.md")


def test_windows_reparse_attribute_is_rejected_even_without_symlink_mode(tmp_path, monkeypatch):
    path = tmp_path / "junction"
    monkeypatch.setattr(
        Path, "lstat", lambda self: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
    )
    assert is_link_or_reparse(path)
    assert has_symlink_component(tmp_path, path)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows junction capture")
def test_windows_junction_is_never_followed(tmp_path):
    import _winapi

    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "secret").write_bytes(b"must not be captured")
    junction = tmp_path / "junction"
    _winapi.CreateJunction(str(actual), str(junction))
    try:
        assert has_symlink_component(tmp_path, junction / "secret")
        with pytest.raises((ContractError, OSError)):
            _read(tmp_path, "junction/secret", 64)
        with pytest.raises(OSError):
            open_readonly_nofollow(junction / "secret")
    finally:
        os.rmdir(junction)
