"""Connexion MIDI à la X-Touch avec reconnexion automatique."""
from __future__ import annotations

import threading

import mido
from PySide6.QtCore import QObject, QTimer, Signal


def list_ports() -> tuple[list[str], list[str]]:
    try:
        return mido.get_input_names(), mido.get_output_names()
    except Exception:
        return [], []


def _match(names: list[str], hint: str) -> str | None:
    hint = hint.strip().lower()
    if not hint:
        return None
    for n in names:
        if n.lower() == hint:
            return n
    for n in names:
        if hint in n.lower():
            return n
    return None


class MidiLink(QObject):
    message = Signal(object)              # mido.Message (émis depuis le thread rtmidi)
    status_changed = Signal(bool, str)    # connecté, nom du port

    def __init__(self, in_hint: str = "X-TOUCH", out_hint: str = "X-TOUCH"):
        super().__init__()
        self.in_hint, self.out_hint = in_hint, out_hint
        self._in = None
        self._out = None
        self._lock = threading.Lock()
        self.connected = False
        self.port_name = ""
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._check)

    def start(self):
        self._check()
        self._timer.start()

    def configure(self, in_hint: str, out_hint: str):
        if (in_hint, out_hint) != (self.in_hint, self.out_hint):
            self.in_hint, self.out_hint = in_hint, out_hint
            self._close()
            self._check()

    def _check(self):
        ins, outs = list_ports()
        if self.connected:
            if self.port_name in ins:
                return
            self._close()
        in_name = _match(ins, self.in_hint)
        if not in_name:
            return
        out_name = _match(outs, self.out_hint or self.in_hint)
        try:
            self._in = mido.open_input(in_name, callback=self._on_msg)
            if out_name:
                self._out = mido.open_output(out_name)
        except Exception:
            self._close()
            return
        self.connected, self.port_name = True, in_name
        self.status_changed.emit(True, in_name)

    def _on_msg(self, msg):
        self.message.emit(msg)

    def send(self, msg: mido.Message):
        with self._lock:
            if self._out is None:
                return
            try:
                self._out.send(msg)
            except Exception:
                pass

    def _close(self):
        with self._lock:
            for port in (self._in, self._out):
                try:
                    if port is not None:
                        port.close()
                except Exception:
                    pass
            self._in = self._out = None
        was = self.connected
        self.connected, self.port_name = False, ""
        if was:
            self.status_changed.emit(False, "")

    def stop(self):
        self._timer.stop()
        self._close()
