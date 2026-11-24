# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import copy_metadata

project_dir = Path(SPECPATH)
sys.path.insert(0, str(project_dir))
from tools.package_release import validate_profile
resources = (
    "ddhid.63340.dll", "start.wav", "stop.wav",
    "assets/yunshang.png", "assets/feitian.png", "assets/brand.png",
)
missing = [filename for filename in resources if not (project_dir / filename).is_file()]
if missing:
    raise SystemExit("Required release resources are missing: " + ", ".join(missing)
                     + ". See docs/BUILD.md before building.")
config_source = project_dir / "config.ini"
if not config_source.is_file():
    config_source = project_dir / "config.example.ini"
if not config_source.is_file():
    raise SystemExit("Release configuration is missing: provide config.ini or config.example.ini.")
validate_profile(config_source)
resource_data = [(str(project_dir / filename), str(Path(filename).parent)) for filename in resources]
resource_data.extend((str(project_dir / filename), str(Path(filename).parent))
                     for filename in ("LICENSE", "NOTICE.md", "LICENSES/pydd-MIT.txt"))
resource_data.extend(copy_metadata("Pillow"))
if (project_dir / "24983.png").is_file():
    resource_data.append((str(project_dir / "24983.png"), "."))

a = Analysis(
    [str(project_dir / "YanziHelper.py")],
    pathex=[str(project_dir)],
    binaries=[],
    datas=resource_data,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pillow's optional scientific-image plugins can see NumPy in a developer
    # environment. The application only decodes PNGs and uses Pillow's core.
    excludes=["numpy"],
    noarchive=False,
    optimize=0,
)
# Always expose the selected source as config.ini inside the executable.
a.datas.append(("config.ini", str(config_source), "DATA"))
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="YanziHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
)
