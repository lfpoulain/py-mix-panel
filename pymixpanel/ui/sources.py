"""Panneau de gauche : sources vMix en direct, glissables sur la surface."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (QWidget, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QStyle, QStyledItemDelegate, QVBoxLayout)

from . import theme as T
from .params import input_signature
from .surface import MIME

KEY = Qt.UserRole


class _Delegate(QStyledItemDelegate):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine

    def sizeHint(self, option, index):
        return QSize(200, 46)

    def paint(self, p: QPainter, option, index):
        inp = self.engine.state.input(index.data(KEY))
        if inp is None:
            return
        st = self.engine.state
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        if option.state & QStyle.State_MouseOver:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.PANEL2))
            p.drawRoundedRect(r, 9, 9)
        # numéro + tally
        tally = T.PGM if inp.number == st.active else T.PVW if inp.number == st.preview else None
        chip = QRectF(r.x() + 6, r.center().y() - 11, 28, 22)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tally) if tally else QColor(T.RAISED))
        p.drawRoundedRect(chip, 6, 6)
        f = QFont(option.font)
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#0b0d10") if tally else QColor(T.MUTED))
        p.drawText(chip, Qt.AlignCenter, str(inp.number))
        # titre / type
        right = 52 if inp.has_audio else 8
        tx = chip.right() + 10
        w = r.right() - tx - right
        f.setPointSizeF(9.5)
        f.setBold(False)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        fm = QFontMetricsF(f)
        p.drawText(QRectF(tx, r.y() + 4, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(inp.title, Qt.ElideRight, w))
        f2 = QFont(option.font)
        f2.setPointSizeF(7.5)
        p.setFont(f2)
        p.setPen(QColor(T.FAINT))
        sub = inp.type + ("  ·  muet" if inp.has_audio and inp.muted else "")
        p.drawText(QRectF(tx, r.y() + 22, w, 16), Qt.AlignLeft | Qt.AlignVCenter, sub)
        # niveau du fader vMix
        if inp.has_audio:
            bar = QRectF(r.right() - 46, r.center().y() - 2.5, 38, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.RAISED))
            p.drawRoundedRect(bar, 2.5, 2.5)
            fill = QRectF(bar.x(), bar.y(), bar.width() * min(1.0, inp.volume / 100), bar.height())
            p.setBrush(QColor(T.FAINT if inp.muted else T.ACCENT))
            p.drawRoundedRect(fill, 2.5, 2.5)
        p.restore()


class _List(QListWidget):
    def mimeData(self, items):
        md = QMimeData()
        if items:
            md.setData(MIME, items[0].data(KEY).encode())
        return md

    def mimeTypes(self):
        return [MIME]


class SourcesPanel(QWidget):
    activated = Signal(str)  # double-clic -> associer au contrôle sélectionné

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._sig = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = _List()
        self.list.setItemDelegate(_Delegate(engine, self.list))
        self.list.setDragEnabled(True)
        self.list.setDragDropMode(QListWidget.DragOnly)
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.itemDoubleClicked.connect(lambda it: self.activated.emit(it.data(KEY)))
        lay.addWidget(self.list, 1)
        self.placeholder = QLabel("vMix n'est pas connecté.\nVérifie l'adresse dans les réglages\n"
                                  "(API TCP, port 8099).")
        self.placeholder.setObjectName("Hint")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        lay.addWidget(self.placeholder, 1)
        tip = QLabel("Glisse une source sur une tranche pour l'assigner en un geste. "
                     "Double-clic : l'associer au contrôle sélectionné.")
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        engine.vmix.state_changed.connect(self._on_state)
        self._on_state(engine.state)

    def _on_state(self, state):
        online = state.online
        self.list.setVisible(online)
        self.placeholder.setVisible(not online)
        sig = input_signature(state)
        if sig != self._sig:
            self._sig = sig
            self.list.clear()
            for inp in state.ordered_inputs():
                it = QListWidgetItem()
                it.setData(KEY, inp.key)
                it.setData(Qt.DisplayRole, "")
                it.setToolTip(f"{inp.number} · {inp.title}\n{inp.type}")
                it.setData(Qt.UserRole + 1, inp.title.lower())
                self.list.addItem(it)
            self._filter(self.search.text())
        else:
            self.list.viewport().update()

    def _filter(self, text: str):
        t = text.strip().lower()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.data(Qt.UserRole + 1))
