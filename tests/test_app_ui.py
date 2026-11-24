import configparser
import queue
import tkinter as tk
import time
import unittest
from itertools import combinations, product
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import YanziHelper as app
from app_config import DEFAULTS, TARGET_MODES
from dd_lifecycle import DDDriverSession


class AppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.patches = [patch.object(app, "load_config", return_value=dict(DEFAULTS)),
                        patch.object(app, "save_config"), patch.object(app, "play_sound"),
                        patch.object(app.messagebox, "showerror")]
        self.loaded, self.save, self.sound, self.error = [item.start() for item in self.patches]
        self.driver = MagicMock()
        self.ui = app.YanziHelper(self.root, self.driver, start_listener=False)
        self.root.update_idletasks()

    def tearDown(self):
        if self.ui.engine and not self.ui.closed:
            self.ui.engine.close()
        self.ui.hook.close()
        try:
            if self.ui.poll_id is not None:
                self.root.after_cancel(self.ui.poll_id)
            self.root.destroy()
        except tk.TclError:
            pass
        for item in reversed(self.patches):
            item.stop()

    def drain(self):
        self.root.after_cancel(self.ui.poll_id)
        self.ui.poll_events()

    def test_old_profile_has_no_added_behavior(self):
        self.assertEqual(DEFAULTS, self.ui.collect_config())
        self.assertEqual({"sy_low", "xl_low"}, {k for k in self.ui.key_combos if k.endswith("_low")})
        self.assertIsNone(self.ui.engine)
        self.driver.assert_not_called()

    def test_layout_on_both_pages_at_default_and_small_window_sizes(self):
        self.assertEqual(self.root.title(), "妍姿助手")
        self.root.attributes("-alpha", 0)
        self.root.deiconify()
        self.root.update()
        view = self.ui.view
        common = {"status", "yunchang", "feitian", "foreground_guard", "import", "export", "save"}
        fields = set(DEFAULTS) - {"school", "foreground_guard"}
        feitian_fields = {key for key in fields if key.startswith("ft_")}
        for size in ("default", "minimum"):
            if size == "minimum":
                self.root.geometry("%dx%d" % self.root.minsize())
                self.root.update()
                self.assertTrue(view.horizontal.winfo_ismapped())
                self.assertTrue(view.vertical.winfo_ismapped())
            for school in ("yunchang", "feitian"):
                with self.subTest(size=size, school=school):
                    view.widgets[school].invoke()
                    self.root.update()
                    expected = common | (feitian_fields if school == "feitian" else fields - feitian_fields)
                    canvas = view.canvas
                    shown = {key for key, item in view.windows.items()
                             if canvas.itemcget(item, "state") != "hidden"}
                    self.assertEqual(shown, expected)
                    self.assertEqual(str(view.widgets[school].cget("style")), "Selected.TButton")
                    self.assertTrue(canvas.itemcget(view.background, "image"))
                    boxes = {key: canvas.bbox(view.windows[key]) for key in shown}
                    for key, box in boxes.items():
                        widget = view.widgets[key]
                        if widget.winfo_class() in {"TCombobox", "TEntry"}:
                            self.assertGreaterEqual(box[3] - box[1], widget.winfo_reqheight(), key)
                    for item in canvas.find_withtag("ink"):
                        if canvas.type(item) == "text":
                            boxes[(item, canvas.itemcget(item, "text"))] = canvas.bbox(item)
                    _, _, width, height = map(int, canvas.cget("scrollregion").split())
                    for name, (x1, y1, x2, y2) in boxes.items():
                        self.assertTrue(0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height, name)
                    for (name, a), (other, b) in combinations(boxes.items(), 2):
                        self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3],
                                         f"{name} {a} overlaps {other} {b}")
                    # Canvas unmaps windows that have scrolled completely offscreen.
                    # Every active page control must be fully reachable via the scrollbars.
                    reached = set()
                    for x, y in product((0, 1), repeat=2):
                        canvas.xview_moveto(x)
                        canvas.yview_moveto(y)
                        self.root.update_idletasks()
                        for key in shown:
                            widget = view.widgets[key]
                            if (widget.winfo_ismapped() and widget.winfo_x() >= 0 and widget.winfo_y() >= 0
                                    and widget.winfo_x() + widget.winfo_width() <= canvas.winfo_width()
                                    and widget.winfo_y() + widget.winfo_height() <= canvas.winfo_height()):
                                if key not in reached and key in self.ui.key_combos:
                                    original = widget.get()
                                    for value in ("SPACE", "NUM0"):
                                        widget.set(value)
                                        widget.xview_moveto(0)
                                        self.assertEqual(widget.xview(), (0.0, 1.0), (key, value))
                                    widget.set(original)
                                reached.add(key)
                    self.assertEqual(reached, expected)

    def test_sidebar_cannot_switch_a_running_profile(self):
        self.ui.engine = MagicMock(ready=True, idle=True)
        view = self.ui.view
        for school, other in (("yunchang", "feitian"), ("feitian", "yunchang")):
            view.widgets[school].invoke()
            self.ui.toggle_macro()
            self.assertTrue(self.ui.macro_enabled)
            self.assertTrue(view.widgets[other].instate(["disabled"]))
            view.widgets[other].invoke()
            self.assertEqual(self.ui.collect_config()["school"], school)
            self.ui.toggle_macro()
            self.assertFalse(view.widgets[other].instate(["disabled"]))
            view.widgets[other].invoke()
            self.assertEqual(self.ui.collect_config()["school"], other)

    def test_mode_and_extra_keys_round_trip(self):
        data = {**DEFAULTS, "target_mode": "ally", "target_low_key": "f5", "target_ally_key": "tab",
                "sy_low": "z", "xl_low": "x", "ally_hx_low": "1"}
        self.ui.apply_config(data)
        self.assertEqual(self.ui.collect_config(), data)
        self.assertEqual(self.ui.ally_check.cget("state"), "normal")
        self.ui.cb_target_mode.set(TARGET_MODES["none"])
        self.ui.update_target_state()
        self.assertEqual(self.ui.ally_check.cget("state"), "disabled")

    def test_feitian_loop_burst_and_manual_confirmations(self):
        self.ui.apply_config({**DEFAULTS, "school": "feitian", "ft_loop_ms": "20", "ft_burst_ms": "20"})
        def press(key):
            self.ui.events.put(("press", key))
            self.drain()

        def wait_until(predicate):
            deadline = time.monotonic() + 2
            while not predicate() and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(predicate())

        def keys():
            return [call.args[0] for call in self.driver.key_press.call_args_list]

        press("f3")
        wait_until(lambda: len(keys()) >= 4)
        self.assertEqual(keys()[:4], ["f10", "f11", "f12", "f10"])
        press("f4")
        press("f5")
        wait_until(lambda: self.ui.engine.idle)
        self.assertEqual(keys()[-3:], ["f1", "1", "2"])
        press("f5")
        wait_until(lambda: self.ui.engine.idle)
        self.assertEqual(keys()[-3:], ["3", "4", "5"])
        press("f5")
        wait_until(lambda: keys()[-1] == "f12")
        press("f3")
        self.assertFalse(self.ui.macro_enabled)

    def test_burst_group_preserves_both_stages_when_switching_macro_mode(self):
        config = {**DEFAULTS, "school": "feitian", "ft_self": "f2", "ft_snake": "a",
                  "ft_scorpion": "b", "ft_sacrifice": "c", "ft_nuwa": "d", "ft_butterfly": "e",
                  "ft_macro_key": "g"}
        self.ui.apply_config(config)
        self.root.attributes("-alpha", 0)
        self.root.deiconify()
        self.root.update()
        view = self.ui.view
        canvas = view.canvas
        labels = [canvas.itemcget(item, "text") for item in canvas.find_withtag("ink")
                  if canvas.type(item) == "text"]
        for name in ("爆发流程", "召宠", "献祭召蝶", "选择自己", "灵蛇引", "圣蝎引", "蛊虫献祭", "女娲补天", "碧蝶引"):
            self.assertEqual(labels.count(name), 1, name)
        manual_fields = ("ft_snake", "ft_scorpion", "ft_sacrifice", "ft_nuwa", "ft_butterfly")
        for macro in (True, False):
            view.widgets["ft_use_macro"].invoke()
            for field in manual_fields:
                self.assertEqual(view.widgets[field].instate(["disabled"]), macro, field)
            self.assertTrue(view.widgets["ft_self"].instate(["readonly", "!disabled"]))
            self.assertEqual(view.widgets["ft_macro_key"].instate(["disabled"]), not macro)
            self.assertEqual(self.ui.collect_config(), {**config, "ft_use_macro": str(int(macro))})

    def test_feitian_overdue_plant_waits_for_resume_and_fires_once(self):
        self.ui.apply_config({**DEFAULTS, "school": "feitian", "ft_plant": "g", "ft_burst_ms": "10"})
        self.ui.toggle_macro()
        timer = self.ui.ft_plant_timer
        deadline = time.monotonic() + 2
        while self.driver.key_press.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual([call.args for call in self.driver.key_press.call_args_list][:2], [("g",), ("f10",)])
        self.assertAlmostEqual(timer.due_at - time.monotonic(), 20, delta=1)
        self.driver.key_press.reset_mock()
        self.ui.feitian_press("f4")

        def wait_idle():
            deadline = time.monotonic() + 2
            while not self.ui.engine.idle and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertTrue(self.ui.engine.idle)

        wait_idle()
        timer.due_at = time.monotonic() - 60
        self.ui.feitian_press("f5")
        wait_idle()
        self.assertNotIn(("g",), [call.args for call in self.driver.key_press.call_args_list])
        self.driver.key_press.reset_mock()
        self.ui.feitian_press("f5")
        deadline = time.monotonic() + 2
        while self.driver.key_press.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual([call.args for call in self.driver.key_press.call_args_list][:2], [("g",), ("f10",)])
        self.assertAlmostEqual(timer.due_at - time.monotonic(), 20, delta=1)

    def test_distinct_burst_keys_keep_the_selected_branch(self):
        cfg = {**DEFAULTS, "school": "feitian", "ft_cw_begin": "f6", "ft_weapon": "8", "ft_shengshou": "9"}
        self.ui.apply_config(cfg)
        engine = self.ui.engine = MagicMock()
        engine.idle = engine.ready = True
        self.ui.toggle_macro()
        self.assertIn("f6", self.ui.hook._state.suppressed)
        for trigger, other in (("f6", "f4"), ("f4", "f6")):
            self.ui.feitian_press(trigger)
            self.ui.feitian_press(other)
            self.assertEqual(self.ui.ft_phase, 2)
            engine.idle = False
            self.ui.feitian_press(trigger)
            self.assertEqual(self.ui.ft_phase, 2)
            engine.idle = True
            self.ui.feitian_press(trigger)
            self.assertEqual(self.ui.ft_phase, 3)
            self.ui.feitian_press(trigger)
            if trigger == "f6":
                self.assertEqual(self.ui.ft_phase, 4)
                keys = [key for key, _ in engine.submit.call_args.args[1]]
                self.assertEqual(keys, ["8", "f12"] + ["9", "f10", "f11", "f12"] * 4)
                self.drain()
            self.assertEqual(self.ui.ft_phase, 1)

    def test_game_macro_mode_retries_and_confirms_each_branch(self):
        cfg = {**DEFAULTS, "school": "feitian", "ft_cw_begin": "f6", "ft_weapon": "8",
               "ft_shengshou": "9", "ft_use_macro": "1", "ft_macro_key": DEFAULTS["ft_snake"]}
        self.ui.apply_config(cfg)
        self.assertEqual(self.ui.collect_config(), cfg)
        engine = self.ui.engine = MagicMock()
        engine.idle = engine.ready = True
        self.ui.toggle_macro()
        for key, phase in (("f4", 1), ("f6", 4)):
            for _ in range(2):
                self.ui.feitian_press(key)
                self.assertEqual(self.ui.ft_phase, "game_macro")
                steps = engine.submit.call_args.args[1]
                self.assertEqual([k for k, _ in steps], ["f1"] + [cfg["ft_macro_key"]] * 11)
            self.ui.feitian_press("f5")
            self.assertEqual(self.ui.ft_phase, phase)

    def test_enable_opens_hid_dll_and_close_releases_it(self):
        self.ui.dd = None
        with patch.object(app, "PyDD", return_value=self.driver) as factory:
            self.ui.save_current_config()
            factory.assert_not_called()
            self.ui.toggle_macro()
            factory.assert_called_once_with(app.DD_DLL)
            self.assertTrue(self.ui.macro_enabled)
            self.assertEqual(str(self.ui.cb_sy_low.cget("state")), "disabled")
            self.ui.toggle_macro()
            self.ui.on_close()
            self.assertTrue(self.ui.closed)
            self.driver.close.assert_called_once_with()

    def test_invalid_enable_does_not_load_driver(self):
        self.ui.dd = None
        self.ui.cb_sy_low.set(DEFAULTS["sy1"])
        with patch.object(app, "PyDD") as factory:
            self.ui.toggle_macro()
            factory.assert_not_called()
        self.assertFalse(self.ui.macro_enabled)
        self.error.assert_called_once()

    def test_start_failure_dialog_discards_hotkeys_before_manual_retry(self):
        self.ui.dd = None

        def show_error(*args, **kwargs):
            self.assertTrue(self.ui.profile_dialog_active)
            self.assertEqual(self.ui.hook._state.toggle, "")
            for down in (True, False):
                _, events = self.ui.hook._state.transition(DEFAULTS["toggle"], down)
                self.assertEqual(events, ())
            self.ui.toggle_macro()
            # Also discard a press already in flight when the dialog opened.
            self.ui.events.put(("press", DEFAULTS["toggle"]))

        self.error.side_effect = show_error
        with patch.object(app, "PyDD", side_effect=[app.DDError("temporary failure"), self.driver]) as factory:
            self.ui.events.put(("press", DEFAULTS["toggle"]))
            self.drain()
            self.assertFalse(self.ui.macro_enabled)
            self.assertIsNone(self.ui.engine)
            self.assertTrue(self.ui.events.empty())
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(self.ui.hook._state.toggle, DEFAULTS["toggle"])
            self.ui.toggle_macro()
            self.assertTrue(self.ui.macro_enabled)
            self.assertEqual(factory.call_count, 2)

    def test_queued_presses_use_snapshot_and_correct_plan(self):
        self.ui.apply_config({**DEFAULTS, "sy_low": "z", "target_low_key": "f5"})
        fake_engine = MagicMock()
        with patch.object(app, "MacroEngine", return_value=fake_engine):
            self.ui.toggle_macro()
        self.ui.events.put(("press", "z"))
        self.ui.events.put(("release", "z"))
        self.drain()
        fake_engine.submit.assert_called_once_with("z", (("f5", 0.0), ("f10", .04), ("f11", .04)), True, 1.2)
        fake_engine.release.assert_called_once_with("z")

    def test_yunchang_shows_accepted_sequence_until_an_output_error(self):
        self.ui.apply_config({**DEFAULTS, "sy1": "2", "sy2": "num5", "hx2": "num7", "fx1": ""})
        engine = self.ui.engine = MagicMock()
        engine.submit.side_effect = [True, False]
        self.ui.toggle_macro()
        self.ui.events.put(("press", "2"))
        self.drain()
        status = self.ui.status_label.cget("text")
        self.assertIn("已收到 2，已提交 NUM5 → NUM7", status)
        self.ui.events.put(("press", "3"))
        self.drain()
        self.assertEqual(self.ui.status_label.cget("text"), status)
        self.ui.events.put(("error", "按键输出失败"))
        self.ui.events.put(("press", "2"))
        self.drain()
        self.assertEqual(self.ui.status_label.cget("text"), "● 错误：按键输出失败")
        self.assertEqual(engine.submit.call_count, 2)

    def test_worker_error_disables_before_queued_skill(self):
        fake_engine = MagicMock()
        with patch.object(app, "MacroEngine", return_value=fake_engine):
            self.ui.toggle_macro()
        self.ui.events.put(("error", "driver failure"))
        self.ui.events.put(("press", "e"))
        self.drain()
        self.assertFalse(self.ui.macro_enabled)
        fake_engine.stop.assert_called_once()
        fake_engine.submit.assert_not_called()
        self.assertIn("driver failure", self.ui.status_label.cget("text"))

    def test_rapid_toggle_does_not_replay_pending_skill(self):
        fake_engine = MagicMock()
        with patch.object(app, "MacroEngine", return_value=fake_engine):
            self.ui.events.put(("press", "f3"))
            self.ui.events.put(("release", "f3"))
            self.ui.events.put(("press", "f3"))
            self.ui.events.put(("press", "e"))
            self.drain()
        self.assertFalse(self.ui.macro_enabled)
        fake_engine.submit.assert_not_called()

    def test_close_waits_for_worker_before_unloading(self):
        fake_engine = MagicMock()
        fake_engine.close.side_effect = [False, True]
        self.ui.engine = fake_engine
        with patch.object(self.root, "after") as schedule:
            self.ui.on_close()
            self.driver.close.assert_not_called()
            schedule.assert_called_once_with(100, self.ui.finish_close)
        self.ui.finish_close()
        self.assertTrue(self.ui.closed)
        self.driver.close.assert_called_once()

    def test_device_stop_happens_after_dll_release_and_delays_window_close(self):
        session = MagicMock()
        session.close.side_effect = [False, True]
        order = MagicMock()
        order.attach_mock(self.driver.close, "dll_close")
        order.attach_mock(session.close, "device_close")
        self.ui.device_session = session
        with patch.object(self.root, "after") as schedule:
            self.ui.on_close()
            self.assertFalse(self.ui.closed)
            schedule.assert_called_once_with(100, self.ui.finish_close)
        self.assertEqual([call[0] for call in order.mock_calls], ["dll_close", "device_close"])
        self.ui.finish_close()
        self.assertTrue(self.ui.closed)
        self.error.assert_not_called()

    def test_device_stop_failure_is_visible_and_not_an_infinite_exit_loop(self):
        self.ui.device_session = MagicMock()
        self.ui.device_session.close.side_effect = app.DDError("needs reboot")
        self.ui.on_close()
        self.assertTrue(self.ui.closed)
        self.error.assert_called_once()
        self.assertIn("needs reboot", self.error.call_args.args[1])

    def test_device_stop_requiring_restart_displays_exit_reminder(self):
        self.ui.device_session = MagicMock()
        self.ui.device_session.close.side_effect = app.RestartRequired("driver still running")
        self.ui.on_close()
        self.error.assert_called_once_with(
            "DD 停用需要重启", "如需游玩其他游戏，请先重启电脑，确保 DD 驱动完全停止。", parent=self.root
        )
        self.assertTrue(self.ui.closed)

    def test_missing_stop_directory_exits_after_dll_release_for_guardian_fallback(self):
        with TemporaryDirectory(prefix="yanzi-stop-test-") as folder:
            session = DDDriverSession(Path(folder) / "entry.py", Path(folder) / "report.json")
            session.folder = Path(folder) / "missing-session"
            session.process = MagicMock()
            session.process.poll.return_value = None
            self.ui.device_session = session

            def show_error(*args, **kwargs):
                self.driver.close.assert_called_once()
                self.assertEqual(self.ui.hook._state.toggle, "")

            self.error.side_effect = show_error
            self.ui.on_close()
            self.assertTrue(self.ui.closed)
            self.assertIsNone(self.ui.poll_id)
            self.assertFalse(session.stop_requested)
            self.assertFalse(session.finished)
            self.error.assert_called_once()
            self.assertIn("独立监护", self.error.call_args.args[1])
            self.assertIn("尚未确认", self.error.call_args.args[1])
            session.process.terminate.assert_not_called()
            session.process.kill.assert_not_called()

    def test_save_failure_does_not_skip_cleanup(self):
        self.save.side_effect = OSError("disk full")
        self.ui.on_close()
        self.driver.close.assert_called_once()
        self.assertTrue(self.ui.closed)
        self.error.assert_called_once()

    def test_import_suspends_toggle_and_discards_modal_presses(self):
        imported = {**DEFAULTS, "sy1": "z", "delay_ms": "500"}

        def choose(**kwargs):
            self.assertTrue(self.ui.profile_dialog_active)
            self.assertEqual(self.ui.hook._state.toggle, "")
            self.ui.toggle_macro()
            self.ui.events.put(("press", DEFAULTS["toggle"]))
            self.ui.events.put(("error", "preserve diagnostics"))
            return "profile.ini"

        with patch.object(app.filedialog, "askopenfilename", side_effect=choose), \
                patch.object(app, "read_config_file", return_value=imported):
            self.ui.import_config()
        self.assertFalse(self.ui.macro_enabled)
        self.assertIsNone(self.ui.engine)
        self.assertEqual(self.ui.collect_config(), imported)
        self.save.assert_called_once_with(imported)
        self.assertEqual(self.ui.hook._state.toggle, DEFAULTS["toggle"])
        self.assertEqual(self.ui.events.get_nowait(), ("error", "preserve diagnostics"))
        self.assertTrue(self.ui.events.empty())

    def test_export_suspends_toggle_and_restores_it_on_cancel(self):
        def choose(**kwargs):
            self.ui.toggle_macro()
            self.ui.events.put(("press", DEFAULTS["toggle"]))
            return ""

        with patch.object(app.filedialog, "asksaveasfilename", side_effect=choose), \
                patch.object(app, "write_config_file") as write:
            self.ui.export_config()
        self.assertFalse(self.ui.macro_enabled)
        self.assertFalse(self.ui.profile_dialog_active)
        self.assertEqual(self.ui.hook._state.toggle, DEFAULTS["toggle"])
        self.assertTrue(self.ui.events.empty())
        write.assert_not_called()

    def test_import_failure_restores_toggle_and_keeps_load_protection(self):
        self.ui.config_load_failed = True

        def show_error(*args, **kwargs):
            self.assertTrue(self.ui.profile_dialog_active)
            self.assertEqual(self.ui.hook._state.toggle, "")
            self.ui.toggle_macro()
            self.ui.events.put(("press", DEFAULTS["toggle"]))

        self.error.side_effect = show_error
        with patch.object(app.filedialog, "askopenfilename", return_value="profile.ini"), \
                patch.object(app, "read_config_file", side_effect=OSError("cannot read")):
            self.ui.import_config()
        self.assertTrue(self.ui.config_load_failed)
        self.assertFalse(self.ui.profile_dialog_active)
        self.assertEqual(self.ui.hook._state.toggle, DEFAULTS["toggle"])
        self.assertIsNone(self.ui.engine)
        self.assertTrue(self.ui.events.empty())
        self.error.assert_called_once()

    def test_save_error_dialog_also_suspends_hotkey(self):
        self.save.side_effect = OSError("disk full")

        def show_error(*args, **kwargs):
            self.assertTrue(self.ui.profile_dialog_active)
            self.ui.toggle_macro()

        self.error.side_effect = show_error
        self.ui.save_current_config()
        self.assertIsNone(self.ui.engine)
        self.assertFalse(self.ui.profile_dialog_active)

    def test_explicit_save_clears_load_failure_protection(self):
        self.ui.config_load_failed = True
        self.ui.save_current_config()
        self.assertFalse(self.ui.config_load_failed)
        self.ui.on_close()
        self.assertEqual(self.save.call_count, 2)

    def test_successful_import_clears_load_failure_protection(self):
        self.ui.config_load_failed = True
        with patch.object(app.filedialog, "askopenfilename", return_value="profile.ini"), \
                patch.object(app, "read_config_file", return_value=dict(DEFAULTS)):
            self.ui.import_config()
        self.assertFalse(self.ui.config_load_failed)

    def test_driver_close_failure_retries_without_destroying_window(self):
        self.driver.close.side_effect = [app.DDError("key up failed"), None]
        with patch.object(self.root, "after") as schedule:
            self.ui.on_close()
            self.assertFalse(self.ui.closed)
            self.assertTrue(self.root.winfo_exists())
            schedule.assert_called_once_with(250, self.ui.finish_close)
            self.assertIn("key up failed", self.ui.status_label.cget("text"))
        self.ui.finish_close()
        self.assertTrue(self.ui.closed)
        self.assertEqual(self.driver.close.call_count, 2)
        self.error.assert_not_called()

    def test_inflight_stop_locks_settings_and_blocks_restart_until_ack(self):
        fake_engine = MagicMock(ready=True, idle=False)
        fake_engine.stop.return_value = False
        with patch.object(app, "MacroEngine", return_value=fake_engine):
            self.ui.toggle_macro()
        self.ui.toggle_macro()
        self.assertFalse(self.ui.macro_enabled)
        self.assertTrue(self.ui.stopping)
        self.assertEqual(str(self.ui.cb_sy1.cget("state")), "disabled")
        self.ui.toggle_macro()
        fake_engine.recover.assert_not_called()
        self.ui.events.put(("stopped", ""))
        self.drain()
        self.assertTrue(self.ui.stopping)
        fake_engine.idle = True
        self.ui.events.put(("stopped", ""))
        self.drain()
        self.assertFalse(self.ui.stopping)
        self.assertEqual(str(self.ui.cb_sy1.cget("state")), "readonly")

    def test_faulted_engine_waits_for_recovery_before_enabling(self):
        fake_engine = MagicMock(ready=False)
        self.ui.engine = fake_engine
        self.ui.toggle_macro()
        fake_engine.recover.assert_called_once()
        self.assertFalse(self.ui.macro_enabled)
        self.assertEqual(self.ui.pending_enable_config, DEFAULTS)
        self.assertEqual(str(self.ui.cb_sy1.cget("state")), "disabled")
        fake_engine.ready = True
        self.ui.events.put(("recovered", ""))
        self.drain()
        self.assertTrue(self.ui.macro_enabled)
        self.assertIsNone(self.ui.pending_enable_config)

    def test_canceled_recovery_ignores_late_notification(self):
        self.ui.engine = MagicMock(ready=False)
        self.ui.toggle_macro()
        self.ui.toggle_macro()
        self.ui.events.put(("recovered", ""))
        self.drain()
        self.assertFalse(self.ui.macro_enabled)
        self.assertIsNone(self.ui.pending_enable_config)

    def test_stale_recovered_event_cannot_enable_an_unready_engine(self):
        fake_engine = MagicMock(ready=False)
        self.ui.engine = fake_engine
        self.ui.toggle_macro()
        self.ui.events.put(("recovered", ""))
        self.drain()
        self.assertFalse(self.ui.macro_enabled)
        self.assertIsNotNone(self.ui.pending_enable_config)
        fake_engine.ready = True
        self.ui.events.put(("recovered", ""))
        self.drain()
        self.assertTrue(self.ui.macro_enabled)

    def test_focus_loss_cancels_pending_recovery(self):
        self.ui.apply_config({**DEFAULTS, "foreground_guard": "1"})
        guard = MagicMock()
        guard.allows_output.return_value = True
        self.ui.foreground_guard = guard
        self.ui.engine = MagicMock(ready=False)
        self.ui.toggle_macro()
        self.assertIsNotNone(self.ui.pending_enable_config)
        guard.allows_output.return_value = False
        self.drain()
        self.assertIsNone(self.ui.pending_enable_config)
        self.ui.engine.ready = True
        self.ui.events.put(("recovered", ""))
        self.drain()
        self.assertFalse(self.ui.macro_enabled)

    def test_foreground_protection_roundtrip_and_focus_loss_stops_output(self):
        cfg = {**DEFAULTS, "foreground_guard": "1"}
        self.ui.apply_config(cfg)
        self.assertEqual(self.ui.collect_config(), cfg)
        guard = MagicMock()
        guard.allows_output.return_value = True
        self.ui.foreground_guard = guard
        fake_engine = MagicMock(ready=True)
        with patch.object(app, "MacroEngine", return_value=fake_engine) as factory:
            self.ui.toggle_macro()
        guard.capture.assert_called_once()
        factory.assert_called_once_with(self.driver, self.ui.events, output_allowed=guard.allows_output)
        self.assertTrue(self.ui.macro_enabled)
        guard.allows_output.return_value = False
        self.ui.events.put(("press", DEFAULTS["sy1"]))
        self.drain()
        self.assertFalse(self.ui.macro_enabled)
        fake_engine.submit.assert_not_called()
        fake_engine.stop.assert_called_once()

    def test_foreground_capture_failure_does_not_initialize_driver(self):
        self.ui.apply_config({**DEFAULTS, "foreground_guard": "1"})
        self.ui.dd = None
        with patch.object(self.ui.foreground_guard, "capture", side_effect=ValueError("select game window")), \
                patch.object(app, "PyDD") as factory:
            self.ui.toggle_macro()
        factory.assert_not_called()
        self.assertFalse(self.ui.macro_enabled)
        self.error.assert_called_once()


class ConfigurationLoadFailureTests(unittest.TestCase):
    def test_valid_external_profile_survives_malformed_bundled_defaults(self):
        with TemporaryDirectory(prefix="yanzi-config-test-") as folder:
            bundle, profile = Path(folder) / "bundle.ini", Path(folder) / "config.ini"
            bundle.write_text("[SETTINGS]\ninvalid line\n", encoding="utf-8")
            expected = {**DEFAULTS, "sy1": "z", "delay_ms": "350"}
            app.write_config_file(profile, expected)
            with patch.object(app, "BUNDLED_CONFIG_FILE", bundle), patch.object(app, "CONFIG_FILE", profile):
                self.assertEqual(app.load_config(), expected)

    def test_malformed_bundle_without_valid_profile_is_not_silently_ignored(self):
        with TemporaryDirectory(prefix="yanzi-config-test-") as folder:
            bundle, profile = Path(folder) / "bundle.ini", Path(folder) / "config.ini"
            bundle.write_text("[SETTINGS]\ninvalid line\n", encoding="utf-8")
            with patch.object(app, "BUNDLED_CONFIG_FILE", bundle), patch.object(app, "CONFIG_FILE", profile):
                with self.assertRaises(configparser.Error):
                    app.load_config()
                profile.write_text("[SETTINGS]\nsy1 = f3\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    app.load_config()

    def test_opening_and_closing_malformed_profile_preserves_original_bytes(self):
        with TemporaryDirectory(prefix="yanzi-config-test-") as folder:
            profile = Path(folder) / "config.ini"
            original = b"[SETTINGS]\nsy1 = z\ninvalid line without equals\n"
            profile.write_bytes(original)
            root = tk.Tk()
            root.withdraw()
            ui = None
            try:
                with patch.object(app, "CONFIG_FILE", profile), \
                        patch.object(app, "BUNDLED_CONFIG_FILE", Path(folder) / "missing.ini"), \
                        patch("winsound.PlaySound"), patch.object(app.messagebox, "showerror"):
                    ui = app.YanziHelper(root, MagicMock(), start_listener=False)
                    self.assertTrue(ui.config_load_failed)
                    self.assertIn("读取配置失败", ui.status_label.cget("text"))
                    ui.on_close()
                    self.assertTrue(ui.closed)
                self.assertEqual(profile.read_bytes(), original)
            finally:
                if ui is not None:
                    ui.hook.close()
                if ui is None or not ui.closed:
                    root.destroy()


class MainShutdownTests(unittest.TestCase):
    def test_declining_driver_install_exits_before_guardian_or_main_window(self):
        root = MagicMock()
        with patch.object(app.sys, "argv", ["YanziHelper.exe"]), \
                patch.object(app, "ensure_administrator", return_value=True), \
                patch.object(app.tk, "Tk", return_value=root), \
                patch.object(app, "ensure_dd_driver", side_effect=lambda path, confirm: confirm()), \
                patch.object(app.messagebox, "askyesno", return_value=False) as confirm, \
                patch.object(app, "DDDriverSession") as guardian, \
                patch.object(app, "YanziHelper") as factory:
            app.main()
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.kwargs, {"default": "no", "parent": root})
        guardian.assert_not_called()
        factory.assert_not_called()
        root.destroy.assert_called_once_with()

    def test_packaged_app_dispatches_guardian_and_checks_without_gui(self):
        for args, target, expected in (
                (["--dd-guardian", "1", "2", "session", "report"], "YanziHelper.run_guardian",
                 (1, 2, Path("session"), Path("report"))),
                (["--dd-lifecycle-check", "report"], "dd_lifecycle_check.run", (Path("report"),)),
                (["--self-test", "report"], "app_selftest.run", (Path("report"),))):
            with self.subTest(args=args), patch.object(app.sys, "argv", ["YanziHelper.exe", *args]), \
                    patch.object(app.sys, "frozen", True, create=True), \
                    patch(target, return_value=7) as run, patch.object(app.tk, "Tk") as root, \
                    patch.object(app, "ensure_administrator") as elevate, \
                    patch.object(app, "DDDriverSession") as session:
                with self.assertRaises(SystemExit) as caught:
                    app.main()
                self.assertEqual(caught.exception.code, 7)
                run.assert_called_once_with(*expected)
                elevate.assert_not_called()
                session.assert_not_called()
                root.assert_not_called()

    def test_main_starts_guardian_and_requests_stop_on_mainloop_exit(self):
        for mainloop_error in (None, RuntimeError("original mainloop failure")):
            with self.subTest(mainloop_error=mainloop_error):
                root, session, ui = MagicMock(), MagicMock(), MagicMock()
                root.mainloop.side_effect = mainloop_error
                session.request_stop.side_effect = app.StopRequestError("IPC unavailable")
                ui.closed = False
                ui.engine.close.return_value = True
                with patch.object(app.sys, "argv", ["YanziHelper.py"]), \
                        patch.object(app, "ensure_administrator", return_value=True) as elevate, \
                        patch.object(app, "DDDriverSession", return_value=session) as guardian, \
                        patch.object(app.tk, "Tk", return_value=root), \
                        patch.object(app, "YanziHelper", return_value=ui) as factory:
                    if mainloop_error is None:
                        app.main()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "original mainloop failure"):
                            app.main()
                elevate.assert_called_once_with()
                guardian.assert_called_once_with(Path(app.__file__), app.APP_DIR / "dd-driver-status.json")
                session.start.assert_called_once_with()
                factory.assert_called_once_with(root, device_session=session)
                session.request_stop.assert_called_once_with()
                ui.hook.close.assert_called_once()
                ui.engine.close.assert_called_once()
                ui.dd.close.assert_called_once()

    def test_existing_instance_prevents_a_second_hook_and_macro_worker(self):
        root, session = MagicMock(), MagicMock()
        session.start.side_effect = app.DDError("another instance is active")
        with patch.object(app.sys, "argv", ["YanziHelper.py"]), \
                patch.object(app, "ensure_administrator", return_value=True), \
                patch.object(app, "DDDriverSession", return_value=session), \
                patch.object(app.tk, "Tk", return_value=root), \
                patch.object(app.messagebox, "showerror") as error, \
                patch.object(app, "YanziHelper") as factory:
            app.main()
        factory.assert_not_called()
        root.destroy.assert_called_once()
        error.assert_called_once()


if __name__ == "__main__":
    unittest.main()
