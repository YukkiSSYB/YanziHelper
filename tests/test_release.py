from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import wave
from zipfile import ZipFile

from app_config import DEFAULTS, write_config_file
from tools import package_release as package


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="yanzi-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)

    def fixture(self, project=None, *, existing=True):
        project = project or self.project
        candidate = project / "build_release" / "candidate-test"
        candidate.mkdir(parents=True)
        config = project / "config.example.ini"
        write_config_file(config, DEFAULTS)
        (candidate / package.EXE_NAME).write_bytes(b"new candidate executable")
        report = {
            "status": "PASS", "frozen": True,
            "real_driver_loaded": False, "global_hook_started": False,
            "executable_sha256": package.sha256_file(candidate / package.EXE_NAME),
            "bundled_config_sha256": package.sha256_file(config),
        }
        (candidate / package.REPORT_NAME).write_text(json.dumps(report), encoding="utf-8")
        for name in package.DOCUMENTS:
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("distribution document: " + name, encoding="utf-8")
        if existing:
            release = project / "release"
            release.mkdir()
            (release / package.EXE_NAME).write_bytes(b"old executable")
            (release / package.REPORT_NAME).write_text("old report", encoding="utf-8")
            write_config_file(release / "config.ini", {**DEFAULTS, "toggle": "f4"})
            (release / "saved-profile.ini").write_bytes(b"user-exported-profile")
            (project / package.ARCHIVE_NAME).write_bytes(b"old archive")
        return candidate, config

    def snapshot(self, project=None):
        project = project or self.project
        release = project / "release"
        files = ({str(path.relative_to(release)): path.read_bytes()
                  for path in release.rglob("*") if path.is_file()} if release.exists() else None)
        archive = project / package.ARCHIVE_NAME
        return files, archive.read_bytes() if archive.exists() else None

    def test_actual_configuration_rejects_missing_unrelated_malformed_and_conflicting_files(self):
        profile = self.project / "config.ini"
        with self.assertRaises(ValueError):
            package.validate_profile(profile)
        for contents in ("[OTHER]\na = b\n", "[SETTINGS]\ninvalid line\n",
                         "[SETTINGS]\ntoggle = e\n", "[SETTINGS]\ndelay_ms = nan\n"):
            with self.subTest(contents=contents):
                profile.write_text(contents, encoding="utf-8")
                with self.assertRaises((ValueError, configparser.Error)):
                    package.validate_profile(profile)

    def test_publication_preserves_profile_other_files_and_complete_backup(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        archive = package.publish_release(self.project, candidate, config)
        release = self.project / "release"
        self.assertEqual((release / package.EXE_NAME).read_bytes(), b"new candidate executable")
        self.assertEqual((release / "config.ini").read_bytes(), before[0]["config.ini"])
        self.assertEqual((release / "saved-profile.ini").read_bytes(), b"user-exported-profile")
        backup, = (self.project / "backups").iterdir()
        self.assertEqual(self.snapshot(backup), before)
        with ZipFile(archive) as result:
            self.assertNotIn("saved-profile.ini", result.namelist())
            for name in (package.EXE_NAME, "config.ini", package.REPORT_NAME, *package.DOCUMENTS):
                self.assertEqual(result.read(name), (release / name).read_bytes())

    def test_first_publication_uses_validated_build_profile(self):
        candidate, config = self.fixture(existing=False)
        package.publish_release(self.project, candidate, config)
        self.assertEqual((self.project / "release" / "config.ini").read_bytes(), config.read_bytes())

    def test_each_failed_commit_move_restores_both_old_artifacts(self):
        for failing_move in range(1, 5):
            with self.subTest(failing_move=failing_move):
                project = self.project / str(failing_move)
                candidate, config = self.fixture(project)
                before = self.snapshot(project)
                replace = os.replace
                calls = 0

                def fail_once(source, target):
                    nonlocal calls
                    calls += 1
                    if calls == failing_move:
                        raise PermissionError("simulated locked destination")
                    return replace(source, target)

                with patch.object(package.os, "replace", side_effect=fail_once):
                    with self.assertRaises(PermissionError):
                        package.publish_release(project, candidate, config)
                self.assertEqual(self.snapshot(project), before)
                self.assertTrue((candidate / package.EXE_NAME).exists())
                # An exception must also release the publication lock.
                with package.publication_lock(project / "build_release" / ".publish.lock"):
                    pass

    def test_first_publication_failure_leaves_no_partial_live_release(self):
        candidate, config = self.fixture(existing=False)
        replace = os.replace

        def fail_archive(source, target):
            if Path(target) == self.project / package.ARCHIVE_NAME:
                raise OSError("simulated ZIP publication failure")
            return replace(source, target)

        with patch.object(package.os, "replace", side_effect=fail_archive):
            with self.assertRaises(OSError):
                package.publish_release(self.project, candidate, config)
        self.assertEqual(self.snapshot(), (None, None))

    def test_failed_archive_preparation_does_not_touch_live_release(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        with patch.object(package, "create_archive", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                package.publish_release(self.project, candidate, config)
        self.assertEqual(self.snapshot(), before)

    def test_stale_executable_report_cannot_publish(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        (candidate / package.EXE_NAME).write_bytes(b"different executable")
        with self.assertRaisesRegex(RuntimeError, "executable does not match"):
            package.publish_release(self.project, candidate, config)
        self.assertEqual(self.snapshot(), before)

    def test_changed_build_configuration_cannot_publish(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        write_config_file(config, {**DEFAULTS, "toggle": "f4"})
        with self.assertRaisesRegex(RuntimeError, "bundled configuration"):
            package.publish_release(self.project, candidate, config)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_report_without_hashes_cannot_publish(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        report_path = candidate / package.REPORT_NAME
        report = json.loads(report_path.read_text(encoding="utf-8"))
        del report["executable_sha256"]
        report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            package.publish_release(self.project, candidate, config)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_preserved_profile_aborts_without_overwriting_it(self):
        candidate, config = self.fixture()
        (self.project / "release" / "config.ini").write_text("[SETTINGS]\ntoggle = e\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            package.publish_release(self.project, candidate, config)
        self.assertEqual(self.snapshot(), before)

    def test_profile_edit_during_assembly_is_not_lost(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        create_archive = package.create_archive
        profile = self.project / "release" / "config.ini"

        def edit_profile(*args):
            create_archive(*args)
            write_config_file(profile, {**DEFAULTS, "toggle": "f5"})

        with patch.object(package, "create_archive", side_effect=edit_profile):
            with self.assertRaisesRegex(RuntimeError, "configuration changed"):
                package.publish_release(self.project, candidate, config)
        after = self.snapshot()
        self.assertEqual(package.validate_profile(profile)["toggle"], "f5")
        self.assertEqual(after[0][package.EXE_NAME], before[0][package.EXE_NAME])
        self.assertEqual(after[1], before[1])

    def test_existing_unowned_lock_file_does_not_block_publication(self):
        candidate, config = self.fixture()
        (self.project / "build_release" / ".publish.lock").write_bytes(b"previous process")
        package.publish_release(self.project, candidate, config)

    def test_live_publication_lock_rejects_concurrent_builder(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        with package.publication_lock(self.project / "build_release" / ".publish.lock"):
            with self.assertRaisesRegex(RuntimeError, "Another release publication"):
                package.publish_release(self.project, candidate, config)
        self.assertEqual(self.snapshot(), before)

    def test_terminated_builder_releases_os_publication_lock(self):
        candidate, config = self.fixture()
        lock_path = self.project / "build_release" / ".publish.lock"
        script = ("import os, sys\nfrom pathlib import Path\n"
                  "from tools.package_release import publication_lock\n"
                  "with publication_lock(Path(sys.argv[1])):\n    os._exit(0)\n")
        result = subprocess.run([sys.executable, "-B", "-c", script, str(lock_path)],
                                cwd=Path(package.__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        package.publish_release(self.project, candidate, config)

    def test_candidate_must_be_inside_project_build_directory(self):
        _, config = self.fixture()
        before = self.snapshot()
        with self.assertRaises(ValueError):
            package.publish_release(self.project, self.project / "release", config)
        self.assertEqual(self.snapshot(), before)

    def test_failed_rollback_keeps_backup_and_reports_recovery_location(self):
        candidate, config = self.fixture()
        before = self.snapshot()
        replace = os.replace
        calls = 0

        def fail_commit_and_rollback(source, target):
            nonlocal calls
            calls += 1
            if calls in {4, 5}:
                raise PermissionError("simulated locked new artifact")
            return replace(source, target)

        with patch.object(package.os, "replace", side_effect=fail_commit_and_rollback):
            with self.assertRaisesRegex(RuntimeError, "rollback failed; recovery files"):
                package.publish_release(self.project, candidate, config)
        backup, = (self.project / "backups").iterdir()
        self.assertEqual((backup / "release" / package.EXE_NAME).read_bytes(), before[0][package.EXE_NAME])
        self.assertEqual((self.project / package.ARCHIVE_NAME).read_bytes(), before[1])


class PackagedSelfTestTests(unittest.TestCase):
    def setUp(self):
        import YanziHelper
        self.app = YanziHelper
        self.temporary = TemporaryDirectory(prefix="yanzi-selftest-test-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.profile = self.folder / "config.ini"
        self.executable = self.folder / "YanziHelper.exe"
        self.executable.write_bytes(b"self-test executable identity")

    def run_frozen(self):
        from app_selftest import run
        report_path = self.folder / "report.json"
        with patch.object(sys, "frozen", True, create=True), \
                patch.object(sys, "executable", str(self.executable)), \
                patch.object(self.app, "BUNDLED_CONFIG_FILE", self.profile), \
                patch.object(self.app, "RESOURCE_DIR", self.folder), \
                patch.object(self.app, "DD_DLL", self.folder / "ddhid.63340.dll"):
            result = run(report_path)
        return result, json.loads(report_path.read_text(encoding="utf-8"))

    def test_invalid_bundled_configuration_fails_before_constructing_ui(self):
        for contents in ("[SETTINGS]\ninvalid line\n", "[SETTINGS]\ntoggle = e\n"):
            with self.subTest(contents=contents):
                self.profile.write_text(contents, encoding="utf-8")
                with patch("tkinter.Tk") as create_ui:
                    result, report = self.run_frozen()
                self.assertEqual(result, 1)
                self.assertEqual(report["status"], "FAIL")
                create_ui.assert_not_called()
                self.assertEqual(self.profile.read_text(encoding="utf-8"), contents)

    def test_hidden_selftest_binds_pass_to_executable_and_bundled_config(self):
        write_config_file(self.profile, {**DEFAULTS, "toggle": "f4"})
        original = self.profile.read_bytes()
        (self.folder / "ddhid.63340.dll").write_bytes(b"placeholder; must never be loaded")
        for filename in ("start.wav", "stop.wav"):
            with wave.open(str(self.folder / filename), "wb") as sound:
                sound.setnchannels(1)
                sound.setsampwidth(1)
                sound.setframerate(8000)
                sound.writeframes(b"\x80" * 80)
        result, report = self.run_frozen()
        self.assertEqual(result, 0, report)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["executable_sha256"], package.sha256_file(self.executable))
        self.assertEqual(report["bundled_config_sha256"], package.sha256_file(self.profile))
        self.assertEqual(self.profile.read_bytes(), original)
        self.assertFalse(report["real_driver_loaded"])
        self.assertFalse(report["global_hook_started"])


if __name__ == "__main__":
    unittest.main()
