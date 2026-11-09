"""Fenêtre principale."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..config import Profile, list_profiles, load_settings, profile_path, save_settings
from ..importer import import_ctrl
from . import theme as T
from .diagnostic import DiagnosticDialog
from .dialogs import SettingsDialog
from .inspector import Inspector
from .library import LibraryPanel
from .surface import SurfaceWidget
from .updates import UpdateController


def icon_pixmap(size: int = 64) -> QPixmap:
    """Icône de l'app (aussi utilisée pour générer l'icône de l'exe)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#14171d"))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    for i, h in enumerate((0.55, 0.85, 0.4)):
        x = 15 + i * 15
        p.setBrush(QColor("#2a303b"))
        p.drawRoundedRect(QRectF(x, 12, 6, 40), 3, 3)
        p.setBrush(QColor(T.ACCENT))
        y = 12 + 40 * (1 - h)
        p.drawRoundedRect(QRectF(x - 4, y - 4, 14, 8), 3, 3)
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for s in (16, 32, 64, 256):
        icon.addPixmap(icon_pixmap(s))
    return icon


class StatusPill(QLabel):
    def __init__(self, name: str):
        super().__init__()
        self.setObjectName("Pill")
        self.name = name
        self.set(False, "")

    def set(self, ok: bool, detail: str):
        dot = T.OK if ok else T.DANGER
        txt = detail if ok else "déconnecté"
        self.setText(f"<span style='color:{dot}'>●</span>&nbsp; <b>{self.name}</b>"
                     f"&nbsp;<span style='color:{T.MUTED}'>{txt}</span>")


class MainWindow(QMainWindow):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setWindowIcon(app_icon())
        self.resize(1560, 900)
        self.setMinimumSize(1200, 640)

        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._header())

        body = QHBoxLayout()
        body.setContentsMargins(14, 14, 14, 14)
        body.setSpacing(14)
        self.sources = LibraryPanel(engine)
        self.surface = SurfaceWidget(engine)
        self.inspector = Inspector(engine)
        center = QVBoxLayout()
        center.setSpacing(10)
        center.addWidget(self.surface, 1)
        hint = QLabel("Clic : sélectionner  ·  Glisser une source sur une tranche : tout assigner  ·  "
                      "Clic droit : options  ·  Touche un contrôle de la X-Touch pour le retrouver ici")
        hint.setObjectName("Hint")
        hint.setAlignment(Qt.AlignCenter)
        center.addWidget(hint)
        self.monitor = QPlainTextEdit()
        self.monitor.setReadOnly(True)
        self.monitor.setMaximumBlockCount(400)
        self.monitor.setFixedHeight(150)
        self.monitor.hide()
        self.mon_out = QCheckBox("Afficher aussi les messages envoyés à la X-Touch (LED, moteurs, anneaux)")
        self.mon_out.toggled.connect(lambda on: setattr(self.engine, "log_midi_out", on))
        self.mon_out.hide()
        center.addWidget(self.mon_out)
        center.addWidget(self.monitor)
        body.addWidget(self.sources)
        body.addLayout(center, 1)
        body.addWidget(self.inspector)
        v.addLayout(body, 1)

        self.surface.selected.connect(self.select)
        self.surface.dropped.connect(self._dropped)
        self.surface.dropped_ref.connect(self._dropped_ref)
        self.surface.context_requested.connect(self._context_menu)
        self.sources.activated.connect(self._source_activated)
        self.inspector.request_select.connect(self.select)
        engine.activity.connect(self._on_activity)
        engine.log.connect(self._log)
        engine.vmix.log.connect(self._log)
        engine.midi.status_changed.connect(lambda ok, n: self._midi_status())
        engine.console_mode_changed.connect(lambda _: self._midi_status())
        engine.vmix.status_changed.connect(self._vmix_status)
        engine.locked_changed.connect(self._apply_lock)

        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._escape)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.lock_btn.click)
        QShortcut(QKeySequence(Qt.Key_Delete), self,
                  activated=lambda: self.surface.sel and engine.set_mapping(self.surface.sel, None))
        self._refresh_profiles()
        self._update_title()
        self._vmix_version = ""
        self._lat_timer = QTimer(self, interval=1000)
        self._lat_timer.timeout.connect(self._show_latency)
        self._lat_timer.start()
        self.updates = UpdateController(self, self.update_btn)
        self.updates.check_later()
        if load_settings().get("locked"):   # le verrou survit à un redémarrage en plein direct
            engine.set_locked(True)

    # -- en-tête ------------------------------------------------------------------
    def _header(self) -> QFrame:
        h = QFrame()
        h.setObjectName("Header")
        h.setFixedHeight(62)
        lay = QHBoxLayout(h)
        lay.setContentsMargins(20, 0, 16, 0)
        lay.setSpacing(10)
        b1, b2 = QLabel("Mix"), QLabel("Panel")
        b1.setObjectName("Brand")
        b2.setObjectName("BrandAccent")
        brand = QHBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(b1)
        brand.addWidget(b2)
        lay.addLayout(brand)
        lay.addSpacing(14)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.activated.connect(self._profile_selected)
        lay.addWidget(self.profile_combo)
        lay.addStretch(1)

        self.midi_pill = StatusPill("X-Touch")
        self.vmix_pill = StatusPill("vMix")
        lay.addWidget(self.midi_pill, 0, Qt.AlignVCenter)
        lay.addWidget(self.vmix_pill, 0, Qt.AlignVCenter)
        lay.addSpacing(10)

        seg = QFrame()
        seg.setObjectName("SegBox")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(2)
        group = QButtonGroup(self)
        for i, (label, tip) in enumerate((
                ("Édition", "Clic sur un bouton = le sélectionner pour l'éditer"),
                ("Live", "Clic sur un bouton = l'actionner, comme sur la X-Touch"))):
            b = QPushButton(label)
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setToolTip(tip)
            group.addButton(b, i)
            sl.addWidget(b)
        group.idToggled.connect(lambda i, on: on and self._set_live(i == 1))
        self.seg = seg
        lay.addWidget(seg, 0, Qt.AlignVCenter)

        self.follow = QCheckBox("Suivre la X-Touch")
        self.follow.setChecked(True)
        self.follow.setToolTip("Toucher un contrôle physique le sélectionne dans l'éditeur")
        lay.addWidget(self.follow)
        lay.addSpacing(6)
        self.update_btn = QPushButton()
        self.update_btn.setObjectName("Primary")
        lay.addWidget(self.update_btn)
        self.lock_btn = QPushButton("Verrouiller")
        self.lock_btn.setCheckable(True)
        self.lock_btn.setToolTip("Direct : bloque l'édition et tous les clics à l'écran. "
                                 "La X-Touch continue de fonctionner. (Ctrl+L)")
        self.lock_btn.clicked.connect(self._lock_clicked)
        lay.addWidget(self.lock_btn)
        imp = QPushButton("Importer")
        imp.setToolTip("Importer un show Central Control (.ctrl) ou un profil MixPanel (.json)")
        imp.clicked.connect(self.import_file)
        lay.addWidget(imp)
        self.import_btn = imp
        self.mon_btn = QPushButton("Moniteur")
        self.mon_btn.setObjectName("Ghost")
        self.mon_btn.setCheckable(True)
        self.mon_btn.toggled.connect(self._toggle_monitor)
        lay.addWidget(self.mon_btn)
        diag = QPushButton("Diagnostic")
        diag.setObjectName("Ghost")
        diag.setToolTip("Vérifier la X-Touch : LED, anneaux, moteurs et chaque contrôle")
        diag.clicked.connect(lambda: DiagnosticDialog(self.engine, self).exec())
        lay.addWidget(diag)
        self.diag_btn = diag
        gear = QPushButton("Réglages")
        gear.setObjectName("Ghost")
        gear.clicked.connect(self.open_settings)
        lay.addWidget(gear)
        self.gear_btn = gear
        return h

    # -- sélection / interactions -----------------------------------------------------
    def select(self, cid: str):
        self.surface.select(cid)
        self.inspector.set_control(cid)

    def _on_activity(self, cid: str):
        if self.follow.isChecked() and not self.surface.live and cid != self.surface.sel \
                and not self.engine.learn_target and not self.engine.locked:
            self.select(cid)

    def _set_live(self, live: bool):
        self.surface.live = live

    def _lock_clicked(self, on: bool):
        if not on:
            ask = QMessageBox.question(
                self, "Déverrouiller", "Déverrouiller l'édition et les clics à l'écran ?\n"
                "Assure-toi que le direct ne risque rien.")
            if ask != QMessageBox.Yes:
                self.lock_btn.setChecked(True)
                return
        self.engine.set_locked(on)
        s = load_settings()
        s["locked"] = on
        save_settings(s)

    def _apply_lock(self, on: bool):
        self.lock_btn.setChecked(on)
        self.lock_btn.setText("Verrouillé" if on else "Verrouiller")
        self.lock_btn.setStyleSheet(f"background:{T.DANGER}; color:white;" if on else "")
        for w in (self.sources, self.inspector, self.seg, self.profile_combo, self.import_btn,
                  self.diag_btn, self.gear_btn, self.update_btn):
            w.setEnabled(not on)
        self.surface.setAcceptDrops(not on)
        self.surface.hover = ""
        self.surface.setCursor(Qt.ArrowCursor)
        self.surface.update()

    def _escape(self):
        if self.engine.learn_target:
            self.engine.cancel_learn()
        else:
            self.select("")

    def _dropped_ref(self, cid: str, kind: str, ref: str):
        self.engine.assign_ref(cid, kind, ref)
        self.select(cid)

    def _dropped(self, cid: str, key: str):
        self.engine.assign_input(cid, key)
        self.select(cid)

    def _source_activated(self, key: str):
        if self.surface.sel:
            self.engine.assign_input(self.surface.sel, key)

    def _context_menu(self, cid: str, pos):
        e = self.engine
        c = e.controls[cid]
        menu = QMenu(self)
        menu.addAction("Apprendre le MIDI", lambda: e.start_learn(cid))
        if e.mapping(cid):
            menu.addAction("Tester l'action", lambda: e.test(cid))
            menu.addAction("Effacer l'association", lambda: e.set_mapping(cid, None))
        if c.strip is not None and c.strip < 8:
            menu.addSeparator()
            sub = menu.addMenu(f"Assigner la tranche {c.strip + 1} à")
            for inp in e.state.ordered_inputs():
                sub.addAction(f"{inp.number}  {inp.title}",
                              lambda k=inp.key, s=c.strip: e.assign_strip(s, k))
            sub.setEnabled(bool(e.state.inputs))
            menu.addAction(f"Vider la tranche {c.strip + 1}", lambda: e.clear_strip(c.strip))
        menu.exec(pos)

    def _log(self, text: str):
        self.monitor.appendPlainText(f"{time.strftime('%H:%M:%S')}  {text}")

    def _vmix_status(self, ok: bool, version: str):
        self._vmix_version = version
        self.vmix_pill.set(ok, f"v{version}" if version else "")
        self._log(f"vMix {'connecté (v' + version + ')' if ok else 'déconnecté'}")

    def _toggle_monitor(self, on: bool):
        self.monitor.setVisible(on)
        self.mon_out.setVisible(on)

    def _midi_status(self):
        m = self.engine.midi
        mc = self.engine.console_mode == "mc"
        self.midi_pill.set(m.connected, m.port_name + ("  ·  mode MC (en pause)" if mc else ""))

    def _show_latency(self):
        v = self.engine.vmix
        if v.online and v.latency_ms is not None:
            self.vmix_pill.set(True, f"v{self._vmix_version}  ·  {v.latency_ms:.0f} ms")
            self.vmix_pill.setToolTip("Temps aller-retour moyen d'une commande vers vMix")

    # -- profils --------------------------------------------------------------------
    def _refresh_profiles(self):
        c = self.profile_combo
        c.blockSignals(True)
        c.clear()
        for path in list_profiles():
            c.addItem(path.stem, str(path))
        c.insertSeparator(c.count())
        c.addItem("＋  Nouveau profil…", "__new__")
        cur = self.engine.profile.path
        c.setCurrentIndex(max(0, c.findData(str(cur)) if cur else 0))
        c.blockSignals(False)

    def _profile_selected(self, idx: int):
        data = self.profile_combo.itemData(idx)
        if data == "__new__":
            name, ok = QInputDialog.getText(self, "Nouveau profil", "Nom du profil :")
            if ok and name.strip():
                p = Profile(name=name.strip())
                p.save(_unique(profile_path(p.name)))
                self.switch_profile(p)
            else:
                self._refresh_profiles()
        elif data:
            self.switch_profile(Profile.load(Path(data)))

    def switch_profile(self, profile: Profile):
        self.engine.profile.save()
        self.engine.set_profile(profile)
        self.select("")
        s = load_settings()
        s["last_profile"] = str(profile.path)
        save_settings(s)
        self._refresh_profiles()
        self._update_title()
        self._vmix_version = ""
        self._lat_timer = QTimer(self, interval=1000)
        self._lat_timer.timeout.connect(self._show_latency)
        self._lat_timer.start()
        self._log(f"Profil chargé : {profile.name}")

    def _update_title(self):
        self.setWindowTitle(f"{APP_NAME} {__version__} — {self.engine.profile.name}")

    def import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Importer", str(Path.home() / "Desktop"),
            "Shows et profils (*.ctrl *.json);;Central Control (*.ctrl);;MixPanel (*.json)")
        if not path:
            return
        try:
            if path.lower().endswith(".ctrl"):
                prof, notes = import_ctrl(path)
            else:
                prof, notes = Profile.load(Path(path)), []
                prof.path = None
        except Exception as ex:
            QMessageBox.warning(self, "Import impossible", f"Ce fichier n'a pas pu être lu :\n{ex}")
            return
        prof.save(_unique(profile_path(prof.name)))
        self.switch_profile(prof)
        msg = f"<b>{len(prof.mappings)} associations</b> importées dans le profil « {prof.name} »."
        if notes:
            msg += "<br><br>" + "<br>".join(f"• {n}" for n in notes)
        QMessageBox.information(self, "Import terminé", msg)

    def open_settings(self):
        p = self.engine.profile
        dlg = SettingsDialog(p, self, engine=self.engine)
        if dlg.exec():
            old_name = p.name
            dlg.apply()
            if p.name != old_name and p.path:
                new_path = _unique(profile_path(p.name))
                old_path = p.path
                p.save(new_path)
                if old_path != new_path:
                    old_path.unlink(missing_ok=True)
            else:
                p.save()
            self.engine.set_profile(p)
            s = load_settings()
            s["last_profile"] = str(p.path)
            save_settings(s)
            self._refresh_profiles()
            self._update_title()

    def closeEvent(self, ev):
        if self.engine.locked and QMessageBox.question(
                self, "Quitter", "MixPanel est verrouillé (direct en cours ?).\nQuitter quand même ?") \
                != QMessageBox.Yes:
            ev.ignore()
            return
        if self.engine.ducker.active:
            # ne jamais laisser la musique baissée en quittant
            self.engine.ducker.release()
            time.sleep(0.3)
        self.engine.profile.save()
        super().closeEvent(ev)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (cand := path.with_name(f"{path.stem} ({n}){path.suffix}")).exists():
        n += 1
    return cand
