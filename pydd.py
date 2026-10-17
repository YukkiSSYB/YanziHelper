from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | DD-01 | publication trace; see NOTICE.md.
# PyDD reference: 66maer/pydd (MIT); see LICENSES/pydd-MIT.txt.
import ctypes
import _ctypes
import threading
import time
from pathlib import Path


class DDError(Exception):
    pass


class DDInitializationError(DDError):
    pass


KEY_CODES = {
    "esc": 100,
    "f1": 101,
    "f2": 102,
    "f3": 103,
    "f4": 104,
    "f5": 105,
    "f6": 106,
    "f7": 107,
    "f8": 108,
    "f9": 109,
    "f10": 110,
    "f11": 111,
    "f12": 112,
    "`": 200,
    "1": 201,
    "2": 202,
    "3": 203,
    "4": 204,
    "5": 205,
    "6": 206,
    "7": 207,
    "8": 208,
    "9": 209,
    "0": 210,
    "-": 211,
    "+": 212,
    "=": 212,
    "\\": 213,
    "backspace": 214,
    "tab": 300,
    "q": 301,
    "w": 302,
    "e": 303,
    "r": 304,
    "t": 305,
    "y": 306,
    "u": 307,
    "i": 308,
    "o": 309,
    "p": 310,
    "[": 311,
    "]": 312,
    "enter": 313,
    "caps": 400,
    "capslock": 400,
    "a": 401,
    "s": 402,
    "d": 403,
    "f": 404,
    "g": 405,
    "h": 406,
    "j": 407,
    "k": 408,
    "l": 409,
    ";": 410,
    "'": 411,
    "shift": 500,
    "z": 501,
    "x": 502,
    "c": 503,
    "v": 504,
    "b": 505,
    "n": 506,
    "m": 507,
    ",": 508,
    ".": 509,
    "/": 510,
    "ctrl": 600,
    "win": 601,
    "alt": 602,
    "space": 603,
    "delete": 706,
    "del": 706,
    "up": 709,
    "left": 710,
    "down": 711,
    "right": 712,
    "num0": 800,
    "num1": 801,
    "num2": 802,
    "num3": 803,
    "num4": 804,
    "num5": 805,
    "num6": 806,
    "num7": 807,
    "num8": 808,
    "num9": 809,
    "numlock": 810,
    "num/": 811,
    "num*": 812,
    "num-": 813,
    "num+": 814,
    "numenter": 815,
    "num.": 816,
}


class PyDD:
    def __init__(self, dll_path: str | Path = "ddhid.63340.dll") -> None:
        # Serialize complete down/up pairs with shutdown. A DLL must never be
        # unloaded while another thread is executing one of its functions.
        self._lock = threading.RLock()
        self._closed = False
        self._dll_handle = 0
        self.dd_dll = None
        self._pressed_keys: set[int] = set()
        self._pressed_mouse_buttons: set[int] = set()
        path = Path(dll_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        if not path.exists():
            raise DDInitializationError(f"找不到 DD DLL: {path}")

        try:
            self.dd_dll = ctypes.WinDLL(str(path))
            # WinDLL above acquired this reference; do not use windll's shared
            # library cache or free a handle owned by another component.
            self._dll_handle = self.dd_dll._handle
            for name, arguments in (
                ("DD_btn", [ctypes.c_int]),
                ("DD_key", [ctypes.c_int, ctypes.c_int]),
                ("DD_mov", [ctypes.c_int, ctypes.c_int]),
                ("DD_movR", [ctypes.c_int, ctypes.c_int]),
                ("DD_whl", [ctypes.c_int]),
                ("DD_str", [ctypes.c_char_p]),
            ):
                function = getattr(self.dd_dll, name)
                function.argtypes = arguments
                function.restype = ctypes.c_int

            result = self.dd_dll.DD_btn(0)
            if result != 1:
                raise DDInitializationError(f"DD驱动初始化失败，DD_btn(0) 返回 {result}")
        except Exception as exc:
            cleanup_error = ""
            try:
                self.close()
            except DDError as cleanup_exc:
                cleanup_error = f"；资源清理失败: {cleanup_exc}"
            raise DDInitializationError(
                f"加载或初始化 DD DLL 失败: {path} ({exc}){cleanup_error}"
            ) from exc

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _ensure_open(self) -> None:
        if self._closed or self.dd_dll is None:
            raise DDError("DD 驱动已关闭")

    def _key_down_code(self, code: int) -> None:
        # Remember the input before calling native code: if it raises after
        # sending the event, shutdown still attempts the corresponding release.
        self._pressed_keys.add(code)
        self.dd_dll.DD_key(code, 1)

    def _key_up_code(self, code: int) -> None:
        self.dd_dll.DD_key(code, 2)
        self._pressed_keys.discard(code)

    def _key_code(self, key: str) -> int:
        key = (key or "").strip().lower()
        if not key:
            raise DDError("按键为空")
        try:
            return KEY_CODES[key]
        except KeyError as exc:
            raise DDError(f"不支持的按键: {key}") from exc

    def key_down(self, key: str) -> None:
        with self._lock:
            self._ensure_open()
            self._key_down_code(self._key_code(key))

    def key_up(self, key: str) -> None:
        with self._lock:
            self._ensure_open()
            self._key_up_code(self._key_code(key))

    def key_press(self, key: str, duration: float = 0.04) -> None:
        # Yukki / ssyb [DD-02]: serialize the down/up pair with DLL shutdown.
        with self._lock:
            self._ensure_open()
            if not (key or "").strip():
                return
            code = self._key_code(key)
            try:
                self._key_down_code(code)
                time.sleep(max(0.0, duration))
            finally:
                self._key_up_code(code)

    def mouse_move(self, x: int, y: int) -> None:
        with self._lock:
            self._ensure_open()
            self.dd_dll.DD_mov(int(x), int(y))

    def mouse_move_relative(self, dx: int, dy: int) -> None:
        with self._lock:
            self._ensure_open()
            self.dd_dll.DD_movR(int(dx), int(dy))

    def mouse_click(self, button: str = "left", duration: float = 0.05) -> None:
        down_up = {
            "left": (1, 2),
            "right": (4, 8),
            "middle": (16, 32),
        }
        with self._lock:
            self._ensure_open()
            try:
                down, up = down_up[button.lower()]
            except (KeyError, AttributeError) as exc:
                raise DDError(f"不支持的鼠标按键: {button}") from exc
            self._pressed_mouse_buttons.add(up)
            try:
                self.dd_dll.DD_btn(down)
                time.sleep(max(0.0, duration))
            finally:
                self.dd_dll.DD_btn(up)
                self._pressed_mouse_buttons.discard(up)

    def _release_held_inputs(self) -> list[str]:
        errors = []
        if self.dd_dll is None:
            return errors
        for code in sorted(self._pressed_keys):
            try:
                self._key_up_code(code)
            except Exception as exc:
                errors.append(f"释放按键 {code}: {exc}")
        for up in sorted(self._pressed_mouse_buttons):
            try:
                self.dd_dll.DD_btn(up)
                self._pressed_mouse_buttons.discard(up)
            except Exception as exc:
                errors.append(f"释放鼠标按键 {up}: {exc}")
        return errors

    def release_all(self) -> None:
        """Release inputs sent by this instance, retaining failed releases for retry."""
        with self._lock:
            errors = self._release_held_inputs()
            if errors:
                raise DDError("；".join(errors))

    def close(self) -> None:
        """Stop input and release our DLL reference; this is not driver removal.

        Each call attempts every held input once. Failed releases keep their
        DLL reference for a later close() retry; new input remains disabled.

        The bundled DLLs have no documented shutdown export. FreeLibrary only
        releases this process's library reference and cannot establish whether
        a kernel driver has been unloaded or another game's checks will pass.
        """
        with self._lock:
            self._closed = True
            errors = self._release_held_inputs()
            if errors:
                # Unloading here would discard the only way to retry a failed
                # key-up or mouse release and could leave that input held.
                raise DDError("；".join(errors))
            if self._dll_handle:
                try:
                    _ctypes.FreeLibrary(self._dll_handle)
                except Exception as exc:
                    # Retain the handle for a later close() retry, but forbid
                    # any new input as soon as shutdown has been requested.
                    errors.append(f"释放 DD DLL: {exc}")
                else:
                    self._dll_handle = 0
                    self.dd_dll = None
                    self._pressed_keys.clear()
                    self._pressed_mouse_buttons.clear()
            if errors:
                raise DDError("；".join(errors))

    def __enter__(self) -> PyDD:
        with self._lock:
            self._ensure_open()
            return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
