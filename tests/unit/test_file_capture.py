import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from apmx.contracts.models import ContractError
from apmx.contracts.workspace import _read
from apmx.utils.file_capture import open_readonly_nofollow
from apmx.utils import file_capture
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


def test_windows_named_identity_uses_a_second_nofollow_handle(tmp_path, monkeypatch):
    path = tmp_path / "selected"
    path.write_bytes(b"selected bytes")
    expected = path.stat()
    monkeypatch.setattr(file_capture, "os", SimpleNamespace(
        name="nt", fstat=os.fstat, fdopen=os.fdopen,
    ))
    monkeypatch.setattr(file_capture, "open_readonly_nofollow", lambda selected: os.open(
        selected, os.O_RDONLY | getattr(os, "O_BINARY", 0),
    ))
    monkeypatch.setattr(
        Path, "stat",
        lambda *args, **kwargs: pytest.fail("Do not mix path-stat and descriptor-stat APIs"),
    )
    actual = file_capture.capture_path_stat(path)
    assert (actual.st_dev, actual.st_ino, actual.st_size) == (
        expected.st_dev, expected.st_ino, expected.st_size,
    )


def test_capture_preserves_identity_and_bytes_across_timestamp_updates(tmp_path):
    path = tmp_path / "selected"
    for version in range(12):
        data = f"version {version}\r\n".encode()
        path.write_bytes(data)
        timestamp = 1_700_000_000_123_456_700 + version * 100
        os.utime(path, ns=(timestamp, timestamp))
        captured, entry = _read(tmp_path, path.name, 128)
        assert captured == data
        assert entry.size == len(data)


@pytest.mark.parametrize("field", [
    "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns",
])
def test_every_named_identity_component_still_blocks_drift(tmp_path, monkeypatch, field):
    path = tmp_path / "selected"
    path.write_bytes(b"same bytes")
    original = file_capture.capture_path_stat

    def changed(selected):
        info = original(selected)
        identity = {
            key: getattr(info, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        }
        identity[field] += 1
        return SimpleNamespace(**identity)

    monkeypatch.setattr("apmx.contracts.workspace.capture_path_stat", changed)
    with pytest.raises(ContractError) as error:
        _read(tmp_path, path.name, 128)
    assert error.value.code == "source_changed"


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
