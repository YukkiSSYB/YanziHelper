"""Explicit administrator-only integration check; never called by the build.

Exercises real device start/stop and DD initialization, without sending input,
installing a keyboard hook or changing the user's configuration.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import traceback

from dd_device import DDDeviceController, RestartRequired
from dd_lifecycle import DDDriverSession, ProcessHandles, write_state


def run(report_path):
    module = sys.modules.get("__main__")
    if module is None or not hasattr(module, "YanziHelper"):
        import YanziHelper as module
    import tkinter as tk

    report = {"status": "RUNNING", "pid": os.getpid(), "sent_input": False,
              "global_hook_started": False, "frozen": bool(getattr(sys, "frozen", False))}
    session = driver = root = ui = None
    controller = DDDeviceController()
    try:
        report["before"] = controller.backend.snapshot()
        write_state(report_path, **report)
        # Establish and verify the default-disabled state while excluding any
        # other managed assistant. Release before handing ownership to guardian.
        handles = ProcessHandles()
        mutex = handles.acquire()
        try:
            try:
                report["baseline_disabled"] = controller.stop()
            except RestartRequired as exc:
                report["baseline_restart_required"] = str(exc)
        finally:
            handles.release(mutex)
        session = DDDriverSession(Path(module.__file__), report_path.with_name("dd-driver-status.json"))
        session.start()
        report["started"] = controller.backend.snapshot()
        report["phase"] = "initializing_dll"
        write_state(report_path, **report)
        driver = module.PyDD(module.DD_DLL)  # DD_btn(0) only; no input calls.
        report["dll_initialized"] = module.DD_DLL.name
        module.load_config = lambda: dict(module.DEFAULTS)
        module.save_config = lambda data: None
        module.play_sound = lambda *args: None
        dialogs = []
        module.messagebox.showerror = lambda title, message, **kw: dialogs.append(f"{title}: {message}")
        root = tk.Tk()
        root.withdraw()
        ui = module.YanziHelper(root, driver, start_listener=False, device_session=session)
        ui.on_close()
        deadline = time.monotonic() + 40
        while not ui.closed:
            root.update()
            if time.monotonic() > deadline:
                raise TimeoutError("Application close timed out")
            time.sleep(.02)
        report["dialogs"] = dialogs
        if dialogs and any("DD 停用需要重启" not in message for message in dialogs):
            raise RuntimeError("; ".join(dialogs))
        report["after_close"] = controller.backend.snapshot()
        if dialogs:
            report.update(status="RESTART_REQUIRED", phase="complete")
            return 2
        if report["after_close"]["service_state"] != 1:
            raise AssertionError("DD service still running after application close")
        report.update(status="PASS", phase="complete")
        return 0
    except Exception:
        report.update(status="FAIL", error=traceback.format_exc())
        return 1
    finally:
        if driver is not None:
            driver.close()
        if session is not None and not session.finished:
            session.request_stop()
            deadline = time.monotonic() + 35
            try:
                while not session.close() and time.monotonic() < deadline:
                    time.sleep(.1)
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        if root is not None and (ui is None or not ui.closed):
            root.destroy()
        report["final"] = controller.backend.snapshot()
        write_state(report_path, **report)
