import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import YanziHelper as app
from app_config import DEFAULTS
from keyboard_hook import VK_CODES


class KeyCaptureTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(app, "load_config", return_value=dict(DEFAULTS)):
            self.ui = app.YanziHelper(self.root, MagicMock(), start_listener=False)
        self.capture = self.ui.key_capture
        self.root.update_idletasks()

    def tearDown(self):
        if self.ui.closed:
            return
        self.capture.cancel()
        if self.ui.poll_id is not None:
            self.root.after_cancel(self.ui.poll_id)
        self.root.destroy()

    def double_click(self, box):
        # Tk recognizes a double-click from two timed press/release pairs.
        # Generate the real mouse bindings without moving the desktop pointer.
        for stamp in (1000, 1100):
            box.event_generate("<ButtonPress-1>", x=4, y=6, time=stamp)
            box.event_generate("<ButtonRelease-1>", x=4, y=6, time=stamp + 20)
        self.assertIs(self.capture.box, box)

    def key(self, box, keysym, code, state=0):
        event = SimpleNamespace(widget=box, keysym=keysym, keycode=code, state=state)
        return self.capture._key_press(event)

    def test_new_toggle_and_pending_old_press_do_not_start_the_macro_during_capture(self):
        box = self.ui.cb_toggle
        self.ui.events.put(("press", "f3"))
        self.double_click(box)
        self.assertEqual(self.ui.hook._state.toggle, "")
        self.assertTrue(self.ui.events.empty())
        self.ui.toggle_macro()
        self.assertFalse(self.ui.macro_enabled)
        hook = self.ui.hook._state
        self.assertEqual(hook.transition("f6", True), (False, ()))
        self.assertEqual(self.key(box, "F6", VK_CODES["f6"]), "break")
        self.assertFalse(self.capture.active)
        self.assertEqual(self.ui.collect_config()["toggle"], "f6")
        self.assertEqual(hook.toggle, "f6")
        # Repeats and the original release still belong to the recording hold.
        self.assertEqual(hook.transition("f6", True), (False, ()))
        self.assertEqual(hook.transition("f6", False), (False, ()))
        self.assertEqual(hook.transition("f3", True), (False, ()))
        self.assertEqual(hook.transition("f6", True), (True, (("press", "f6"),)))
        with patch.object(app, "save_config") as save:
            self.ui.save_current_config()
        self.assertEqual(save.call_args.args[0]["toggle"], "f6")

    def test_keypad_digit_and_main_digit_stay_distinct_across_schools(self):
        box = self.ui.cb_sy1
        self.double_click(box)
        self.key(box, "KP_1", VK_CODES["num1"])
        self.assertEqual(self.ui.collect_config()["sy1"], "num1")
        self.ui.apply_config({**self.ui.collect_config(), "school": "feitian"})
        box = self.ui.cb_ft_begin
        self.double_click(box)
        self.key(box, "0", VK_CODES["0"])
        data = self.ui.collect_config()
        self.assertEqual(data["ft_begin"], "0")
        self.assertEqual(data["sy1"], "num1")

    def test_invalid_key_modifier_escape_and_focus_loss_preserve_original(self):
        box = self.ui.cb_sy1
        old_style = box.cget("style")
        old_status = self.ui.status_label.cget("text")
        self.double_click(box)
        self.key(box, "Home", 0x24)
        self.key(box, "z", VK_CODES["z"], state=4)
        self.assertTrue(self.capture.active)
        self.assertEqual(self.ui.collect_config()["sy1"], DEFAULTS["sy1"])
        self.key(box, "Escape", 0x1B)
        self.assertEqual(box.cget("style"), old_style)
        self.assertEqual(self.ui.status_label.cget("text"), old_status)
        self.double_click(box)
        box.event_generate("<FocusOut>")
        self.assertFalse(self.capture.active)
        self.assertEqual(self.ui.hook._state.toggle, "f3")

    def test_clear_optional_binding_and_reject_duplicate_on_save(self):
        box = self.ui.cb_wm1
        self.double_click(box)
        self.key(box, "BackSpace", 8)
        self.assertEqual(self.ui.collect_config()["wm1"], "")
        self.double_click(box)
        self.key(box, "e", VK_CODES["e"])
        with patch.object(app, "save_config") as save, patch.object(app.messagebox, "showerror") as error:
            self.ui.save_current_config()
        save.assert_not_called()
        self.assertIn("不能使用同一个按键", error.call_args.args[1])

    def test_disabled_or_running_controls_cannot_start_recording(self):
        box = self.ui.cb_ft_macro_key
        event = SimpleNamespace(widget=box, x=4, y=6)
        self.capture._double_click(event)
        self.assertFalse(self.capture.active)
        self.ui.macro_enabled = True
        self.capture._double_click(SimpleNamespace(widget=self.ui.cb_sy1, x=4, y=6))
        self.assertFalse(self.capture.active)

    def test_invalid_recorded_binding_cannot_overwrite_profile_on_close(self):
        box = self.ui.cb_sy1
        self.double_click(box)
        self.key(box, "F3", VK_CODES["f3"])
        with patch.object(app, "save_config") as save, patch.object(app.messagebox, "showerror") as error:
            self.ui.on_close()
        self.assertTrue(self.ui.closed)
        save.assert_not_called()
        self.assertIn("不能使用同一个按键", error.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
