"""Hidden, driver-free smoke checks usable by the packaged executable."""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import traceback
import wave
from pathlib import Path
from types import SimpleNamespace


HID_DLL_SHA256 = "01e8db6893cf79e9e7aa3afbee76bea6c4220c4d1a2c63bc2e5b7c109fdb831e"


class _RecordedKeys:
    """Record DD_key calls at the native DLL boundary without sending input."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.worker_ids: set[int] = set()
        self.held: set[str] = set()
        self.loaded: list[Path] = []
        self.freed: list[int] = []

    def load(self, path: str):
        from pydd import KEY_CODES
        names = {code: name for name, code in KEY_CODES.items()}
        self.loaded.append(Path(path))

        def button(flag):
            if flag != 0:
                raise AssertionError("unexpected mouse button output")
            return 1

        def key(code, direction):
            self.send(names[code], direction)
            return 1

        def unexpected(*args):
            raise AssertionError("unexpected DD operation")

        return SimpleNamespace(_handle=1, DD_btn=button, DD_key=key, DD_mov=unexpected,
                               DD_movR=unexpected, DD_whl=unexpected, DD_str=unexpected)

    def send(self, key: str, direction: int) -> None:
        with self._lock:
            self.worker_ids.add(threading.get_ident())
            if direction == 2:
                self.held.remove(key)
                self.calls.append(key)
            elif direction == 1:
                if key in self.held:
                    raise RuntimeError("duplicate key down: " + key)
                self.held.add(key)
            else:
                raise AssertionError(f"unexpected DD key direction {direction}")

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.calls)


def run(report_path: Path) -> int:
    """Write a UTF-8 JSON PASS/FAIL report and return the process exit code.

    The executable's entry module is preferred so these checks exercise its
    actual class and resources. The static fallback also supports importing
    this function from source and lets the packager discover dependencies.
    Only the bundled/default configuration is read; user profiles are untouched.
    No global hook is installed. PyDD delivers its native calls to an
    in-memory recorder instead of loading a library or changing any device.
    """
    started = time.monotonic()
    deadline = started + 10
    report = {
        "status": "FAIL",
        "frozen": bool(getattr(sys, "frozen", False)),
        "real_driver_loaded": False,
        "real_input_sent": False,
        "global_hook_started": False,
        "checks": [],
    }
    root = ui = None
    recorder = _RecordedKeys()
    patches = []
    dialogs: list[str] = []
    callbacks: list[str] = []
    saved_configs: list[dict] = []

    def patch(owner, name, replacement):
        patches.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def check(name, **details):
        report["checks"].append({"name": name, "status": "PASS", **details})

    def forbid_driver(*args, **kwargs):
        raise AssertionError("self-test must not initialize the real DD driver")

    def pump_until(predicate, message):
        while True:
            root.update()
            require(not dialogs, "unexpected error dialog: " + "; ".join(dialogs))
            require(not callbacks, "Tk callback failed: " + "\n".join(callbacks))
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(message)
            time.sleep(0.002)

    def trace(trigger, expected, label):
        offset = len(recorder.snapshot())
        # Queue a complete tap before the worker can depend on key hold time.
        ui.events.put(("press", trigger))
        ui.events.put(("release", trigger))
        pump_until(lambda: len(recorder.snapshot()) >= offset + len(expected), label + " timed out")
        actual = recorder.snapshot()[offset:]
        require(actual == expected, f"{label}: expected {expected}, received {actual}")
        check(label, trigger=trigger, keys=actual)

    try:
        module = sys.modules.get("__main__")
        if module is None or not hasattr(module, "YanziHelper"):
            import YanziHelper as module
        import tkinter as tk
        import pydd

        config_path = (module.BUNDLED_CONFIG_FILE if report["frozen"]
                       else module.BASE_DIR / "config.example.ini")
        require(config_path.is_file(), "bundled/default configuration missing")
        bundled_config = module.read_config_file(config_path)
        require(bool(bundled_config), "bundled/default configuration has no usable [SETTINGS]")
        module.validate_config(bundled_config)
        with config_path.open("rb") as stream:
            report["bundled_config_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        if report["frozen"]:
            with Path(sys.executable).open("rb") as stream:
                report["executable_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        check("bundled_configuration_valid")
        require(module.DD_DLL == module.RESOURCE_DIR / "ddhid.63340.dll" and module.DD_DLL.is_file(),
                "matching ddhid.63340.dll missing")
        with module.DD_DLL.open("rb") as stream:
            dll_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        require(dll_hash == HID_DLL_SHA256, "incorrect ddhid.63340.dll SHA-256")
        check("matching_hid_dll_bundled_without_loading", sha256=dll_hash)

        patch(module, "load_config", lambda: dict(module.DEFAULTS))
        patch(module, "save_config", lambda data: saved_configs.append(dict(data)))
        patch(module, "play_sound", lambda *args, **kwargs: None)
        patch(module, "DDDriverSession", forbid_driver)
        # Replace only PyDD's bindings, leaving ctypes used by Tk and Pillow intact.
        patch(pydd, "ctypes", SimpleNamespace(WinDLL=recorder.load, c_int=pydd.ctypes.c_int,
                                              c_char_p=pydd.ctypes.c_char_p))
        patch(pydd, "_ctypes", SimpleNamespace(FreeLibrary=recorder.freed.append))
        patch(module.messagebox, "showerror", lambda title, message, **kwargs: dialogs.append(f"{title}: {message}"))
        # The normal close path stops asynchronous sounds; keep QA fully quiet.
        try:
            import winsound
        except ImportError:
            winsound = None
        if winsound is not None:
            patch(winsound, "PlaySound", lambda *args, **kwargs: None)

        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda kind, value, tb: callbacks.append(
            "".join(traceback.format_exception(kind, value, tb))
        )
        ui = module.YanziHelper(root, start_listener=False)
        root.update_idletasks()
        require(root.state() == "withdrawn", "self-test unexpectedly displayed its window")
        require(ui.hook._thread is None, "self-test unexpectedly started a keyboard listener")
        from desktop_ui import DEFAULT_SIZE
        # A withdrawn root can temporarily request scrollbars before the window
        # manager allocates its client area. Check the requested design viewport.
        width, height = ui.view.canvas.winfo_reqwidth(), ui.view.canvas.winfo_reqheight()
        min_width, min_height = root.minsize()
        max_width = min(ui.view.px(DEFAULT_SIZE[0]), root.winfo_screenwidth())
        max_height = min(ui.view.px(DEFAULT_SIZE[1]), root.winfo_screenheight())
        require(min_width <= width <= max_width and min_height <= height <= max_height,
                f"requested layout does not fit this display: {width}x{height}")
        require({"sy_low", "xl_low"} <= set(ui.key_combos), "missing dedicated lowest-health controls")
        check("hidden_ui_and_layout", requested_width=width, requested_height=height)

        from PIL import ImageTk
        from desktop_ui import PALETTES
        backgrounds = {}
        for school, palette in PALETTES.items():
            scene = ui.view.art.render(school, (180, 120), 40, [(50, 8, 165, 107, 4, .82)], palette[0])
            painted = ImageTk.PhotoImage(scene, master=root)
            require((painted.width(), painted.height()) == scene.size, "Tk could not paint " + school)
            backgrounds[school] = hashlib.sha256(scene.tobytes()).hexdigest()
        require(len(set(backgrounds.values())) == 2, "the two schools painted identical backgrounds")
        check("bundled_page_backgrounds", rendered_sha256=backgrounds)

        for filename in ("start.wav", "stop.wav"):
            with wave.open(str(module.RESOURCE_DIR / filename), "rb") as sound:
                require(sound.getcomptype() == "NONE", filename + " is not PCM audio")
                require(sound.getnframes() > 0 and sound.getnchannels() > 0,
                        filename + " contains no audio frames")
        check("bundled_pcm_sounds_without_playback")
        require(ui.collect_config() == module.DEFAULTS, "legacy default settings changed in UI")
        require(ui.dd is None and ui.engine is None and not recorder.loaded,
                "opening settings unexpectedly initialized keyboard output")
        check("legacy_defaults")
        with ui.profile_dialog():
            require(ui.hook._state.toggle == "", "file dialog did not suspend the hotkey")
            ui.toggle_macro()
            ui.events.put(("press", module.DEFAULTS["toggle"]))
        require(not ui.macro_enabled and ui.engine is None, "file dialog enabled macro output")
        require(ui.events.empty(), "file dialog left a delayed enable event")
        check("profile_dialog_blocks_hotkey_reentry")
        ui.toggle_macro()
        require(ui.macro_enabled, "legacy settings failed to enable")
        require(type(ui.dd) is pydd.PyDD and recorder.loaded == [module.DD_DLL],
                "normal entry point did not open the HID 63340 library through PyDD")
        report["input_backend"] = type(ui.dd).__name__
        check("dd_keyboard_backend")
        trace("e", ["f10", "f11"], "legacy_upper_hot_without_selection")
        ui.stop_macro()
        pump_until(lambda: not ui.stopping, "legacy macro did not finish stopping")

        configured = {
            **module.DEFAULTS,
            "sy_low": "z",
            "xl_low": "x",
            "target_mode": "ally",
            "target_low_key": "f5",
            "target_ally_key": "tab",
            "ally_hx_low": "1",
            "delay_ms": "5",
            "target_delay_ms": "5",
        }
        ui.apply_config(configured)
        require(ui.collect_config() == configured, "new settings failed UI roundtrip")
        require(ui.ally_check.cget("state") == "normal", "ally workflow checkbox is not available")
        check("new_settings_roundtrip")
        ui.toggle_macro()
        require(ui.macro_enabled, "new settings failed to enable")
        trace("z", ["f5", "f10", "f11"], "lowest_upper_hot_overrides_ally_mode")
        trace("x", ["f5", "f9", "f11"], "lowest_dance_hot_overrides_ally_mode")
        trace("e", ["tab", "f10", "f5", "f11"], "ally_upper_hot_then_lowest_hx")
        trace("3", ["tab", "f9", "f5", "f11"], "ally_dance_hot_then_lowest_hx")
        trace("r", ["f11"], "other_skill_only_appends_hx")
        suppressed, watched = module.hotkey_sets(ui.active_config)
        require("q" not in suppressed | watched, "direct HX was intercepted")
        require("r" in watched and "r" not in suppressed, "ordinary skill pass-through changed")
        check("unchanged_other_skill_bindings")

        ui.stop_macro()
        pump_until(lambda: not ui.stopping, "Yunchang macro did not finish stopping")
        configured = {**configured, "school": "feitian", "ft_loop_ms": "50"}
        ui.apply_config(configured)
        offset = len(recorder.snapshot())
        ui.toggle_macro()
        require(ui.macro_enabled, "Feitian settings failed to enable")
        pump_until(lambda: len(recorder.snapshot()) >= offset + 3, "Feitian loop timed out")
        loop_keys = recorder.snapshot()[offset:offset + 3]
        require(loop_keys == ["f10", "f11", "f12"], f"unexpected Feitian loop: {loop_keys}")
        check("feitian_healing_loop", keys=loop_keys)
        require(len(recorder.worker_ids) == 1, "macro output used more than one worker")
        require(threading.get_ident() not in recorder.worker_ids, "output ran on the Tk thread")
        check("single_output_worker")

        ui.on_close()
        if not ui.closed:
            pump_until(lambda: ui.closed, "application cleanup timed out")
        require(ui.dd.closed and not recorder.held and recorder.freed == [1],
                "application did not finish DD keyboard and library cleanup")
        require(ui.engine.close(timeout=0), "macro worker remains alive after application close")
        require(ui.hook._thread is None, "a global keyboard hook was started")
        require(saved_configs == [configured], "close did not save the expected in-memory settings")
        require(not dialogs and not callbacks, "unexpected error during close")
        check("clean_exit_without_profile_writes")
        report["status"] = "PASS"
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        try:
            if ui is not None:
                ui.closing = True
                if ui.engine is not None:
                    require(ui.engine.close(timeout=0.2), "self-test worker failed to exit")
                require(ui.hook.close(timeout=0.2), "self-test listener failed to exit")
                if not ui.closed and ui.poll_id is not None:
                    root.after_cancel(ui.poll_id)
            if ui is not None and ui.dd is not None:
                ui.dd.close()
            if root is not None and (ui is None or not ui.closed):
                root.destroy()
        except Exception as exc:
            report["status"] = "FAIL"
            report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            report["cleanup_traceback"] = traceback.format_exc()
        for owner, name, original in reversed(patches):
            setattr(owner, name, original)

    report["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    report_path = Path(report_path)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return 1
    return 0 if report["status"] == "PASS" else 1
