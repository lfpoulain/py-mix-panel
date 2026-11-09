"""Réplique interactive de la X-Touch Compact, dessinée à la main.

Disposition fidèle à la console : 8 tranches (encodeur, 3 lignes de boutons,
fader, bouton sous le fader), colonne MAIN avec les LED d'état, puis le bloc de
droite : 8 encodeurs en 2 colonnes, transport en 3 rangées et boutons LAYER.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPen,
                           QRadialGradient, QTransform)
from PySide6.QtWidgets import QToolTip, QWidget

from .. import actions as A
from ..vmix import position_to_db
from ..xtouch import BUTTON, ENCODER, FADER, PEDAL, TRANSPORT
from . import theme as T

MIME = "application/x-mixpanel-input"
MIME_REF = "application/x-mixpanel-ref"   # "scene:<id>" ou "macro:<id>"

W, H = 940, 650
ENC_Y, ENC_R, PUSH_R = 56, 26, 10
ROWS_Y = (124, 164, 204)
NAME_Y = 240
TRACK_TOP, TRACK_BOT = 264, 556
FADER_TOP, FADER_BOT = 280, 540      # course du centre du curseur
FBTN_Y = 592
MAIN_X = 640
RIGHT_X = (752, 846)
RIGHT_ENC_Y = (56, 122, 188, 254)
TRANSPORT_Y = (334, 378, 422)
PEDAL_Y = 492
_NAMED = {cid for cid, _ in TRANSPORT} | {"exp", "foot"}   # affichent leur nom s'ils sont libres
_GLYPH = {"rew": "◀◀", "ff": "▶▶", "loop": "LOOP", "rec": "●", "stop": "■", "play": "▶"}
_SHORT = {"exp": "Pédale exp.", "foot": "Footswitch"}


def _strip_x(i: int) -> float:
    return 56 + i * 72


def build_geometry() -> dict[str, tuple]:
    """cid -> ("rect", QRectF) | ("circle", centre, rayon) | ("fader", cx)"""
    g: dict[str, tuple] = {}
    for i in range(8):
        x, n = _strip_x(i), i + 1
        g[f"push{n}"] = ("circle", QPointF(x, ENC_Y), PUSH_R)
        g[f"enc{n}"] = ("circle", QPointF(x, ENC_Y), ENC_R)
        for r, y in enumerate(ROWS_Y):
            g[f"m{r + 1}_{n}"] = ("rect", QRectF(x - 30, y - 16, 60, 32))
        g[f"fader{n}"] = ("fader", x)
        g[f"fbtn{n}"] = ("rect", QRectF(x - 30, FBTN_Y - 16, 60, 32))
    g["main"] = ("fader", MAIN_X)
    g["mainbtn"] = ("rect", QRectF(MAIN_X - 30, FBTN_Y - 16, 60, 32))
    for k in range(8):
        c = QPointF(RIGHT_X[k % 2], RIGHT_ENC_Y[k // 2])
        g[f"push{9 + k}"] = ("circle", c, PUSH_R)
        g[f"enc{9 + k}"] = ("circle", c, ENC_R)
    for k, (cid, _) in enumerate(TRANSPORT):
        cx, cy = RIGHT_X[k % 2], TRANSPORT_Y[k // 2]
        g[cid] = ("rect", QRectF(cx - 40, cy - 16, 80, 32))
    g["exp"] = ("rect", QRectF(RIGHT_X[0] - 42, PEDAL_Y - 15, 84, 30))
    g["foot"] = ("rect", QRectF(RIGHT_X[1] - 42, PEDAL_Y - 15, 84, 30))
    return g


def _layer_rect(k: int) -> QRectF:
    """Boutons LAYER A / B (bas du bloc de droite)."""
    return QRectF(RIGHT_X[k] - 40, FBTN_Y - 16, 80, 32)


_DB_ACTIONS = {"volume", "master_volume", "bus_volume", "headphones_volume"}


def _fader_text(action, v: float) -> str:
    """Volume en dB, comme dans le mixeur vMix (0 dB en haut, -∞ en bas)."""
    if action is None or action.key not in _DB_ACTIONS:
        return f"{v * 100:.0f}"
    db = position_to_db(v * 100)
    if db == float("-inf") or db < -99:
        return "-∞"
    return f"{db:+.1f}" if db > -10 else f"{db:.0f}"


def _color(engine, cid: str) -> QColor | None:
    a = engine.action_of(cid)
    return QColor(A.CATEGORIES[a.category][1]) if a else None


def _meter_norm(amp: float) -> float:
    """Amplitude 0..1 -> position sur une échelle -60..0 dB."""
    if amp <= 0:
        return 0.0
    return min(1.0, max(0.0, (20 * math.log10(amp) + 60) / 60))


class SurfaceWidget(QWidget):
    selected = Signal(str)
    dropped = Signal(str, str)            # cid, clé de la source vMix
    dropped_ref = Signal(str, str, str)   # cid, "scene" | "macro", id
    context_requested = Signal(str, object)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.geo = build_geometry()
        self.sel = ""
        self.hover = ""
        self.drop_target = ""
        self.live = False
        self._disp: dict[str, float] = {}
        self._meters: dict[str, float] = {}
        self._flash: dict[str, float] = {}
        self._drag: tuple | None = None
        self.setMinimumSize(640, 440)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._tick)
        engine.changed.connect(self._kick)
        engine.mapping_changed.connect(lambda _: self.update())
        engine.activity.connect(self._on_activity)
        engine.page_changed.connect(lambda _: self.update())
        self._layer_hover = -1

    # -- géométrie ----------------------------------------------------------------
    def _xf(self) -> QTransform:
        s = min(self.width() / W, self.height() / H)
        t = QTransform()
        t.translate((self.width() - W * s) / 2, (self.height() - H * s) / 2)
        t.scale(s, s)
        return t

    def _to_logical(self, pos) -> QPointF:
        inv, _ = self._xf().inverted()
        return inv.map(QPointF(pos))

    def hit(self, pos) -> str:
        p = self._to_logical(pos)

        def inside(g):
            return (p.x() - g[1].x()) ** 2 + (p.y() - g[1].y()) ** 2 <= g[2] ** 2

        # les appuis (centre des encodeurs) sont prioritaires sur la rotation
        for cid, g in self.geo.items():
            if cid.startswith("push") and inside(g):
                return cid
        for cid, g in self.geo.items():
            if g[0] == "circle" and inside(g):
                return cid
            if g[0] == "rect" and g[1].contains(p):
                return cid
            if g[0] == "fader" and abs(p.x() - g[1]) <= 28 and TRACK_TOP - 6 <= p.y() <= TRACK_BOT + 6:
                return cid
        return ""

    def select(self, cid: str):
        self.sel = cid
        self.update()

    # -- animation ------------------------------------------------------------------
    def _kick(self):
        # Ne pas relancer un timer actif : avec un flux MIDI rapide, start() repousserait
        # sans cesse le prochain rafraîchissement (le fader à l'écran semblerait figé).
        if not self._timer.isActive():
            self._timer.start()

    def _on_activity(self, cid: str):
        self._flash[cid] = time.monotonic()
        self._kick()

    def _tick(self):
        busy = False
        e = self.engine
        for cid, v in e.values.items():
            d = self._disp.get(cid, v)
            if (self._drag and self._drag[0] == cid) or e.recently_moved(cid):
                d = v   # main sur le fader : affichage direct, sans lissage
            elif abs(v - d) > 0.002:
                d += (v - d) * 0.45
                busy = True
            else:
                d = v
            self._disp[cid] = d
        for cid, target in self._meter_targets().items():
            cur = self._meters.get(cid, 0.0)
            cur = target if target >= cur else max(target, cur - 0.025)  # retombée douce
            self._meters[cid] = cur
            busy = busy or cur > target
        now = time.monotonic()
        for cid, t0 in list(self._flash.items()):
            if now - t0 > 0.5:
                del self._flash[cid]
            else:
                busy = True
        self.update()
        if not busy:
            self._timer.stop()

    def _meter_targets(self) -> dict[str, float]:
        e, st = self.engine, self.engine.state
        out = {}
        for cid in [f"fader{i + 1}" for i in range(8)] + ["main"]:
            m, a = e.mapping(cid), e.action_of(cid)
            amp = 0.0
            if a and m:
                if m.get("input"):
                    inp = st.input(m["input"])
                    amp = inp.meter if inp and not inp.muted else 0.0
                elif a.key == "master_volume":
                    amp = 0.0 if st.master.muted else st.master.meter
                elif a.key == "bus_volume" and m.get("bus") in st.buses:
                    amp = st.buses[m["bus"]].meter
            out[cid] = _meter_norm(amp)
        return out

    # -- dessin ---------------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setTransform(self._xf())
        self._paint_body(p)
        e = self.engine
        for cid, g in self.geo.items():
            c = e.controls[cid]
            if g[0] == "fader":
                self._paint_fader(p, cid, g[1])
            elif c.kind == ENCODER:
                self._paint_encoder(p, cid, g[1], g[2])
            elif cid.startswith("push"):
                continue
            else:
                self._paint_button(p, cid, g[1])
        for i in range(16):
            g = self.geo[f"push{i + 1}"]
            self._paint_push(p, f"push{i + 1}", g[1], g[2])
        p.end()

    def _font(self, size: float, bold=False, spacing=0.0) -> QFont:
        f = QFont(self.font())
        f.setPointSizeF(size)
        f.setBold(bold)
        if spacing:
            f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
        return f

    def _paint_body(self, p: QPainter):
        body = QRectF(6, 6, W - 12, H - 12)
        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0, QColor("#1c2029"))
        grad.setColorAt(1, QColor("#121419"))
        p.setPen(QPen(QColor("#2b313d"), 1.2))
        p.setBrush(grad)
        p.drawRoundedRect(body, 22, 22)
        # bloc de droite légèrement en retrait, comme sur la console
        p.setPen(QPen(QColor("#232833"), 1))
        p.setBrush(QColor(255, 255, 255, 6))
        p.drawRoundedRect(QRectF(700, 22, W - 718, H - 44), 14, 14)
        p.setPen(QPen(QColor("#232833"), 1))
        p.drawLine(QPointF(600, 26), QPointF(600, H - 26))
        if self.drop_target:
            c = self.engine.controls[self.drop_target]
            if c.strip is not None and c.strip < 8:
                x = _strip_x(c.strip)
                p.setPen(QPen(QColor(T.ACCENT), 1.5, Qt.DashLine))
                p.setBrush(QColor(45, 212, 191, 22))
                p.drawRoundedRect(QRectF(x - 35, 20, 70, H - 40), 14, 14)
        self._paint_status(p)
        # légendes du bloc de droite
        p.setFont(self._font(6.8, True, 1.5))
        p.setPen(QColor("#3c4352"))
        mid = (RIGHT_X[0] + RIGHT_X[1]) / 2
        p.drawText(QRectF(mid - 100, 292, 200, 14), Qt.AlignCenter, "USB / MIDI CONTROLLER")
        p.drawText(QRectF(mid - 100, 462, 200, 14), Qt.AlignCenter, "PRISES ARRIÈRE")
        # boutons LAYER (gérés par la X-Touch elle-même)
        page = self.engine.page
        for k, (label, lit) in enumerate((("◀  A", page == "A"), ("B  ▶", page == "B"))):
            r = _layer_rect(k)
            p.setPen(QPen(QColor("#4b5568"), 1.4) if self._layer_hover == k else QPen(QColor("#2a303b"), 1))
            p.setBrush(QColor(251, 191, 36, 40) if lit else QColor("#181b22"))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor("#fbbf24") if lit else QColor("#4a5263"))
            p.setFont(self._font(8.2, True))
            p.drawText(r, Qt.AlignCenter, label)
        p.setFont(self._font(6.8, True, 1.5))
        p.setPen(QColor("#3c4352"))
        p.drawText(QRectF(mid - 30, FBTN_Y + 18, 60, 14), Qt.AlignCenter, "LAYER")
        p.setFont(self._font(8.5, True, 3))
        p.setPen(QColor("#2f3542"))
        p.drawText(QRectF(26, 616, 300, 18), Qt.AlignLeft, "X-TOUCH COMPACT")

    def _paint_status(self, p: QPainter):
        """Colonne MAIN : LED d'état (comme les LED de mode de la console) + tally."""
        e, st = self.engine, self.engine.state
        panel = QRectF(MAIN_X - 32, 28, 64, 196)
        p.setPen(QPen(QColor("#262b35"), 1))
        p.setBrush(QColor("#0f1115"))
        p.drawRoundedRect(panel, 9, 9)
        y = 44
        leds = [("X-TOUCH", getattr(e.midi, "connected", False), T.OK),
                ("VMIX", st.online, T.OK),
                ("STREAM", st.streaming, T.PGM),
                ("REC", st.recording, T.PGM)]
        p.setFont(self._font(6.4, True, 0.5))
        for label, on, col in leds:
            c = QColor(col) if on else QColor("#2a303b")
            if on:
                halo = QColor(col)
                halo.setAlphaF(0.25)
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(QPointF(MAIN_X - 20, y), 6, 6)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(MAIN_X - 20, y), 3.2, 3.2)
            p.setPen(QColor(T.TEXT) if on else QColor("#4a5263"))
            p.drawText(QRectF(MAIN_X - 13, y - 7, 44, 14), Qt.AlignLeft | Qt.AlignVCenter, label)
            y += 22
        for label, num, col in (("PGM", st.active, T.PGM), ("PVW", st.preview, T.PVW)):
            r = QRectF(MAIN_X - 24, y - 2, 48, 30)
            p.setPen(QPen(QColor(col), 1.2) if st.online else QPen(QColor("#2a303b"), 1))
            p.setBrush(QColor(col).darker(320) if st.online else Qt.NoBrush)
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(col) if st.online else QColor("#4a5263"))
            p.setFont(self._font(5.8, True, 1))
            p.drawText(QRectF(r.x(), r.y() + 2, r.width(), 10), Qt.AlignCenter, label)
            p.setFont(self._font(10, True))
            p.setPen(QColor(T.TEXT) if st.online else QColor("#4a5263"))
            p.drawText(QRectF(r.x(), r.y() + 10, r.width(), 18), Qt.AlignCenter,
                       str(num) if st.online and num else "–")
            y += 36

    def _outline(self, cid: str, mapped_color: QColor | None) -> QPen:
        if cid == self.sel:
            return QPen(QColor("#ffffff"), 2)
        if cid in self._flash:
            a = max(0.0, 1 - (time.monotonic() - self._flash[cid]) / 0.5)
            c = QColor(T.ACCENT)
            c.setAlphaF(0.35 + 0.65 * a)
            return QPen(c, 2)
        if cid == self.hover or cid == self.drop_target:
            return QPen(QColor("#4b5568"), 1.4)
        if mapped_color is not None:
            c = QColor(mapped_color)
            c.setAlphaF(0.5)
            return QPen(c, 1.2)
        return QPen(QColor("#2a303b"), 1)

    def _glow(self, p: QPainter, rect: QRectF, color: QColor, radius: float):
        for k, a in ((7, 0.05), (4.5, 0.09), (2.2, 0.16)):
            c = QColor(color)
            c.setAlphaF(a)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(rect.adjusted(-k, -k, k, k), radius + k, radius + k)

    def _texts(self, p: QPainter, rect: QRectF, lines: tuple[str, str] | None, fg: QColor, sub: QColor):
        if lines is None:
            p.setPen(QColor("#3a4150"))
            p.setFont(self._font(9))
            p.drawText(rect, Qt.AlignCenter, "·")
            return
        main, second = lines
        f = self._font(9, True)
        fm = QFontMetricsF(f)
        w = rect.width() - 8
        p.setFont(f)
        p.setPen(fg)
        if second:
            p.drawText(QRectF(rect.x() + 4, rect.y() + 2, w, rect.height() / 2 + 1),
                       Qt.AlignHCenter | Qt.AlignBottom, fm.elidedText(main, Qt.ElideRight, w))
            f2 = self._font(7.2)
            p.setFont(f2)
            p.setPen(sub)
            p.drawText(QRectF(rect.x() + 4, rect.center().y() + 1, w, rect.height() / 2 - 3),
                       Qt.AlignHCenter | Qt.AlignTop,
                       QFontMetricsF(f2).elidedText(second, Qt.ElideRight, w))
        else:
            p.drawText(rect, Qt.AlignCenter, fm.elidedText(main, Qt.ElideRight, w))

    def _missing_dot(self, p: QPainter, cid: str, at: QPointF):
        m = self.engine.mapping(cid)
        if m and self.engine.input_missing(m):
            p.setPen(QPen(QColor("#1a1e26"), 1.5))
            p.setBrush(QColor(T.DANGER))
            p.drawEllipse(at, 4, 4)

    def _paint_button(self, p: QPainter, cid: str, rect: QRectF):
        e = self.engine
        col = _color(e, cid)
        on = e.leds.get(cid, False) and col is not None
        if on:
            self._glow(p, rect, col, 8)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0, col.lighter(115))
            grad.setColorAt(1, col.darker(115))
            p.setBrush(grad)
        else:
            p.setBrush(QColor("#1e222b") if col is not None else QColor("#181b22"))
        p.setPen(self._outline(cid, col))
        p.drawRoundedRect(rect, 8, 8)
        lines = e.surface_label(cid)
        if lines is None and cid in _NAMED:
            p.setFont(self._font(9 if cid in _GLYPH else 7.6, cid in _GLYPH))
            p.setPen(QColor("#4a5263"))
            p.drawText(rect, Qt.AlignCenter, _GLYPH.get(cid) or _SHORT.get(cid, e.controls[cid].name))
            return
        fg = QColor("#0b0d10") if on else (QColor(T.TEXT) if col is not None else QColor(T.FAINT))
        sub = QColor(11, 13, 16, 190) if on else QColor(T.MUTED)
        self._texts(p, rect, lines, fg, sub)
        self._missing_dot(p, cid, rect.topRight() + QPointF(-4, 4))

    def _paint_encoder(self, p: QPainter, cid: str, c: QPointF, r: float):
        e = self.engine
        col = _color(e, cid)
        a = e.action_of(cid)
        v = self._disp.get(cid, e.values.get(cid, 0.0))
        ring = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#262b36"), 3.6, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 225 * 16, -270 * 16)
        mode = e.ring_mode(cid)
        if mode in ("state", "state_blink"):
            # anneau = témoin de l'appui : plein quand l'action est active (pointillés = clignotant)
            if e.push_state(cid):
                pcol = _color(e, cid.replace("enc", "push")) or QColor(T.ACCENT)
                style = Qt.DashLine if mode == "state_blink" else Qt.SolidLine
                p.setPen(QPen(pcol, 3.6, style, Qt.RoundCap))
                p.drawArc(ring, 225 * 16, -270 * 16)
        elif mode == "off":
            pass
        elif e.is_meter_ring(cid):
            span = int(-270 * 16 * v)
            grad = QLinearGradient(ring.bottomLeft(), ring.topRight())
            grad.setColorAt(0, QColor("#22c55e"))
            grad.setColorAt(0.7, QColor("#facc15"))
            grad.setColorAt(1, QColor("#ef4444"))
            p.setPen(QPen(grad, 3.6, Qt.SolidLine, Qt.RoundCap))
            if span:
                p.drawArc(ring, 225 * 16, span)
        elif col is not None:
            if a and a.step:
                # encodeur cranté : anneau en pointillés, on tourne sans butée
                dim = QColor(col)
                dim.setAlphaF(0.55)
                p.setPen(QPen(dim, 3.6, Qt.DotLine, Qt.RoundCap))
                p.drawArc(ring, 225 * 16, -270 * 16)
            else:
                p.setPen(QPen(col, 3.6, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(ring, 225 * 16, int(-270 * 16 * v))
        knob = ring.adjusted(6.5, 6.5, -6.5, -6.5)
        g = QRadialGradient(knob.center() - QPointF(4, 6), knob.width())
        g.setColorAt(0, QColor("#3a404d"))
        g.setColorAt(1, QColor("#1a1d24"))
        p.setBrush(g)
        p.setPen(self._outline(cid, None) if cid in (self.sel, self.hover) or cid in self._flash
                 else QPen(QColor("#0f1115"), 1))
        p.drawEllipse(knob)
        # étiquettes : rotation (couleur de l'action) et appui (⏎)
        push = cid.replace("enc", "push")
        rot, prs = e.surface_label(cid), e.surface_label(push)
        pcol = _color(e, push)
        two_lines = e.controls[cid].strip is not None     # place sous les encodeurs des tranches
        items = []
        if rot:
            items.append((rot[0], col))
        if prs:
            items.append(("⏎ " + prs[0], pcol))
        f = self._font(7.4, True)
        p.setFont(f)
        fm = QFontMetricsF(f)
        if two_lines:
            for k, (txt, cc) in enumerate(items):
                p.setPen(cc)
                p.drawText(QRectF(c.x() - 36, c.y() + r + 1 + k * 11, 72, 12), Qt.AlignCenter,
                           fm.elidedText(txt, Qt.ElideRight, 70))
        elif items:
            txt = "  ".join(t for t, _ in items)
            p.setPen(items[0][1])
            p.drawText(QRectF(c.x() - 46, c.y() + r, 92, 12), Qt.AlignCenter,
                       fm.elidedText(txt, Qt.ElideRight, 90))
        self._missing_dot(p, cid, c + QPointF(r * 0.72, -r * 0.72))

    def _paint_push(self, p: QPainter, cid: str, c: QPointF, r: float):
        e = self.engine
        col = _color(e, cid)
        on = e.leds.get(cid, False) and col is not None
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        if on:
            self._glow(p, rect, col, r)
            p.setBrush(col)
        else:
            p.setBrush(QColor("#2a2f3a") if col is None else QColor("#262b35"))
        pen = self._outline(cid, col)
        if col is None and cid not in (self.sel, self.hover) and cid not in self._flash:
            pen = QPen(QColor("#3a404d"), 1)
        p.setPen(pen)
        p.drawEllipse(rect)
        self._missing_dot(p, cid, c + QPointF(r * 0.8, -r * 0.8))

    def _paint_fader(self, p: QPainter, cid: str, x: float):
        e = self.engine
        col = _color(e, cid)
        v = self._disp.get(cid, e.values.get(cid, 0.0))
        y = FADER_BOT - v * (FADER_BOT - FADER_TOP)
        # graduations à gauche
        p.setPen(QPen(QColor("#2a303b"), 1))
        for k in range(11):
            ty = FADER_BOT - k * (FADER_BOT - FADER_TOP) / 10
            w = 8 if k in (0, 5, 10) else 4
            p.drawLine(QPointF(x - 17 - w, ty), QPointF(x - 17, ty))
        # vu-mètre à droite
        lvl = self._meters.get(cid, 0.0)
        meter = QRectF(x + 18, FADER_TOP, 5, FADER_BOT - FADER_TOP)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0b0d11"))
        p.drawRoundedRect(meter, 2, 2)
        if lvl > 0.001:
            mg = QLinearGradient(0, FADER_BOT, 0, FADER_TOP)
            mg.setColorAt(0, QColor("#22c55e"))
            mg.setColorAt(0.75, QColor("#a3e635"))
            mg.setColorAt(0.9, QColor("#facc15"))
            mg.setColorAt(1, QColor("#ef4444"))
            p.setBrush(mg)
            hgt = meter.height() * lvl
            p.drawRoundedRect(QRectF(meter.x(), meter.bottom() - hgt, meter.width(), hgt), 2, 2)
        # rail
        p.setBrush(QColor("#0b0d11"))
        p.drawRoundedRect(QRectF(x - 3.5, TRACK_TOP, 7, TRACK_BOT - TRACK_TOP), 3.5, 3.5)
        if col is not None:
            c = QColor(col)
            c.setAlphaF(0.85)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x - 2, y, 4, FADER_BOT - y + 14), 2, 2)
        # curseur
        cap = QRectF(x - 23, y - 15, 46, 30)
        touched = cid in e.touched or (self._drag and self._drag[0] == cid)
        if touched and col is not None:
            self._glow(p, cap, col, 6)
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        g.setColorAt(0, QColor("#4a5160"))
        g.setColorAt(0.48, QColor("#2b3039"))
        g.setColorAt(0.52, QColor("#1b1e25"))
        g.setColorAt(1, QColor("#2d323c"))
        p.setBrush(g)
        p.setPen(self._outline(cid, None) if cid in (self.sel, self.hover) or cid in self._flash
                 else QPen(QColor("#0d0f13"), 1))
        p.drawRoundedRect(cap, 6, 6)
        p.setPen(QPen(col if col is not None else QColor("#8b93a5"), 2))
        p.drawLine(QPointF(cap.left() + 6, y), QPointF(cap.right() - 6, y))
        if col is not None:
            p.setFont(self._font(7.6, True))
            p.setPen(QColor("#c9ceda"))
            p.drawText(QRectF(x - 23, y - 15, 46, 13), Qt.AlignCenter, _fader_text(e.action_of(cid), v))
        self._paint_strip_label(p, cid, x, col)

    def _paint_strip_label(self, p: QPainter, cid: str, x: float, col: QColor | None):
        e = self.engine
        rect = QRectF(x - 32, NAME_Y - 12, 64, 24)
        lines = e.surface_label(cid)
        m = e.mapping(cid)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#191c23"))
        p.drawRoundedRect(rect, 7, 7)
        # tally : rouge au programme, vert en preview
        tally = None
        if m and m.get("input"):
            inp = e.state.input(m["input"])
            if inp and inp.number == e.state.active:
                tally = QColor(T.PGM)
            elif inp and inp.number == e.state.preview:
                tally = QColor(T.PVW)
        bar = tally or col
        if bar is not None:
            p.setBrush(bar)
            p.drawRoundedRect(QRectF(rect.x() + 8, rect.y(), rect.width() - 16, 3), 1.5, 1.5)
        f = self._font(8.2, True)
        p.setFont(f)
        p.setPen(QColor(T.TEXT) if lines else QColor("#3a4150"))
        text = lines[0] if lines else ("MAIN" if cid == "main" else e.controls[cid].name)
        p.drawText(rect.adjusted(3, 3, -3, 0), Qt.AlignCenter,
                   QFontMetricsF(f).elidedText(text, Qt.ElideRight, 58))
        self._missing_dot(p, cid, rect.topRight() + QPointF(-3, 3))

    # -- souris -------------------------------------------------------------------
    def _fader_value(self, pos) -> float:
        y = self._to_logical(pos).y()
        return min(1.0, max(0.0, (FADER_BOT - y) / (FADER_BOT - FADER_TOP)))

    def _is_step(self, cid: str) -> bool:
        a = self.engine.action_of(cid)
        return bool(a and a.step)

    def _step(self, cid: str, direction: int):
        self.engine.turn(cid, direction)

    def _layer_hit(self, pos) -> int:
        p = self._to_logical(pos)
        return next((k for k in (0, 1) if _layer_rect(k).contains(p)), -1)

    def mousePressEvent(self, ev):
        if self.engine.locked:   # verrouillé : aucun clic ne part vers vMix
            return
        k = self._layer_hit(ev.position())
        if k >= 0:
            self.engine.set_page("AB"[k])   # bascule aussi la console sur le Layer A / B
            return
        cid = self.hit(ev.position())
        if not cid:
            return
        if ev.button() == Qt.RightButton:
            self.selected.emit(cid)
            self.context_requested.emit(cid, ev.globalPosition().toPoint())
            return
        self.selected.emit(cid)
        c = self.engine.controls[cid]
        if c.kind == FADER:
            self._drag = (cid, None)
            self.engine.handle(cid, self._fader_value(ev.position()), from_ui=True)
        elif c.kind in (ENCODER, PEDAL):
            self._drag = (cid, (ev.position().y(), self.engine.values.get(cid, 0.0)))
        elif c.kind == BUTTON and self.live:
            self._drag = (cid, "press")
            self.engine.handle(cid, 1.0)
        self._kick()

    def mouseMoveEvent(self, ev):
        if self._drag:
            cid, info = self._drag
            kind = self.engine.controls[cid].kind
            if kind == FADER:
                self.engine.handle(cid, self._fader_value(ev.position()), from_ui=True)
            elif kind in (ENCODER, PEDAL) and info:
                y0, v0 = info
                dy = y0 - ev.position().y()
                if self._is_step(cid):
                    if abs(dy) >= 14:   # un cran tous les 14 px
                        self._step(cid, 1 if dy > 0 else -1)
                        self._drag = (cid, (ev.position().y(), v0))
                else:
                    self.engine.handle(cid, min(1.0, max(0.0, v0 + dy / 180)), from_ui=True)
            return
        cid = self.hit(ev.position())
        layer = self._layer_hit(ev.position())
        if cid != self.hover or layer != self._layer_hover:
            self.hover, self._layer_hover = cid, layer
            self.setCursor(Qt.PointingHandCursor if cid or layer >= 0 else Qt.ArrowCursor)
            self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag and self._drag[1] == "press":
            self.engine.handle(self._drag[0], 0.0)
        self._drag = None
        self._kick()

    def leaveEvent(self, _):
        self.hover = ""
        self.update()

    def wheelEvent(self, ev):
        cid = self.hit(ev.position())
        if cid and self.engine.controls[cid].continuous:
            up = ev.angleDelta().y() > 0
            if self._is_step(cid):
                self._step(cid, 1 if up else -1)
            else:
                v = self.engine.values.get(cid, 0.0) + (0.02 if up else -0.02)
                self.engine.handle(cid, min(1.0, max(0.0, v)), from_ui=True)
            self._kick()

    def event(self, ev):
        if ev.type() == ev.Type.ToolTip:
            cid = self.hit(ev.position() if hasattr(ev, "position") else ev.pos())
            if cid:
                e = self.engine
                c = e.controls[cid]
                a = e.action_of(cid)
                txt = f"<b>{c.name}</b><br><span style='color:{T.MUTED}'>{c.midi.label()}</span>"
                if a:
                    m = e.mapping(cid)
                    who = f" — {e.input_title(m)}" if A.needs_input(a) else ""
                    txt += f"<br>{a.label}{who}"
                    if e.input_missing(m):
                        txt += f"<br><span style='color:{T.DANGER}'>Source introuvable dans vMix</span>"
                else:
                    txt += "<br><i>Non associé</i>"
                QToolTip.showText(ev.globalPos(), txt, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(ev)

    # -- glisser-déposer ------------------------------------------------------------
    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(MIME) or ev.mimeData().hasFormat(MIME_REF):
            ev.acceptProposedAction()

    def dragMoveEvent(self, ev):
        cid = self.hit(ev.position())
        if cid != self.drop_target:
            self.drop_target = cid
            self.update()
        if cid:
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragLeaveEvent(self, _):
        self.drop_target = ""
        self.update()

    def dropEvent(self, ev):
        cid = self.hit(ev.position())
        self.drop_target = ""
        if cid:
            md = ev.mimeData()
            if md.hasFormat(MIME_REF):
                kind, _, ref = bytes(md.data(MIME_REF)).decode().partition(":")
                self.dropped_ref.emit(cid, kind, ref)
            else:
                self.dropped.emit(cid, bytes(md.data(MIME)).decode())
            ev.acceptProposedAction()
        self.update()
