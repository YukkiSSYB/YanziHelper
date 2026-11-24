import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import dd_install
from dd_device import DeviceError


class DriverInstallTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.driver = Path(folder) / "driver"
        self.driver.mkdir()
        payloads = {name: name.encode() for name in dd_install.DRIVER_FILES}
        for name, payload in payloads.items():
            (self.driver / name).write_bytes(payload)
        self.enterContext(patch.object(dd_install, "DRIVER_FILES", {
            name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()
        }))
        self.probe = self.enterContext(patch.object(dd_install, "WindowsDDDevice")).return_value.devices
        self.probe.return_value = []
        self.handles = self.enterContext(patch.object(dd_install, "ProcessHandles")).return_value
        self.enterContext(patch.object(dd_install, "_require_x64"))
        self.install = self.enterContext(patch.object(dd_install, "_install", return_value=0))
        self.delay = self.enterContext(patch.object(dd_install.time, "sleep"))
        self.confirm = Mock(return_value=True)

    def test_installed_device_or_standard_package_skips_install(self):
        self.assertTrue(dd_install.ensure_dd_driver(self.driver / "absent", self.confirm))
        self.probe.assert_not_called()
        self.probe.return_value = ["existing device"]
        self.assertTrue(dd_install.ensure_dd_driver(self.driver, self.confirm))
        self.confirm.assert_not_called()
        self.install.assert_not_called()
        self.handles.acquire.assert_not_called()

    def test_decline_releases_lock_and_never_starts_installer(self):
        self.confirm.return_value = False
        self.assertFalse(dd_install.ensure_dd_driver(self.driver, self.confirm))
        self.confirm.assert_called_once_with()
        self.install.assert_not_called()
        self.handles.release.assert_called_once_with(self.handles.acquire.return_value)

    def test_confirm_runs_verified_copy_and_waits_for_device(self):
        self.probe.side_effect = [[], [], [], ["installed device"]]

        def install(kernel, staging):
            self.confirm.assert_called_once_with()
            self.assertNotEqual(staging, self.driver)
            self.assertEqual({path.name for path in staging.iterdir()}, dd_install.DRIVER_FILES.keys())
            self.assertEqual((staging / "ddc.exe").read_bytes(), b"ddc.exe")
            return 0

        self.install.side_effect = install
        self.assertTrue(dd_install.ensure_dd_driver(self.driver, self.confirm))
        self.install.assert_called_once()
        self.delay.assert_called_once_with(.5)
        self.handles.release.assert_called_once_with(self.handles.acquire.return_value)

    def test_tampered_payload_never_runs(self):
        (self.driver / "ddc.exe").write_bytes(b"modified installer")
        with self.assertRaisesRegex(DeviceError, "ddc.exe"):
            dd_install.ensure_dd_driver(self.driver, self.confirm)
        self.install.assert_not_called()
        self.handles.release.assert_called_once_with(self.handles.acquire.return_value)

    def test_installer_reboot_or_missing_device_blocks_startup(self):
        for result, error in ((3010, "重启"), (1641, "重启"), (1, "退出码：1")):
            with self.subTest(result=result):
                self.install.return_value = result
                with self.assertRaisesRegex(DeviceError, error):
                    dd_install.ensure_dd_driver(self.driver, self.confirm)
        self.assertEqual(self.handles.release.call_count, 3)
