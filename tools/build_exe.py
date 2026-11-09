"""Construit l'installateur MixPanel-<version>-setup.exe (PyInstaller + Inno Setup).

    pip install -r requirements-build.txt
    python tools/build_exe.py

Étapes : l'app est d'abord compilée en dossier (dist/MixPanel/, sans console), puis Inno Setup
(ISCC.exe, à installer ou déjà présent sur les runners GitHub) l'emballe dans dist/.
Les profils vivent dans %APPDATA%\\MixPanel, indépendamment de l'installation.
`--no-installer` s'arrête au dossier dist/MixPanel/.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

from pymixpanel import __version__  # noqa: E402

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040C04B0', [
      StringStruct('CompanyName', 'MixPanel'),
      StringStruct('FileDescription', 'MixPanel - pilotage vMix pour Behringer X-Touch Compact'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('InternalName', 'MixPanel'),
      StringStruct('OriginalFilename', 'MixPanel.exe'),
      StringStruct('ProductName', 'MixPanel'),
      StringStruct('ProductVersion', '{v}')])]),
    VarFileInfo([VarStruct('Translation', [1036, 1200])])
  ]
)
"""


def make_icon(path: Path):
    """Icône multi-tailles dessinée par l'app elle-même."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from pymixpanel.ui.main_window import icon_pixmap

    make_icon.app = QApplication.instance() or QApplication(["build"])  # requis par QPixmap
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    icon_pixmap(256).save(buf, "PNG")
    png = BUILD / "icon-256.png"
    png.write_bytes(bytes(buf.data()))
    Image.open(png).save(path, sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])


def find_iscc() -> str | None:
    """ISCC.exe : dans le PATH, sinon dans les dossiers d'installation habituels d'Inno Setup."""
    found = shutil.which("ISCC")
    if found:
        return found
    for var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        for sub in ("Inno Setup 6", r"Programs\Inno Setup 6"):
            cand = Path(base) / sub / "ISCC.exe"
            if cand.exists():
                return str(cand)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--version", default=__version__,
                    help="version attendue (la compilation échoue si elle diffère de pymixpanel.__version__)")
    ap.add_argument("--no-installer", action="store_true",
                    help="s'arrêter au dossier dist/MixPanel/, sans créer l'installateur")
    args = ap.parse_args()
    if args.version.lstrip("v") != __version__:
        sys.exit(f"Version demandée {args.version} != pymixpanel.__version__ {__version__}. "
                 "Utilise tools/release.py pour changer de version.")
    iscc = None
    if not args.no_installer:
        iscc = find_iscc()
        if not iscc:
            sys.exit("ISCC.exe (Inno Setup 6) introuvable : installe-le (https://jrsoftware.org/isdl.php) "
                     "ou utilise --no-installer.")

    import PyInstaller.__main__

    BUILD.mkdir(exist_ok=True)
    ico = BUILD / "mixpanel.ico"
    make_icon(ico)
    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    vfile = BUILD / "version_info.txt"
    vfile.write_text(VERSION_INFO.format(t=tuple(int(x) for x in parts), v=__version__), encoding="utf-8")

    name = "MixPanel"
    shutil.rmtree(DIST / name, ignore_errors=True)
    PyInstaller.__main__.run([
        str(ROOT / "main.py"),
        "--name", name,
        "--onedir", "--windowed", "--noconfirm", "--clean",
        "--icon", str(ico),
        "--version-file", str(vfile),
        "--add-data", f"{ROOT / 'pymixpanel' / 'ui' / 'assets'};pymixpanel/ui/assets",
        "--hidden-import", "mido.backends.rtmidi",
        "--hidden-import", "rtmidi",
        "--exclude-module", "tkinter",
        "--distpath", str(DIST),
        "--workpath", str(BUILD / "work"),
        "--specpath", str(BUILD),
    ])
    shutil.rmtree(BUILD / "work", ignore_errors=True)
    app_dir = DIST / name
    print(f"\nOK : {app_dir.relative_to(ROOT)}/{name}.exe")
    if iscc:
        subprocess.run([
            iscc, "/Qp",
            f"/DAppVersion={__version__}",
            f"/DSourceDir={app_dir}",
            f"/DOutputDir={DIST}",
            f"/DIconFile={ico}",
            str(ROOT / "tools" / "installer.iss"),
        ], check=True)
        setup = DIST / f"MixPanel-{__version__}-setup.exe"
        print(f"OK : {setup.relative_to(ROOT)}  ({setup.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
