from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | UI-01 | publication trace; see NOTICE.md.
import configparser
import queue
import sys
import time
import tkinter as tk
from contextlib import contextmanager
from pathlib import Path
from tkinter import filedialog, messagebox

from app_config import (DEFAULTS, FEITIAN_GROUPS, TARGET_MODES, action_for, cw_finisher, feitian_steps,
                        hotkey_sets, normalize_key, read_config_file,
                        validate_config, write_config_file)
from keyboard_hook import KeyboardHook
from key_capture import KeyCapture
from desktop_ui import DesktopUI, configure_display
from macro_engine import MacroEngine, PeriodicKey
from pydd import DDError, PyDD
from foreground_guard import ForegroundGuard
from dd_lifecycle import DDDriverSession, StopRequestError, ensure_administrator, run_guardian
from dd_device import RestartRequired
from dd_install import ensure_dd_driver

BASE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
CONFIG_FILE = APP_DIR / "config.ini"
BUNDLED_CONFIG_FILE = RESOURCE_DIR / "config.ini"
DD_DLL = RESOURCE_DIR / "ddhid.63340.dll"
COLOR_TITLE, COLOR_STOP, COLOR_RUN = "#654470", "#8b424d", "#246452"


def load_config():
    # A valid user profile must remain usable even if an older executable
    # contains a malformed bundled default. Never hide errors in that profile.
    external = read_config_file(CONFIG_FILE)
    if BUNDLED_CONFIG_FILE == CONFIG_FILE:
        return {**DEFAULTS, **external}
    try:
        bundled = read_config_file(BUNDLED_CONFIG_FILE)
    except (OSError, configparser.Error, UnicodeError):
        if not external:
            raise
        return validate_config(external)
    return {**DEFAULTS, **bundled, **external}


def save_config(data):
    write_config_file(CONFIG_FILE, data)


def play_sound(filename):
    path = RESOURCE_DIR / filename
    if path.exists():
        try:
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except (RuntimeError, OSError):
            pass


class YanziHelper:
    def __init__(self, root: tk.Tk, dd=None, *, start_listener=True, device_session=None):
        self.root, self.dd = root, dd
        self.device_session = device_session
        self.engine = None
        self.events = queue.Queue()
        self.hook = KeyboardHook(self.events)
        self.foreground_guard = ForegroundGuard()
        self.macro_enabled = self.closing = self.closed = False
        self.profile_dialog_active = False
        self.config_load_failed = False
        self.pending_enable_config = None
        self.stopping = False
        self.stop_error = ""
        self.close_retry_ms = 250
        self.poll_id = None
        self.active_config = dict(DEFAULTS)
        self.all_combos, self.all_entries, self.config_buttons = [], [], []
        self.key_combos, self.entries = {}, {}
        self.cfg = dict(DEFAULTS)
        config_error = ""
        try:
            self.cfg = load_config()
        except (OSError, configparser.Error, UnicodeError, ValueError) as exc:
            self.config_load_failed = True
            config_error = f"读取配置失败：{exc}"
        root.title("妍姿助手")
        icon = RESOURCE_DIR / "24983.png"
        if icon.exists():
            try:
                root.iconphoto(False, tk.PhotoImage(file=str(icon)))
            except tk.TclError:
                pass
        self.key_capture = KeyCapture(self)
        self.view = DesktopUI(self, RESOURCE_DIR)
        for box in self.key_combos.values():
            self.key_capture.bind(box)
        self.refresh_hotkey_cache()
        if start_listener:
            try:
                self.hook.start()
            except OSError as exc:
                self.status_label.config(text=f"● 键盘钩子启动失败：{exc}", fg=COLOR_STOP)
        if config_error:
            self.status_label.config(text=config_error, fg=COLOR_STOP)
        self.poll_events()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    def select_school(self, school):
        if (school == self.view.school or self.macro_enabled or self.stopping or self.closing
                or self.profile_dialog_active or self.pending_enable_config is not None):
            return
        self.key_capture.cancel()
        self.view.select(school)
        self.refresh_hotkey_cache()

    def update_macro_fields(self):
        macro = self.use_game_macro.get()
        self.cb_ft_macro_key.config(state="readonly" if macro else "disabled")
        for group in FEITIAN_GROUPS[2:]:
            for field, _, _ in group:
                if field != "ft_self":
                    self.key_combos[field].config(state="disabled" if macro else "readonly")

    def update_target_state(self):
        state = "normal" if (not self.macro_enabled and not self.stopping and self.pending_enable_config is None and
                             not self.closing and self.cb_target_mode.get() == TARGET_MODES["ally"]) else "disabled"
        self.ally_check.config(state=state)

    def set_all_state(self, enable):
        self.view.lock_navigation(not enable)
        for combo in self.all_combos:
            combo.config(state="readonly" if enable else "disabled")
        for widget in self.all_entries + self.config_buttons:
            widget.config(state="normal" if enable else "disabled")
        if enable:
            self.update_macro_fields()
        self.update_target_state()

    def collect_config(self):
        data = {key: normalize_key(cb.get()) for key, cb in self.key_combos.items()}
        data.update({key: entry.get().strip() for key, entry in self.entries.items()})
        mode = self.cb_target_mode.get()
        data["target_mode"] = next((key for key, label in TARGET_MODES.items() if label == mode), mode)
        data["ally_hx_low"] = "1" if self.ally_hx_low.get() else "0"
        data["foreground_guard"] = "1" if self.foreground_guard_enabled.get() else "0"
        data["school"] = self.view.school
        data["ft_use_macro"] = "1" if self.use_game_macro.get() else "0"
        return data

    def apply_config(self, data):
        self.key_capture.cancel()
        merged = {**DEFAULTS, **data}
        self.view.select(merged["school"])
        self.use_game_macro.set(merged["ft_use_macro"] == "1")
        self.update_macro_fields()
        for key, cb in self.key_combos.items():
            cb.set(merged[key].upper())
        for key, entry in self.entries.items():
            entry.delete(0, tk.END)
            entry.insert(0, merged[key])
        self.cb_target_mode.set(TARGET_MODES.get(merged["target_mode"], merged["target_mode"]))
        self.ally_hx_low.set(merged["ally_hx_low"] == "1")
        self.foreground_guard_enabled.set(merged["foreground_guard"] == "1")
        self.update_target_state()
        self.refresh_hotkey_cache()

    def save_current_config(self):
        if self.macro_enabled or self.stopping or self.pending_enable_config is not None or self.closing or self.profile_dialog_active:
            return
        with self.profile_dialog():
            try:
                save_config(validate_config(self.collect_config()))
                self.config_load_failed = False
            except (ValueError, OSError) as exc:
                messagebox.showerror("保存配置", str(exc), parent=self.root)
                return
            self.status_label.config(text="● 配置已保存", fg=COLOR_TITLE)

    def export_config(self):
        if self.macro_enabled or self.stopping or self.pending_enable_config is not None or self.closing or self.profile_dialog_active:
            return
        with self.profile_dialog():
            try:
                data = validate_config(self.collect_config())
                path = filedialog.asksaveasfilename(title="导出配置", initialdir=str(APP_DIR), initialfile="YanziHelper_profile.ini",
                                                   defaultextension=".ini", filetypes=[("INI 配置文件", "*.ini")], parent=self.root)
                if not path:
                    return
                write_config_file(Path(path), data)
            except (ValueError, OSError) as exc:
                messagebox.showerror("导出配置", str(exc), parent=self.root)
                return
            self.status_label.config(text="● 配置已导出", fg=COLOR_TITLE)

    def import_config(self):
        if self.macro_enabled or self.stopping or self.pending_enable_config is not None or self.closing or self.profile_dialog_active:
            return
        with self.profile_dialog():
            try:
                path = filedialog.askopenfilename(title="导入配置", initialdir=str(APP_DIR), filetypes=[("INI 配置文件", "*.ini")], parent=self.root)
                if not path:
                    return
                data = read_config_file(Path(path))
                if not data:
                    raise ValueError("这个 INI 文件里没有可用的 [SETTINGS] 配置。")
                data = validate_config(data)
                save_config(data)
                self.apply_config(data)
                self.config_load_failed = False
            except (ValueError, OSError, configparser.Error, UnicodeError) as exc:
                messagebox.showerror("导入配置", str(exc), parent=self.root)
                return
            self.status_label.config(text="● 配置已导入", fg=COLOR_TITLE)

    def discard_pending_presses(self):
        retained = []
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] != "press":
                retained.append(event)
        for event in retained:
            self.events.put(event)

    @contextmanager
    def profile_dialog(self):
        # Yukki / ssyb [UI-02]: suspend toggles while profile dialogs own input.
        # Native file dialogs run a nested event loop. Suspend the global toggle
        # too, so typing a filename cannot enable output or queue a later enable.
        self.profile_dialog_active = True
        self.key_capture.cancel()
        self.refresh_hotkey_cache()
        self.discard_pending_presses()
        try:
            yield
        finally:
            self.discard_pending_presses()
            self.profile_dialog_active = False
            self.refresh_hotkey_cache()

    def refresh_hotkey_cache(self):
        cfg = self.active_config if self.macro_enabled else self.collect_config()
        if cfg["school"] == "feitian":
            toggle = cfg["ft_toggle"]
            suppressed, watched = {cfg["ft_begin"], cfg["ft_confirm"]}, set()
            if cfg["ft_cw_begin"]:
                suppressed.add(cfg["ft_cw_begin"])
        else:
            toggle = cfg["toggle"]
            suppressed, watched = hotkey_sets(cfg)
        suspended = self.profile_dialog_active or self.key_capture.active or self.closing
        self.hook.configure("" if suspended else toggle, suppressed, watched,
                            self.macro_enabled and not suspended)

    def stop_macro(self, error=""):
        self.key_capture.cancel()
        self.macro_enabled = False
        self.pending_enable_config = None
        self.stop_error = error
        self.refresh_hotkey_cache()
        if error:
            # A toggle queued before focus loss or an output fault belongs to
            # the old run. It cannot authorize starting in a different window.
            self.discard_pending_presses()
        self.stopping = self.engine is not None and not self.engine.stop()
        if self.stopping:
            self.set_all_state(False)
            self.status_label.config(text="● 正在停止，等待当前按键释放…", fg=COLOR_TITLE)
            return
        self.finish_stop()

    def finish_stop(self):
        self.stopping = False
        self.foreground_guard.clear()
        self.set_all_state(not self.closing)
        self.status_label.config(text=f"● 错误：{self.stop_error}" if self.stop_error else "● 已停止", fg=COLOR_STOP)

    def toggle_macro(self):
        if self.closing or self.stopping or self.profile_dialog_active or self.key_capture.active:
            return
        if self.macro_enabled or self.pending_enable_config is not None:
            self.stop_macro()
            play_sound("stop.wav")
            return
        try:
            cfg = validate_config(self.collect_config())
            if cfg["foreground_guard"] == "1":
                self.foreground_guard.capture()
            else:
                self.foreground_guard.clear()
            if self.device_session is not None:
                self.device_session.check_alive()
            if self.dd is None:
                self.dd = PyDD(DD_DLL)
            if self.engine is None:
                self.engine = MacroEngine(self.dd, self.events, output_allowed=self.foreground_guard.allows_output)
            if not self.engine.ready:
                self.pending_enable_config = cfg
                self.set_all_state(False)
                self.status_label.config(text="● 正在恢复按键状态…", fg=COLOR_TITLE)
                self.engine.recover()
            else:
                self.enable_config(cfg)
        except (ValueError, DDError, OSError) as exc:
            self.stop_macro(str(exc))
            with self.profile_dialog():
                messagebox.showerror("无法启动", str(exc), parent=self.root)

    def enable_config(self, cfg):
        if not self.foreground_guard.allows_output():
            self.stop_macro("目标窗口已离开前台，请回到目标窗口重新启用。")
            return
        self.pending_enable_config = None
        self.active_config = cfg
        self.macro_enabled = True
        self.refresh_hotkey_cache()
        self.set_all_state(False)
        self.status_label.config(text="● 运行中", fg=COLOR_RUN)
        if cfg["school"] == "feitian":
            self.ft_plant_timer = PeriodicKey(cfg["ft_plant"], 20, time.monotonic()) if cfg["ft_plant"] else None
            self.start_feitian_phase(1)
        play_sound("start.wav")

    def start_feitian_phase(self, phase):
        cfg = self.active_config
        self.ft_phase = phase
        trigger = f"feitian-{phase}"
        steps = cw_finisher(cfg) if phase == 4 else feitian_steps(cfg, phase)
        self.engine.submit(trigger, steps, phase == 1, float(cfg["ft_loop_ms"]) / 1000,
                           periodic=self.ft_plant_timer if phase == 1 else None)
        if phase != 1:
            self.engine.release(trigger)
        status = {1: "常态刷血", 2: "召宠中 · 看到圣蝎后再按当前爆发键",
                  3: "献祭并召蝶 · 看到碧蝶后再按当前爆发键", 4: "橙武特效 · 圣手爆菜"}
        self.status_label.config(text="● " + status[phase], fg=COLOR_RUN)

    def feitian_press(self, key):
        cfg = self.active_config
        if self.ft_phase == 1 and key and key in (cfg["ft_begin"], cfg["ft_cw_begin"]):
            self.ft_burst_key = key
            self.engine.release("feitian-1")
            if cfg["ft_use_macro"] == "1":
                self.send_game_macro()
            else:
                self.start_feitian_phase(2)
        elif self.ft_phase == "game_macro" and self.engine.idle:
            if key == self.ft_burst_key:
                self.send_game_macro()
            elif key == cfg["ft_confirm"]:
                self.start_feitian_phase(4 if self.ft_burst_key == cfg["ft_cw_begin"] else 1)
        elif self.ft_phase in (2, 3) and key in (cfg["ft_confirm"], self.ft_burst_key) and self.engine.idle:
            if self.ft_phase == 2:
                self.start_feitian_phase(3)
            elif self.ft_phase == 3:
                self.start_feitian_phase(4 if self.ft_burst_key == cfg["ft_cw_begin"] else 1)

    def send_game_macro(self):
        cfg = self.active_config
        self.ft_phase = "game_macro"
        # Key presses last 40 ms. Eleven taps spaced by 100 ms run for about 1.5 s.
        steps = ((cfg["ft_self"], 0),) + ((cfg["ft_macro_key"], 0.1),) * 11
        self.engine.submit("game_macro", steps)
        self.engine.release("game_macro")
        self.status_label.config(text="● 游戏宏爆发 · 看到碧蝶按通用确认键，未出蝶可重按爆发键", fg=COLOR_RUN)

    def poll_events(self):
        if self.closing:
            return
        if self.device_session is not None:
            try:
                self.device_session.check_alive()
            except DDError as exc:
                self.stop_macro(str(exc))
                with self.profile_dialog():
                    messagebox.showerror("DD 设备监护中断", str(exc), parent=self.root)
                self.on_close()
                return
        if ((self.macro_enabled or self.pending_enable_config is not None)
                and not self.foreground_guard.allows_output()):
            self.stop_macro("目标窗口已离开前台，请回到目标窗口重新启用。")
        # Tk is only accessed on this thread; the hook only enqueues events.
        for _ in range(200):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "error":
                self.stop_macro(value)
            elif event == "stopped" and self.stopping and self.engine.idle:
                self.finish_stop()
            elif event == "recovered" and self.pending_enable_config is not None and self.engine.ready:
                self.enable_config(self.pending_enable_config)
            elif event == "press":
                if self.profile_dialog_active or self.key_capture.active:
                    continue
                cfg = self.active_config if self.macro_enabled else self.collect_config()
                if value == cfg["ft_toggle" if cfg["school"] == "feitian" else "toggle"]:
                    self.toggle_macro()
                elif self.macro_enabled and cfg["school"] == "feitian":
                    self.feitian_press(value)
                elif self.macro_enabled:
                    action = action_for(self.active_config, value)
                    if action:
                        steps, repeat, interval = action
                        if self.engine.submit(value, steps, repeat, interval):
                            sequence = " → ".join(key.upper() for key, _ in steps)
                            self.status_label.config(
                                text=f"● 运行中 · 已收到 {value.upper()}，已提交 {sequence}", fg=COLOR_RUN)
            elif event == "release" and self.engine:
                self.engine.release(value)
        if (self.macro_enabled and self.active_config["school"] == "feitian"
                and self.ft_phase == 4 and self.engine.idle and self.engine.ready):
            self.start_feitian_phase(1)
        self.poll_id = self.root.after(10, self.poll_events)

    def on_close(self):
        if self.closing:
            return
        self.closing = True
        self.stop_macro()
        self.status_label.config(text="● 正在退出，释放按键与监听…", fg=COLOR_TITLE)
        if self.poll_id is not None:
            self.root.after_cancel(self.poll_id)
            self.poll_id = None
        try:
            if not self.config_load_failed:
                save_config(validate_config(self.collect_config()))
        except (OSError, ValueError) as exc:
            messagebox.showerror("配置保存失败", f"{exc}\n本次编辑未保存，已有配置文件会保留。程序将继续退出。", parent=self.root)
        self.finish_close()

    def finish_close(self):
        hook_stopped = self.hook.close(timeout=0.1)
        engine_stopped = self.engine is None or self.engine.close(timeout=0.1)
        if not (hook_stopped and engine_stopped):
            detail = self.hook.cleanup_error or "等待正在发送的按键抬起…"
            self.status_label.config(text=f"● 正在退出：{detail}", fg=COLOR_TITLE)
            self.root.after(100, self.finish_close)
            return
        if self.dd:
            try:
                self.dd.close()
            except (DDError, OSError) as exc:
                self.status_label.config(text=f"● 正在重试释放按键与 DD 驱动：{exc}", fg=COLOR_STOP)
                self.root.after(self.close_retry_ms, self.finish_close)
                self.close_retry_ms = min(self.close_retry_ms * 2, 2000)
                return
        if self.device_session is not None:
            try:
                if not self.device_session.close():
                    self.status_label.config(text="● 正在退出，停用 DD 设备并确认驱动停止…", fg=COLOR_TITLE)
                    self.root.after(100, self.finish_close)
                    return
            except StopRequestError as exc:
                # Hook, worker and DLL are already closed. Exiting the parent
                # lets the independent guardian use its process-death fallback.
                messagebox.showerror("DD 停用请求未送达",
                                     f"{exc}\n程序将退出，由独立监护进程继续尝试停用。"
                                     "当前尚未确认驱动停止；请检查 dd-driver-status.json，必要时重启系统。",
                                     parent=self.root)
            except RestartRequired:
                messagebox.showerror("DD 停用需要重启",
                                     "如需游玩其他游戏，请先重启电脑，确保 DD 驱动完全停止。",
                                     parent=self.root)
            except DDError as exc:
                messagebox.showerror("DD 驱动尚未确认停止",
                                     f"{exc}\n请查看程序目录的 dd-driver-status.json；必要时重启系统。",
                                     parent=self.root)
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except (OSError, RuntimeError):
            pass
        self.closed = True
        self.root.destroy()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if len(sys.argv) == 6 and mode == "--dd-guardian":
        raise SystemExit(run_guardian(int(sys.argv[2]), int(sys.argv[3]),
                                     Path(sys.argv[4]), Path(sys.argv[5])))
    if len(sys.argv) == 3 and mode == "--self-test":
        from app_selftest import run
        configure_display()
        raise SystemExit(run(Path(sys.argv[2])))
    if len(sys.argv) == 3 and mode == "--dd-lifecycle-check":
        from dd_lifecycle_check import run
        raise SystemExit(run(Path(sys.argv[2])))
    try:
        if not ensure_administrator():
            return
    except (DDError, OSError) as exc:
        messagebox.showerror("无法启动", str(exc))
        return
    configure_display()
    root = tk.Tk()
    root.withdraw()
    app = None
    device_session = None
    try:
        if not ensure_dd_driver(APP_DIR / "driver", lambda: messagebox.askyesno(
                "安装 DD 驱动",
                "未检测到 DD HID 63340 驱动。妍姿助手需要安装随包附带的 DD 虚拟键盘驱动才能运行。\n\n"
                "是否现在安装？安装完成后将继续启动妍姿助手。",
                default="no", parent=root)):
            return
        device_session = DDDriverSession(Path(__file__), APP_DIR / "dd-driver-status.json")
        device_session.start()
        app = YanziHelper(root, device_session=device_session)
        root.deiconify()
        root.mainloop()
    except (DDError, OSError) as exc:
        messagebox.showerror("助手启动或运行失败", str(exc), parent=root)
    finally:
        # Normal window closure waits for the guardian in finish_close(). If
        # mainloop exits early, release the sender before requesting shutdown.
        request_stop = app is None
        if app is None:
            root.destroy()
        elif not app.closed:
            app.hook.close()
            request_stop = app.engine is None or app.engine.close()
            if request_stop and app.dd is not None:
                try:
                    app.dd.close()
                except (DDError, OSError):
                    # The guardian also watches the parent process handle.
                    request_stop = False
        if request_stop and device_session is not None:
            try:
                device_session.request_stop()
            except DDError:
                pass  # Parent exit also signals the guardian if IPC has failed.


if __name__ == "__main__":
    main()
