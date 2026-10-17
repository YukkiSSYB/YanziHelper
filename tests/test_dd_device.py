import unittest
from unittest.mock import MagicMock, patch

from dd_device import DDDeviceController, DeviceError, RestartRequired, WindowsDDDevice, HARDWARE_ID, HID_CLASS


def snapshot(state, started, problem=0):
    return {"service_state": state, "devices": [
        {"instance": "DD-test", "started": started, "problem": problem}]}


class DeviceControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend = MagicMock()
        self.backend.devices.return_value = ["DD-test"]
        self.controller = DDDeviceController(self.backend, timeout=0)

    def test_missing_device_does_not_claim_start_or_install_anything(self):
        self.backend.devices.return_value = []
        with self.assertRaisesRegex(DeviceError, "未找到"):
            self.controller.start()
        self.backend.enable.assert_not_called()

    def test_enable_requires_running_driver_and_started_device(self):
        self.backend.snapshot.return_value = snapshot(4, True)
        self.controller.start()
        self.backend.enable.assert_called_once_with("DD-test")
        for state in [snapshot(1, True), snapshot(4, False), snapshot(4, True, 14)]:
            self.backend.snapshot.return_value = state
            with self.assertRaises(DeviceError):
                self.controller.start()

    def test_stop_checks_kernel_driver_not_just_device_disable_return(self):
        self.backend.snapshot.return_value = snapshot(4, False, 22)
        with self.assertRaisesRegex(DeviceError, "内核驱动尚未停止"):
            self.controller.stop()
        self.backend.snapshot.return_value = snapshot(1, False, 22)
        self.assertEqual(self.controller.stop(), snapshot(1, False, 22))

    def test_stopped_service_but_enabled_device_is_not_success(self):
        self.backend.snapshot.return_value = snapshot(1, False)
        with self.assertRaises(DeviceError):
            self.controller.stop()

    def test_reboot_required_never_reports_running_service_as_stopped(self):
        self.backend.disable.side_effect = RestartRequired("reboot")
        self.backend.snapshot.return_value = snapshot(4, True)
        with self.assertRaises(RestartRequired):
            self.controller.stop()

    def test_non_disableable_device_uses_only_exact_verified_pnp_target(self):
        native = WindowsDDDevice.__new__(WindowsDDDevice)
        native.cm = MagicMock()
        native.cm.CM_Disable_DevNode.return_value = 0x28
        with patch.object(native, "_node", return_value=123), \
                patch.object(native, "_pnp_change", side_effect=RestartRequired("reboot")) as pnp:
            with self.assertRaises(RestartRequired):
                native.disable("DD-test")
        pnp.assert_called_once_with("/disable-device", "DD-test", force=True)

    def test_stop_attempts_every_matching_device_and_preserves_errors(self):
        self.backend.devices.return_value = ["DD-1", "DD-2"]
        self.backend.disable.side_effect = [DeviceError("vetoed"), None]
        with self.assertRaisesRegex(DeviceError, "vetoed"):
            self.controller.stop()
        self.assertEqual(self.backend.disable.call_count, 2)

    def test_disable_uses_persistent_flag_and_validates_before_mutation(self):
        native = WindowsDDDevice.__new__(WindowsDDDevice)
        native.cm = MagicMock()
        native.cm.CM_Disable_DevNode.return_value = 0
        with patch.object(native, "_node", return_value=123):
            native.disable("DD-test")
        native.cm.CM_Disable_DevNode.assert_called_once_with(123, 3)
        native.cm.reset_mock()
        with patch.object(native, "_node", side_effect=DeviceError("mismatch")):
            with self.assertRaises(DeviceError):
                native.disable("physical-keyboard")
        native.cm.CM_Disable_DevNode.assert_not_called()

    def test_identity_must_match_service_hardware_and_hid_class(self):
        native = WindowsDDDevice.__new__(WindowsDDDevice)
        for values in [("ddhid63340", [HARDWARE_ID], HID_CLASS),
                       ("kbdhid", [HARDWARE_ID], HID_CLASS),
                       ("ddhid63340", ["physical"], HID_CLASS),
                       ("ddhid63340", [HARDWARE_ID], "wrong-class")]:
            with patch("dd_device.winreg.OpenKey"), patch("dd_device.winreg.QueryValueEx",
                    side_effect=[(v, 0) for v in values]):
                if values[0] == "ddhid63340" and values[1] == [HARDWARE_ID] and values[2] == HID_CLASS:
                    native.validate("test")
                else:
                    with self.assertRaises(DeviceError):
                        native.validate("test")
