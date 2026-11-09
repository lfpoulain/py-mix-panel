"""Assistant « Diagnostic X-Touch » : vérifie en une minute que la console et l'app
se comprennent, dans les deux sens.

  1. Sorties : l'app allume les LED rangée par rangée, remplit les anneaux et fait
     bouger les faders. Tu regardes si chaque chose s'allume au bon endroit.
  2. Entrées : tu appuies, tournes et bouges chaque contrôle ; il se coche ici.

Pendant le diagnostic, la console ne déclenche rien dans vMix.
"""
from __future__ import annotations

import mido
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from ..xtouch import TRANSPORT
from . import theme as T
from .params import label

# (titre, [(cid, rôle, texte de la pastille)])
GROUPS = [
    ("Encodeurs · rotation", [(f"enc{i}", "main", str(i)) for i in range(1, 17)]),
    ("Encodeurs · appui", [(f"push{i}", "main", str(i)) for i in range(1, 17)]),
    ("Ligne 1", [(f"m1_{i}", "main", str(i)) for i in range(1, 9)]),
    ("Ligne 2", [(f"m2_{i}", "main", str(i)) for i in range(1, 9)]),
    ("Ligne 3", [(f"m3_{i}", "main", str(i)) for i in range(1, 9)]),
    ("Faders · position", [(f"fader{i}", "main", str(i)) for i in range(1, 9)] + [("main", "main", "M")]),
    ("Faders · tactile", [(f"fader{i}", "touch", str(i)) for i in range(1, 9)] + [("main", "touch", "M")]),
    ("Boutons de fader", [(f"fbtn{i}", "main", str(i)) for i in range(1, 9)] + [("mainbtn", "main", "M")]),
    ("Transport", [(cid, "main", g) for (cid, _), g in zip(TRANSPORT, ("◀◀", "▶▶", "LOOP", "●", "■", "▶"))]),
]

CHIP = ("QLabel {{ background: {bg}; color: {fg}; border: 1px solid {bd}; border-radius: 7px;"
        " padding: 4px 0; font-weight: 600; min-width: 34px; }}")

# LED : groupes physiques (notes de retour) dans l'ordre de la console
LED_GROUPS = [("Ligne 1", range(0, 8)), ("Ligne 2", range(8, 16)), ("Ligne 3", range(16, 24)),
              ("Boutons de fader", range(24, 33)), ("Transport", range(33, 39))]


class DiagnosticDialog(QDialog):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Diagnostic X-Touch")
        self.resize(760, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        seg = QFrame()
        seg.setObjectName("SegBox")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        self.stack = QStackedWidget()
        self.tabs = []
        for i, text in enumerate(("1 · Sorties", "2 · Entrées", "3 · Rafraîchissement")):
            b = QPushButton(text)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, i=i: self._tab(i))
            sl.addWidget(b, 1)
            self.tabs.append(b)
        lay.addWidget(seg)
        lay.addWidget(self.stack, 1)
        self.stack.addWidget(self._outputs_page())
        self.stack.addWidget(self._inputs_page())
        self.stack.addWidget(self._perf_page())

        bottom = QHBoxLayout()
        bottom.addWidget(label("Pendant le diagnostic, la console ne déclenche rien dans vMix.", "Hint"))
        bottom.addStretch(1)
        close = QPushButton("Terminer")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        lay.addLayout(bottom)

        engine.diagnostic = True
        engine.diag_input.connect(self._on_input)
        engine.diag_unknown.connect(self._on_unknown)
        self._seq: list = []
        self._timer = QTimer(self, interval=90)
        self._timer.timeout.connect(self._step)

    # -- pages ----------------------------------------------------------------------
    def _outputs_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(12)
        v.addWidget(label("L'app va allumer les LED rangée par rangée, remplir les anneaux des encodeurs "
                          "puis faire monter et descendre les faders. Regarde la console : chaque étape doit "
                          "se produire au bon endroit.", "Hint", True))
        start = QPushButton("▶  Lancer la démonstration")
        start.setObjectName("Primary")
        start.clicked.connect(self._run_demo)
        v.addWidget(start, 0, Qt.AlignLeft)
        self.phase = QLabel("")
        self.phase.setStyleSheet("font-size: 22px; font-weight: 700; padding: 16px 0;")
        v.addWidget(self.phase)
        v.addWidget(label("RIEN NE BOUGE ?", "H2"))
        v.addWidget(label("• Aucun retour du tout : le canal global ne correspond pas → Réglages → "
                          "Canal global → Détecter….\n"
                          "• Les LED clignotent au lieu de rester fixes : Réglages → LED des boutons → "
                          "« Manuel Behringer ».\n"
                          "• La console doit être en mode MIDI standard (pas en mode MC).", "Hint", True))
        v.addStretch(1)
        return w

    def _inputs_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)
        v.addWidget(label("Appuie sur chaque bouton, tourne et appuie chaque encodeur, touche et bouge chaque "
                          "fader. Chaque contrôle reconnu se coche ici et clignote sur la surface.", "Hint", True))
        self.count = label("", "H1")
        v.addWidget(self.count)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(8)
        self.chips: dict[tuple[str, str], QLabel] = {}
        for row, (title, items) in enumerate(GROUPS):
            grid.addWidget(label(title, "Muted"), row, 0)
            for col, (cid, role, text) in enumerate(items):
                chip = QLabel(text)
                chip.setAlignment(Qt.AlignCenter)
                self.chips[(cid, role)] = chip
                grid.addWidget(chip, row, col + 1)
        v.addLayout(grid)
        self.layer_hint = label("", "Hint", True)
        self.layer_hint.setStyleSheet(f"color: {T.DANGER};")
        v.addWidget(self.layer_hint)
        self.unknown = label("", "Hint", True)
        v.addWidget(self.unknown)
        v.addStretch(1)
        self._seen: set = set()
        self._unknown: list[str] = []
        self._refresh_chips()
        return w

    def _perf_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(10)
        v.addWidget(label("Mesures en direct, actualisées chaque seconde. Bouge des faders pour voir la charge "
                          "augmenter.", "Hint", True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.perf: dict[str, QLabel] = {}
        rows = [("cmd", "vMix · commandes"), ("xml", "vMix · lecture de l'état"),
                ("midi", "MIDI (X-Touch)"), ("prot", "Protection des faders")]
        for r, (key, title) in enumerate(rows):
            grid.addWidget(label(title, "Muted"), r, 0)
            val = QLabel("—")
            val.setStyleSheet("font-weight: 600;")
            self.perf[key] = val
            grid.addWidget(val, r, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)
        self.perf_hint = label("", "Hint", True)
        v.addWidget(self.perf_hint)
        v.addStretch(1)
        self._prev = None
        self._perf_timer = QTimer(self, interval=1000)
        self._perf_timer.timeout.connect(self._update_perf)
        self._perf_timer.start()
        return w

    def _update_perf(self):
        import time
        vm, e, p = self.engine.vmix, self.engine, self.engine.profile
        now = time.monotonic()
        cur = (now, vm.stats["cmd"], vm.stats["xml"], e.stats["midi_in"], e.stats["midi_out"])
        if self._prev is None:
            self._prev = cur
            return
        dt = max(0.001, now - self._prev[0])
        cmd_s, xml_s, mi_s, mo_s = ((c - o) / dt for c, o in zip(cur[1:], self._prev[1:]))
        self._prev = cur
        lat = f"{vm.latency_ms:.0f} ms aller-retour" if vm.latency_ms is not None else "—"
        self.perf["cmd"].setText(f"{lat}  ·  {cmd_s:.0f} commandes/s")
        xml = f"{vm.xml_ms:.0f} ms par lecture" if vm.xml_ms is not None else "—"
        self.perf["xml"].setText(f"{xml}  ·  {xml_s:.1f} lectures/s (réglage : toutes les {p.poll_ms} ms)")
        self.perf["midi"].setText(f"{mi_s:.0f} messages reçus/s  ·  {mo_s:.0f} envoyés/s")
        self.perf["prot"].setText(f"{p.hold_ms} ms après un geste, puis attente de la confirmation de vMix "
                                  f"(2 s maximum)")
        hints = []
        if not vm.online:
            hints.append("vMix n'est pas connecté.")
        if vm.xml_ms is not None and vm.xml_ms > p.poll_ms:
            hints.append(f"vMix met {vm.xml_ms:.0f} ms à produire son état, plus que l'intervalle réglé "
                         f"({p.poll_ms} ms) : les retours arrivent en retard. Augmente le rafraîchissement "
                         "dans les Réglages ou allège vMix.")
        if vm.latency_ms is not None and vm.latency_ms > 40:
            hints.append(f"vMix répond lentement aux commandes ({vm.latency_ms:.0f} ms) : il est probablement "
                         "très chargé (processeur, nombre de sources).")
        self.perf_hint.setText("\n".join(hints) or "Tout est dans les temps.")

    def _tab(self, i: int):
        for k, b in enumerate(self.tabs):
            b.setChecked(k == i)
        self.stack.setCurrentIndex(i)

    # -- entrées ----------------------------------------------------------------------
    def _refresh_chips(self):
        for key, chip in self.chips.items():
            ok = key in self._seen
            chip.setStyleSheet(CHIP.format(bg="#14361f" if ok else T.PANEL2,
                                           fg=T.OK if ok else T.FAINT,
                                           bd=T.OK if ok else T.BORDER))
        total = len(self.chips)
        done = len(self._seen & set(self.chips))
        self.count.setText(f"{done} / {total} contrôles reconnus" + ("  ✓" if done == total else ""))

    def _on_input(self, cid: str, role: str, page: str):
        self._seen.add((cid, role))
        self._refresh_chips()
        if page == "B":
            self.layer_hint.setText("La console est en Layer B (page B de l'app). C'est normal si tu "
                                    "l'utilises ; sinon appuie sur « ◀ A ».")

    def _on_unknown(self, mid):
        if mid.type == "pitch":
            self.layer_hint.setText("La console envoie du pitch bend : elle est en mode Mackie (MC). "
                                    "Réglages → Mode de la console → « Mode standard ».")
        text = mid.label()
        if text not in self._unknown:
            self._unknown.append(text)
            self._show_unknown()

    def _show_unknown(self):
        self.unknown.setText("<b>Messages non reconnus</b> (autre canal d'envoi, mode MC, configuration "
                             "modifiée dans X-Touch Editor ?) : " + ", ".join(self._unknown[-12:]))

    # -- sorties ----------------------------------------------------------------------
    def _send(self, msg: mido.Message):
        self.engine.midi.send(msg)

    def _run_demo(self):
        ch = self.engine._out_ch
        p = self.engine.profile
        cc = lambda n, v: mido.Message("control_change", channel=ch, control=n, value=v)
        note = lambda n, v: mido.Message("note_on", channel=ch, note=n, velocity=v)
        seq: list = []

        def phase(text):
            seq.append(("text", text))

        def wait(ticks):
            seq.extend([("wait", None)] * ticks)

        # tout éteindre
        seq.append(("send", [note(n, p.led_off) for n in range(39)] + [cc(26 + i, 0) for i in range(16)]))
        for name, notes in LED_GROUPS:
            phase(f"LED · {name}")
            seq.append(("send", [note(n, p.led_on) for n in notes]))
            wait(8)
            seq.append(("send", [note(n, p.led_off) for n in notes]))
        phase("Anneaux des 16 encodeurs")
        seq.append(("send", [cc(10 + i, 2) for i in range(16)]))     # mode « fan »
        for v in range(1, 14):
            seq.append(("send", [cc(26 + i, v) for i in range(16)]))
        wait(4)
        phase("Faders motorisés · en haut")
        seq.append(("send", [cc(1 + i, 127) for i in range(9)]))
        wait(12)
        phase("Faders motorisés · en bas")
        seq.append(("send", [cc(1 + i, 0) for i in range(9)]))
        wait(12)
        phase("Faders motorisés · au milieu")
        seq.append(("send", [cc(1 + i, 64) for i in range(9)]))
        wait(12)
        phase("Terminé : tout s'est allumé au bon endroit ?")
        seq.append(("restore", None))
        self._seq = seq
        self._timer.start()

    def _step(self):
        if not self._seq:
            self._timer.stop()
            return
        kind, data = self._seq.pop(0)
        if kind == "text":
            self.phase.setText(data)
        elif kind == "send":
            for m in data:
                self._send(m)
        elif kind == "restore":
            self.engine.resend_all()

    def done(self, r):
        self._timer.stop()
        self._perf_timer.stop()
        self.engine.diagnostic = False
        self.engine.diag_input.disconnect(self._on_input)
        self.engine.diag_unknown.disconnect(self._on_unknown)
        self.engine.resend_all()
        super().done(r)
