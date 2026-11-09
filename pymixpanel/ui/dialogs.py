"""Fenêtre de réglages du profil."""
from __future__ import annotations

import mido
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QMessageBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from .. import actions as A
from ..config import APP_DIR, PROFILES_DIR, Profile
from ..midi_io import list_ports
from .updates import auto_check_enabled, set_auto_check


def _spin(lo, hi, val, suffix="") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    s.setSuffix(suffix)
    return s


def _section(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("H2")
    lab.setContentsMargins(0, 10, 0, 2)
    return lab


class SettingsDialog(QDialog):
    def __init__(self, profile: Profile, parent=None, midi=None, engine=None):
        super().__init__(parent)
        self.engine = engine
        self.midi = midi or (engine.midi if engine else None)
        midi = self.midi
        self.setWindowTitle("Réglages du profil")
        self.setMinimumWidth(460)
        self.p = profile
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        form = QFormLayout()
        form.setSpacing(10)

        self.name = QLineEdit(profile.name)
        form.addRow("Nom du profil", self.name)

        form.addRow(_section("VMIX"))
        self.host = QLineEdit(profile.vmix_host)
        self.host.setPlaceholderText("localhost ou 192.168.x.x")
        form.addRow("Adresse", self.host)
        self.port = _spin(1, 65535, profile.vmix_port)
        form.addRow("Port TCP", self.port)
        self.poll = _spin(30, 2000, profile.poll_ms, " ms")
        self.poll.setToolTip("Fréquence de lecture de l'état vMix (retours LED / faders)")
        form.addRow("Rafraîchissement", self.poll)

        form.addRow(_section("X-TOUCH"))
        ins, outs = list_ports()
        self.midi_in = self._port_combo(ins, profile.midi_in)
        self.midi_out = self._port_combo(outs, profile.midi_out)
        form.addRow("Entrée MIDI", self.midi_in)
        form.addRow("Sortie MIDI", self.midi_out)
        chan = QHBoxLayout()
        self.global_ch = _spin(1, 16, profile.global_channel)
        self.global_ch.setToolTip("Canal sur lequel la X-Touch écoute les retours : moteurs des faders, "
                                  "LED et anneaux. Réglé dans le menu de configuration de la console.")
        chan.addWidget(self.global_ch)
        find = QPushButton("Détecter…")
        find.setToolTip("Fait bouger le fader 1 canal par canal : clique quand il bouge")
        find.clicked.connect(self._detect_channel)
        find.setEnabled(bool(midi and midi.connected))
        chan.addWidget(find)
        chan.addStretch(1)
        form.addRow("Canal global", chan)
        mode = QHBoxLayout()
        self.std_btn = QPushButton("Mode standard")
        self.std_btn.setToolTip("Le mode de MixPanel : tous les contrôles sont libres")
        self.std_btn.clicked.connect(lambda: self._set_mode("standard"))
        self.mc_btn = QPushButton("Mode Mackie (MC)…")
        self.mc_btn.setToolTip("Pour piloter un autre logiciel (Cubase, Reaper, Logic…) en Mackie Control")
        self.mc_btn.clicked.connect(lambda: self._set_mode("mc"))
        for b in (self.std_btn, self.mc_btn):
            b.setEnabled(bool(engine and midi and midi.connected))
            mode.addWidget(b)
        self.mode_label = QLabel()
        self.mode_label.setObjectName("Muted")
        mode.addWidget(self.mode_label, 1)
        form.addRow("Mode de la console", mode)
        self.force_std = QCheckBox("Remettre la console en mode standard au lancement de MixPanel")
        self.force_std.setChecked(bool(profile.force_standard))
        self.force_std.setToolTip("Utile si un autre logiciel l'a laissée en mode Mackie (MC)")
        form.addRow("", self.force_std)
        self._show_mode()
        leds = QHBoxLayout()
        self.led_preset = QComboBox()
        self.led_preset.addItem("Firmware récent (2 / 3)", (2, 0, 3))
        self.led_preset.addItem("Manuel Behringer (1 / 2)", (1, 0, 2))
        self.led_preset.addItem("Personnalisé", None)
        cur = (profile.led_on, profile.led_off, profile.led_blink)
        self.led_preset.setCurrentIndex(max(0, self.led_preset.findData(cur)) if cur in
                                        ((2, 0, 3), (1, 0, 2)) else 2)
        self.led_on = _spin(0, 127, profile.led_on)
        self.led_off = _spin(0, 127, profile.led_off)
        self.led_blink = _spin(0, 127, profile.led_blink)
        self.led_preset.currentIndexChanged.connect(self._led_preset)
        leds.addWidget(self.led_preset, 1)
        for text, sp in (("on", self.led_on), ("off", self.led_off), ("clignote", self.led_blink)):
            leds.addWidget(QLabel(text))
            leds.addWidget(sp)
        form.addRow("LED des boutons", leds)
        self.hold = _spin(0, 3000, profile.hold_ms, " ms")
        self.hold.setToolTip("Après un mouvement manuel, le moteur du fader attend ce délai "
                             "avant de suivre vMix (évite qu'il « lutte » contre ta main)")
        form.addRow("Protection fader", self.hold)
        self.ring = QCheckBox("Encodeurs libres des tranches = vu-mètre de la source")
        self.ring.setChecked(bool(profile.ring_meters))
        self.ring.setToolTip("L'anneau LED d'un encodeur sans action de rotation affiche le niveau "
                             "audio de la source de son fader")
        form.addRow("Anneaux LED", self.ring)

        form.addRow(_section("MODÈLE DE TRANCHE"))
        self.rows = []
        for i in range(3):
            c = QComboBox()
            c.addItem("Aucun", "-")
            for k, label in A.BUS_NAMES.items():
                c.addItem(label, k)
            cur = profile.strip_template[i] if i < len(profile.strip_template) else "-"
            c.setCurrentIndex(max(0, c.findData(cur)))
            self.rows.append(c)
            form.addRow(f"Ligne de boutons {i + 1}", c)
        form.addRow(_section("DONNÉES"))
        data = QHBoxLayout()
        path = QLabel(str(APP_DIR))
        path.setObjectName("Muted")
        path.setTextInteractionFlags(path.textInteractionFlags() | Qt.TextSelectableByMouse)
        data.addWidget(path, 1)
        open_btn = QPushButton("Ouvrir")
        open_btn.clicked.connect(lambda: (PROFILES_DIR.mkdir(parents=True, exist_ok=True),
                                          QDesktopServices.openUrl(QUrl.fromLocalFile(str(PROFILES_DIR)))))
        data.addWidget(open_btn)
        form.addRow("Profils", data)
        form.addRow(_section("MISES À JOUR"))
        self.auto_update = QCheckBox("Chercher une nouvelle version au démarrage")
        self.auto_update.setChecked(auto_check_enabled())
        form.addRow("", self.auto_update)
        updates = getattr(parent, "updates", None)
        if updates:
            check_btn = QPushButton("Vérifier maintenant")
            check_btn.clicked.connect(lambda: updates.check(manual=True))
            form.addRow("", check_btn)
        lay.addLayout(form)
        hint = QLabel("Les profils sont rangés dans ton dossier utilisateur : mettre MixPanel à jour "
                      "ne touche jamais à ta configuration.\n"
                      "Les ports MIDI sont reconnus par une partie de leur nom "
                      "(ex. « X-TOUCH ») : la X-Touch est retrouvée même si Windows change son numéro.")
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Enregistrer")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _show_mode(self):
        mc = bool(self.engine and self.engine.console_mode == "mc")
        self.mode_label.setText("● Mackie (MC) : MixPanel est en pause" if mc else "● Standard (MixPanel)")
        self.mode_label.setStyleSheet(f"color: {'#fbbf24' if mc else '#34d399'};")

    def _set_mode(self, mode: str):
        if mode == "mc":
            ok = QMessageBox.question(
                self, "Passer en mode Mackie (MC)",
                "La X-Touch va passer en mode Mackie Control pour piloter un autre logiciel "
                "(Cubase, Reaper, Logic…).\n\nTant qu'elle est dans ce mode, MixPanel l'ignore : "
                "aucune action vMix, aucun retour LED ni moteur.\n\nPour revenir : « Mode standard », "
                "ou relance MixPanel.",
                QMessageBox.Ok | QMessageBox.Cancel)
            if ok != QMessageBox.Ok:
                return
        self.engine.set_console_mode(mode)
        self._show_mode()

    def _led_preset(self):
        vals = self.led_preset.currentData()
        if vals:
            for sp, v in zip((self.led_on, self.led_off, self.led_blink), vals):
                sp.setValue(v)

    def _detect_channel(self):
        dlg = ChannelFinder(self.midi, self)
        if dlg.exec() and dlg.found:
            self.global_ch.setValue(dlg.found)

    @staticmethod
    def _port_combo(names, current) -> QComboBox:
        c = QComboBox()
        c.setEditable(True)
        for n in names:
            c.addItem(n)
        c.setCurrentText(current)
        return c

    def apply(self):
        p = self.p
        set_auto_check(self.auto_update.isChecked())
        p.name = self.name.text().strip() or p.name
        p.vmix_host = self.host.text().strip() or "localhost"
        p.vmix_port = self.port.value()
        p.poll_ms = self.poll.value()
        p.midi_in = self.midi_in.currentText().strip()
        p.midi_out = self.midi_out.currentText().strip()
        p.led_on, p.led_off, p.led_blink = self.led_on.value(), self.led_off.value(), self.led_blink.value()
        p.global_channel = self.global_ch.value()
        p.hold_ms = self.hold.value()
        p.ring_meters = self.ring.isChecked()
        p.force_standard = self.force_std.isChecked()
        p.strip_template = [c.currentData() for c in self.rows]


class ChannelFinder(QDialog):
    """Trouve le canal global de la X-Touch : le fader 1 et la première ligne de LED
    bougent canal par canal ; l'utilisateur clique quand la console réagit."""

    def __init__(self, midi, parent=None):
        super().__init__(parent)
        self.midi, self.found, self.ch, self.up = midi, 0, 0, False
        self.setWindowTitle("Détecter le canal global")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(14)
        hint = QLabel("Regarde la X-Touch : le <b>fader 1</b> et les LED de la première ligne vont "
                      "bouger quand le bon canal sera essayé. Clique alors sur « Il bouge ! ».")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.big = QLabel()
        self.big.setAlignment(Qt.AlignCenter)
        self.big.setStyleSheet("font-size: 34px; font-weight: 700; padding: 12px;")
        lay.addWidget(self.big)
        row = QHBoxLayout()
        ok = QPushButton("Il bouge !")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._accept)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)
        self.timer = QTimer(self, interval=450)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._tick()

    def _send(self, ch: int, on: bool):
        self.midi.send(mido.Message("control_change", channel=ch, control=1, value=110 if on else 10))
        for n in range(8):
            self.midi.send(mido.Message("note_on", channel=ch, note=n, velocity=2 if on else 0))

    def _tick(self):
        # chaque canal : fader en haut + LED, puis en bas, puis canal suivant
        if self.up:
            self._send(self.ch, False)   # le texte reste sur le canal qui vient de bouger
            self.ch = (self.ch + 1) % 16
            self.up = False
        else:
            self.up = True
            self._send(self.ch, True)
            self.shown = self.ch + 1
            self.big.setText(f"Canal {self.shown}")

    def _accept(self):
        self.found = self.shown
        self.accept()

    def done(self, r):
        self.timer.stop()
        for ch in range(16):
            self._send(ch, False)
        super().done(r)
