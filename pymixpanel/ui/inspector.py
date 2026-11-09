"""Panneau de droite : édition de l'association du contrôle sélectionné."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .. import actions as A
from ..xtouch import ENCODER, FADER
from .params import ParamForm, carry_over, fill_actions, fill_inputs, input_signature
from .params import label as _label

KIND_LABEL = {"fader": "Fader motorisé", "encoder": "Encodeur", "button": "Bouton", "pedal": "Pédale"}


class Inspector(QFrame):
    request_select = Signal(str)   # bascule rotation <-> appui d'un encodeur

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.setObjectName("Side")
        self.setFixedWidth(340)
        self.engine = engine
        self.cid = ""
        self._sig = ()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(10)

        self.empty = _label("Clique un contrôle sur la surface,\nou touche-le directement sur la X-Touch.",
                            "Hint", True)
        self.empty.setAlignment(Qt.AlignCenter)

        self.body = QWidget()
        b = QVBoxLayout(self.body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(10)
        b.addWidget(_label("CONTRÔLE", "H2"))
        self.title = _label("", "H1")
        b.addWidget(self.title)
        chips = QHBoxLayout()
        self.kind_chip = _label("", "Chip")
        self.midi_chip = _label("", "Chip")
        chips.addWidget(self.kind_chip)
        chips.addWidget(self.midi_chip)
        chips.addStretch()
        b.addLayout(chips)
        lr = QHBoxLayout()
        self.learn_btn = QPushButton("Apprendre le MIDI")
        self.learn_btn.setObjectName("Learn")
        self.learn_btn.setCheckable(True)
        self.learn_btn.setToolTip("Clique puis bouge le contrôle sur la X-Touch pour l'identifier")
        self.learn_btn.toggled.connect(self._toggle_learn)
        self.reset_btn = QPushButton("Par défaut")
        self.reset_btn.setObjectName("Ghost")
        self.reset_btn.clicked.connect(lambda: self.engine.reset_midi(self.cid))
        lr.addWidget(self.learn_btn, 1)
        lr.addWidget(self.reset_btn)
        b.addLayout(lr)
        self.learn_hint = _label("", "Hint", True)
        self.learn_hint.hide()
        b.addWidget(self.learn_hint)

        # encodeur : deux fonctions, rotation et appui
        self.enc_box = QFrame()
        self.enc_box.setObjectName("SegBox")
        eb = QHBoxLayout(self.enc_box)
        eb.setContentsMargins(3, 3, 3, 3)
        eb.setSpacing(2)
        self.rot_btn = QPushButton()
        self.push_btn = QPushButton()
        for btn in (self.rot_btn, self.push_btn):
            btn.setObjectName("Seg")
            btn.setCheckable(True)
            eb.addWidget(btn, 1)
        self.rot_btn.clicked.connect(lambda: self._switch_enc("enc"))
        self.push_btn.clicked.connect(lambda: self._switch_enc("push"))
        b.addWidget(self.enc_box)
        # anneau LED : curseur de la rotation ou témoin de l'appui (les appuis n'ont pas de LED)
        self.ring_row = QWidget()
        rr = QHBoxLayout(self.ring_row)
        rr.setContentsMargins(0, 0, 0, 0)
        rr.addWidget(_label("Anneau LED", "Muted"))
        self.ring_combo = QComboBox()
        for text, data in (("Auto", "auto"), ("Curseur : niveau de la rotation", "cursor"),
                           ("État de l'appui : plein", "state"),
                           ("État de l'appui : clignotant", "state_blink"), ("Éteint", "off")):
            self.ring_combo.addItem(text, data)
        self.ring_combo.setToolTip("Auto : curseur si une action de rotation est associée, sinon l'anneau "
                                   "s'allume en entier quand l'action de l'appui est active.")
        self.ring_combo.currentIndexChanged.connect(self._ring_changed)
        rr.addWidget(self.ring_combo, 1)
        b.addWidget(self.ring_row)

        b.addSpacing(8)
        b.addWidget(_label("ACTION VMIX", "H2"))
        self.action_combo = QComboBox()
        self.action_combo.setMaxVisibleItems(20)
        self.action_combo.currentIndexChanged.connect(self._action_changed)
        b.addWidget(self.action_combo)
        self.help = _label("", "Hint", True)
        b.addWidget(self.help)
        self.params = ParamForm(engine)
        self.params.changed.connect(self._commit)
        b.addWidget(self.params)

        self.strip_box = QWidget()
        s = QVBoxLayout(self.strip_box)
        s.setContentsMargins(0, 12, 0, 0)
        s.setSpacing(8)
        self.strip_title = _label("", "H2")
        s.addWidget(self.strip_title)
        self.strip_combo = QComboBox()
        self.strip_combo.activated.connect(self._strip_assign)
        s.addWidget(self.strip_combo)
        self.strip_hint = _label("", "Hint", True)
        s.addWidget(self.strip_hint)
        clear_strip = QPushButton("Vider la tranche")
        clear_strip.setObjectName("Ghost")
        clear_strip.clicked.connect(self._strip_clear)
        s.addWidget(clear_strip, 0, Qt.AlignLeft)
        b.addWidget(self.strip_box)

        b.addStretch(1)
        actions = QHBoxLayout()
        self.test_btn = QPushButton("▶  Tester")
        self.test_btn.setObjectName("Primary")
        self.test_btn.clicked.connect(lambda: self.engine.test(self.cid))
        self.clear_btn = QPushButton("Effacer")
        self.clear_btn.setObjectName("Danger")
        self.clear_btn.clicked.connect(lambda: self.engine.set_mapping(self.cid, None))
        actions.addWidget(self.test_btn, 1)
        actions.addWidget(self.clear_btn)
        b.addLayout(actions)

        lay.addWidget(self.empty, 1)
        lay.addWidget(self.body, 1)
        self.body.hide()

        engine.mapping_changed.connect(self._on_mapping_changed)
        engine.learning.connect(self._on_learning)
        engine.vmix.state_changed.connect(self._on_state)

    # -- sélection ----------------------------------------------------------------
    def set_control(self, cid: str):
        if cid == self.cid:
            return
        self.engine.cancel_learn()
        self.cid = cid
        self.empty.setVisible(not cid)
        self.body.setVisible(bool(cid))
        if cid:
            self._populate()

    def _populate(self):
        e, cid = self.engine, self.cid
        c = e.controls[cid]
        self.title.setText(c.name)
        self.kind_chip.setText(f"{KIND_LABEL.get(c.kind, c.kind)} · page {e.page}")
        midi = c.midi.label()
        if c.touch:
            midi += f"  ·  tactile {c.touch.label().split(' ·')[0]}"
        self.midi_chip.setText(midi)
        self.reset_btn.setVisible(cid in e.profile.midi_overrides)

        m = e.mapping(cid) or {}
        fill_actions(self.action_combo, A.actions_for(c.continuous, c.kind == ENCODER),
                     m.get("action", ""))
        self._build_form()

        is_enc = cid.startswith(("enc", "push"))
        self.enc_box.setVisible(is_enc)
        self.ring_row.setVisible(is_enc)
        if is_enc:
            n = cid.lstrip("encpush")
            mode = e.profile.ring_modes.get(f"{e.page}:enc{n}", "auto")
            self.ring_combo.blockSignals(True)
            self.ring_combo.setCurrentIndex(max(0, self.ring_combo.findData(mode)))
            self.ring_combo.blockSignals(False)
            for btn, other, icon, name in ((self.rot_btn, f"enc{n}", "↻", "Rotation"),
                                           (self.push_btn, f"push{n}", "⏎", "Appui")):
                a = e.action_of(other)
                btn.setChecked(other == cid)
                btn.setText(f"{icon}  {name}" + ("  ●" if a else ""))
                btn.setToolTip(a.label if a else "Non associé")

        if c.strip is not None and c.strip < 8:
            self.strip_box.show()
            self.strip_title.setText(f"TRANCHE {c.strip + 1}")
            tpl = " / ".join(A.BUS_NAMES.get(b, "—") for b in e.profile.strip_template)
            self.strip_hint.setText(f"Fader = volume, bouton = on/off, lignes 1-3 = {tpl}.\n"
                                    "Ou glisse une source sur la tranche.")
            self._fill_strip_combo()
        else:
            self.strip_box.hide()

    def _fill_strip_combo(self):
        cur = (self.engine.mapping(f"fader{self.engine.controls[self.cid].strip + 1}") or {})
        fill_inputs(self.strip_combo, self.engine, "", "", True, "Assigner toute la tranche à…")
        if cur.get("input"):
            self.strip_combo.setItemText(0, f"Tranche actuelle : {self.engine.input_title(cur)}")

    def _build_form(self):
        e, cid = self.engine, self.cid
        a = e.action_of(cid)
        self.help.setText(a.help if a else "")
        self.help.setVisible(bool(a and a.help))
        self.test_btn.setEnabled(a is not None)
        self.clear_btn.setEnabled(a is not None)
        self.params.set(a, e.mapping(cid) or {})
        self._sig = input_signature(e.state)

    # -- modifications ------------------------------------------------------------
    def _commit(self, mapping: dict | None):
        self._applying = True
        try:
            self.engine.set_mapping(self.cid, mapping)
        finally:
            self._applying = False

    def _action_changed(self):
        key = self.action_combo.currentData()
        if not key:
            self._commit(None)
        else:
            new = carry_over(key, self.engine.mapping(self.cid) or {})
            if A.needs_input(A.ACTIONS[key]) and "input" not in new:
                guess = self._guess_input()
                if guess:
                    new["input"], new["input_title"] = guess
            self._commit(new)
        self._build_form()

    def _guess_input(self):
        """Propose la source déjà utilisée par la tranche du contrôle."""
        c = self.engine.controls[self.cid]
        if c.strip is None or c.strip >= 8:
            return None
        for other in self.engine.controls.values():
            m = self.engine.mapping(other.id)
            if other.strip == c.strip and m and m.get("input"):
                return m["input"], m.get("input_title", "")
        return None

    def _strip_assign(self, idx: int):
        key = self.strip_combo.itemData(idx)
        if key:
            self.engine.assign_strip(self.engine.controls[self.cid].strip, key)

    def _strip_clear(self):
        self.engine.clear_strip(self.engine.controls[self.cid].strip)

    def _ring_changed(self):
        if self.cid.startswith(("enc", "push")):
            self._applying = True
            try:
                self.engine.set_ring_mode(f"enc{self.cid.lstrip('encpush')}", self.ring_combo.currentData())
            finally:
                self._applying = False

    def _switch_enc(self, kind: str):
        n = self.cid.lstrip("encpush")
        target = f"{kind}{n}"
        if target != self.cid:
            self.request_select.emit(target)
        else:
            self._populate()

    # -- apprentissage ------------------------------------------------------------
    def _toggle_learn(self, on: bool):
        if on and self.cid:
            self.engine.start_learn(self.cid)
        elif not on:
            self.engine.cancel_learn()

    def _on_learning(self, cid: str):
        active = bool(cid) and cid == self.cid
        self.learn_btn.blockSignals(True)
        self.learn_btn.setChecked(active)
        self.learn_btn.blockSignals(False)
        self.learn_btn.setText("En écoute…  (Échap pour annuler)" if active else "Apprendre le MIDI")
        if active:
            c = self.engine.controls[cid]
            txt = ("Pose le doigt sur le fader et bouge-le : sa position et son capteur tactile "
                   "sont appris ensemble."
                   if c.kind == FADER else "Bouge ou appuie sur le contrôle de la X-Touch.")
            self.learn_hint.setText(txt)
        self.learn_hint.setVisible(active)

    # -- signaux moteur -------------------------------------------------------------
    def _on_mapping_changed(self, cid: str):
        if not self.cid or getattr(self, "_applying", False):
            return
        if cid in ("", self.cid):
            self._populate()

    def _on_state(self, state):
        if not self.cid:
            return
        sig = input_signature(state)
        if sig != self._sig:
            self._sig = sig
            self._populate()
