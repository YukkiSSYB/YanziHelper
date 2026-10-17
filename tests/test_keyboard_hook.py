import ctypes
import queue
import threading
import unittest
from unittest.mock import patch

from keyboard_hook import (
    ERROR_INVALID_HOOK_HANDLE, HC_ACTION, KBDLLHOOKSTRUCT, LLKHF_INJECTED,
    LLKHF_LOWER_IL_INJECTED, VK_CODES, WM_KEYDOWN, WM_KEYUP, WM_QUIT,
    HookState, KeyboardHook,
)


class HookStateTests(unittest.TestCase):
    def setUp(self):
        self.state = HookState()
        self.configure(True)

    def configure(self, enabled):
        self.state.configure("f3", {"e", "3", "z"}, {"r"}, enabled)

    def test_mapped_hold_emits_once_and_suppresses_repeat_and_release(self):
        self.assertEqual(self.state.transition("e", True), (True, (("press", "e"),)))
        for _ in range(100):
            self.assertEqual(self.state.transition("e", True), (True, ()))
        self.assertEqual(self.state.transition("e", False), (True, (("release", "e"),)))
        self.assertEqual(self.state.transition("e", True), (True, (("press", "e"),)))

    def test_disabled_hold_does_not_activate_when_enabled_mid_hold(self):
        self.configure(False)
        self.assertEqual(self.state.transition("e", True), (False, ()))
        self.configure(True)
        self.assertEqual(self.state.transition("e", True), (False, ()))
        self.assertEqual(self.state.transition("e", False), (False, ()))
        self.assertEqual(self.state.transition("e", True), (True, (("press", "e"),)))

    def test_disabling_preserves_suppression_until_physical_release(self):
        self.state.transition("e", True)
        self.configure(False)
        self.assertEqual(self.state.transition("e", True), (True, ()))
        self.assertEqual(self.state.transition("e", False), (True, (("release", "e"),)))
        self.assertEqual(self.state.transition("e", True), (False, ()))

    def test_reconfiguration_preserves_existing_lifecycle(self):
        self.state.transition("e", True)
        self.state.configure("f4", {"x"}, set(), True)
        self.assertEqual(self.state.transition("e", True), (True, ()))
        self.assertEqual(self.state.transition("e", False), (True, (("release", "e"),)))

    def test_toggle_is_suppressed_even_when_disabled(self):
        self.configure(False)
        self.assertEqual(self.state.transition("F3", True), (True, (("press", "f3"),)))
        self.assertEqual(self.state.transition("f3", True), (True, ()))
        self.assertEqual(self.state.transition("f3", False), (True, (("release", "f3"),)))

    def test_watched_key_keeps_original_key_events(self):
        self.assertEqual(self.state.transition("r", True), (False, (("press", "r"),)))
        self.assertEqual(self.state.transition("r", True), (False, ()))
        self.assertEqual(self.state.transition("r", False), (False, (("release", "r"),)))

    def test_injected_events_cannot_end_or_start_a_physical_hold(self):
        self.assertEqual(self.state.transition("e", True, injected=True), (False, ()))
        self.state.transition("e", True)
        self.assertEqual(self.state.transition("e", False, injected=True), (False, ()))
        self.assertEqual(self.state.transition("e", True), (True, ()))
        self.assertEqual(self.state.transition("e", False), (True, (("release", "e"),)))

    def test_unpaired_keyup_is_passed_through(self):
        self.assertEqual(self.state.transition("e", False), (False, ()))
        self.assertEqual(self.state.transition("f3", False), (False, ()))


class FakeBackend:
    """Native API substitute: its message loop waits but never hooks real input."""

    def __init__(self, *, message_error=False, unhook_ok=True, install_ok=True,
                 held_keys=(), unhook_error=0):
        self.user32 = self
        self.kernel32 = self
        self.callback_type = lambda callback: callback
        self.wake = threading.Event()
        self.message_entered = threading.Event()
        self.unhooks = []
        self.posts = []
        self.next_calls = []
        self.message_error = message_error
        self.unhook_ok = unhook_ok
        self.install_ok = install_ok
        self.held_keys = set(held_keys)
        self.unhook_error = unhook_error

    def GetCurrentThreadId(self):
        return 12345

    def GetModuleHandleW(self, name):
        return 789

    def PeekMessageW(self, *args):
        return False

    def GetAsyncKeyState(self, vk):
        return 0x8000 if vk in self.held_keys else 0

    def SetWindowsHookExW(self, *args):
        return 0x123456789 if self.install_ok else None

    def GetMessageW(self, *args):
        self.message_entered.set()
        if self.message_error:
            return -1
        self.wake.wait(2)
        return 0

    def PostThreadMessageW(self, thread_id, message, w_param, l_param):
        self.posts.append((thread_id, message))
        self.wake.set()
        return True

    def UnhookWindowsHookEx(self, handle):
        self.unhooks.append(handle)
        ctypes.set_last_error(self.unhook_error)
        return self.unhook_ok

    def CallNextHookEx(self, *args):
        self.next_calls.append(args)
        return 99


class KeyboardHookTests(unittest.TestCase):
    def setUp(self):
        self.events = queue.Queue()
        self.hook = KeyboardHook(self.events)
        self.hook.configure("f3", {"e"}, {"r"}, True)

    def test_close_wakes_blocked_loop_unhooks_and_joins(self):
        backend = FakeBackend()
        with patch("keyboard_hook._Win32Backend", return_value=backend):
            self.hook.start()
            self.assertTrue(backend.message_entered.wait(1))
            self.assertTrue(self.hook.close())
        self.assertIn((12345, WM_QUIT), backend.posts)
        self.assertEqual(backend.unhooks, [0x123456789])
        self.assertFalse(self.hook._thread.is_alive())
        self.assertIsNone(self.hook._callback_ref)
        self.assertTrue(self.hook.close())
        self.assertEqual(len(backend.unhooks), 1)

    def test_failed_install_is_reported_and_thread_exits(self):
        with patch("keyboard_hook._Win32Backend", return_value=FakeBackend(install_ok=False)):
            with self.assertRaises(OSError):
                self.hook.start()
        self.assertTrue(self.hook.close())
        self.assertEqual(self.events.get_nowait()[0], "error")

    def test_message_loop_failure_still_removes_hook(self):
        backend = FakeBackend(message_error=True)
        with patch("keyboard_hook._Win32Backend", return_value=backend):
            self.hook.start()
            self.assertTrue(self.hook.close())
        self.assertEqual(backend.unhooks, [0x123456789])
        self.assertEqual(self.events.get_nowait()[0], "error")

    def test_failed_unhook_is_not_reported_as_success(self):
        backend = FakeBackend(unhook_ok=False)
        with patch("keyboard_hook._Win32Backend", return_value=backend):
            self.hook.start()
            self.assertFalse(self.hook.close())
        self.assertIsNotNone(self.hook._callback_ref)
        self.assertTrue(self.hook.cleanup_error)
        self.assertEqual(self.events.get_nowait()[0], "error")

    def test_failed_unhook_can_be_retried_after_worker_exits(self):
        backend = FakeBackend(unhook_ok=False)
        with patch("keyboard_hook._Win32Backend", return_value=backend):
            self.hook.start()
            self.assertFalse(self.hook.close())
            self.assertFalse(self.hook._thread.is_alive())
            backend.unhook_ok = True
            self.assertTrue(self.hook.close())
        self.assertIsNone(self.hook._callback_ref)
        self.assertIsNone(self.hook._handle)
        self.assertEqual(self.hook.cleanup_error, "")

    def test_hook_already_removed_by_windows_counts_as_clean(self):
        backend = FakeBackend(unhook_ok=False, unhook_error=ERROR_INVALID_HOOK_HANDLE)
        with patch("keyboard_hook._Win32Backend", return_value=backend):
            self.hook.start()
            self.assertTrue(self.hook.close())
        self.assertIsNone(self.hook._callback_ref)
        self.assertTrue(self.events.empty())

    def test_startup_held_key_passes_through_until_fresh_press(self):
        backend = FakeBackend(held_keys={VK_CODES["e"], VK_CODES["f3"]})
        with patch("keyboard_hook._Win32Backend", return_value=backend):
            self.hook.start()
            for key in ("e", "f3"):
                event = KBDLLHOOKSTRUCT(vkCode=VK_CODES[key])
                pointer = ctypes.addressof(event)
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYDOWN, pointer), 99)
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYUP, pointer), 99)
                self.assertTrue(self.events.empty())
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYDOWN, pointer), 1)
                self.assertEqual(self.events.get_nowait(), ("press", key))
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYUP, pointer), 1)
                self.assertEqual(self.events.get_nowait(), ("release", key))
            self.assertTrue(self.hook.close())

    def test_tab_and_space_work_as_configured_trigger_keys(self):
        self.hook.configure("tab", {"space"}, set(), True)
        self.hook._backend = FakeBackend()
        for key, vk in (("tab", 0x09), ("space", 0x20)):
            self.assertEqual(VK_CODES[key], vk)
            event = KBDLLHOOKSTRUCT(vkCode=vk)
            pointer = ctypes.addressof(event)
            self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYDOWN, pointer), 1)
            self.assertEqual(self.events.get_nowait(), ("press", key))
            self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYUP, pointer), 1)
            self.assertEqual(self.events.get_nowait(), ("release", key))

    def test_native_callback_filters_injected_flags_and_queues_physical_input(self):
        backend = FakeBackend()
        self.hook._backend = backend
        event = KBDLLHOOKSTRUCT(vkCode=ord("E"))
        pointer = ctypes.addressof(event)
        for flag in (LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED):
            event.flags = flag
            self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYDOWN, pointer), 99)
        self.assertTrue(self.events.empty())
        event.flags = 0
        self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYDOWN, pointer), 1)
        self.assertEqual(self.events.get_nowait(), ("press", "e"))
        event.flags = LLKHF_INJECTED
        self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYUP, pointer), 99)
        self.assertTrue(self.events.empty())
        event.flags = 0
        self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYUP, pointer), 1)
        self.assertEqual(self.events.get_nowait(), ("release", "e"))


if __name__ == "__main__":
    unittest.main()
