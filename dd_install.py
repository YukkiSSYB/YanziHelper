"""Install the matching local DD HID 63340 driver with user consent."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes as w
import hashlib
import msvcrt
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import _winapi

from dd_device import DeviceError, WindowsDDDevice, _signature
from dd_lifecycle import ProcessHandles


# Original DD HID 63340 x64 payload, also checked by the legacy launcher.
DRIVER_FILES = {
    "ddc.exe": "3c535b334f0897b8a0870bcb476c30ea79afd09cfc18f8e00190bdc7c6c46785",
    "ddhid63340.sys": "fbe510402b3822c63e94752051b7d5895b67875f22ec48593de19764a649f8b1",
    "ddhid63340.inf": "17fe3814f57e98dd2af97f56b63502e474ea5e41cda1a510ffe435ee6ad7a104",
    "ddhid63340.cat": "6135c664711127a62e0988f6844521e345d78ace9d3747a392400ce99be96983",
}


def _require_x64(kernel):
    native = w.USHORT()
    try:
        query = _signature(kernel, "IsWow64Process2", w.BOOL, w.HANDLE,
                           ctypes.POINTER(w.USHORT), ctypes.POINTER(w.USHORT))
    except AttributeError as exc:
        raise DeviceError("请更新 Windows 后再运行 DD 驱动安装程序。") from exc
    if not query(-1, ctypes.byref(w.USHORT()), ctypes.byref(native)):
        raise ctypes.WinError(ctypes.get_last_error())
    if native.value != 0x8664:  # IMAGE_FILE_MACHINE_AMD64
        raise DeviceError("此 DD 驱动安装包仅支持 Windows x64（AMD64）。")


def _locked_reader(path):
    # FILE_SHARE_READ prevents modification and replacement while held.
    handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, 1, 0,
                                _winapi.OPEN_EXISTING, 0x00200000, 0)  # FILE_FLAG_OPEN_REPARSE_POINT
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        _winapi.CloseHandle(handle)
        raise
    return os.fdopen(fd, "rb")


@contextmanager
def _stage_verified_driver(directory, staging):
    # Run the elevated installer only from a fresh directory containing the
    # four verified files. The unpacked ZIP directory can contain other files.
    directory_stat = directory.lstat()
    if (not stat.S_ISDIR(directory_stat.st_mode)
            or directory_stat.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or {path.name for path in directory.iterdir()} != DRIVER_FILES.keys()):
        raise DeviceError("driver 文件夹内容不完整或已被修改，请重新完整解压助手压缩包。")
    with ExitStack() as locked_files:
        for name, expected in DRIVER_FILES.items():
            source, target = directory / name, staging / name
            error = f"DD 驱动文件校验失败：{name}。请重新完整解压助手压缩包。"
            info = source.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise DeviceError(error)
            shutil.copyfile(source, target)
            stream = locked_files.enter_context(_locked_reader(target))
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise DeviceError(error)
        yield


def _install(kernel, directory):
    set_dll_directory = _signature(kernel, "SetDllDirectoryW", w.BOOL, w.LPCWSTR)
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled and not set_dll_directory(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # The vendor installer locates its INF, SYS and CAT in its working directory.
        process = subprocess.Popen([str(directory / "ddc.exe")], cwd=directory)
    finally:
        if bundled:
            set_dll_directory(bundled)
    with process:
        return process.wait()


def ensure_dd_driver(directory, confirm):
    """Skip setup for an installed device or a package without an installer."""
    if not directory.exists():
        return True
    backend = WindowsDDDevice()
    if backend.devices():
        return True
    # Reuse the device guardian's lock while installing, so no concurrent
    # assistant can install or manage the same device during setup.
    handles = ProcessHandles()
    mutex = handles.acquire()
    try:
        if backend.devices():
            return True
        _require_x64(handles.dll)
        if not confirm():
            return False
        with TemporaryDirectory(prefix="yanzi-dd-install-") as folder:
            staging = Path(folder)
            with _stage_verified_driver(directory, staging):
                code = _install(handles.dll, staging)
            if code in (1641, 3010):
                raise DeviceError("DD 驱动安装需要重启电脑。请保存工作，重启后再打开妍姿助手。")
            for _ in range(20):
                if backend.devices():
                    return True
                time.sleep(.5)
            if backend.devices():
                return True
        raise DeviceError(f"安装后未检测到 DD HID 63340 设备（安装程序退出码：{code}）。"
                          "请检查安装结果，再重新打开妍姿助手。")
    finally:
        handles.release(mutex)
