import queue
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import YanziHelper as app
from app_config import DEFAULTS, TARGET_MODES


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

    def test_default_window_fits_requested_layout(self):
        self.root.update_idletasks()
        self.assertLessEqual(self.root.winfo_reqwidth(), 740)
        self.assertLessEqual(self.root.winfo_reqheight(), 780)

    def test_mode_and_extra_keys_round_trip(self):
        data = {**DEFAULTS, "target_mode": "ally", "target_low_key": "f5", "target_ally_key": "tab",
                "sy_low": "z", "xl_low": "x", "ally_hx_low": "1"}
        self.ui.apply_config(data)
        self.assertEqual(self.ui.collect_config(), data)
        self.assertEqual(self.ui.ally_check.cget("state"), "normal")
        self.ui.cb_target_mode.set(TARGET_MODES["none"])
        self.ui.update_target_state()
        self.assertEqual(self.ui.ally_check.cget("state"), "disabled")

    def test_driver_only_loaded_on_enable(self):
        self.ui.dd = None
        with patch.object(app, "PyDD", return_value=self.driver) as factory:
            self.ui.save_current_config()
            factory.assert_not_called()
            self.ui.toggle_macro()
            factory.assert_called_once_with(app.DD_DLL)
        self.assertTrue(self.ui.macro_enabled)
        self.assertEqual(str(self.ui.cb_sy_low.cget("state")), "disabled")

    def test_invalid_enable_does_not_load_driver(self):
        self.ui.dd = None
        self.ui.cb_sy_low.set(DEFAULTS["sy1"])
        with patch.object(app, "PyDD") as factory:
            self.ui.toggle_macro()
            factory.assert_not_called()
        self.assertFalse(self.ui.macro_enabled)
        self.error.assert_called_once()

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

    def test_device_stop_failure_is_visible_and_not_an_infinite_exit_loop(self):
        self.ui.device_session = MagicMock()
        self.ui.device_session.close.side_effect = app.DDError("needs reboot")
        self.ui.on_close()
        self.assertTrue(self.ui.closed)
        self.error.assert_called_once()
        self.assertIn("needs reboot", self.error.call_args.args[1])

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


if __name__ == "__main__":
    unittest.main()
