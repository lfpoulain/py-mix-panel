"""Éditeur de macro : une liste d'étapes (actions et pauses) réordonnables."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import actions as A
from .params import ParamForm, carry_over, fill_actions, label

STEP = Qt.UserRole


class MacroDialog(QDialog):
    def __init__(self, engine, macro: dict, parent=None):
        super().__init__(parent)
        self.engine, self.macro = engine, macro
        self.steps = copy.deepcopy(macro["steps"])
        self.setWindowTitle("Macro")
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(label("NOM DE LA MACRO", "H2"))
        self.name = QLineEdit(macro["name"])
        lay.addWidget(self.name)

        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        left.addWidget(label("ÉTAPES  (glisse pour réordonner)", "H2"))
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.currentRowChanged.connect(self._select)
        self.list.model().rowsMoved.connect(self._reordered)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, fn in (("+ Action", self._add_action), ("+ Pause", self._add_wait),
                         ("Dupliquer", self._duplicate), ("Supprimer", self._remove)):
            b = QPushButton(text)
            if text == "Supprimer":
                b.setObjectName("Danger")
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        body.addLayout(left, 3)

        right = QVBoxLayout()
        right.addWidget(label("ÉTAPE SÉLECTIONNÉE", "H2"))
        self.stack = QStackedWidget()
        empty = label("Ajoute une action ou une pause.\nLes étapes s'enchaînent de haut en bas ; "
                      "une pause retarde tout ce qui suit.", "Hint", True)
        empty.setAlignment(Qt.AlignTop)
        self.stack.addWidget(empty)
        act_page = QWidget()
        ap = QVBoxLayout(act_page)
        ap.setContentsMargins(0, 0, 0, 0)
        self.action_combo = QComboBox()
        self.action_combo.setMaxVisibleItems(22)
        self.action_combo.currentIndexChanged.connect(self._action_changed)
        ap.addWidget(self.action_combo)
        self.params = ParamForm(engine)
        self.params.changed.connect(self._params_changed)
        ap.addWidget(self.params)
        ap.addStretch(1)
        self.stack.addWidget(act_page)
        wait_page = QWidget()
        wp = QVBoxLayout(wait_page)
        wp.setContentsMargins(0, 0, 0, 0)
        self.wait_spin = QSpinBox()
        self.wait_spin.setRange(0, 60000)
        self.wait_spin.setSingleStep(100)
        self.wait_spin.setSuffix(" ms")
        self.wait_spin.valueChanged.connect(self._wait_changed)
        wp.addWidget(label("Durée de la pause", "Muted"))
        wp.addWidget(self.wait_spin)
        wp.addStretch(1)
        self.stack.addWidget(wait_page)
        right.addWidget(self.stack, 1)
        body.addLayout(right, 2)
        lay.addLayout(body, 1)

        bottom = QHBoxLayout()
        test = QPushButton("▶  Tester la macro")
        test.clicked.connect(lambda: engine.macros.run_steps("__test__", self.name.text(), self.steps))
        bottom.addWidget(test)
        bottom.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("Enregistrer")
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.button(QDialogButtonBox.Cancel).setText("Annuler")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        bottom.addWidget(bb)
        lay.addLayout(bottom)

        self._refresh_list()
        if self.steps:
            self.list.setCurrentRow(0)

    # -- liste ------------------------------------------------------------------
    def _refresh_list(self, keep: int | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for i, st in enumerate(self.steps):
            it = QListWidgetItem(f"{i + 1}.  {self.engine.describe(st)}")
            it.setData(STEP, id(st))
            self.list.addItem(it)
        self.list.blockSignals(False)
        if keep is not None and self.steps:
            self.list.setCurrentRow(min(keep, len(self.steps) - 1))
        elif not self.steps:
            self.stack.setCurrentIndex(0)

    def _current(self) -> dict | None:
        r = self.list.currentRow()
        return self.steps[r] if 0 <= r < len(self.steps) else None

    def _reordered(self, *_):
        by_id = {id(st): st for st in self.steps}
        self.steps = [by_id[self.list.item(i).data(STEP)] for i in range(self.list.count())]
        self._refresh_list(self.list.currentRow())

    def _select(self, row: int):
        st = self._current()
        if st is None:
            self.stack.setCurrentIndex(0)
            return
        if st.get("action") == "wait":
            self.stack.setCurrentIndex(2)
            self.wait_spin.blockSignals(True)
            self.wait_spin.setValue(int(st.get("ms", 0)))
            self.wait_spin.blockSignals(False)
            return
        self.stack.setCurrentIndex(1)
        fill_actions(self.action_combo, A.macro_actions(), st.get("action", ""), none_label=None)
        self.params.set(A.ACTIONS.get(st.get("action", "")), st)

    def _add_action(self):
        self.steps.insert(self.list.currentRow() + 1, {"action": "audio_toggle"})
        self._refresh_list(self.list.currentRow() + 1)

    def _add_wait(self):
        self.steps.insert(self.list.currentRow() + 1, {"action": "wait", "ms": 500})
        self._refresh_list(self.list.currentRow() + 1)

    def _duplicate(self):
        st = self._current()
        if st:
            self.steps.insert(self.list.currentRow() + 1, dict(st))
            self._refresh_list(self.list.currentRow() + 1)

    def _remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self.steps):
            del self.steps[r]
            self._refresh_list(r)

    # -- édition d'une étape ----------------------------------------------------------
    def _replace_current(self, new: dict):
        r = self.list.currentRow()
        if 0 <= r < len(self.steps):
            self.steps[r].clear()
            self.steps[r].update(new)
            self.list.item(r).setText(f"{r + 1}.  {self.engine.describe(self.steps[r])}")

    def _action_changed(self):
        st = self._current()
        key = self.action_combo.currentData()
        if st is None or not key or key == st.get("action"):
            return
        self._replace_current(carry_over(key, st))
        self.params.set(A.ACTIONS[key], self._current())

    def _params_changed(self, mapping: dict):
        self._replace_current(mapping)

    def _wait_changed(self, v: int):
        self._replace_current({"action": "wait", "ms": v})

    def _save(self):
        self.macro["name"] = self.name.text().strip() or self.macro["name"]
        self.macro["steps"] = self.steps
        self.engine.profile_changed()
        self.accept()
