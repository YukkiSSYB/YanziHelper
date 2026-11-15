"""A separate, exclusive guardian owns the DD device for one application session.

The guardian watches a real Windows process handle (including creation time),
so a crashed GUI is cleaned up too. It never loads an input DLL or sends keys.
"""
from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | LIFE-01 | publication trace; see NOTICE.md.
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from dd_device import DDDeviceController, DeviceError, RestartRequired, _signature

MUTEX = r"Global\YanziHelper.DDHID63340.Lifecycle.v1"


class StopRequestError(DeviceError):
    """The parent could not notify its guardian; device shutdown is unverified."""


def write_state(path, **state):
    data = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **state}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeviceError(f"读取 DD 监护状态失败，当前无法确认驱动状态：{path}（{exc}）") from exc
    if not isinstance(state, dict):
        raise DeviceError(f"DD 监护状态格式无效，当前无法确认驱动状态：{path}（应为 JSON 对象）")
    return state


class ProcessHandles:
    def __init__(self):
        self.dll = ctypes.WinDLL("kernel32", use_last_error=True)
        _signature(self.dll, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
        _signature(self.dll, "GetProcessTimes", w.BOOL, w.HANDLE,
                   *([ctypes.POINTER(w.FILETIME)] * 4))
        _signature(self.dll, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
        _signature(self.dll, "CreateMutexW", w.HANDLE, w.LPVOID, w.BOOL, w.LPCWSTR)
        _signature(self.dll, "ReleaseMutex", w.BOOL, w.HANDLE)
        _signature(self.dll, "CloseHandle", w.BOOL, w.HANDLE)

    def open_process(self, pid):
        handle = self.dll.OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def birth(self, handle):
        times = [w.FILETIME() for _ in range(4)]
        if not self.dll.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def alive(self, handle):
        result = self.dll.WaitForSingleObject(handle, 0)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 258

    def acquire(self):
        handle = self.dll.CreateMutexW(None, False, MUTEX)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if self.dll.WaitForSingleObject(handle, 0) not in (0, 0x80):
            self.close(handle)
            raise DeviceError("另一个助手正在使用或清理 DD 驱动，请先关闭它。")
        return handle

    def close(self, handle):
        if handle:
            self.dll.CloseHandle(handle)

    def release(self, handle):
        self.dll.ReleaseMutex(handle)
        self.close(handle)


def run_guardian(parent_pid, parent_birth, folder, report_path):
    """Internal executable mode. No Tk, no global hook, no input DLL."""
    # Yukki / ssyb [LIFE-02]: bind cleanup to process identity, including birth time.
    state_path = folder / "state.json"
    handles = ProcessHandles()
    parent = mutex = None
    controller = None
    owns_device = False

    def report(phase, **details):
        # A refused second instance must not overwrite the active one's report.
        paths = (report_path, state_path) if mutex else (state_path,)
        report_errors = {}
        state_written = False
        for path in paths:
            try:
                write_state(path, phase=phase, guardian_pid=os.getpid(),
                            **details, **({"report_errors": report_errors} if report_errors else {}))
            except OSError as exc:
                # Neither a missing IPC directory nor a failed diagnostic write
                # may prevent the guardian from disabling an owned device.
                report_errors[str(path)] = str(exc)
            else:
                if path == state_path:
                    state_written = True
        return state_written

    def stop(startup_error=""):
        context = {"startup_error": startup_error} if startup_error else {}
        error = ""
        for attempt in range(3):
            try:
                snapshot = controller.stop()
                if startup_error:
                    report("failed", error=startup_error, cleanup_ok=True, snapshot=snapshot)
                else:
                    report("stopped", snapshot=snapshot)
                return True
            except RestartRequired as exc:
                details = {"error": str(exc), "cleanup_ok": False, **context}
                try:
                    details["snapshot"] = controller.backend.snapshot()
                except Exception as snapshot_exc:
                    details["snapshot_error"] = str(snapshot_exc)
                report("restart_required", **details)
                return False
            except Exception as exc:
                error = str(exc)
                report("stopping", error=error, **context)
                time.sleep(.5 * (attempt + 1))
        report("failed", error="DD 驱动停用未完成：" + error, cleanup_ok=False, **context)
        return False

    try:
        parent = handles.open_process(parent_pid)
        if handles.birth(parent) != parent_birth or not handles.alive(parent):
            raise DeviceError("助手进程已退出，未启用 DD 驱动。")
        mutex = handles.acquire()
        controller = DDDeviceController()
        owns_device = True  # Also roll back a partially failed start.
        if (folder / "stop").exists() or not handles.alive(parent):
            return 0 if stop() else 1
        snapshot = controller.start()
        if not report("ready", snapshot=snapshot):
            raise DeviceError("无法向助手报告 DD 启动状态，监护进程将停用设备。")
        while handles.alive(parent) and not (folder / "stop").exists():
            time.sleep(.1)
        return 0 if stop() else 1
    except Exception as exc:
        error = str(exc)
        if owns_device:
            # Keep the terminal cleanup result (especially restart_required)
            # instead of replacing it with the original startup failure.
            stop(startup_error=error)
        else:
            report("failed", error=error, cleanup_ok=True)
        return 1
    finally:
        if mutex:
            handles.release(mutex)
        handles.close(parent)
        # The parent usually removes IPC files. On a crash, retain the report
        # beside the EXE and remove only known files in this session directory.
        if parent and not _parent_exists(parent_pid, parent_birth):
            cleanup_session(folder)


def _parent_exists(pid, birth):
    handles = ProcessHandles()
    handle = None
    try:
        handle = handles.open_process(pid)
        return handles.birth(handle) == birth and handles.alive(handle)
    except OSError:
        return False
    finally:
        handles.close(handle)


def cleanup_session(folder):
    for name in ("state.json", "state.tmp", "stop"):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


class DDDriverSession:
    def __init__(self, entry_path, report_path):
        self.entry_path, self.report_path = Path(entry_path), Path(report_path)
        self.process = None
        self.folder = None
        self.finished = False
        self.error = ""
        self.error_type = DeviceError
        self.stop_requested = False

    def start(self, timeout=45):
        self.folder = Path(tempfile.mkdtemp(prefix="yanzi-dd-"))
        handles = ProcessHandles()
        parent = handles.open_process(os.getpid())
        try:
            birth = handles.birth(parent)
        finally:
            handles.close(parent)
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command += [str(self.entry_path)]
        command += ["--dd-guardian", str(os.getpid()), str(birth), str(self.folder), str(self.report_path)]
        env = os.environ.copy()
        # An independent extraction survives a crashed one-file parent.
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        try:
            self.process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            cleanup_session(self.folder)
            raise
        deadline = time.monotonic() + timeout
        while True:
            state = read_state(self.folder / "state.json")
            if self.process.poll() is not None:
                self.error = state.get("error") or "DD 监护进程未能启动。"
                cleanup_session(self.folder)
                raise DeviceError(self.error)
            if state.get("phase") == "ready":
                return state
            if time.monotonic() >= deadline:
                # Do not kill a guardian that might own an enabled device.
                self.request_stop()
                raise DeviceError("等待 DD 设备启动超时，监护进程将继续完成清理。")
            time.sleep(.05)

    def request_stop(self):
        if self.process is not None and not self.stop_requested and self.process.poll() is None:
            try:
                (self.folder / "stop").touch()
            except OSError as exc:
                raise StopRequestError(
                    f"无法发送 DD 停用请求，当前尚未确认驱动停止：{exc}。"
                    "助手进程退出后，独立监护将继续尝试清理。"
                ) from exc
            self.stop_requested = True

    def close(self):
        """Nonblocking: False while cleaning, True when verified, error on failure."""
        if self.finished:
            if self.error:
                raise self.error_type(self.error)
            return True
        if self.process is None:
            self.finished = True
            return True
        self.request_stop()
        if self.process.poll() is None:
            return False
        state = read_state(self.folder / "state.json")
        self.finished = True
        if self.process.returncode != 0 or state.get("phase") != "stopped":
            self.error = state.get("error") or "未能确认 DD 驱动已经停止，请查看 dd-driver-status.json。"
            self.error_type = RestartRequired if state.get("phase") == "restart_required" else DeviceError
        cleanup_session(self.folder)
        if self.error:
            raise self.error_type(self.error)
        return True

    def check_alive(self):
        if self.process is not None and self.process.poll() is not None:
            state = read_state(self.folder / "state.json")
            raise DeviceError(state.get("error") or "DD 监护进程意外退出，请退出助手后重新打开。")


def ensure_administrator():
    """Relaunch normal app entry via UAC; automated driver-free self-tests skip it."""
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    _signature(shell, "IsUserAnAdmin", w.BOOL)
    if shell.IsUserAnAdmin():
        return True
    _signature(shell, "ShellExecuteW", ctypes.c_ssize_t,
               w.HWND, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, ctypes.c_int)
    args = sys.argv[1:] if getattr(sys, "frozen", False) else [str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]
    # The elevated process must not reuse a one-file extraction that disappears
    # as soon as this unelevated launcher returns.
    old_reset = os.environ.get("PYINSTALLER_RESET_ENVIRONMENT")
    os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    try:
        result = shell.ShellExecuteW(None, "runas", sys.executable, subprocess.list2cmdline(args), None, 1)
    finally:
        if old_reset is None:
            os.environ.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
        else:
            os.environ["PYINSTALLER_RESET_ENVIRONMENT"] = old_reset
    if result <= 32:
        raise DeviceError("需要管理员权限才能启停 DD 虚拟设备；授权未完成，助手未启动。")
    return False
