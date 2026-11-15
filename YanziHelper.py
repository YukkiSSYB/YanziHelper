from __future__ import annotations

# YH-ORIGIN: Yukki / ssyb | UI-01 | publication trace; see NOTICE.md.
import configparser
import queue
import sys
import tkinter as tk
from contextlib import contextmanager
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app_config import (DEFAULTS, KEY_OPTIONS, SKILLS, TARGET_MODES, action_for,
                        hotkey_sets, normalize_key, read_config_file,
                        validate_config, write_config_file)
from keyboard_hook import KeyboardHook
from macro_engine import MacroEngine
from pydd import DDError, PyDD
from foreground_guard import ForegroundGuard
from dd_lifecycle import DDDriverSession, StopRequestError, ensure_administrator, run_guardian
from dd_device import RestartRequired

BASE_DIR = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
CONFIG_FILE = APP_DIR / "config.ini"
BUNDLED_CONFIG_FILE = RESOURCE_DIR / "config.ini"
DD_DLL = RESOURCE_DIR / "ddhid.63340.dll"
BG_MAIN, BG_CARD = "#2c2622", "#3d352e"
COLOR_TITLE, COLOR_TEXT = "#f7d96b", "#e0c8a0"
COLOR_STOP, COLOR_RUN = "#ff7777", "#8fff8f"


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
        root.title("YanziHelper")
        root.geometry("740x780")
        root.minsize(740, 780)
        root.configure(bg=BG_MAIN)
        icon = RESOURCE_DIR / "24983.png"
        if icon.exists():
            try:
                root.iconphoto(False, tk.PhotoImage(file=str(icon)))
            except tk.TclError:
                pass
        self.create_ui()
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

    def label(self, parent, text, **kwargs):
        return tk.Label(parent, text=text, bg=BG_CARD, fg=COLOR_TEXT, font=("微软雅黑", 10), **kwargs)

    def create_ui(self):
        main = tk.Frame(self.root, bg=BG_MAIN, padx=20, pady=14)
        main.pack(fill="both", expand=True)
        tk.Label(main, text="YanziHelper", font=("微软雅黑", 18, "bold"), bg=BG_MAIN,
                 fg=COLOR_TITLE).pack(pady=(0, 5))
        self.status_label = tk.Label(main, text="● 已停止", font=("微软雅黑", 11),
                                    bg=BG_MAIN, fg=COLOR_STOP, wraplength=680)
        self.status_label.pack(pady=(0, 8))
        timing = tk.Frame(main, bg=BG_CARD)
        timing.pack(fill="x", pady=(0, 8))
        self.label(timing, "总开关").grid(row=0, column=0, sticky="w", padx=10, pady=7)
        self.make_combo(timing, "toggle", 0, 1)
        self.make_entry(timing, "delay_ms", "接回雪延迟(ms)", 0, 2)
        self.make_entry(timing, "loop_interval_s", "二段回雪时间(s)", 1, 0)
        self.make_entry(timing, "target_delay_ms", "选人后等待(ms)", 1, 2)
        skills = tk.Frame(main, bg=BG_CARD)
        skills.pack(fill="x", pady=(0, 8))
        for col, text in enumerate(("技能", "普通触发键", "游戏第二快捷键", "最低血量专用触发键")):
            self.label(skills, text).grid(row=0, column=col, padx=10, pady=(8, 4), sticky="w")
        for row, (name, prefix) in enumerate(SKILLS, 1):
            self.label(skills, name).grid(row=row, column=0, sticky="w", padx=10, pady=5)
            self.make_combo(skills, f"{prefix}1", row, 1)
            self.make_combo(skills, f"{prefix}2", row, 2)
            if prefix in {"sy", "xl"}:
                self.make_combo(skills, f"{prefix}_low", row, 3)
            else:
                self.label(skills, "—").grid(row=row, column=3)
        target = tk.Frame(main, bg=BG_CARD)
        target.pack(fill="x", pady=(0, 8))
        self.label(target, "上元／翔舞普通键选人模式").grid(row=0, column=0, sticky="w", padx=10, pady=7)
        self.cb_target_mode = ttk.Combobox(target, values=list(TARGET_MODES.values()), state="readonly", width=14)
        self.cb_target_mode.set(TARGET_MODES.get(self.cfg["target_mode"], self.cfg["target_mode"]))
        self.cb_target_mode.grid(row=0, column=1, padx=6, pady=6, sticky="w")
        self.cb_target_mode.bind("<<ComboboxSelected>>", lambda event: self.update_target_state())
        self.all_combos.append(self.cb_target_mode)
        self.label(target, "游戏：选择最低血量").grid(row=1, column=0, sticky="w", padx=10, pady=5)
        self.make_combo(target, "target_low_key", 1, 1)
        self.label(target, "游戏：选择盟友").grid(row=2, column=0, sticky="w", padx=10, pady=5)
        self.make_combo(target, "target_ally_key", 2, 1)
        self.ally_hx_low = tk.BooleanVar(value=self.cfg["ally_hx_low"] == "1")
        self.ally_check = tk.Checkbutton(target, text="选盟友铺 HOT 后，回雪前切到最低血量", variable=self.ally_hx_low,
                                        bg=BG_CARD, fg=COLOR_TEXT, selectcolor="#554a42", activebackground=BG_CARD,
                                        activeforeground=COLOR_TEXT, font=("微软雅黑", 10))
        self.ally_check.grid(row=3, column=0, columnspan=3, padx=7, pady=(3, 8), sticky="w")
        self.update_target_state()
        buttons = tk.Frame(main, bg=BG_MAIN)
        buttons.pack(fill="x", pady=(0, 6))
        for text, command in (("保存当前", self.save_current_config), ("导出配置", self.export_config),
                              ("导入配置", self.import_config)):
            button = tk.Button(buttons, text=text, command=command, font=("微软雅黑", 9), bg="#554a42", fg="white",
                               activebackground="#6a5b4f", activeforeground="white", bd=0, padx=12, pady=6)
            button.pack(side="left", padx=(0, 8))
            self.config_buttons.append(button)
        self.foreground_guard_enabled = tk.BooleanVar(value=self.cfg["foreground_guard"] == "1")
        self.foreground_check = tk.Checkbutton(
            buttons, text="前台保护（在游戏窗口按开关）", variable=self.foreground_guard_enabled,
            bg=BG_MAIN, fg=COLOR_TEXT, selectcolor="#554a42", activebackground=BG_MAIN,
            activeforeground=COLOR_TEXT, font=("微软雅黑", 9))
        self.foreground_check.pack(side="right")
        self.config_buttons.append(self.foreground_check)
        for tip in (
            "上元、翔舞：普通键按所选模式选人；专用键始终先选最低血量。空白表示未设置。",
            "短按施放一次并接回雪；长按重复，松开停止重复。专用键也使用同一游戏第二快捷键。",
            "回雪、风袖、王母、婆罗门沿用原有操作；游戏选人键须与游戏内设置一致。",
            "选盟友由游戏决定循环顺序，可能重复选人；鼠标右键方案暂未启用。",
        ):
            tk.Label(main, text=tip, bg=BG_MAIN, fg=COLOR_TEXT, font=("微软雅黑", 9),
                     justify="left", wraplength=690).pack(anchor="w", pady=2)
        tk.Label(main, text="原作者：@小柴胡。优化使用：@汜水月白。", bg=BG_MAIN,
                 fg=COLOR_TITLE, font=("微软雅黑", 9)).pack(anchor="w", pady=(6, 0))
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground="#554a42", background="#554a42", foreground="white")
        style.map("TCombobox", fieldbackground=[("readonly", "#554a42")], foreground=[("readonly", "white")])

    def make_combo(self, parent, key, row, column):
        cb = ttk.Combobox(parent, values=KEY_OPTIONS, width=10, state="readonly")
        cb.set(self.cfg[key])
        cb.grid(row=row, column=column, padx=6, pady=5, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda event: self.refresh_hotkey_cache())
        self.key_combos[key] = cb
        setattr(self, f"cb_{key}", cb)
        self.all_combos.append(cb)
        return cb

    def make_entry(self, parent, key, text, row, column):
        self.label(parent, text).grid(row=row, column=column, sticky="w", padx=10, pady=6)
        entry = tk.Entry(parent, font=("微软雅黑", 10), width=10, bg="#554a42", fg="white", bd=0)
        entry.insert(0, self.cfg[key])
        entry.grid(row=row, column=column + 1, padx=6, pady=6, sticky="w")
        self.entries[key] = entry
        self.all_entries.append(entry)

    def update_target_state(self):
        state = "normal" if (not self.macro_enabled and not self.stopping and self.pending_enable_config is None and
                             not self.closing and self.cb_target_mode.get() == TARGET_MODES["ally"]) else "disabled"
        self.ally_check.config(state=state)

    def set_all_state(self, enable):
        for combo in self.all_combos:
            combo.config(state="readonly" if enable else "disabled")
        for widget in self.all_entries + self.config_buttons:
            widget.config(state="normal" if enable else "disabled")
        self.update_target_state()

    def collect_config(self):
        data = {key: normalize_key(cb.get()) for key, cb in self.key_combos.items()}
        data.update({key: entry.get().strip() for key, entry in self.entries.items()})
        mode = self.cb_target_mode.get()
        data["target_mode"] = next((key for key, label in TARGET_MODES.items() if label == mode), mode)
        data["ally_hx_low"] = "1" if self.ally_hx_low.get() else "0"
        data["foreground_guard"] = "1" if self.foreground_guard_enabled.get() else "0"
        return data

    def apply_config(self, data):
        merged = {**DEFAULTS, **data}
        for key, cb in self.key_combos.items():
            cb.set(merged[key])
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
        suppressed, watched = hotkey_sets(cfg)
        suspended = self.profile_dialog_active or self.closing
        self.hook.configure("" if suspended else cfg["toggle"], suppressed, watched,
                            self.macro_enabled and not suspended)

    def stop_macro(self, error=""):
        self.macro_enabled = False
        self.pending_enable_config = None
        self.stop_error = error
        self.refresh_hotkey_cache()
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
        if self.closing or self.stopping or self.profile_dialog_active:
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
            # Opening settings alone must not initialize the input driver.
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
        play_sound("start.wav")

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
                if self.profile_dialog_active:
                    continue
                if value == (self.active_config["toggle"] if self.macro_enabled else normalize_key(self.cb_toggle.get())):
                    self.toggle_macro()
                elif self.macro_enabled:
                    action = action_for(self.active_config, value)
                    if action:
                        steps, repeat, interval = action
                        self.engine.submit(value, steps, repeat, interval)
            elif event == "release" and self.engine:
                self.engine.release(value)
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
                save_config(self.collect_config())
        except OSError as exc:
            messagebox.showerror("配置保存失败", f"{exc}\n程序仍会完成退出清理。", parent=self.root)
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
            except DDError as exc:
                self.status_label.config(text=f"● 正在重试释放按键与驱动：{exc}", fg=COLOR_STOP)
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
            except DDError as exc:
                messagebox.showerror("DD 停用需要重启" if isinstance(exc, RestartRequired) else "DD 驱动尚未确认停止",
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
    if len(sys.argv) == 6 and sys.argv[1] == "--dd-guardian":
        raise SystemExit(run_guardian(int(sys.argv[2]), int(sys.argv[3]),
                                     Path(sys.argv[4]), Path(sys.argv[5])))
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from app_selftest import run
        raise SystemExit(run(Path(sys.argv[2])))
    if len(sys.argv) == 3 and sys.argv[1] == "--dd-lifecycle-check":
        from dd_lifecycle_check import run
        raise SystemExit(run(Path(sys.argv[2])))
    try:
        if not ensure_administrator():
            return
    except DDError as exc:
        messagebox.showerror("无法启动", str(exc))
        return
    root = tk.Tk()
    root.withdraw()
    app = None
    device_session = DDDriverSession(Path(__file__), APP_DIR / "dd-driver-status.json")
    try:
        device_session.start()
        app = YanziHelper(root, device_session=device_session)
        root.deiconify()
        root.mainloop()
    except (DDError, OSError) as exc:
        messagebox.showerror("助手启动或运行失败", str(exc), parent=root)
    finally:
        # Also cover unexpected mainloop termination; never unload under a sender.
        if app is not None:
            app.hook.close()
            stopped = app.engine is None or app.engine.close()
            if stopped and app.dd is not None:
                try:
                    app.dd.close()
                except DDError:
                    # Normal window closing retries visibly in finish_close().
                    # Do not replace an unrelated mainloop exception here.
                    pass
            request_stop = stopped
        else:
            request_stop = True
        if request_stop:
            try:
                device_session.request_stop()
            except DDError:
                # A failed IPC write must not prevent the parent from exiting:
                # the guardian also watches this process handle independently.
                pass


if __name__ == "__main__":
    main()
