"""Native no-follow binary descriptors for bounded identity-checked captures."""

import os
from pathlib import Path


def open_readonly_nofollow(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    return _windows_open(path)


def capture_path_stat(path: Path) -> os.stat_result:
    """Compare Windows capture identities through the same handle-based API.

    Keep the original capture descriptor open while calling this helper so its
    no-write/no-delete share lease also protects the named-path observation.
    """
    if os.name != "nt":
        return path.stat(follow_symlinks=False)
    with os.fdopen(open_readonly_nofollow(path), "rb") as named:
        info = os.fstat(named.fileno())
        if not info.st_ino:
            raise OSError("Windows capture could not establish file identity.")
        return info


def _windows_open(path: Path) -> int:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from .path_security import has_symlink_component, validate_windows_segments

    absolute = path.absolute()
    validate_windows_segments(str(absolute))
    if has_symlink_component(Path(absolute.anchor), absolute):
        raise OSError("Capture path contains a Windows reparse point.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetFileType.argtypes = [wintypes.HANDLE]
    kernel.GetFileType.restype = wintypes.DWORD
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    # No write/delete sharing: a concurrent writer cannot be admitted while the
    # capture handle is open. OPEN_REPARSE_POINT inspects rather than follows the
    # final component; ancestor reparses are independently refused.
    handle = kernel.CreateFileW(
        str(absolute), 0x80000000, 1, None, 3, 0x00200000, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = AttributeTag()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10) or kernel.GetFileType(handle) != 1:
            raise OSError("Capture requires a regular non-reparse disk file.")
        if has_symlink_component(Path(absolute.anchor), absolute):
            raise OSError("Capture path changed to a Windows reparse point.")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return fd
