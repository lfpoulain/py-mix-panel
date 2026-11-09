"""Lance MixPanel.  Usage : python main.py [show.ctrl | profil.json]"""
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pymixpanel import APP_NAME
from pymixpanel.config import (Profile, list_profiles, load_settings, migrate_legacy, profile_path,
                               save_settings)
from pymixpanel.engine import Engine
from pymixpanel.importer import import_ctrl
from pymixpanel.midi_io import MidiLink
from pymixpanel.ui.main_window import MainWindow
from pymixpanel.ui.theme import QSS
from pymixpanel.vmix import VmixClient


def initial_profile(argv: list[str]) -> Profile:
    if len(argv) > 1:
        src = Path(argv[1])
        if src.suffix.lower() == ".ctrl":
            prof, _ = import_ctrl(src)
            prof.save(profile_path(prof.name))
            return prof
        return Profile.load(src)
    last = load_settings().get("last_profile")
    if last and Path(last).exists():
        return Profile.load(Path(last))
    existing = list_profiles()
    if existing:
        return Profile.load(existing[0])
    prof = Profile()
    prof.save(profile_path(prof.name))
    return prof


def main():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mixpanel.xtouch.vmix")
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(QSS)

    migrate_legacy()   # profils d'une ancienne version (à côté de l'exe ou du projet)
    profile = initial_profile(sys.argv)
    settings = load_settings()
    settings["last_profile"] = str(profile.path)
    save_settings(settings)

    midi = MidiLink(profile.midi_in, profile.midi_out)
    vmix = VmixClient(profile.vmix_host, profile.vmix_port, profile.poll_ms)
    engine = Engine(profile, midi, vmix)
    win = MainWindow(engine)
    win.show()
    midi.start()
    vmix.start()
    code = app.exec()
    vmix.stop()
    midi.stop()
    sys.exit(code)


if __name__ == "__main__":
    main()
