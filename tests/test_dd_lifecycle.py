from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

import dd_lifecycle as lifecycle
from dd_device import DeviceError, RestartRequired


class GuardianTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.report = self.folder / "report.json"
        self.handles = MagicMock()
        self.handles.birth.return_value = 99
        self.handles.alive.side_effect = [True, True, False]
        self.controller = MagicMock()
        self.controller.start.return_value = {"service_state": 4}
        self.controller.stop.return_value = {"service_state": 1}

    def run_guardian(self):
        with patch.object(lifecycle, "ProcessHandles", return_value=self.handles), \
                patch.object(lifecycle, "DDDeviceController", return_value=self.controller), \
                patch.object(lifecycle, "_parent_exists", return_value=True), \
                patch.object(lifecycle.time, "sleep"):
            return lifecycle.run_guardian(123, 99, self.folder, self.report)

    def test_parent_death_stops_driver_and_releases_exclusive_lock(self):
        self.assertEqual(self.run_guardian(), 0)
        self.controller.start.assert_called_once()
        self.controller.stop.assert_called_once()
        self.handles.release.assert_called_once()
        self.assertEqual(lifecycle.read_state(self.report)["phase"], "stopped")

    def test_pid_reuse_never_enables_or_disables_device(self):
        self.handles.birth.return_value = 100
        self.assertEqual(self.run_guardian(), 1)
        self.controller.start.assert_not_called()
        self.controller.stop.assert_not_called()

    def test_second_instance_never_touches_active_device_or_report(self):
        lifecycle.write_state(self.report, phase="ready")
        self.handles.acquire.side_effect = DeviceError("another instance")
        self.assertEqual(self.run_guardian(), 1)
        self.controller.start.assert_not_called()
        self.controller.stop.assert_not_called()
        self.assertEqual(lifecycle.read_state(self.report)["phase"], "ready")

    def test_partial_start_failure_is_cleaned_up(self):
        self.controller.start.side_effect = DeviceError("partial enable failed")
        self.assertEqual(self.run_guardian(), 1)
        self.controller.stop.assert_called_once()
        state = lifecycle.read_state(self.report)
        self.assertEqual(state["phase"], "failed")
        self.assertTrue(state["cleanup_ok"])

    def test_stop_failure_reports_failure_after_bounded_retries(self):
        self.controller.stop.side_effect = DeviceError("needs reboot")
        self.assertEqual(self.run_guardian(), 1)
        self.assertEqual(self.controller.stop.call_count, 3)
        self.assertEqual(lifecycle.read_state(self.report)["phase"], "failed")

    def test_canceled_start_does_not_enable_device(self):
        (self.folder / "stop").touch()
        self.assertEqual(self.run_guardian(), 0)
        self.controller.start.assert_not_called()
        self.controller.stop.assert_called_once()

    def test_reboot_required_is_reported_once_without_retry_loop(self):
        self.controller.stop.side_effect = RestartRequired("reboot")
        self.controller.backend.snapshot.return_value = {"service_state": 4}
        self.assertEqual(self.run_guardian(), 1)
        self.controller.stop.assert_called_once()
        self.assertEqual(lifecycle.read_state(self.report)["phase"], "restart_required")

    def test_unwritable_diagnostic_report_does_not_block_ready_or_cleanup(self):
        write_state = lifecycle.write_state

        def write(path, **state):
            if path == self.report:
                raise PermissionError("report is read-only")
            write_state(path, **state)

        with patch.object(lifecycle, "write_state", side_effect=write):
            self.assertEqual(self.run_guardian(), 0)
        self.controller.start.assert_called_once()
        self.controller.stop.assert_called_once()
        state = lifecycle.read_state(self.folder / "state.json")
        self.assertEqual(state["phase"], "stopped")
        self.assertIn(str(self.report), state["report_errors"])

    def test_failed_ready_ipc_report_immediately_cleans_up_owned_device(self):
        write_state = lifecycle.write_state

        def write(path, **state):
            if path == self.folder / "state.json":
                raise OSError("session directory disappeared")
            write_state(path, **state)

        with patch.object(lifecycle, "write_state", side_effect=write):
            self.assertEqual(self.run_guardian(), 1)
        self.controller.stop.assert_called_once()
        state = lifecycle.read_state(self.report)
        self.assertEqual(state["phase"], "failed")
        self.assertTrue(state["cleanup_ok"])
        self.assertEqual(state["snapshot"], {"service_state": 1})

    def test_all_report_writes_failing_does_not_interrupt_stop_retries(self):
        self.controller.stop.side_effect = [DeviceError("temporarily busy"), {"service_state": 1}]
        with patch.object(lifecycle, "write_state", side_effect=OSError("disk full")):
            self.assertEqual(self.run_guardian(), 1)
        self.assertEqual(self.controller.stop.call_count, 2)
        self.handles.release.assert_called_once()

    def test_failed_final_ipc_write_does_not_produce_a_verified_stopped_state(self):
        write_state = lifecycle.write_state

        def write(path, **state):
            if path == self.folder / "state.json" and state["phase"] == "stopped":
                raise OSError("cannot update final state")
            write_state(path, **state)

        with patch.object(lifecycle, "write_state", side_effect=write):
            self.assertEqual(self.run_guardian(), 0)
        self.assertEqual(lifecycle.read_state(self.report)["phase"], "stopped")
        session = lifecycle.DDDriverSession("entry.py", self.report)
        session.folder = self.folder
        session.process = MagicMock(returncode=0)
        session.process.poll.return_value = 0
        with self.assertRaises(DeviceError):
            session.close()

    def test_startup_failure_preserves_restart_required_cleanup_result(self):
        self.controller.start.side_effect = DeviceError("partial enable failed")
        self.controller.stop.side_effect = RestartRequired("restart to finish disabling")
        self.controller.backend.snapshot.side_effect = OSError("snapshot unavailable")
        self.assertEqual(self.run_guardian(), 1)
        self.controller.stop.assert_called_once()
        state = lifecycle.read_state(self.report)
        self.assertEqual(state["phase"], "restart_required")
        self.assertEqual(state["error"], "restart to finish disabling")
        self.assertEqual(state["startup_error"], "partial enable failed")
        self.assertEqual(state["snapshot_error"], "snapshot unavailable")
        self.assertFalse(state["cleanup_ok"])


class SessionTests(unittest.TestCase):
    def test_state_read_permission_failure_is_reported_without_confirming_shutdown(self):
        for operation in ("close", "check_alive"):
            with self.subTest(operation=operation), TemporaryDirectory() as tmp:
                session = lifecycle.DDDriverSession("entry.py", Path(tmp) / "report.json")
                session.folder = Path(tmp)
                state_path = session.folder / "state.json"
                lifecycle.write_state(state_path, phase="stopped")
                session.process = MagicMock(returncode=0)
                session.process.poll.return_value = 0
                with patch.object(Path, "read_text", side_effect=PermissionError("read denied")):
                    with self.assertRaisesRegex(DeviceError, "读取 DD 监护状态失败") as raised:
                        getattr(session, operation)()
                self.assertIsInstance(raised.exception.__cause__, PermissionError)
                self.assertFalse(session.finished)
                self.assertTrue(state_path.exists())
                self.assertTrue(session.close())

    def test_start_reports_state_read_error_as_device_error(self):
        with TemporaryDirectory() as tmp:
            session = lifecycle.DDDriverSession("entry.py", Path(tmp) / "report.json")
            process = MagicMock()
            process.poll.return_value = None
            with patch.object(lifecycle.tempfile, "mkdtemp", return_value=tmp), \
                    patch.object(lifecycle, "ProcessHandles"), \
                    patch.object(lifecycle.subprocess, "Popen", return_value=process), \
                    patch.object(Path, "read_text", side_effect=PermissionError("read denied")):
                with self.assertRaisesRegex(DeviceError, "读取 DD 监护状态失败"):
                    session.start()
            self.assertFalse(session.finished)
            self.assertIs(session.process, process)
            session.request_stop()
            self.assertTrue((Path(tmp) / "stop").exists())

    def test_malformed_state_never_confirms_shutdown(self):
        for content in (b"\xff", b"{", b"[]", b"null", b'"stopped"', b"0"):
            for operation in ("close", "check_alive"):
                with self.subTest(content=content, operation=operation), TemporaryDirectory() as tmp:
                    session = lifecycle.DDDriverSession("entry.py", Path(tmp) / "report.json")
                    session.folder = Path(tmp)
                    state_path = session.folder / "state.json"
                    state_path.write_bytes(content)
                    session.process = MagicMock(returncode=0)
                    session.process.poll.return_value = 0
                    with self.assertRaises(DeviceError):
                        getattr(session, operation)()
                    self.assertFalse(session.finished)
                    self.assertEqual(state_path.read_bytes(), content)

    def test_failed_final_state_with_empty_error_never_confirms_shutdown(self):
        for error in ("", None):
            with self.subTest(error=error), TemporaryDirectory() as tmp:
                session = lifecycle.DDDriverSession("entry.py", Path(tmp) / "report.json")
                session.folder = Path(tmp)
                lifecycle.write_state(session.folder / "state.json", phase="failed", error=error)
                session.process = MagicMock(returncode=1)
                session.process.poll.return_value = 1
                for _ in range(2):
                    with self.assertRaisesRegex(DeviceError, "未能确认 DD 驱动已经停止"):
                        session.close()

    def test_failed_stop_request_remains_unverified_and_can_be_retried(self):
        with TemporaryDirectory() as tmp:
            session = lifecycle.DDDriverSession("entry.py", Path(tmp) / "report.json")
            session.folder = Path(tmp) / "missing-session"
            session.process = MagicMock()
            session.process.poll.return_value = None
            with self.assertRaises(lifecycle.StopRequestError) as raised:
                session.close()
            self.assertIsInstance(raised.exception.__cause__, FileNotFoundError)
            self.assertFalse(session.stop_requested)
            self.assertFalse(session.finished)

            session.folder.mkdir()
            self.assertFalse(session.close())
            self.assertTrue(session.stop_requested)
            self.assertTrue((session.folder / "stop").exists())
            self.assertFalse(session.finished)

    def test_close_waits_for_verified_guardian_result(self):
        with TemporaryDirectory() as tmp:
            session = lifecycle.DDDriverSession("entry.py", Path(tmp) / "report.json")
            session.folder = Path(tmp) / "session"
            session.folder.mkdir()
            session.process = MagicMock()
            session.process.poll.return_value = None
            self.assertFalse(session.close())
            self.assertTrue((session.folder / "stop").exists())
            lifecycle.write_state(session.folder / "state.json", phase="stopped")
            session.process.poll.return_value = 0
            session.process.returncode = 0
            self.assertTrue(session.close())
            self.assertTrue(session.close())

    def test_zero_exit_without_verified_stopped_state_is_failure(self):
        with TemporaryDirectory() as tmp:
            session = lifecycle.DDDriverSession("entry.py", Path(tmp) / "report.json")
            session.folder = Path(tmp) / "session"
            session.folder.mkdir()
            session.process = MagicMock(returncode=0)
            session.process.poll.return_value = 0
            lifecycle.write_state(session.folder / "state.json", phase="ready")
            with self.assertRaises(DeviceError):
                session.close()
