"""Instance unique : un second lancement réveille la fenêtre de la première au lieu d'en ouvrir une autre."""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_SERVER_NAME = "MixPanel.xtouch.vmix.single-instance"


def notify_running_instance() -> bool:
    """Prévient une instance déjà lancée. True si elle existe (et a été réveillée) : on doit alors quitter."""
    sock = QLocalSocket()
    sock.connectToServer(_SERVER_NAME)
    if not sock.waitForConnected(500):
        return False
    if sys.platform == "win32":
        try:   # autorise l'instance existante à repasser au premier plan
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
        except Exception:
            pass
    sock.write(b"show")
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    """Écoute les lancements suivants ; émet `activated` à chacun."""
    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = QLocalServer(self)
        QLocalServer.removeServer(_SERVER_NAME)   # reste éventuel d'un crash (no-op sous Windows)
        self._ok = self._server.listen(_SERVER_NAME)
        self._server.newConnection.connect(self._on_connection)

    def _on_connection(self):
        while (conn := self._server.nextPendingConnection()) is not None:
            conn.disconnected.connect(conn.deleteLater)
            conn.readyRead.connect(conn.readAll)
            conn.disconnectFromServer()
            self.activated.emit()
