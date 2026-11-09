"""Panneau de gauche : Sources · Scènes · Macros · Ducking."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFormLayout, QFrame, QHBoxLayout,
                               QInputDialog, QListWidget, QListWidgetItem, QMenu, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QStackedWidget, QStyle,
                               QStyledItemDelegate, QVBoxLayout, QWidget)

from ..automation import resolve
from . import theme as T
from .macro_dialog import MacroDialog
from .params import input_signature, label
from .sources import SourcesPanel
from .surface import MIME_REF
REF = Qt.UserRole


# --- listes de scènes / macros ------------------------------------------------------


class _RefList(QListWidget):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragOnly)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def mimeData(self, items):
        md = QMimeData()
        if items:
            md.setData(MIME_REF, f"{self.kind}:{items[0].data(REF)}".encode())
        return md

    def mimeTypes(self):
        return [MIME_REF]


class _RefDelegate(QStyledItemDelegate):
    """Ligne : pastille (active / en cours), nom, résumé."""

    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.page = page

    def sizeHint(self, option, index):
        return QSize(200, 46)

    def paint(self, p: QPainter, option, index):
        info = self.page.row_info(index.data(REF))
        if info is None:
            return
        name, sub, lit = info
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(option.rect).adjusted(2, 2, -2, -2)
        if option.state & (QStyle.State_MouseOver | QStyle.State_Selected):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.RAISED if option.state & QStyle.State_Selected else T.PANEL2))
            p.drawRoundedRect(r, 9, 9)
        color = QColor("#a3e635")
        dot = QRectF(r.x() + 10, r.center().y() - 5, 10, 10)
        p.setPen(QPen(color, 1.4))
        p.setBrush(color if lit else Qt.NoBrush)
        p.drawEllipse(dot)
        tx = dot.right() + 12
        w = r.right() - tx - 8
        f = QFont(option.font)
        f.setPointSizeF(9.5)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(T.TEXT))
        p.drawText(QRectF(tx, r.y() + 4, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(f).elidedText(name, Qt.ElideRight, w))
        f2 = QFont(option.font)
        f2.setPointSizeF(7.5)
        p.setFont(f2)
        p.setPen(QColor(T.FAINT))
        p.drawText(QRectF(tx, r.y() + 22, w, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   QFontMetricsF(f2).elidedText(sub, Qt.ElideRight, w))
        p.restore()


class ScenesPage(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Enregistre tous les volumes, mutes et envois de bus. "
                            "Glisse une scène sur un bouton pour la rappeler en direct.", "Hint", True))
        add = QPushButton("＋  Capturer l'état actuel")
        add.setObjectName("Primary")
        add.clicked.connect(self._capture)
        lay.addWidget(add)
        self.list = _RefList("scene")
        self.list.setItemDelegate(_RefDelegate(self, self.list))
        self.list.itemDoubleClicked.connect(lambda it: engine.scenes.recall(it.data(REF)))
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        lay.addWidget(label("Double-clic : rappeler  ·  clic droit : options  ·  "
                            "pastille pleine = la console est dans cette scène", "Hint", True))
        engine.automation_changed.connect(self.refresh)
        engine.vmix.state_changed.connect(lambda _: self.list.viewport().update())
        self.refresh()

    def row_info(self, sid):
        s = self.engine.scenes.get(sid)
        if not s:
            return None
        fade = int(s.get("fade_ms", 0))
        sub = f"{len(s['inputs'])} sources" + (f"  ·  fondu {fade / 1000:g} s" if fade else "")
        return s["name"], sub, self.engine.scenes.matches(sid)

    def refresh(self):
        self.list.clear()
        for s in self.engine.profile.scenes:
            it = QListWidgetItem()
            it.setData(REF, s["id"])
            self.list.addItem(it)

    def _capture(self):
        if self.engine.scenes.capture() is None:
            self.engine.log.emit("vMix n'est pas connecté : impossible de capturer une scène")

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        sid, e = it.data(REF), self.engine
        s = e.scenes.get(sid)
        m = QMenu(self)
        m.addAction("Rappeler", lambda: e.scenes.recall(sid))
        m.addAction("Mettre à jour avec l'état actuel", lambda: e.scenes.update(sid))
        m.addSeparator()
        m.addAction("Renommer…", lambda: self._rename(s))
        m.addAction("Fondu au rappel…", lambda: self._fade(s))
        m.addSeparator()
        m.addAction("Supprimer", lambda: e.scenes.delete(sid))
        m.exec(self.list.mapToGlobal(pos))

    def _rename(self, s):
        name, ok = QInputDialog.getText(self, "Renommer la scène", "Nom :", text=s["name"])
        if ok and name.strip():
            s["name"] = name.strip()
            self.engine.profile_changed()

    def _fade(self, s):
        v, ok = QInputDialog.getInt(self, "Fondu au rappel",
                                    "Durée du fondu des volumes (ms, 0 = immédiat) :",
                                    int(s.get("fade_ms", 0)), 0, 20000, 250)
        if ok:
            s["fade_ms"] = v
            self.engine.profile_changed()


class MacrosPage(QWidget):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Un bouton, plusieurs actions : couper un micro, changer de caméra, "
                            "attendre, lancer une scène… Glisse une macro sur un bouton.", "Hint", True))
        add = QPushButton("＋  Nouvelle macro")
        add.setObjectName("Primary")
        add.clicked.connect(self._new)
        lay.addWidget(add)
        self.list = _RefList("macro")
        self.list.setItemDelegate(_RefDelegate(self, self.list))
        self.list.itemDoubleClicked.connect(lambda it: self._edit(it.data(REF)))
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        lay.addWidget(label("Double-clic : modifier  ·  clic droit : lancer, dupliquer…  ·  "
                            "pastille pleine = en cours", "Hint", True))
        engine.automation_changed.connect(self.refresh)
        engine.changed.connect(lambda: self.list.viewport().update())
        self.refresh()

    def row_info(self, mid):
        mc = self.engine.macros.get(mid)
        if not mc:
            return None
        n = sum(1 for st in mc["steps"] if st.get("action") != "wait")
        total = sum(int(st.get("ms", 0)) for st in mc["steps"] if st.get("action") == "wait")
        sub = f"{n} action{'s' if n > 1 else ''}" + (f"  ·  {total / 1000:g} s" if total else "")
        return mc["name"], sub, mid in self.engine.macros.running

    def refresh(self):
        self.list.clear()
        for mc in self.engine.profile.macros:
            it = QListWidgetItem()
            it.setData(REF, mc["id"])
            self.list.addItem(it)

    def _new(self):
        mc = self.engine.macros.create()
        if not self._edit(mc["id"]) and not mc["steps"]:
            self.engine.macros.delete(mc["id"])

    def _edit(self, mid) -> bool:
        mc = self.engine.macros.get(mid)
        return bool(mc and MacroDialog(self.engine, mc, self.window()).exec())

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        mid, e = it.data(REF), self.engine
        m = QMenu(self)
        m.addAction("Lancer", lambda: e.macros.run(mid))
        m.addAction("Modifier…", lambda: self._edit(mid))
        m.addAction("Dupliquer", lambda: e.macros.duplicate(mid))
        m.addSeparator()
        m.addAction("Supprimer", lambda: e.macros.delete(mid))
        m.exec(self.list.mapToGlobal(pos))


# --- ducking ------------------------------------------------------------------------


class _LevelBar(QWidget):
    """Niveau du micro le plus fort, avec le seuil de déclenchement."""

    def __init__(self, ducker):
        super().__init__()
        self.ducker = ducker
        self.setFixedHeight(34)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = self.ducker
        r = QRectF(0, 8, self.width(), 10)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T.PANEL2))
        p.drawRoundedRect(r, 5, 5)

        def x_of(db):
            return r.x() + r.width() * min(1.0, max(0.0, (db + 60) / 60))
        thr = d.cfg["threshold_db"]
        lvl_x = x_of(d.level_db)
        p.setBrush(QColor("#a3e635") if d.level_db >= thr else QColor(T.FAINT))
        p.drawRoundedRect(QRectF(r.x(), r.y(), max(0.0, lvl_x - r.x()), r.height()), 5, 5)
        tx = x_of(thr)
        p.setPen(QPen(QColor(T.TEXT), 2))
        p.drawLine(int(tx), 4, int(tx), 22)
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        p.setFont(f)
        p.setPen(QColor(T.MUTED))
        p.drawText(QRectF(0, 22, self.width(), 12), Qt.AlignLeft, "-60 dB")
        p.drawText(QRectF(0, 22, self.width(), 12), Qt.AlignRight, "0 dB")
        p.drawText(QRectF(tx - 40, 22, 80, 12), Qt.AlignCenter, f"seuil {thr} dB")


class DuckingPage(QScrollArea):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.d = engine.ducker
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QScrollArea { background: transparent; } "
                           "QScrollArea > QWidget > QWidget { background: transparent; }")
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 6, 0)
        lay.setSpacing(10)
        self.setWidget(host)
        lay.addWidget(label("La musique baisse toute seule quand un micro parle, "
                            "puis remonte doucement après un court silence.", "Hint", True))
        top = QHBoxLayout()
        self.enable = QCheckBox("Activer")
        self.enable.toggled.connect(lambda on: self.d.set_enabled(on))
        top.addWidget(self.enable)
        top.addStretch(1)
        self.status = label("", "Chip")
        top.addWidget(self.status)
        lay.addLayout(top)
        self.bar = _LevelBar(self.d)
        lay.addWidget(self.bar)

        lay.addWidget(label("MICROS (DÉCLENCHEURS)", "H2"))
        self.triggers = self._checklist("triggers")
        lay.addWidget(self.triggers)
        lay.addWidget(label("SOURCES À BAISSER", "H2"))
        self.targets = self._checklist("targets")
        lay.addWidget(self.targets)

        lay.addWidget(label("RÉGLAGES", "H2"))
        form = QFormLayout()
        form.setSpacing(8)
        self.thr = QSlider(Qt.Horizontal)
        self.thr.setRange(-60, -5)
        self.thr.valueChanged.connect(lambda v: self._set("threshold_db", v))
        form.addRow(label("Seuil", "Muted"), self.thr)
        self.spins = {}
        for key, text, lo, hi, step, suffix in (
                ("depth_db", "Baisser de", -60, 0, 1, " dB"),
                ("attack_ms", "Baisse en", 0, 5000, 50, " ms"),
                ("hold_ms", "Maintien", 0, 10000, 100, " ms"),
                ("release_ms", "Remontée en", 0, 10000, 100, " ms")):
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setSuffix(suffix)
            sp.valueChanged.connect(lambda v, k=key: self._set(k, v))
            self.spins[key] = sp
            form.addRow(label(text, "Muted"), sp)
        lay.addLayout(form)
        lay.addWidget(label("La musique baisse de ce nombre de dB par rapport à son niveau actuel (-12 dB : bien audible derrière la voix). "
                            "Bouger son fader pendant une baisse te rend la main.", "Hint", True))
        lay.addStretch(1)

        self._sig = None
        self._load()
        self.d.changed.connect(self._live)
        engine.vmix.state_changed.connect(self._on_state)
        engine.automation_changed.connect(self._load)

    def _checklist(self, which: str) -> QListWidget:
        lw = QListWidget()
        lw.setFixedHeight(132)
        lw.setStyleSheet(f"QListWidget {{ background: {T.PANEL2}; border: 1px solid {T.BORDER};"
                         " border-radius: 8px; padding: 4px; }")
        lw.itemChanged.connect(lambda _: self._save_list(which, lw))
        return lw

    def _fill(self, lw: QListWidget, which: str):
        st = self.engine.state
        chosen = self.d.cfg[which]
        keys = set()
        for c in chosen:
            inp = resolve(st, c.get("input", ""), c.get("input_title", ""))
            if inp:
                keys.add(inp.key)
        lw.blockSignals(True)
        lw.clear()
        for inp in st.ordered_inputs():
            if not inp.has_audio:
                continue
            it = QListWidgetItem(f"{inp.number:>2}   {inp.title}")
            it.setData(REF, inp.key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if inp.key in keys else Qt.Unchecked)
            lw.addItem(it)
        if not lw.count():
            it = QListWidgetItem("vMix non connecté")
            it.setFlags(Qt.NoItemFlags)
            lw.addItem(it)
        lw.blockSignals(False)

    def _save_list(self, which: str, lw: QListWidget):
        st = self.engine.state
        out = []
        for i in range(lw.count()):
            it = lw.item(i)
            if it.checkState() == Qt.Checked and it.data(REF):
                inp = st.input(it.data(REF))
                out.append({"input": it.data(REF), "input_title": inp.title if inp else ""})
        self.d.cfg[which] = out
        self.engine.touch_profile()

    def _set(self, key: str, value):
        if self._loading:
            return
        self.d.cfg[key] = value
        self.engine.touch_profile()
        self.bar.update()

    def _load(self):
        self._loading = True
        cfg = self.d.cfg
        self.enable.setChecked(bool(cfg["enabled"]))
        self.thr.setValue(int(cfg["threshold_db"]))
        for k, sp in self.spins.items():
            sp.setValue(int(cfg[k]))
        self._fill(self.triggers, "triggers")
        self._fill(self.targets, "targets")
        self._sig = input_signature(self.engine.state)
        self._loading = False
        self._live()

    def _on_state(self, state):
        if input_signature(state) != self._sig:
            self._load()

    def _live(self):
        cfg = self.d.cfg
        if self.enable.isChecked() != bool(cfg["enabled"]):
            self.enable.blockSignals(True)
            self.enable.setChecked(bool(cfg["enabled"]))
            self.enable.blockSignals(False)
        if not cfg["enabled"]:
            self.status.setText("Désactivé")
        elif self.d.active:
            self.status.setText("● Musique baissée")
        else:
            self.status.setText("En écoute")
        self.status.setStyleSheet(f"color: {'#a3e635' if self.d.active else T.MUTED};")
        self.bar.update()


# --- conteneur à onglets --------------------------------------------------------------


class LibraryPanel(QFrame):
    activated = Signal(str)   # double-clic sur une source

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.setObjectName("Side")
        self.setFixedWidth(300)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)
        seg = QFrame()
        seg.setObjectName("SegBox")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        self.stack = QStackedWidget()
        self.sources = SourcesPanel(engine)
        self.sources.activated.connect(self.activated)
        pages = [("Sources", self.sources), ("Scènes", ScenesPage(engine)),
                 ("Macros", MacrosPage(engine)), ("Ducking", DuckingPage(engine))]
        group = QButtonGroup(self)
        for i, (name, page) in enumerate(pages):
            b = QPushButton(name)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setStyleSheet("padding: 6px 4px;")
            group.addButton(b, i)
            sl.addWidget(b, 1)
            self.stack.addWidget(page)
        group.idClicked.connect(self.stack.setCurrentIndex)
        lay.addWidget(seg)
        lay.addWidget(self.stack, 1)
