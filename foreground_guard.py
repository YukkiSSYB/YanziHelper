"""Optional foreground-window protection; no Tk access or input injection."""

from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | FGD-01 | publication trace; see NOTICE.md.
import ctypes
import os
import threading
from ctypes import wintypes


class _Win32Foreground:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("前台窗口保护仅支持 Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetForegroundWindow.argtypes = []
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    def __call__(self) -> tuple[int, int] | None:
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        thread_id = self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not thread_id or not pid.value or self.user32.GetForegroundWindow() != hwnd:
            return None
        return int(hwnd), int(pid.value)


class ForegroundGuard:
    """Capture on enable, then latch a focus loss until explicitly reset.

    The window handle and process ID must both match. Readers run on the UI
    and macro worker, so state is protected independently of either thread.
    Disabling this optional feature performs no Win32 calls.
    """

    def __init__(self, reader=None, *, own_pid: int | None = None) -> None:
        self._reader = reader
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._lock = threading.Lock()
        self._target: tuple[int, int] | None = None
        self._lost = False

    def capture(self) -> None:
        with self._lock:
            self._target = None
            self._lost = True
            if self._reader is None:
                self._reader = _Win32Foreground()
            current = self._reader()
            if not current or not all(current):
                raise ValueError("无法识别前台窗口，请切换到游戏窗口后再按总开关。")
            if current[1] == self._own_pid:
                raise ValueError("请在游戏窗口中按总开关启用前台保护。")
            self._target = current
            self._lost = False

    def clear(self) -> None:
        with self._lock:
            self._target = None
            self._lost = False

    def allows_output(self) -> bool:
        # Yukki / ssyb [FGD-02]: focus loss stays latched until an explicit reset.
        with self._lock:
            if self._lost:
                return False
            if self._target is None:
                return True
            try:
                current = self._reader()
            except OSError:
                current = None
            if current != self._target:
                self._lost = True
                return False
            return True
