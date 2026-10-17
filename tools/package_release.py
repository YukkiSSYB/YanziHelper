"""Package a verified release with UTF-8 filenames and preserve the old ZIP."""

from pathlib import Path
import json
import shutil
import uuid
from zipfile import ZipFile, ZIP_DEFLATED


def main() -> None:
    project = Path(__file__).resolve().parents[1]
    release = project / "release"
    names = ["YanziHelper.exe", "config.ini", "selftest-result.json"]
    names.extend(name for name in ("使用说明.txt", "LICENSE", "NOTICE.md", "LICENSES/pydd-MIT.txt")
                 if (release / name).is_file())
    report = json.loads((release / "selftest-result.json").read_text(encoding="utf-8"))
    if not (report.get("status") == "PASS" and report.get("frozen") is True
            and report.get("real_driver_loaded") is False
            and report.get("global_hook_started") is False):
        raise RuntimeError("The release has not passed its packaged self-test")
    destination = project / "YanziHelper-新版.zip"
    staging = project / "build_release" / ("release-" + uuid.uuid4().hex + ".zip")
    staging.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(staging, "w", ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(release / name, name)
    with ZipFile(staging) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Release ZIP integrity check failed")
        for name in names:
            if archive.read(name) != (release / name).read_bytes():
                raise RuntimeError(f"Release ZIP content mismatch: {name}")
    if destination.exists():
        backup = project / "backups" / ("archive-" + uuid.uuid4().hex)
        backup.mkdir(parents=True)
        shutil.copy2(destination, backup / destination.name)
    staging.replace(destination)
    print(f"Verified release ZIP: {destination}")


if __name__ == "__main__":
    main()
