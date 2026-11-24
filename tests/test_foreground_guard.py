import tkinter as tk
import unittest
from unittest.mock import Mock, patch

import YanziHelper as app
from app_config import DEFAULTS
from foreground_guard import ForegroundGuard


class ForegroundGuardTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock(return_value=(123, 456))
        self.guard = ForegroundGuard(self.reader, own_pid=789)

    def test_disabled_guard_does_not_query_windows(self):
        with patch("foreground_guard._Win32Foreground") as backend:
            self.assertTrue(ForegroundGuard().allows_output())
            backend.assert_not_called()
        self.assertTrue(self.guard.allows_output())
        self.reader.assert_not_called()

    def test_capture_allows_only_the_same_window_and_process(self):
        for other in ((234, 456), (123, 555), None):
            with self.subTest(other=other):
                self.reader.return_value = (123, 456)
                self.guard.capture()
                self.assertTrue(self.guard.allows_output())
                self.reader.return_value = other
                self.assertFalse(self.guard.allows_output())
                self.reader.return_value = (123, 456)
                self.assertFalse(self.guard.allows_output(), "focus loss must stay latched")

    def test_invalid_or_own_window_cannot_be_captured(self):
        for window in (None, (0, 456), (123, 0), (123, 789)):
            with self.subTest(window=window):
                self.reader.return_value = window
                with self.assertRaises(ValueError):
                    self.guard.capture()
                self.assertFalse(self.guard.allows_output())

    def test_read_error_stops_output_and_clear_disables_guard(self):
        self.guard.capture()
        self.reader.side_effect = OSError("window lookup failed")
        self.assertFalse(self.guard.allows_output())
        self.guard.clear()
        self.assertTrue(self.guard.allows_output())

    def test_new_capture_resets_a_previous_focus_loss(self):
        self.guard.capture()
        self.reader.return_value = (345, 678)
        self.assertFalse(self.guard.allows_output())
        self.guard.capture()
        self.assertTrue(self.guard.allows_output())


class ForegroundToggleTests(unittest.TestCase):
    def test_focus_loss_discards_an_already_queued_toggle(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        cfg = {**DEFAULTS, "foreground_guard": "1"}
        reader = Mock(return_value=(100, 101))
        with patch.object(app, "load_config", return_value=cfg), patch.object(app, "play_sound"):
            ui = app.YanziHelper(root, Mock(), start_listener=False)
            ui.foreground_guard = ForegroundGuard(reader, own_pid=999)
            ui.toggle_macro()
            self.addCleanup(ui.engine.close)
            ui.events.put(("press", cfg["toggle"]))
            reader.return_value = (200, 201)
            root.after_cancel(ui.poll_id)
            ui.poll_events()
            root.after_cancel(ui.poll_id)
            self.assertFalse(ui.macro_enabled)
            self.assertIn("目标窗口已离开前台", ui.stop_error)

            reader.return_value = (100, 101)
            ui.events.put(("press", cfg["toggle"]))
            ui.poll_events()
            root.after_cancel(ui.poll_id)
            self.assertTrue(ui.macro_enabled)


if __name__ == "__main__":
    unittest.main()
