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


class SessionTests(unittest.TestCase):
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
