"""Lifecycle tests only use fake native functions; no DD DLL is loaded."""

import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import pydd


class FakeLibrary:
    def __init__(self):
        self._handle = 12345
        for name in ("DD_btn", "DD_key", "DD_mov", "DD_movR", "DD_whl", "DD_str"):
            setattr(self, name, Mock(return_value=1))


class PyDDLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.dll = FakeLibrary()
        self.loader = patch("pydd.ctypes.WinDLL", return_value=self.dll, create=True).start()
        self.free = patch("pydd._ctypes.FreeLibrary", create=True).start()
        self.addCleanup(patch.stopall)
        # Any existing file is sufficient because the actual DLL loader is mocked.
        self.dd = pydd.PyDD(Path(__file__))
        self.dll.DD_btn.reset_mock()

    def tearDown(self):
        self.dll.DD_key.side_effect = None
        self.dll.DD_btn.side_effect = None
        self.free.side_effect = None
        self.dd.close()

    def test_key_is_released_when_wait_is_interrupted(self):
        with patch("pydd.time.sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.dd.key_press("q")
        self.assertEqual(self.dll.DD_key.call_args_list, [call(301, 1), call(301, 2)])
        self.dd.release_all()
        self.assertEqual(self.dll.DD_key.call_count, 2)

    def test_key_is_released_when_native_key_down_raises(self):
        self.dll.DD_key.side_effect = [OSError("input error"), 1]
        with self.assertRaises(OSError):
            self.dd.key_press("q", duration=0)
        self.assertEqual(self.dll.DD_key.call_args_list, [call(301, 1), call(301, 2)])

    def test_mouse_is_released_when_wait_is_interrupted(self):
        with patch("pydd.time.sleep", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.dd.mouse_click("right")
        self.assertEqual(self.dll.DD_btn.call_args_list, [call(4), call(8)])

    def test_failed_key_release_is_retried(self):
        self.dll.DD_key.side_effect = [1, OSError("release failed"), 1]
        with self.assertRaises(OSError):
            self.dd.key_press("q", duration=0)
        self.dd.release_all()
        self.assertEqual(self.dll.DD_key.call_args_list,
                         [call(301, 1), call(301, 2), call(301, 2)])

    def test_close_releases_held_inputs_before_unloading_once(self):
        self.dd.key_down("ctrl")
        self.dll.DD_btn.side_effect = [1, OSError("release failed")]
        with self.assertRaises(OSError):
            self.dd.mouse_click("right", duration=0)
        self.dll.DD_btn.side_effect = None
        order = Mock()
        order.attach_mock(self.dll.DD_key, "key")
        order.attach_mock(self.dll.DD_btn, "button")
        order.attach_mock(self.free, "free")

        self.dd.close()
        self.dd.close()

        self.assertEqual(order.mock_calls, [call.key(600, 2), call.button(8), call.free(12345)])
        self.assertTrue(self.dd.closed)
        self.assertIsNone(self.dd.dd_dll)

    def test_close_retries_only_failed_keys_before_unloading(self):
        self.dd.key_down("q")
        self.dd.key_down("w")
        self.dll.DD_key.reset_mock()
        self.dll.DD_key.side_effect = [OSError("release failed"), 1, 1]
        order = Mock()
        order.attach_mock(self.dll.DD_key, "key")
        order.attach_mock(self.free, "free")
        with self.assertRaises(pydd.DDError):
            self.dd.close()
        self.assertEqual(order.mock_calls, [call.key(301, 2), call.key(302, 2)])
        self.free.assert_not_called()
        self.assertIs(self.dd.dd_dll, self.dll)
        self.assertTrue(self.dd.closed)
        with self.assertRaises(pydd.DDError):
            self.dd.key_down("e")

        self.dd.close()
        self.dd.close()

        self.assertEqual(order.mock_calls, [call.key(301, 2), call.key(302, 2),
                                           call.key(301, 2), call.free(12345)])
        self.free.assert_called_once_with(12345)
        self.assertIsNone(self.dd.dd_dll)

    def test_repeated_mouse_release_failures_keep_cleanup_retryable(self):
        self.dll.DD_btn.side_effect = [1, OSError("release failed")]
        with self.assertRaises(OSError):
            self.dd.mouse_click("right", duration=0)
        self.dll.DD_btn.reset_mock()
        self.dll.DD_btn.side_effect = OSError("still unavailable")

        for _ in range(2):
            with self.assertRaises(pydd.DDError):
                self.dd.close()
        self.assertEqual(self.dll.DD_btn.call_args_list, [call(8), call(8)])
        self.free.assert_not_called()
        with self.assertRaises(pydd.DDError):
            self.dd.mouse_click("left", duration=0)

        self.dll.DD_btn.side_effect = None
        self.dd.close()

        self.assertEqual(self.dll.DD_btn.call_args_list, [call(8), call(8), call(8)])
        self.free.assert_called_once_with(12345)
        self.assertIsNone(self.dd.dd_dll)

    def test_close_retries_failed_free_but_refuses_new_input(self):
        self.free.side_effect = [OSError("busy"), None]
        with self.assertRaises(pydd.DDError):
            self.dd.close()
        with self.assertRaises(pydd.DDError):
            self.dd.key_down("q")
        self.dd.close()
        self.assertEqual(self.free.call_count, 2)
        self.assertIsNone(self.dd.dd_dll)

    def test_no_input_method_can_use_unloaded_library(self):
        self.dd.close()
        for operation in (
            lambda: self.dd.key_down("q"),
            lambda: self.dd.key_up("q"),
            lambda: self.dd.key_press("q"),
            lambda: self.dd.key_press(""),
            lambda: self.dd.mouse_click(),
            lambda: self.dd.mouse_move(1, 2),
            lambda: self.dd.mouse_move_relative(1, 2),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(pydd.DDError):
                    operation()
        self.dll.DD_key.assert_not_called()
        self.dll.DD_btn.assert_not_called()

    def test_close_waits_for_current_key_press(self):
        entered_wait = threading.Event()
        finish_wait = threading.Event()
        close_started = threading.Event()
        errors = []
        order = Mock()
        order.attach_mock(self.dll.DD_key, "key")
        order.attach_mock(self.free, "free")

        def wait_for_release(_duration):
            entered_wait.set()
            if not finish_wait.wait(2):
                raise AssertionError("test input was not released")

        def send_input():
            try:
                self.dd.key_press("q")
            except BaseException as exc:
                errors.append(exc)

        def close_input():
            close_started.set()
            try:
                self.dd.close()
            except BaseException as exc:
                errors.append(exc)

        with patch("pydd.time.sleep", side_effect=wait_for_release):
            worker = threading.Thread(target=send_input)
            closer = threading.Thread(target=close_input)
            worker.start()
            self.assertTrue(entered_wait.wait(2))
            closer.start()
            self.assertTrue(close_started.wait(2))
            self.free.assert_not_called()
            finish_wait.set()
            worker.join(2)
            closer.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(order.mock_calls, [call.key(301, 1), call.key(301, 2), call.free(12345)])

    def test_failed_initialization_frees_its_own_library_reference(self):
        self.dd.close()
        self.free.reset_mock()
        self.dll.DD_btn.return_value = 0
        with self.assertRaises(pydd.DDInitializationError):
            pydd.PyDD(Path(__file__))
        self.free.assert_called_once_with(12345)

    def test_missing_native_export_frees_library(self):
        self.dd.close()
        self.free.reset_mock()
        del self.dll.DD_movR
        with self.assertRaises(pydd.DDInitializationError):
            pydd.PyDD(Path(__file__))
        self.free.assert_called_once_with(12345)

    def test_loader_failure_does_not_free_unowned_handle(self):
        self.dd.close()
        self.free.reset_mock()
        self.loader.side_effect = OSError("cannot load")
        with self.assertRaises(pydd.DDInitializationError):
            pydd.PyDD(Path(__file__))
        self.free.assert_not_called()


if __name__ == "__main__":
    unittest.main()
