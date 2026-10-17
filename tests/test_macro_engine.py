from __future__ import annotations

import queue
import threading
import time
import unittest

from macro_engine import MacroEngine


class RecordingDD:
    def __init__(self):
        self.calls: list[str] = []
        self.times: list[float] = []
        self.threads: set[int] = set()
        self.condition = threading.Condition()
        self.block_key: str | None = None
        self.entered = threading.Event()
        self.unblock = threading.Event()
        self.fail_key: str | None = None

    def key_press(self, key):
        with self.condition:
            self.calls.append(key)
            self.times.append(time.monotonic())
            self.threads.add(threading.get_ident())
            self.condition.notify_all()
        if key == self.block_key:
            self.entered.set()
            if not self.unblock.wait(2):
                raise RuntimeError("test driver was never unblocked")
        if key == self.fail_key:
            raise RuntimeError("driver failed")

    def wait_count(self, count, timeout=1):
        with self.condition:
            return self.condition.wait_for(lambda: len(self.calls) >= count, timeout)


class FailedKeyUpDD(RecordingDD):
    def __init__(self, cleanup_error=""):
        super().__init__()
        self.held = set()
        self.fail_key = "hot"
        self.release_threads = []
        self.cleanup_error = cleanup_error

    def key_press(self, key):
        self.held.add(key)
        super().key_press(key)
        self.held.discard(key)

    def release_all(self):
        self.release_threads.append(threading.get_ident())
        if self.cleanup_error:
            raise RuntimeError(self.cleanup_error)
        self.held.clear()


class BlockingCleanupDD(FailedKeyUpDD):
    def __init__(self):
        super().__init__(cleanup_error="release retry failed")
        self.block_cleanup = False
        self.cleanup_entered = threading.Event()
        self.cleanup_unblock = threading.Event()

    def release_all(self):
        if self.block_cleanup:
            self.cleanup_entered.set()
            if not self.cleanup_unblock.wait(2):
                raise RuntimeError("test cleanup was never unblocked")
        super().release_all()


class MacroEngineTests(unittest.TestCase):
    def setUp(self):
        self.dd = RecordingDD()
        self.events = queue.Queue()
        self.engine = MacroEngine(self.dd, self.events)

    def tearDown(self):
        self.dd.unblock.set()
        if isinstance(self.dd, BlockingCleanupDD):
            self.dd.cleanup_unblock.set()
        self.assertTrue(self.engine.close())

    def test_target_hot_lowest_hx_order_and_delays(self):
        self.engine.submit(
            "e", (("ally", 0), ("hot", 0.025), ("lowest", 0.025), ("hx", 0.025))
        )
        self.assertTrue(self.dd.wait_count(4))
        self.assertEqual(self.dd.calls, ["ally", "hot", "lowest", "hx"])
        self.assertEqual(len(self.dd.threads), 1)
        self.assertNotIn(threading.get_ident(), self.dd.threads)
        for before, after in zip(self.dd.times, self.dd.times[1:]):
            self.assertGreaterEqual(after - before, 0.020)

    def test_duplicate_keydowns_do_not_restart_or_queue_extra_sequences(self):
        self.dd.block_key = "hot"
        self.engine.submit("e", (("hot", 0), ("hx", 0)))
        self.assertTrue(self.dd.entered.wait(1))
        for _ in range(20):
            self.engine.submit("e", (("duplicate", 0),))
        self.dd.unblock.set()
        self.assertTrue(self.dd.wait_count(2))
        self.assertTrue(self.engine.close())
        self.assertEqual(self.dd.calls, ["hot", "hx"])

    def test_held_repeat_uses_same_worker_and_release_stops(self):
        self.engine.submit("e", (("hot", 0), ("hx", 0)), repeat=True, interval=0.025)
        self.assertTrue(self.dd.wait_count(4))
        self.engine.release("e")
        self.assertTrue(self.engine.close())
        self.assertEqual(self.dd.calls[:4], ["hot", "hx", "hot", "hx"])
        self.assertEqual(len(self.dd.threads), 1)

    def test_release_before_worker_pickup_preserves_short_tap(self):
        self.dd.block_key = "previous"
        self.engine.submit("x", (("previous", 0), ("obsolete", 0)))
        self.assertTrue(self.dd.entered.wait(1))
        self.engine.submit("e", (("target", 0), ("hot", 0), ("hx", 0)), repeat=True)
        self.engine.release("e")
        self.dd.unblock.set()
        self.assertTrue(self.dd.wait_count(4))
        self.assertTrue(self.engine.close())
        self.assertEqual(self.dd.calls, ["previous", "target", "hot", "hx"])

    def test_release_during_first_delay_preserves_remainder(self):
        self.engine.submit("e", (("hot", 0), ("hx", 0.03)), repeat=True, interval=0.03)
        self.assertTrue(self.dd.wait_count(1))
        self.engine.release("e")
        self.assertTrue(self.dd.wait_count(2))
        self.assertTrue(self.engine.close())
        self.assertEqual(self.dd.calls, ["hot", "hx"])

    def test_release_cancels_repeated_sequence_tail(self):
        self.engine.submit("e", (("hot", 0), ("hx", 0.1)), repeat=True, interval=0.01)
        self.assertTrue(self.dd.wait_count(3))
        self.engine.release("e")
        # Wait past the now-cancelled delay without submitting another macro.
        self.assertFalse(self.dd.wait_count(4, timeout=0.15))
        self.assertEqual(self.dd.calls, ["hot", "hx", "hot"])

    def test_new_trigger_cancels_old_tail_and_wakes_delay(self):
        self.engine.submit("e", (("old-hot", 0), ("obsolete-hx", 5)))
        self.assertTrue(self.dd.wait_count(1))
        self.engine.submit("r", (("new-target", 0), ("new-hot", 0)))
        self.assertTrue(self.dd.wait_count(3, timeout=0.5))
        self.assertEqual(self.dd.calls, ["old-hot", "new-target", "new-hot"])

    def test_stop_cancels_initial_delay_promptly(self):
        self.engine.submit("e", (("hot", 0), ("obsolete-hx", 5)))
        self.assertTrue(self.dd.wait_count(1))
        self.engine.stop()
        self.assertTrue(self.engine.close(timeout=0.5))
        self.assertEqual(self.dd.calls, ["hot"])

    def test_release_allows_same_trigger_to_be_pressed_again(self):
        self.engine.submit("e", (("hot", 0),))
        self.assertTrue(self.dd.wait_count(1))
        self.engine.release("e")
        self.engine.submit("e", (("hot", 0),))
        self.assertTrue(self.dd.wait_count(2))
        self.assertEqual(self.dd.calls, ["hot", "hot"])

    def test_close_waits_for_keyup_and_prevents_any_later_output(self):
        self.dd.block_key = "hot"
        self.engine.submit("e", (("hot", 0), ("obsolete-hx", 0)))
        self.assertTrue(self.dd.entered.wait(1))
        self.assertFalse(self.engine.close(timeout=0.01))
        with self.assertRaises(RuntimeError):
            self.engine.submit("r", (("new", 0),))
        self.dd.unblock.set()
        self.assertTrue(self.engine.close(timeout=0.5))
        self.assertTrue(self.engine.close(timeout=0))
        self.assertEqual(self.dd.calls, ["hot"])

    def test_stop_reenable_during_inflight_press_remains_serial(self):
        self.dd.block_key = "old"
        self.engine.submit("e", (("old", 0), ("obsolete", 0)))
        self.assertTrue(self.dd.entered.wait(1))
        started = time.monotonic()
        self.assertFalse(self.engine.stop())
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertFalse(self.engine.idle)
        self.assertFalse(self.engine.ready)
        self.assertFalse(self.engine.recover())
        self.engine.submit("r", (("ignored-while-stopping", 0),))
        self.assertEqual(self.dd.calls, ["old"])
        self.dd.unblock.set()
        self.assertEqual(self.events.get(timeout=1), ("stopped", ""))
        self.assertTrue(self.engine.idle)
        self.assertTrue(self.engine.ready)
        self.engine.submit("r", (("new", 0), ("hx", 0)))
        self.assertTrue(self.dd.wait_count(3))
        self.assertEqual(self.dd.calls, ["old", "new", "hx"])
        self.assertEqual(len(self.dd.threads), 1)

    def test_error_cancels_pending_work_and_reports_to_ui(self):
        self.dd.block_key = "bad"
        self.dd.fail_key = "bad"
        self.engine.submit("e", (("bad", 0), ("obsolete", 0)))
        self.assertTrue(self.dd.entered.wait(1))
        self.engine.submit("r", (("pending", 0),))
        self.dd.unblock.set()
        self.assertEqual(self.events.get(timeout=1), ("error", "driver failed"))
        self.engine.submit("x", (("ignored-until-reset", 0),))
        self.assertFalse(self.dd.wait_count(2, timeout=0.05))
        self.assertTrue(self.engine.stop())
        self.assertFalse(self.engine.ready)
        self.engine.submit("r", (("ignored-after-stop", 0),))
        self.assertFalse(self.dd.wait_count(2, timeout=0.02))
        self.assertTrue(self.engine.recover())
        self.assertEqual(self.events.get(timeout=1), ("recovered", ""))
        self.assertTrue(self.engine.ready)
        self.engine.submit("r", (("recovered", 0),))
        self.assertTrue(self.dd.wait_count(2))
        self.assertEqual(self.dd.calls, ["bad", "recovered"])

    def test_failed_keyup_retries_release_on_worker_before_notifying_ui(self):
        self.assertTrue(self.engine.close())
        self.dd = FailedKeyUpDD()
        self.engine = MacroEngine(self.dd, self.events)
        self.engine.submit("e", (("hot", 0), ("obsolete-hx", 0)))
        self.assertEqual(self.events.get(timeout=1), ("error", "driver failed"))
        self.assertEqual(self.dd.held, set())
        self.assertEqual(self.dd.release_threads, list(self.dd.threads))
        self.assertNotIn(threading.get_ident(), self.dd.release_threads)
        self.assertEqual(self.dd.calls, ["hot"])

    def test_failed_cleanup_reports_original_and_release_errors(self):
        self.assertTrue(self.engine.close())
        self.dd = FailedKeyUpDD(cleanup_error="release retry failed")
        self.engine = MacroEngine(self.dd, self.events)
        self.engine.submit("e", (("hot", 0), ("obsolete-hx", 0)))
        kind, message = self.events.get(timeout=1)
        self.assertEqual(kind, "error")
        self.assertIn("driver failed", message)
        self.assertIn("release retry failed", message)
        self.assertEqual(self.dd.held, {"hot"})
        self.assertEqual(self.dd.release_threads, list(self.dd.threads))
        self.engine.submit("r", (("must-not-run", 0),))
        self.assertFalse(self.dd.wait_count(2, timeout=0.05))
        self.assertEqual(self.dd.calls, ["hot"])

    def test_failed_cleanup_requires_successful_worker_recovery_before_new_output(self):
        self.assertTrue(self.engine.close())
        self.dd = FailedKeyUpDD(cleanup_error="release retry failed")
        self.engine = MacroEngine(self.dd, self.events)
        self.engine.submit("e", (("hot", 0),))
        self.assertEqual(self.events.get(timeout=1)[0], "error")
        self.assertEqual(self.dd.held, {"hot"})
        self.assertTrue(self.engine.stop())
        self.assertFalse(self.engine.ready)
        self.engine.submit("r", (("must-not-run", 0),))
        self.assertFalse(self.dd.wait_count(2, timeout=0.02))

        self.assertTrue(self.engine.recover())
        kind, message = self.events.get(timeout=1)
        self.assertEqual(kind, "error")
        self.assertIn("release retry failed", message)
        self.assertFalse(self.engine.ready)
        self.assertEqual(self.dd.held, {"hot"})

        self.dd.cleanup_error = ""
        self.dd.fail_key = None
        self.assertTrue(self.engine.recover())
        self.assertEqual(self.events.get(timeout=1), ("recovered", ""))
        self.assertTrue(self.engine.ready)
        self.assertEqual(self.dd.held, set())
        self.engine.submit("r", (("safe-after-recovery", 0),))
        self.assertTrue(self.dd.wait_count(2))
        self.assertEqual(self.dd.calls, ["hot", "safe-after-recovery"])
        self.assertEqual(len(self.dd.release_threads), 3)
        self.assertEqual(set(self.dd.release_threads), self.dd.threads)

    def test_stop_during_recovery_cancels_stale_success_notification(self):
        self.assertTrue(self.engine.close())
        self.dd = BlockingCleanupDD()
        self.engine = MacroEngine(self.dd, self.events)
        self.engine.submit("e", (("hot", 0),))
        self.assertEqual(self.events.get(timeout=1)[0], "error")
        self.dd.cleanup_error = ""
        self.dd.block_cleanup = True
        self.assertTrue(self.engine.recover())
        self.assertTrue(self.dd.cleanup_entered.wait(1))
        self.assertTrue(self.engine.recover())  # Coalesces with the active retry.
        self.assertFalse(self.engine.ready)
        self.assertFalse(self.engine.stop())
        self.assertFalse(self.engine.close(timeout=0.01))
        self.dd.cleanup_unblock.set()
        self.assertTrue(self.engine.close())
        self.assertTrue(self.events.empty())
        self.assertEqual(len(self.dd.release_threads), 2)

    def test_cancelled_recovery_reports_stopped_and_needs_another_recovery(self):
        self.assertTrue(self.engine.close())
        self.dd = BlockingCleanupDD()
        self.engine = MacroEngine(self.dd, self.events)
        self.engine.submit("e", (("hot", 0),))
        self.assertEqual(self.events.get(timeout=1)[0], "error")
        self.dd.cleanup_error = ""
        self.dd.block_cleanup = True
        self.engine.recover()
        self.assertTrue(self.dd.cleanup_entered.wait(1))
        self.assertFalse(self.engine.stop())
        self.dd.cleanup_unblock.set()
        self.assertEqual(self.events.get(timeout=1), ("stopped", ""))
        self.assertTrue(self.engine.idle)
        self.assertFalse(self.engine.ready)
        self.assertTrue(self.events.empty())
        self.engine.recover()
        self.assertEqual(self.events.get(timeout=1), ("recovered", ""))
        self.assertTrue(self.engine.ready)

    def test_final_dispatch_check_rejects_cancellation_after_delay_completed(self):
        for operation in ("stop", "replace", "close"):
            with self.subTest(operation=operation):
                dd = RecordingDD()
                engine = MacroEngine(dd, queue.Queue())
                checked = threading.Event()
                proceed = threading.Event()
                original_wait = engine._wait

                def pause_after_wait(sequence, delay, first):
                    result = original_wait(sequence, delay, first)
                    if sequence.trigger == "e" and not checked.is_set():
                        checked.set()
                        if not proceed.wait(2):
                            raise RuntimeError("test dispatcher was never unblocked")
                    return result

                engine._wait = pause_after_wait
                try:
                    engine.submit("e", (("obsolete", 0),))
                    self.assertTrue(checked.wait(1))
                    if operation == "stop":
                        self.assertTrue(engine.stop())
                    elif operation == "replace":
                        engine.submit("r", (("replacement", 0),))
                    else:
                        self.assertFalse(engine.close(timeout=0))
                    proceed.set()
                    if operation == "replace":
                        self.assertTrue(dd.wait_count(1))
                    self.assertTrue(engine.close())
                    self.assertEqual(dd.calls, ["replacement"] if operation == "replace" else [])
                finally:
                    proceed.set()
                    engine.close()

    def test_stop_during_foreground_check_is_bounded_and_cancels_dispatch(self):
        self.assertTrue(self.engine.close())
        entered = threading.Event()
        proceed = threading.Event()

        def output_allowed():
            entered.set()
            if not proceed.wait(2):
                raise RuntimeError("test foreground check was never unblocked")
            return True

        self.engine = MacroEngine(self.dd, self.events, output_allowed=output_allowed)
        try:
            self.engine.submit("e", (("obsolete", 0),))
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.assertFalse(self.engine.stop(timeout=0.01))
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertEqual(self.dd.calls, [])
            proceed.set()
            self.assertEqual(self.events.get(timeout=1), ("stopped", ""))
            self.assertTrue(self.engine.close())
            self.assertEqual(self.dd.calls, [])
        finally:
            proceed.set()

    def test_committed_call_cannot_be_reported_stopped_before_its_first_output(self):
        entered = threading.Event()
        proceed = threading.Event()
        original_press = self.dd.key_press

        def press_after_gate(key):
            entered.set()
            if not proceed.wait(2):
                raise RuntimeError("test driver was never unblocked before output")
            original_press(key)

        self.dd.key_press = press_after_gate
        try:
            self.engine.submit("e", (("committed", 0), ("obsolete-tail", 0)))
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.dd.calls, [])
            self.assertFalse(self.engine.stop())
            self.assertTrue(self.events.empty())
            proceed.set()
            self.assertEqual(self.events.get(timeout=1), ("stopped", ""))
            self.assertTrue(self.engine.idle)
            self.assertEqual(self.dd.calls, ["committed"])
            self.assertTrue(self.engine.close())
            self.assertEqual(self.dd.calls, ["committed"])
        finally:
            proceed.set()

    def test_foreground_is_checked_before_every_step(self):
        self.assertTrue(self.engine.close())
        allowed = threading.Event()
        allowed.set()
        self.engine = MacroEngine(self.dd, self.events, output_allowed=allowed.is_set)
        self.dd.block_key = "hot"
        self.engine.submit("e", (("hot", 0), ("must-not-reach-other-window", 0)))
        self.assertTrue(self.dd.entered.wait(1))
        allowed.clear()
        self.dd.unblock.set()
        self.assertEqual(self.events.get(timeout=1), ("error", "目标窗口已切换，宏已停止"))
        self.assertEqual(self.dd.calls, ["hot"])
        self.assertTrue(self.engine.ready)  # Losing focus is not a driver fault.

    def test_invalid_timing_never_reaches_driver(self):
        for delay in (-1, float("nan"), float("inf")):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                self.engine.submit("e", (("hot", delay),))
        with self.assertRaises(ValueError):
            self.engine.submit("e", (("hot", 0),), repeat=True, interval=0)
        for timeout in (-1, float("nan"), float("inf"), threading.TIMEOUT_MAX * 2):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.engine.stop(timeout)
            with self.subTest(close_timeout=timeout), self.assertRaises(ValueError):
                self.engine.close(timeout)
        self.assertEqual(self.dd.calls, [])


if __name__ == "__main__":
    unittest.main()
