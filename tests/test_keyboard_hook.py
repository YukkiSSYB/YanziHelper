import ctypes
import queue
import threading
import unittest
from unittest.mock import patch

from keyboard_hook import (
    ERROR_INVALID_HOOK_HANDLE, HC_ACTION, KBDLLHOOKSTRUCT, LLKHF_EXTENDED,
    LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED, MAPVK_VK_TO_VSC_EX,
    VK_CODES, WM_KEYDOWN, WM_KEYUP, WM_QUIT,
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

    def test_physical_hold_keeps_first_key_and_decision_after_reconfiguration(self):
        physical_key = (0x12, False)
        self.assertEqual(self.state.transition("e", True, physical_key=physical_key),
                         (True, (("press", "e"),)))
        self.state.configure("f4", {"x"}, set(), False)
        self.assertEqual(self.state.transition("x", True, physical_key=physical_key),
                         (True, ()))
        self.assertEqual(self.state.transition("x", False, physical_key=physical_key),
                         (True, (("release", "e"),)))


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

    def MapVirtualKeyW(self, vk, map_type):
        assert map_type == MAPVK_VK_TO_VSC_EX
        numpad_scans = (0x52, 0x4F, 0x50, 0x51, 0x4B, 0x4C, 0x4D, 0x47, 0x48, 0x49)
        scans = {VK_CODES[f"num{i}"]: scan for i, scan in enumerate(numpad_scans)}
        scans.update({VK_CODES["e"]: 0x12, VK_CODES["f3"]: 0x3D})
        return scans.get(vk, 0)

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

    def callback_key(self, vk, scan, down, flags=0):
        event = KBDLLHOOKSTRUCT(vkCode=vk, scanCode=scan,
                               flags=flags | (0 if down else 0x80))
        return self.hook._callback(HC_ACTION, WM_KEYDOWN if down else WM_KEYUP,
                                   ctypes.addressof(event))

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
            for key, scan in (("e", 0x12), ("f3", 0x3D)):
                event = KBDLLHOOKSTRUCT(vkCode=VK_CODES[key], scanCode=scan)
                pointer = ctypes.addressof(event)
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYDOWN, pointer), 99)
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYUP, pointer), 99)
                self.assertTrue(self.events.empty())
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYDOWN, pointer), 1)
                self.assertEqual(self.events.get_nowait(), ("press", key))
                self.assertEqual(self.hook._callback(HC_ACTION, WM_KEYUP, pointer), 1)
                self.assertEqual(self.events.get_nowait(), ("release", key))
            self.assertTrue(self.hook.close())

    def test_numpad_vk_change_preserves_repeat_and_release(self):
        self.hook.configure("f3", {"num8"}, set(), True)
        self.hook._backend = FakeBackend()
        self.assertEqual(self.callback_key(0x68, 0x48, True), 1)
        self.assertEqual(self.events.get_nowait(), ("press", "num8"))
        # Shift can change the numpad key's VK from NUMPAD8 to UP mid-hold.
        for vk in (0x26, 0x68, 0x26):
            self.assertEqual(self.callback_key(vk, 0x48, True), 1)
        self.assertTrue(self.events.empty())
        self.assertEqual(self.callback_key(0x26, 0x48, False), 1)
        self.assertEqual(self.events.get_nowait(), ("release", "num8"))
        self.assertEqual(self.callback_key(0x68, 0x48, True), 1)
        self.assertEqual(self.events.get_nowait(), ("press", "num8"))
        self.assertEqual(self.callback_key(0x68, 0x48, False), 1)
        self.assertEqual(self.events.get_nowait(), ("release", "num8"))

    def test_independent_arrow_cannot_release_numpad_hold(self):
        self.hook.configure("f3", {"num8"}, set(), True)
        self.hook._backend = FakeBackend()
        self.assertEqual(self.callback_key(0x68, 0x48, True), 1)
        self.assertEqual(self.events.get_nowait(), ("press", "num8"))
        # The separate Up key shares scan 0x48 but has the extended flag.
        self.assertEqual(self.callback_key(0x26, 0x48, True, LLKHF_EXTENDED), 99)
        self.assertEqual(self.callback_key(0x26, 0x48, False, LLKHF_EXTENDED), 99)
        self.assertTrue(self.events.empty())
        self.assertEqual(self.callback_key(0x26, 0x48, True), 1)
        self.assertTrue(self.events.empty())
        self.assertEqual(self.callback_key(0x26, 0x48, False), 1)
        self.assertEqual(self.events.get_nowait(), ("release", "num8"))

    def test_unsupported_first_vk_cannot_activate_mid_hold(self):
        self.hook.configure("f3", {"num8"}, set(), True)
        self.hook._backend = FakeBackend()
        self.assertEqual(self.callback_key(0x26, 0x48, True), 99)
        self.assertEqual(self.callback_key(0x68, 0x48, True), 99)
        self.assertEqual(self.callback_key(0x68, 0x48, False), 99)
        self.assertTrue(self.events.empty())
        self.assertEqual(self.callback_key(0x68, 0x48, True), 1)
        self.assertEqual(self.events.get_nowait(), ("press", "num8"))
        self.assertEqual(self.callback_key(0x26, 0x48, False), 1)
        self.assertEqual(self.events.get_nowait(), ("release", "num8"))

    def test_injected_scan_events_cannot_start_or_end_numpad_hold(self):
        self.hook.configure("f3", {"num8"}, set(), True)
        self.hook._backend = FakeBackend()
        for flags in (LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED):
            with self.subTest(flags=flags):
                self.assertEqual(self.callback_key(0x68, 0x48, True, flags), 99)
                self.assertTrue(self.events.empty())
                self.assertEqual(self.callback_key(0x68, 0x48, True), 1)
                self.assertEqual(self.events.get_nowait(), ("press", "num8"))
                self.assertEqual(self.callback_key(0x26, 0x48, False, flags), 99)
                self.assertTrue(self.events.empty())
                self.assertEqual(self.callback_key(0x26, 0x48, True), 1)
                self.assertTrue(self.events.empty())
                self.assertEqual(self.callback_key(0x26, 0x48, False), 1)
                self.assertEqual(self.events.get_nowait(), ("release", "num8"))

    def test_startup_numpad_hold_accepts_changed_vk_without_activating(self):
        self.hook.configure("f3", {"num8"}, set(), True)
        for repeat_before_release in (False, True):
            with self.subTest(repeat_before_release=repeat_before_release):
                backend = FakeBackend(held_keys={VK_CODES["num8"]})
                with patch("keyboard_hook._Win32Backend", return_value=backend):
                    self.hook.start()
                    try:
                        if repeat_before_release:
                            self.assertEqual(self.callback_key(0x26, 0x48, True), 99)
                        self.assertEqual(self.callback_key(0x26, 0x48, False), 99)
                        self.assertTrue(self.events.empty())
                        self.assertEqual(self.callback_key(0x68, 0x48, True), 1)
                        self.assertEqual(self.events.get_nowait(), ("press", "num8"))
                        self.assertEqual(self.callback_key(0x26, 0x48, False), 1)
                        self.assertEqual(self.events.get_nowait(), ("release", "num8"))
                    finally:
                        self.assertTrue(self.hook.close())

    def test_startup_navigation_alias_cannot_activate_numpad_mid_hold(self):
        aliases = (("num0", 0x2D, 0x52), ("num1", 0x23, 0x4F),
                   ("num2", 0x28, 0x50), ("num3", 0x22, 0x51),
                   ("num4", 0x25, 0x4B), ("num5", 0x0C, 0x4C),
                   ("num6", 0x27, 0x4D), ("num7", 0x24, 0x47),
                   ("num8", 0x26, 0x48), ("num9", 0x21, 0x49))
        for key, alias_vk, scan in aliases:
            for repeat_before_release in (False, True):
                with self.subTest(key=key, repeat_before_release=repeat_before_release):
                    self.hook.configure("f3", {key}, set(), True)
                    backend = FakeBackend(held_keys={alias_vk})
                    with patch("keyboard_hook._Win32Backend", return_value=backend):
                        self.hook.start()
                        try:
                            if repeat_before_release:
                                for vk in (alias_vk, VK_CODES[key], alias_vk):
                                    self.assertEqual(self.callback_key(vk, scan, True), 99)
                            self.assertEqual(self.callback_key(VK_CODES[key], scan, False), 99)
                            self.assertTrue(self.events.empty())
                            self.assertEqual(self.callback_key(VK_CODES[key], scan, True), 1)
                            self.assertEqual(self.events.get_nowait(), ("press", key))
                            self.assertEqual(self.callback_key(alias_vk, scan, False), 1)
                            self.assertEqual(self.events.get_nowait(), ("release", key))
                        finally:
                            self.assertTrue(self.hook.close())

    def test_startup_separate_arrow_conservatively_passes_first_matching_numpad_press(self):
        self.hook.configure("f3", {"num8", "num9"}, set(), True)
        backend = FakeBackend(held_keys={0x26})
        with patch("keyboard_hook._Win32Backend", return_value=backend):
            self.hook.start()
            try:
                # The same startup VK could also include a held numpad key, so
                # the separate arrow's release cannot clear the numpad seed.
                self.assertEqual(self.callback_key(0x26, 0x48, False, LLKHF_EXTENDED), 99)
                self.assertEqual(self.callback_key(0x68, 0x48, True), 99)
                self.assertEqual(self.callback_key(0x68, 0x48, False), 99)
                self.assertTrue(self.events.empty())
                for vk, scan, key in ((0x68, 0x48, "num8"), (0x69, 0x49, "num9")):
                    self.assertEqual(self.callback_key(vk, scan, True), 1)
                    self.assertEqual(self.events.get_nowait(), ("press", key))
                    self.assertEqual(self.callback_key(vk, scan, False), 1)
                    self.assertEqual(self.events.get_nowait(), ("release", key))
            finally:
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
