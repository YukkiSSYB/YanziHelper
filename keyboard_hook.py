"""Thread-safe keyboard hook; UI work is delivered through a queue only."""

from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | KEY-01 | publication trace; see NOTICE.md.
import ctypes
import os
import queue
import threading
from ctypes import wintypes
from dataclasses import dataclass


WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_INJECTED = 0x10
LLKHF_LOWER_IL_INJECTED = 0x02
ERROR_INVALID_HOOK_HANDLE = 1404

VK_CODES = {chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)}
VK_CODES.update({str(i): ord(str(i)) for i in range(10)})
VK_CODES.update({f"f{i}": 0x70 + i - 1 for i in range(1, 13)})
VK_CODES.update({f"num{i}": 0x60 + i for i in range(10)})
VK_CODES.update({"tab": 0x09, "space": 0x20})
VK_TO_KEY = {vk: key for key, vk in VK_CODES.items()}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


def _normalize(key: str) -> str:
    return (key or "").strip().lower()


@dataclass(frozen=True)
class _HeldKey:
    suppressed: bool
    watched: bool


class HookState:
    """Pure physical-key state machine, called under KeyboardHook's state lock.

    A key's first physical down determines the handling of its entire hold.
    Reconfiguration cannot leak a suppressed release, or turn an existing
    disabled hold into a new macro press. Injected events never alter holds.
    """

    def __init__(self) -> None:
        self.toggle = ""
        self.suppressed: frozenset[str] = frozenset()
        self.watched: frozenset[str] = frozenset()
        self.enabled = False
        self.held: dict[str, _HeldKey] = {}

    def configure(
        self, toggle: str, suppressed: set[str], watched: set[str], enabled: bool
    ) -> None:
        self.toggle = _normalize(toggle)
        self.suppressed = frozenset(_normalize(key) for key in suppressed if _normalize(key))
        self.watched = frozenset(_normalize(key) for key in watched if _normalize(key))
        self.enabled = bool(enabled)

    def transition(
        self, key: str, down: bool, injected: bool = False
    ) -> tuple[bool, tuple[tuple[str, str], ...]]:
        # Yukki / ssyb [KEY-02]: a physical hold keeps its first-down decision.
        key = _normalize(key)
        if injected or not key:
            return False, ()
        if down:
            held = self.held.get(key)
            if held is not None:
                return held.suppressed, ()
            toggle = bool(self.toggle) and key == self.toggle
            suppressed = toggle or (self.enabled and key in self.suppressed)
            watched = toggle or (self.enabled and (key in self.suppressed or key in self.watched))
            self.held[key] = _HeldKey(suppressed, watched)
            return suppressed, (("press", key),) if watched else ()
        held = self.held.pop(key, None)
        if held is None:
            # The hook may have started while this key was already held.
            return False, ()
        return held.suppressed, (("release", key),) if held.watched else ()


class _Win32Backend:
    """Declare every WinAPI signature, including pointer-sized hook handles."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("键盘钩子仅支持 Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        self.user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, self.callback_type, wintypes.HINSTANCE, wintypes.DWORD
        ]
        self.user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self.user32.CallNextHookEx.argtypes = [
            wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        ]
        self.user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self.user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
        ]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND,
            wintypes.UINT, wintypes.UINT, wintypes.UINT
        ]
        self.user32.PeekMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.TranslateMessage.restype = wintypes.BOOL
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self.user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        ]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.user32.GetAsyncKeyState.restype = wintypes.SHORT
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class KeyboardHook:
    def __init__(self, events: queue.Queue) -> None:
        self.events = events
        self._state = HookState()
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._backend: _Win32Backend | None = None
        self._handle = None
        self._callback_ref = None
        self._startup_error: OSError | None = None
        self._cleanup_ok = True
        self._cleanup_error = ""

    @property
    def cleanup_error(self) -> str:
        """Most recent incomplete shutdown reason, cleared after recovery."""
        return self._cleanup_error

    def configure(
        self, toggle: str, suppressed: set[str], watched: set[str], enabled: bool
    ) -> None:
        with self._state_lock:
            self._state.configure(toggle, suppressed, watched, enabled)

    def _emit(self, event: tuple[str, str]) -> None:
        # The application uses an unbounded Queue; never wait inside a hook.
        try:
            self.events.put_nowait(event)
        except queue.Full:
            pass

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._handle is not None and not self._remove_hook():
                raise OSError(self._cleanup_error)
            self._backend = _Win32Backend()
            self._stop.clear()
            self._ready.clear()
            self._startup_error = None
            self._cleanup_ok = True
            self._cleanup_error = ""
            self._thread_id = None
            with self._state_lock:
                self._state.held.clear()
            self._thread = threading.Thread(
                target=self._run, name="keyboard-hook", daemon=True
            )
            self._thread.start()
            if not self._ready.wait(2.0):
                self._stop.set()
                self._wake()
                raise OSError("键盘钩子启动超时")
            if self._startup_error is not None:
                self._thread.join(0.5)
                raise self._startup_error

    def _wake(self) -> bool:
        if self._backend is None or self._thread_id is None:
            return False
        return bool(self._backend.user32.PostThreadMessageW(
            self._thread_id, WM_QUIT, 0, 0
        ))

    def close(self, timeout: float = 2.0) -> bool:
        """Wake the blocking message loop and wait until its hook is removed."""
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            if thread is None:
                return True
            if thread.is_alive():
                self._wake()
                if thread is threading.current_thread():
                    return False
                thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                self._report_cleanup_error("键盘监听线程尚未退出，正在重试关闭")
                return False
            # An earlier native unhook failure must be retryable. Only retry
            # after joining, so no callback can still be executing on the worker.
            if self._handle is not None:
                return self._remove_hook()
            self._cleanup_error = ""
            return self._cleanup_ok

    def _report_cleanup_error(self, message: str) -> None:
        if self._cleanup_error != message:
            self._emit(("error", message))
        self._cleanup_error = message

    def _remove_hook(self) -> bool:
        if self._handle is None:
            self._cleanup_ok = True
            self._cleanup_error = ""
            self._callback_ref = None
            return True
        backend = self._backend
        assert backend is not None
        try:
            ctypes.set_last_error(0)
            removed = backend.user32.UnhookWindowsHookEx(self._handle)
            if not removed:
                error = ctypes.get_last_error()
                # Windows may already have removed the hook after its owning
                # thread exited. An invalid handle confirms there is no hook.
                if error != ERROR_INVALID_HOOK_HANDLE:
                    raise ctypes.WinError(error) if error else OSError("UnhookWindowsHookEx 失败")
        except Exception as exc:
            self._cleanup_ok = False
            self._report_cleanup_error(f"键盘钩子释放失败: {exc}")
            # Native code can retain this pointer until removal is confirmed.
            return False
        self._handle = None
        self._callback_ref = None
        self._cleanup_ok = True
        self._cleanup_error = ""
        return True

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        backend = self._backend
        assert backend is not None
        try:
            if n_code == HC_ACTION and w_param in (
                WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP
            ):
                event = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                key = VK_TO_KEY.get(int(event.vkCode))
                injected = bool(event.flags & (LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED))
                if key and not injected:
                    with self._state_lock:
                        suppress, events = self._state.transition(
                            key, w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                        )
                        for queued in events:
                            self._emit(queued)
                    if suppress:
                        return 1
        except Exception as exc:
            self._emit(("error", f"键盘钩子处理失败: {exc}"))
        return backend.user32.CallNextHookEx(self._handle, n_code, w_param, l_param)

    def _run(self) -> None:
        backend = self._backend
        assert backend is not None
        try:
            self._thread_id = int(backend.kernel32.GetCurrentThreadId())
            msg = wintypes.MSG()
            # Create this thread's message queue before close() can post WM_QUIT.
            backend.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            self._callback_ref = backend.callback_type(self._callback)
            self._handle = backend.user32.SetWindowsHookExW(
                WH_KEYBOARD_LL, self._callback_ref,
                backend.kernel32.GetModuleHandleW(None), 0
            )
            if not self._handle:
                raise ctypes.WinError(ctypes.get_last_error())
            with self._state_lock:
                for key, vk in VK_CODES.items():
                    if backend.user32.GetAsyncKeyState(vk) & 0x8000:
                        # Preserve an existing game's key-down/up pair and do
                        # not mistake its next autorepeat for a new trigger.
                        self._state.held.setdefault(key, _HeldKey(False, False))
            self._ready.set()
            while not self._stop.is_set():
                result = backend.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                backend.user32.TranslateMessage(ctypes.byref(msg))
                backend.user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            error = OSError(f"键盘监听失败: {exc}")
            if not self._ready.is_set():
                self._startup_error = error
            self._emit(("error", str(error)))
        finally:
            self._remove_hook()
            self._ready.set()
            self._thread_id = None
