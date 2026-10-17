"""Hidden, driver-free smoke checks usable by the packaged executable."""

from __future__ import annotations

import json
import sys
import threading
import time
import traceback
import wave
from pathlib import Path


class _FakeDD:
    """Record output in memory; this class never loads a DLL or sends input."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.worker_ids: set[int] = set()
        self.closed = False

    def key_press(self, key: str) -> None:
        with self._lock:
            if self.closed:
                raise RuntimeError("output attempted after fake driver cleanup")
            self.calls.append(key)
            self.worker_ids.add(threading.get_ident())

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self.calls)

    def close(self) -> None:
        with self._lock:
            self.closed = True


def run(report_path: Path) -> int:
    """Write a UTF-8 JSON PASS/FAIL report and return the process exit code.

    The executable's entry module is preferred so these checks exercise its
    actual class and resources. The static fallback also supports importing
    this function from source and lets the packager discover dependencies.
    No user profile is read or written, no global hook is installed, and all
    requested keys are delivered solely to the in-memory fake driver.
    """
    started = time.monotonic()
    deadline = started + 4.5
    report = {
        "status": "FAIL",
        "frozen": bool(getattr(sys, "frozen", False)),
        "real_driver_loaded": False,
        "global_hook_started": False,
        "checks": [],
    }
    root = ui = None
    driver = _FakeDD()
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
        offset = len(driver.snapshot())
        # Queue a complete tap before the worker can depend on key hold time.
        ui.events.put(("press", trigger))
        ui.events.put(("release", trigger))
        pump_until(lambda: len(driver.snapshot()) >= offset + len(expected), label + " timed out")
        actual = driver.snapshot()[offset:]
        require(actual == expected, f"{label}: expected {expected}, received {actual}")
        check(label, trigger=trigger, keys=actual)

    try:
        module = sys.modules.get("__main__")
        if module is None or not hasattr(module, "YanziHelper"):
            import YanziHelper as module
        import tkinter as tk

        patch(module, "load_config", lambda: dict(module.DEFAULTS))
        patch(module, "save_config", lambda data: saved_configs.append(dict(data)))
        patch(module, "play_sound", lambda *args, **kwargs: None)
        patch(module, "PyDD", forbid_driver)
        patch(module, "DDDriverSession", forbid_driver)
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
        ui = module.YanziHelper(root, driver, start_listener=False)
        root.update_idletasks()
        require(root.state() == "withdrawn", "self-test unexpectedly displayed its window")
        require(ui.hook._thread is None, "self-test unexpectedly started a keyboard listener")
        width, height = root.winfo_reqwidth(), root.winfo_reqheight()
        require(width <= 740 and height <= 780, f"requested layout exceeds default window: {width}x{height}")
        require({"sy_low", "xl_low"} <= set(ui.key_combos), "missing dedicated lowest-health controls")
        check("hidden_ui_and_layout", requested_width=width, requested_height=height)

        for filename in ("start.wav", "stop.wav"):
            with wave.open(str(module.RESOURCE_DIR / filename), "rb") as sound:
                require(sound.getcomptype() == "NONE", filename + " is not PCM audio")
                require(sound.getnframes() > 0 and sound.getnchannels() > 0,
                        filename + " contains no audio frames")
        check("bundled_pcm_sounds_without_playback")
        require(module.DD_DLL.name == "ddhid.63340.dll" and module.DD_DLL.is_file(),
                "matching HID 63340 DLL missing")
        check("matching_hid_dll_bundled_without_loading")

        require(ui.collect_config() == module.DEFAULTS, "legacy default settings changed in UI")
        require(ui.engine is None and not driver.snapshot(), "opening settings unexpectedly emitted output")
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
        require(len(driver.worker_ids) == 1, "macro output used more than one worker")
        require(threading.get_ident() not in driver.worker_ids, "output ran on the Tk thread")
        check("single_output_worker")

        suppressed, watched = module.hotkey_sets(ui.active_config)
        require("q" not in suppressed | watched, "direct HX was intercepted")
        require("r" in watched and "r" not in suppressed, "ordinary skill pass-through changed")
        check("unchanged_other_skill_bindings")

        ui.on_close()
        if not ui.closed:
            pump_until(lambda: ui.closed, "application cleanup timed out")
        require(driver.closed, "application did not close fake driver")
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
            driver.close()
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
