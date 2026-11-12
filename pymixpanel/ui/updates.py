"""Mise à jour dans l'app : bouton dans l'en-tête, téléchargement et relance."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from .. import __version__
from .. import updater as U
from ..config import load_settings, save_settings


def auto_check_enabled() -> bool:
    return bool(load_settings().get("check_updates", True))


def set_auto_check(on: bool):
    s = load_settings()
    s["check_updates"] = on
    save_settings(s)


class UpdateController(QObject):
    """Vérifie GitHub en arrière-plan ; ne télécharge ni n'installe rien sans ton accord."""
    _found = Signal(object, bool)    # Update | None, manuel
    _failed = Signal(str, bool)
    _progress = Signal(int, int)
    _downloaded = Signal(object)

    def __init__(self, window, button):
        super().__init__(window)
        self.win, self.btn = window, button
        self.update: U.Update | None = None
        self._busy = False
        self._dlg: QProgressDialog | None = None
        self.btn.hide()
        self.btn.clicked.connect(self.prompt)
        self._found.connect(self._on_found)
        self._failed.connect(self._on_failed)
        self._progress.connect(self._on_progress)
        self._downloaded.connect(self._on_downloaded)

    def check_later(self):
        if auto_check_enabled():
            QTimer.singleShot(4000, lambda: self.check(manual=False))

    def check(self, manual: bool = True):
        if self._busy:
            return
        self._busy = True

        def work():
            try:
                self._found.emit(U.fetch_latest(), manual)
            except U.UpdateError as ex:
                self._failed.emit(str(ex), manual)
        threading.Thread(target=work, daemon=True).start()

    def _on_found(self, up, manual: bool):
        self._busy = False
        self.update = up
        if up is None:
            self.btn.hide()
            if manual:
                QMessageBox.information(self.win, "Mises à jour", f"MixPanel {__version__} est à jour.")
            return
        self.btn.setText(f"Mise à jour {up.version}")
        self.btn.setToolTip("Une nouvelle version de MixPanel est disponible")
        self.btn.show()
        if manual:
            self.prompt()

    def _on_failed(self, msg: str, manual: bool):
        self._busy = False
        if manual:
            QMessageBox.warning(self.win, "Mises à jour", msg)

    def prompt(self):
        up = self.update
        if not up or self.win.engine.locked:
            return
        box = QMessageBox(self.win)
        box.setWindowTitle("Mise à jour disponible")
        box.setText(f"<b>MixPanel {up.version}</b> est disponible (version actuelle : {__version__}).")
        if up.notes:
            box.setDetailedText(up.notes)
        if U.can_self_update():
            box.setInformativeText("MixPanel va se fermer le temps de l'installation, puis se relancer. "
                                   "Tes profils ne sont pas touchés.\nÀ faire hors direct.")
            go = box.addButton("Installer maintenant", QMessageBox.AcceptRole)
        else:
            box.setInformativeText("Cette copie ne s'installe pas toute seule : ouvre la page de la release.")
            go = box.addButton("Ouvrir la page", QMessageBox.AcceptRole)
        box.addButton("Plus tard", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not go:
            return
        if not U.can_self_update():
            QDesktopServices.openUrl(QUrl(U.RELEASES_PAGE))
            return
        self._start_download(up)

    def _start_download(self, up: U.Update):
        self._dlg = QProgressDialog(f"Téléchargement de MixPanel {up.version}…", None, 0, 100, self.win)
        self._dlg.setWindowTitle("Mise à jour")
        self._dlg.setMinimumDuration(0)
        self._dlg.setAutoClose(False)
        self._dlg.setValue(0)
        self.btn.setEnabled(False)

        def work():
            try:
                self._downloaded.emit(U.download(up, lambda d, t: self._progress.emit(d, t)))
            except U.UpdateError as ex:
                self._downloaded.emit(str(ex))
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done: int, total: int):
        if self._dlg and total:
            self._dlg.setValue(min(99, int(done * 100 / total)))

    def _on_downloaded(self, result):
        if self._dlg:
            self._dlg.close()
            self._dlg = None
        self.btn.setEnabled(not self.win.engine.locked)
        if isinstance(result, str):
            QMessageBox.warning(self.win, "Mise à jour", f"La mise à jour n'a pas été installée.\n\n{result}")
            return
        if self.win.engine.locked:   # verrouillé pendant le téléchargement : on n'ose pas fermer l'app
            QMessageBox.information(self.win, "Mise à jour", "MixPanel a été verrouillé : mise à jour annulée.")
            return
        try:
            U.launch_installer(result)
        except OSError as ex:
            QMessageBox.warning(self.win, "Mise à jour", f"L'installateur n'a pas pu démarrer :\n{ex}")
            return
        self.win.quit_app()
