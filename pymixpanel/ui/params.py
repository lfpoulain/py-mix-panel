"""Éditeurs de paramètres d'action, partagés par l'inspecteur et l'éditeur de macros."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QStandardItem
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QSpinBox, QWidget

from .. import actions as A


def label(text: str, name: str, wrap=False) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def input_signature(state) -> tuple:
    return tuple((i.key, i.number, i.title, i.has_audio) for i in state.ordered_inputs())


def fill_inputs(combo: QComboBox, engine, current: str, current_title: str, audio_first: bool,
                placeholder="Choisir une source…"):
    combo.blockSignals(True)
    combo.clear()
    inputs = engine.state.ordered_inputs()
    if audio_first:
        inputs.sort(key=lambda i: (not i.has_audio, i.number))
    if not current:
        combo.addItem(placeholder, "")
    elif current not in engine.state.inputs:
        combo.addItem(f"⚠  {current_title or current} (introuvable)", current)
    for i in inputs:
        suffix = "" if i.has_audio or not audio_first else "   · sans audio"
        combo.addItem(f"{i.number:>2}   {i.title}{suffix}", i.key)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.blockSignals(False)


def fill_actions(combo: QComboBox, actions: list, current: str, none_label="—  Aucune action"):
    """Liste d'actions regroupées par catégorie (en-têtes colorés non sélectionnables)."""
    combo.blockSignals(True)
    combo.clear()
    if none_label:
        combo.addItem(none_label, "")
    model = combo.model()
    for cat, (cat_label, color) in A.CATEGORIES.items():
        acts = [a for a in actions if a.category == cat]
        if not acts:
            continue
        head = QStandardItem(cat_label.upper())
        head.setEnabled(False)
        head.setForeground(QColor(color))
        model.appendRow(head)
        for a in acts:
            combo.addItem(f"   {a.label}", a.key)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.blockSignals(False)


def carry_over(action_key: str, old: dict) -> dict:
    """Nouvelle association pour `action_key`, en gardant les paramètres compatibles."""
    a = A.ACTIONS[action_key]
    new = {"action": action_key, **A.defaults(a)}
    names = {p.name for p in a.params}
    for k in ("input", "input_title", "bus", "duration", "effect", "scene", "macro", "level"):
        if k in old and (k in names or (k == "input_title" and "input" in names)):
            new[k] = old[k]
    return new


class ParamForm(QWidget):
    """Formulaire des paramètres d'une action. Émet la nouvelle association à chaque modif."""
    changed = Signal(dict)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.mapping: dict = {}
        self.action: A.Action | None = None
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 4, 0, 0)
        self.form.setSpacing(8)
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    def set(self, action: A.Action | None, mapping: dict):
        self.action, self.mapping = action, dict(mapping)
        while self.form.rowCount():
            self.form.removeRow(0)
        if not action:
            return
        for prm in action.params:
            self.form.addRow(label(prm.label, "Muted"), self._editor(prm))

    def _emit(self, **kv):
        self.mapping.update(kv)
        self.changed.emit(dict(self.mapping))

    def _combo(self, items, current, name) -> QComboBox:
        w = QComboBox()
        w.setMaxVisibleItems(18)
        for text, data in items:
            w.addItem(text, data)
        w.setCurrentIndex(max(0, w.findData(current)))
        w.currentIndexChanged.connect(lambda _=0: w.currentData() not in (None, "")
                                      and self._emit(**{name: w.currentData()}))
        return w

    def _editor(self, prm: A.Param) -> QWidget:
        e, m, a = self.engine, self.mapping, self.action
        val = m.get(prm.name, prm.default)
        t = prm.type
        if t == "input":
            w = QComboBox()
            w.setMaxVisibleItems(18)
            fill_inputs(w, e, m.get("input", ""), m.get("input_title", ""),
                        a.category in (A.AUDIO, A.ROUTING))

            def on_input(_=0, w=w):
                key = w.currentData()
                if key:
                    inp = e.state.input(key)
                    self._emit(input=key, input_title=inp.title if inp else m.get("input_title", ""))
            w.currentIndexChanged.connect(on_input)
            return w
        if t == "scene":
            items = [(s["name"], s["id"]) for s in e.profile.scenes]
            return self._combo(items or [("Aucune scène : crée-en une à gauche", "")], val, prm.name)
        if t == "macro":
            items = [(mc["name"], mc["id"]) for mc in e.profile.macros]
            return self._combo(items or [("Aucune macro : crée-en une à gauche", "")], val, prm.name)
        if t == "meter_source":
            items = [("Master", "master")] + [(f"Bus {b}", f"bus:{b}") for b in sorted(e.state.buses)]
            items += [(f"{i.number:>2}   {i.title}", i.key) for i in e.state.ordered_inputs() if i.has_audio]
            if val and val not in [d for _, d in items]:
                items.insert(0, (f"⚠  {val} (introuvable)", val))
            return self._combo(items, val, prm.name)
        if t in ("bus", "route", "transition", "overlay", "slot"):
            if t == "slot":
                items = [(f"Transition {n}", n) for n in range(1, 5)]
            elif t == "transition":
                items = [(x, x) for x in A.TRANSITIONS]
            elif t == "overlay":
                items = [(f"Overlay {n}", n) for n in range(1, 5)]
            else:
                keys = (["M"] if t == "route" else []) + A.BUSES
                items = [(A.BUS_NAMES[k], k) for k in keys]
            return self._combo(items, val, prm.name)
        if t in ("duration", "level"):
            w = QSpinBox()
            if t == "duration":
                w.setRange(0, 20000)
                w.setSingleStep(100)
                w.setSuffix(" ms")
            else:
                w.setRange(0, 100)
                w.setSingleStep(5)
                w.setSuffix(" %")
            w.setValue(int(val or 0))
            w.valueChanged.connect(lambda v, n=prm.name: self._emit(**{n: v}))
            return w
        w = QLineEdit(str(val or ""))
        w.setPlaceholderText("ex. Input=3&Value={value}" if prm.name == "query" else "ex. Fade")
        w.editingFinished.connect(lambda w=w, n=prm.name: self._emit(**{n: w.text().strip()}))
        return w
