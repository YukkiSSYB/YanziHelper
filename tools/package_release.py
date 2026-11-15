"""Validate, assemble and publish a release, rolling back failed commits."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_config import read_config_file, validate_config


EXE_NAME = "YanziHelper.exe"
REPORT_NAME = "selftest-result.json"
ARCHIVE_NAME = "YanziHelper-新版.zip"
DOCUMENTS = ("使用说明.txt", "LICENSE", "NOTICE.md", "LICENSES/pydd-MIT.txt")


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate_profile(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"Release configuration is missing: {path}")
    data = read_config_file(path)
    if not data:
        raise ValueError(f"No usable [SETTINGS] configuration in {path}")
    return validate_config(data)


def validate_candidate(candidate: Path, config_source: Path) -> None:
    validate_profile(config_source)
    report = json.loads((candidate / REPORT_NAME).read_text(encoding="utf-8"))
    if not (report.get("status") == "PASS" and report.get("frozen") is True
            and report.get("real_driver_loaded") is False
            and report.get("global_hook_started") is False):
        raise RuntimeError("The candidate has not passed its packaged self-test")
    if report.get("executable_sha256") != sha256_file(candidate / EXE_NAME):
        raise RuntimeError("The candidate executable does not match its self-test report")
    if report.get("bundled_config_sha256") != sha256_file(config_source):
        raise RuntimeError("The build configuration does not match the tested bundled configuration")


def _within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"Release path must remain inside {root}: {path}")
    return resolved


def create_archive(folder: Path, archive_path: Path, names: list[str]) -> None:
    with ZipFile(archive_path, "w", ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(folder / name, name)
    with ZipFile(archive_path) as archive:
        if archive.namelist() != names or archive.testzip() is not None:
            raise RuntimeError("Release ZIP integrity check failed")
        for name in names:
            with archive.open(name) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != sha256_file(folder / name):
                raise RuntimeError(f"Release ZIP content mismatch: {name}")


@contextmanager
def publication_lock(path: Path):
    # Keep the file in place: deleting it after unlocking could let another
    # publisher lock an old inode while a third publisher creates a new one.
    # The OS releases ownership even when a builder crashes or is terminated.
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Another release publication owns {path}") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def commit_release(project: Path, prepared: Path, archive_path: Path) -> Path:
    """Rename complete artifacts into place; undo successful moves on failure.

    Old artifacts remain together in backups after success. If the operating
    system also refuses a rollback, retain every artifact and identify the
    backup directory in the error instead of removing recovery material.
    """
    project = project.resolve()
    prepared = _within(prepared, project)
    archive_path = _within(archive_path, project)
    release = _within(project / "release", project)
    destination = _within(project / ARCHIVE_NAME, project)
    backup = _within(project / "backups" / ("release-" + uuid.uuid4().hex), project)
    backup.mkdir(parents=True)
    moves = []
    if release.exists():
        moves.append((release, backup / "release"))
    if destination.exists():
        moves.append((destination, backup / ARCHIVE_NAME))
    moves.extend(((prepared, release), (archive_path, destination)))
    completed = []
    try:
        for source, target in moves:
            os.replace(source, target)
            completed.append((source, target))
    except BaseException as exc:
        rollback_errors = []
        for source, target in reversed(completed):
            try:
                os.replace(target, source)
            except OSError as rollback_error:
                rollback_errors.append(f"{target} -> {source}: {rollback_error}")
        if rollback_errors:
            raise RuntimeError(f"Release commit and rollback failed; recovery files: {backup}. "
                               + "; ".join(rollback_errors)) from exc
        raise
    return backup


def publish_release(project: Path, candidate: Path, config_source: Path) -> Path:
    project = project.resolve()
    build = _within(project / "build_release", project)
    candidate = _within(candidate, build)
    release = _within(project / "release", project)
    build.mkdir(parents=True, exist_ok=True)
    # Two simultaneous builders must not back up or replace each other's work.
    lock_path = build / ".publish.lock"
    with publication_lock(lock_path):
        validate_candidate(candidate, config_source)
        selected_profile = release / "config.ini" if (release / "config.ini").exists() else config_source
        validate_profile(selected_profile)
        prepared = _within(build / ("package-" + uuid.uuid4().hex), project)
        if release.exists():
            # Preserve user-exported profiles and other local files as well
            # as config.ini. Only the explicit distribution list enters ZIP.
            shutil.copytree(release, prepared)
        else:
            prepared.mkdir()
        for name in (EXE_NAME, REPORT_NAME):
            shutil.copy2(candidate / name, prepared / name)
        shutil.copy2(selected_profile, prepared / "config.ini")
        names = [EXE_NAME, "config.ini", REPORT_NAME]
        for name in DOCUMENTS:
            source = project / name
            if source.is_file():
                target = prepared / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            if (prepared / name).is_file():
                names.append(name)
        validate_candidate(prepared, config_source)
        validate_profile(prepared / "config.ini")
        archive_path = build / ("release-" + uuid.uuid4().hex + ".zip")
        create_archive(prepared, archive_path, names)
        # Detect an edit to the live profile during candidate assembly before
        # moving the release; otherwise that edit would silently disappear.
        if sha256_file(selected_profile) != sha256_file(prepared / "config.ini"):
            raise RuntimeError("Release configuration changed while assembling the candidate; retry")
        commit_release(project, prepared, archive_path)
    return project / ARCHIVE_NAME


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--validate-config", type=Path)
    mode.add_argument("--candidate", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.validate_config is not None:
        validate_profile(args.validate_config)
        print(f"Validated release configuration: {args.validate_config}")
        return
    project = Path(__file__).resolve().parents[1]
    config_source = args.config or project / "config.ini"
    if args.config is None and not config_source.is_file():
        config_source = project / "config.example.ini"
    archive = publish_release(project, args.candidate, config_source)
    print(f"Verified release ZIP: {archive}")


if __name__ == "__main__":
    main()
