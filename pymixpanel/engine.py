"""Moteur : X-Touch -> actions vMix, état vMix -> LED et faders motorisés."""
from __future__ import annotations

import time

import mido
from PySide6.QtCore import QObject, QTimer, Signal

from . import actions as A
from .automation import Ducker, MacroRunner, SceneBook, SoftFades, migrate_scenes
from .config import Profile
from .vmix import VmixState, meter_norm, position_to_db
from .xtouch import (ENCODER, FADER, RING_SINGLE, ControlDef, MidiId, decode,
                     TRANSPORT, default_controls, ring_value, strip_controls)

TRANSPORT_IDS = tuple(cid for cid, _ in TRANSPORT)

ENC_STEP = 0.01          # un cran d'encodeur = 1 % de la course
CONFIRM_TIMEOUT = 3.0    # s : au-delà, une valeur vMix différente vient d'ailleurs
RESEND_EVERY = 0.4       # s : renvoi de la position d'un fader tant que vMix ne l'a pas confirmée
FINAL_RESEND_MS = 150    # ms : second envoi de la position finale après le relâchement


def _db(pos: float) -> str:
    db = position_to_db(pos)
    return "-∞ dB" if db == float("-inf") else f"{db:.1f} dB"
LED_OFF, LED_ON, LED_BLINK = 0, 1, 2


def _describe(msg: mido.Message) -> str:
    ch = f"canal {msg.channel + 1}" if hasattr(msg, "channel") else ""
    if msg.type == "note_on":
        return f"Note {msg.note} vélocité {msg.velocity} · {ch}"
    if msg.type == "control_change":
        return f"CC {msg.control} = {msg.value} · {ch}"
    if msg.type == "program_change":
        return f"Program Change {msg.program} · {ch}"
    return str(msg)


class Engine(QObject):
    changed = Signal()                 # valeurs / LED à redessiner
    mapping_changed = Signal(str)      # cid ("" = tout)
    activity = Signal(str)             # contrôle physique touché
    learning = Signal(str)             # cid en attente d'apprentissage ("" = fini)
    automation_changed = Signal()      # liste des scènes / macros modifiée
    page_changed = Signal(str)         # "A" | "B"
    diag_input = Signal(str, str, str)   # mode diagnostic : cid, rôle ("main"/"touch"), page
    diag_unknown = Signal(object)        # mode diagnostic : MidiId non reconnu
    console_mode_changed = Signal(str)   # "standard" | "mc"
    log = Signal(str)
    locked_changed = Signal(bool)      # verrouillage anti fausse manip (direct)

    def __init__(self, profile: Profile, midi, vmix):
        super().__init__()
        self.midi, self.vmix = midi, vmix
        self.locked = False   # True : plus d'édition ni de clic à l'écran (la X-Touch reste active)
        self.state = VmixState()
        self.values: dict[str, float] = {}
        self.leds: dict[str, bool] = {}
        self.touched: set[str] = set()
        self._last_move: dict[str, float] = {}
        self._sent: dict[str, int | bool] = {}
        self._enc_last: dict[str, int] = {}      # dernière valeur brute reçue par encodeur
        self._own: dict[tuple, float] = {}       # dernière position de volume envoyée à vMix
        self._fb_errors: set[str] = set()
        self._pending: dict[str, float] = {}     # cid -> échéance : valeur envoyée pas encore confirmée
        self._resent: dict[str, float] = {}      # cid -> dernier renvoi de la position à vMix
        self._touch_start: dict[str, float] = {}  # cid -> début du contact (capteur tactile)
        self.stats = {"midi_in": 0, "midi_out": 0}
        self._learn_cand = None                  # apprentissage d'un fader (position + tactile)
        self._ring: dict[str, float] = {}
        self.page = "A"
        self.diagnostic = False   # diagnostic : on écoute la console sans rien déclencher
        self._pedal_ok = False    # pédale d'expression réellement branchée ?
        self.log_midi_out = False         # Moniteur : afficher les messages envoyés à la console
        self.console_mode = "standard"   # "mc" : console laissée en Mackie pour un autre logiciel
        self._mc_warned = False
        self.learn_target = ""
        self.profile = profile
        self.scenes = SceneBook(self)
        self.macros = MacroRunner(self)
        self.ducker = Ducker(self)
        self.fades = SoftFades(self)
        self._save_timer = QTimer(self, singleShot=True, interval=400)
        self._save_timer.timeout.connect(lambda: self.profile.save())
        self._hold_timer = QTimer(self, singleShot=True)
        self._hold_timer.timeout.connect(self.refresh_feedback)
        self._reassert_timer = QTimer(self, singleShot=True)
        self._reassert_timer.timeout.connect(self._reassert_leds)
        self._reassert_late = QTimer(self, singleShot=True)
        self._reassert_late.timeout.connect(self._reassert_leds)
        # La console réécrit parfois ses LED d'elle-même (appui, relâchement) : on renvoie
        # régulièrement l'état réel de toutes les LED, sans toucher aux moteurs.
        self._led_keepalive = QTimer(self, interval=1500)
        self._led_keepalive.timeout.connect(self._reassert_leds)
        self._led_keepalive.start()
        self.set_profile(profile)

        midi.message.connect(self.on_midi)
        midi.status_changed.connect(self._on_midi_status)
        vmix.state_changed.connect(self.on_state)

    # -- profil -------------------------------------------------------------
    def set_profile(self, profile: Profile):
        self.profile = profile
        if migrate_scenes(profile):
            self.touch_profile()
        self.controls: dict[str, ControlDef] = default_controls()
        for cid, ov in profile.midi_overrides.items():
            if cid in self.controls:
                c = self.controls[cid]
                if "midi" in ov:
                    c.midi = MidiId.from_dict(ov["midi"])
                if "touch" in ov:
                    c.touch = MidiId.from_dict(ov["touch"])
        self._rebuild_lookup()
        self._sent.clear()
        self.vmix.configure(profile.vmix_host, profile.vmix_port, profile.poll_ms)
        self.midi.configure(profile.midi_in, profile.midi_out)
        self.mapping_changed.emit("")
        self.resend_all()

    def _rebuild_lookup(self):
        self._lookup: dict[MidiId, tuple[str, str, str]] = {}
        for c in self.controls.values():          # Layer B : table d'usine
            if c.midi_b:
                self._lookup[c.midi_b] = (c.id, "main", "B")
            if c.touch_b:
                self._lookup[c.touch_b] = (c.id, "touch", "B")
        # les contrôles réappris passent en dernier : ils gagnent en cas de doublon
        ordered = sorted(self.controls.values(), key=lambda c: c.id in self.profile.midi_overrides)
        for c in ordered:
            self._lookup[c.midi] = (c.id, "main", "A")
            if c.touch:
                self._lookup[c.touch] = (c.id, "touch", "A")

    def touch_profile(self):
        """À appeler après toute modification : sauvegarde différée."""
        self._save_timer.start()

    def profile_changed(self):
        """Scènes, macros ou ducking modifiés."""
        self.touch_profile()
        self.automation_changed.emit()
        self.mapping_changed.emit("")
        self.refresh_feedback()

    # -- associations ---------------------------------------------------------
    @property
    def pmap(self) -> dict:
        """Associations de la page affichée (Layer A ou B de la console)."""
        return self.profile.mappings if self.page == "A" else self.profile.mappings_b

    def set_page(self, page: str, from_console: bool = False):
        """Change de page. Depuis l'app, on bascule aussi la console (Program Change) ;
        depuis la console (bouton LAYER), elle a déjà basculé."""
        if page not in ("A", "B") or page == self.page:
            return
        self.page = page
        self._enc_last.clear()
        self.touched.clear()
        self.log.emit(f"Page {page}" + (" (bouton LAYER de la console)" if from_console else ""))
        self.mapping_changed.emit("")
        self.page_changed.emit(page)
        if from_console:
            self._sent.clear()
            self.refresh_feedback()
        else:
            self.resend_all()

    def mapping(self, cid: str) -> dict | None:
        return self.pmap.get(cid)

    def action_of(self, cid: str) -> A.Action | None:
        m = self.mapping(cid)
        return A.ACTIONS.get(m.get("action", "")) if m else None

    def set_locked(self, on: bool):
        if on == self.locked:
            return
        self.locked = on
        if on:
            self.cancel_learn()
        self.locked_changed.emit(on)
        self.log.emit("Verrouillé : édition et clics à l'écran bloqués" if on else "Déverrouillé")

    def set_mapping(self, cid: str, mapping: dict | None):
        if self.locked:
            return
        if mapping and mapping.get("action"):
            self.pmap[cid] = mapping
        else:
            self.pmap.pop(cid, None)
        self._sent.pop(cid, None)
        self.touch_profile()
        self.mapping_changed.emit(cid)
        self.refresh_feedback()

    def input_title(self, m: dict) -> str:
        inp = self.state.input(m.get("input", ""))
        return inp.title if inp else (m.get("input_title") or "?")

    def input_missing(self, m: dict) -> bool:
        return bool(m.get("input")) and self.state.online and m["input"] not in self.state.inputs

    def surface_label(self, cid: str) -> tuple[str, str] | None:
        m, a = self.mapping(cid), self.action_of(cid)
        if not a:
            return None
        title = ""
        if A.needs_input(a):
            title = self.input_title(m)
        elif a.key in ("scene_recall", "scene_save"):
            title = (self.scenes.get(m.get("scene", "")) or {}).get("name", "")
        elif a.key == "macro":
            title = (self.macros.get(m.get("macro", "")) or {}).get("name", "")
        elif a.key == "meter":
            src = str(m.get("source", "master"))
            title = "MASTER" if src == "master" else (f"BUS {src[4:]}" if src.startswith("bus:")
                                                      else self.input_title({"input": src}))
        elif "slot" in m:
            effect, dur = self.state.transitions.get(int(m["slot"]), ("", 0))
            title = effect if a.key == "transition_effect" else (f"{dur} ms" if dur else "")
        return a.short(m, title) if a.short else (a.label, "")

    def assign_input(self, cid: str, input_key: str):
        """Glisser-déposer d'une source : association la plus logique."""
        if self.locked:
            return
        c = self.controls[cid]
        inp = self.state.input(input_key)
        title = inp.title if inp else ""
        if c.strip is not None and c.strip < 8:
            self.assign_strip(c.strip, input_key)
            return
        base = {"input": input_key, "input_title": title}
        if c.continuous:
            self.set_mapping(cid, {"action": "volume", **base})
        else:
            current = self.mapping(cid)
            a = A.ACTIONS.get(current.get("action", "")) if current else None
            if a and A.needs_input(a):
                self.set_mapping(cid, {**current, **base})
            else:
                self.set_mapping(cid, {"action": "audio_toggle", **base})

    def assign_ref(self, cid: str, kind: str, ref_id: str):
        """Glisser-déposer d'une scène ou d'une macro sur un bouton."""
        if self.locked:
            return
        c = self.controls[cid]
        if c.continuous:
            self.log.emit(f"Une {'scène' if kind == 'scene' else 'macro'} se place sur un bouton, "
                          f"pas sur « {c.name} »")
            return
        if kind == "scene":
            self.set_mapping(cid, {"action": "scene_recall", "scene": ref_id})
        else:
            self.set_mapping(cid, {"action": "macro", "macro": ref_id})

    def describe(self, m: dict) -> str:
        """Résumé lisible d'une association ou d'une étape de macro."""
        if m.get("action") == "wait":
            return f"⏱  Pause {int(m.get('ms', 0))} ms"
        a = A.ACTIONS.get(m.get("action", ""))
        if not a:
            return "?"
        detail = ""
        if A.needs_input(a):
            detail = self.input_title(m)
        elif a.key in ("scene_recall", "scene_save"):
            detail = (self.scenes.get(m.get("scene", "")) or {}).get("name", "?")
        elif a.key == "macro":
            detail = (self.macros.get(m.get("macro", "")) or {}).get("name", "?")
        elif a.key == "transition":
            detail = m.get("effect", "")
        extra = ""
        if a.key == "volume_set":
            extra = f" → {m.get('level', 80)} %" + (f" en {m['duration']} ms" if m.get("duration") else "")
        elif a.key == "bus_route":
            extra = f" → {A.BUS_NAMES.get(m.get('bus', 'M'), '')}"
        return a.label + (f"  ·  {detail}" if detail else "") + extra

    def assign_strip(self, strip: int, input_key: str):
        if self.locked:
            return
        inp = self.state.input(input_key)
        base = {"input": input_key, "input_title": inp.title if inp else ""}
        ids = strip_controls(strip)
        self.pmap[ids["fader"]] = {"action": "volume", **base}
        self.pmap[ids["button"]] = {"action": "audio_toggle", **base}
        for row, bus in zip(("row1", "row2", "row3"), self.profile.strip_template):
            if bus and bus != "-":
                self.pmap[ids[row]] = {"action": "bus_route", "bus": bus, **base}
        self._sent.clear()
        self.touch_profile()
        self.mapping_changed.emit("")
        self.refresh_feedback()
        self.log.emit(f"Tranche {strip + 1} → {base['input_title'] or input_key}")

    def clear_strip(self, strip: int):
        if self.locked:
            return
        for cid in strip_controls(strip).values():
            self.pmap.pop(cid, None)
        self._sent.clear()
        self.touch_profile()
        self.mapping_changed.emit("")
        self.refresh_feedback()

    # -- apprentissage MIDI ------------------------------------------------------
    def start_learn(self, cid: str):
        if self.locked:
            return
        self.learn_target = cid
        self.learning.emit(cid)

    def cancel_learn(self):
        self._learn_cand = None
        if self.learn_target:
            self.learn_target = ""
            self.learning.emit("")

    def _learn(self, mid: MidiId):
        self._assign_learned(self.learn_target, midi=mid)

    def _learn_fader(self, mid: MidiId, raw: int):
        """Un fader envoie deux choses : le capteur tactile (0 / 127) dès qu'on le touche,
        puis sa position. On écoute les deux et on les apprend ensemble."""
        if self._learn_cand is None:
            self._learn_cand = (mid, {raw})
            QTimer.singleShot(2500, self._learn_fader_timeout)
            return
        first, values = self._learn_cand
        if mid == first:
            values.add(raw)
            return
        if values <= {0, 127}:
            self._assign_learned(self.learn_target, midi=mid, touch=first)
        else:
            self._assign_learned(self.learn_target, midi=first,
                                 touch=mid if raw in (0, 127) else None)

    def _learn_fader_timeout(self):
        if not self.learn_target or self._learn_cand is None:
            return
        first, values = self._learn_cand
        if values <= {0, 127}:
            self._assign_learned(self.learn_target, touch=first)
        else:
            self._assign_learned(self.learn_target, midi=first)

    def _assign_learned(self, cid: str, midi: MidiId | None = None, touch: MidiId | None = None):
        self.learn_target, self._learn_cand = "", None
        c = self.controls[cid]
        ov = self.profile.midi_overrides.setdefault(cid, {})
        if midi is not None:
            for other in self.controls.values():
                if other.id != cid and other.midi == midi:
                    self.log.emit(f"{midi.label()} était utilisé par « {other.name} », réaffecté")
            c.midi = midi
            ov["midi"] = midi.to_dict()
            self.log.emit(f"« {c.name} » appris : {midi.label()}")
        if touch is not None:
            c.touch = touch
            ov["touch"] = touch.to_dict()
            self.log.emit(f"Capteur tactile de « {c.name} » appris : {touch.label()}")
        self._rebuild_lookup()
        self.touch_profile()
        self.learning.emit("")
        self.mapping_changed.emit(cid)

    def reset_midi(self, cid: str):
        if self.locked:
            return
        self.profile.midi_overrides.pop(cid, None)
        d = default_controls()[cid]
        self.controls[cid].midi, self.controls[cid].touch = d.midi, d.touch
        self._rebuild_lookup()
        self.touch_profile()
        self.mapping_changed.emit(cid)

    # -- entrées -------------------------------------------------------------------
    def on_midi(self, msg: mido.Message):
        self.stats["midi_in"] += 1
        if self.console_mode == "mc":
            return   # la console pilote un autre logiciel : on ne déclenche rien
        dec = decode(msg)
        if dec is None:
            return
        mid, value = dec
        if self.learn_target:
            if self.controls[self.learn_target].kind == FADER:
                self._learn_fader(mid, round(value * 127))
            elif mid.type != "note" or value > 0:
                # un relâchement (vélocité 0) ne sert pas à apprendre un bouton
                self._learn(mid)
            return
        ref = self._lookup.get(mid)
        if ref is None:
            if mid.type == "pitch" and not self._mc_warned and self.console_mode == "standard":
                # les faders envoient du pitch bend : la console est en mode Mackie (MC)
                self._mc_warned = True
                self.log.emit("La X-Touch semble en mode Mackie (MC) : Réglages → Mode de la console "
                              "→ Mode standard")
            if self.diagnostic:
                self.diag_unknown.emit(mid)
            self.log.emit(f"MIDI non associé : {mid.label()} = {round(value * 127)}")
            return
        cid, role, page = ref
        if cid == "exp" and not self._pedal_ok:
            # sans pédale branchée, la console envoie du bruit (0 / 1) sur ce CC
            if round(value * 127) < 4:
                return
            self._pedal_ok = True
        if self.diagnostic:
            self.activity.emit(cid)
            self.diag_input.emit(cid, role, page)
            return
        if page != self.page:
            self.set_page(page, from_console=True)
        if role == "touch":
            if value > 0:
                self.touched.add(cid)
                self._touch_start[cid] = time.monotonic()
            else:
                self.touched.discard(cid)
                self._hold_timer.start(self.profile.hold_ms)
                if self._last_move.get(cid, 0) >= self._touch_start.get(cid, float("inf")):
                    # fin d'un geste : la position finale est renvoyée deux fois, au cas où vMix
                    # aurait laissé passer la dernière commande d'une rafale
                    self._resend_final(cid)
                    QTimer.singleShot(FINAL_RESEND_MS, lambda cid=cid: self._resend_final(cid))
            self.activity.emit(cid)
            self.changed.emit()
            return
        self.activity.emit(cid)
        if self.controls[cid].kind == ENCODER:
            self._encoder_midi(cid, round(value * 127))
        else:
            self.handle(cid, value)

    def _encoder_midi(self, cid: str, raw: int):
        """Les encodeurs envoient une valeur absolue 0-127 tenue par la console, qu'on ne peut
        pas repositionner. On n'utilise donc que le sens et le nombre de crans : pas de saut
        quand le niveau a changé ailleurs. En butée, la console répète 0 ou 127.
        À distance, l'anneau n'affiche qu'une LED : un curseur au niveau vMix."""
        last = self._enc_last.get(cid)
        self._enc_last[cid] = raw
        delta = raw - last if last is not None else 0
        if delta == 0:
            delta = 1 if raw == 127 else -1 if raw == 0 else 0
        if delta:
            self.turn(cid, delta)

    def turn(self, cid: str, delta: int):
        """Rotation d'un encodeur de `delta` crans (console ou molette à l'écran)."""
        a, m = self.action_of(cid), self.mapping(cid)
        if a and a.step:
            self._run(a, m, max(-3, min(3, delta)))
            self.changed.emit()
        else:
            self.handle(cid, min(1.0, max(0.0, self.values.get(cid, 0.0) + delta * ENC_STEP)))

    def handle(self, cid: str, value: float, from_ui: bool = False):
        """Valeur d'un contrôle, venant de la console ou de la surface à l'écran (from_ui)."""
        if from_ui and self.locked:
            return
        c = self.controls[cid]
        a, m = self.action_of(cid), self.mapping(cid)
        if a and a.step:
            return  # un encodeur cranté ne se pilote que par crans (turn)
        if c.continuous:
            self.values[cid] = value
            self._last_move[cid] = time.monotonic()
            if c.kind == FADER:
                if from_ui and cid not in self.touched:
                    self._send_out(c, round(value * 127))   # le moteur suit la souris
                else:
                    self._sent[cid] = round(value * 127)    # le fader physique est déjà là
            elif c.kind == ENCODER:
                self._sent.pop(cid, None)               # la console a redessiné l'anneau
                self._send_out(c, self._ring_out(cid, a, value if a else 0.0))
            if a and a.continuous:
                self._run(a, m, value)
                key = self._own_key(a, m)
                if key:
                    self._own[key] = value * 100
                    # tant que vMix n'a pas confirmé cette valeur, son état relu est périmé
                    self._pending[cid] = time.monotonic() + CONFIRM_TIMEOUT
                if a.key == "volume":
                    self.ducker.user_moved(m.get("input", ""))
                    self.fades.cancel(m.get("input", ""))   # la main reprend un fondu en cours
            self._hold_timer.start(self.profile.hold_ms)
            self.changed.emit()
            return
        pressed = value > 0
        # La console change elle-même ses LED quand on appuie (et parfois celle d'un autre
        # bouton du transport) : on oublie ce qu'on croit lui avoir envoyé et on réaffirme
        # l'état réel juste après l'appui et le relâchement.
        for other in (TRANSPORT_IDS if cid in TRANSPORT_IDS else (cid,)):
            self._sent.pop(other, None)
        self._reassert_timer.start(80)
        self._reassert_late.start(400)
        if pressed and a and not a.continuous:
            self._run(a, m, 1.0)
        if a is None or not self._has_feedback(a):
            # pas de retour d'état : la LED suit simplement l'appui
            self.leds[cid] = pressed and a is not None
            self._send_led(c, self.leds[cid])
        if cid.startswith("push"):
            # l'appui d'encodeur n'a pas de LED : son état peut s'afficher sur l'anneau
            enc = cid.replace("push", "enc")
            self._sent.pop(enc, None)
            if self.ring_mode(enc) != "cursor":
                self.refresh_feedback()
        self.changed.emit()

    def _resend_final(self, cid: str):
        """Renvoie à vMix la dernière position donnée par un fader (fin de geste)."""
        a, m = self.action_of(cid), self.mapping(cid)
        key = self._own_key(a, m) if a else None
        own = self._own.get(key) if key else None
        if own is not None:
            self._run(a, m, own / 100)

    def recently_moved(self, cid: str, within: float = 0.3) -> bool:
        return cid in self.touched or time.monotonic() - self._last_move.get(cid, 0) < within

    def test(self, cid: str):
        if self.locked:
            return
        a, m = self.action_of(cid), self.mapping(cid)
        if a and a.step:
            self._run(a, m, 1)
        elif a:
            self._run(a, m, self.values.get(cid, 0.75) if a.continuous else 1.0)

    def execute(self, step: dict, depth: int = 0):
        """Exécute une étape de macro (mêmes données qu'une association)."""
        a = A.ACTIONS.get(step.get("action", ""))
        if a:
            self._run(a, step, 1.0, depth)

    def _run(self, a: A.Action, m: dict, value: float, depth: int = 0):
        if a.engine:
            if a.key == "scene_recall":
                self.scenes.recall(m.get("scene", ""))
            elif a.key == "scene_save":
                self.scenes.update(m.get("scene", ""))
            elif a.key == "macro":
                self.macros.run(m.get("macro", ""), depth)
            elif a.key == "ducking_toggle":
                self.ducker.set_enabled(not self.ducker.cfg["enabled"])
            return
        try:
            cmds = a.run(m, value, self.state)
        except Exception as e:  # paramètre manquant, etc.
            self.log.emit(f"Action « {a.label} » impossible : {e}")
            return
        for fn, params, key in cmds:
            if fn == "SetVolumeFade" and params.get("Input"):
                # fondu fait par MixPanel (SetVolume pas à pas) : fiable quelle que soit la version de vMix
                lvl, _, ms = str(params.get("Value", "")).partition(",")
                try:
                    self.fade_volume(params["Input"], float(lvl), int(float(ms or 0)))
                    continue
                except ValueError:
                    pass
            self.vmix.send(fn, params, key)

    def fade_volume(self, input_key: str, position: float, ms: int):
        """Amène le volume d'une source à `position` (0-100, échelle des faders) en `ms`."""
        self.fades.start(input_key, position, ms)

    # -- retours ------------------------------------------------------------------
    def on_state(self, state: VmixState):
        prev_online = self.state.online
        self.state = state
        relinked = self._relink_by_title()
        if state.online != prev_online or relinked:
            self.mapping_changed.emit("")
        self.ducker.tick(state)
        self.refresh_feedback()

    def _relink_by_title(self) -> bool:
        """Une source introuvable (nouveau preset vMix, source recréée…) est
        retrouvée automatiquement par son nom."""
        if not self.state.online:
            return False
        by_title: dict[str, list] = {}
        for inp in self.state.inputs.values():
            by_title.setdefault(inp.title, []).append(inp)
        changed = False
        pages = list(self.profile.mappings.items()) + list(self.profile.mappings_b.items())
        for cid, m in pages:
            key = m.get("input")
            if not key or key in self.state.inputs:
                continue
            match = by_title.get(m.get("input_title", ""), [])
            if len(match) == 1:
                m["input"] = match[0].key
                changed = True
                self.log.emit(f"« {m['input_title']} » retrouvée par son nom ({self.controls[cid].name})")
        if changed:
            self._sent.clear()
            self.touch_profile()
        return changed

    def refresh_feedback(self):
        now = time.monotonic()
        hold = self.profile.hold_ms / 1000
        for cid, c in self.controls.items():
            try:
                self._feedback_one(cid, c, now, hold)
            except Exception as e:   # un contrôle en erreur ne doit pas bloquer les autres retours
                if cid not in self._fb_errors:
                    self._fb_errors.add(cid)
                    self.log.emit(f"Retour impossible pour « {c.name} » : {e!r}")
        self.changed.emit()

    def _feedback_one(self, cid: str, c: ControlDef, now: float, hold: float):
        """Calcule et envoie le retour d'un contrôle (moteur, LED ou anneau)."""
        a, m = self.action_of(cid), self.mapping(cid)
        fb = None
        if a and a.step:
            fb = 0.0                 # encodeur cranté : anneau éteint
        elif a and a.engine:
            fb = self._engine_feedback(a, m)
        elif a and a.feedback:
            try:
                fb = self._reconcile(a, m, a.feedback(m, self.state))
            except Exception as e:
                fb = None
                if cid not in self._fb_errors:
                    self._fb_errors.add(cid)
                    self.log.emit(f"État vMix illisible pour « {c.name} » ({a.label}) : {e!r}")
        meter = self._ring_meter(c, a, fb)
        if meter is not None:
            fb = meter
        if c.kind == ENCODER:
            self._send_ring_mode(c, a)
        if c.continuous:
            if fb is None:
                if c.kind == ENCODER and (a is None or self.ring_mode(cid) != "cursor"):
                    self._send_out(c, self._ring_out(cid, a, 0.0))   # état de l'appui, ou éteint
                return
            if c.kind == FADER and (cid in self.touched or now - self._last_move.get(cid, 0) < hold):
                return                     # ne pas lutter contre la main
            if cid in self._pending:
                if self._confirmed(a, m):
                    self._pending.pop(cid)   # vMix a appliqué notre dernière valeur
                    self._resent.pop(cid, None)
                elif now < self._pending[cid]:
                    # Pas encore confirmé : soit l'état relu est périmé, soit vMix n'a pas pris la
                    # dernière commande d'une rafale. Le geste fait foi : on renvoie la position
                    # du fader plutôt que de déplacer le moteur vers une mauvaise valeur.
                    last = self._resent.get(cid, 0.0)
                    if now - self._last_move.get(cid, 0) > hold and now - last > RESEND_EVERY:
                        if not last:
                            self.log.emit(f"vMix n'a pas encore pris la position de « {c.name} » : renvoi")
                        self._resent[cid] = now
                        own = self._own.get(self._own_key(a, m))
                        if own is not None:
                            self._run(a, m, own / 100)
                    return
                else:
                    # jamais confirmé. Écart d'un pas ou moins (précision du XML) : le fader reste là.
                    self._pending.pop(cid)
                    self._resent.pop(cid, None)
                    own = self._own.get(self._own_key(a, m))
                    if own is not None and fb is not None:
                        if abs(round(own / 100 * 127) - round(float(fb) * 127)) <= 1:
                            self.values[cid] = own / 100
                            return
                        self.log.emit(f"vMix n'a pas confirmé « {c.name} » : fader {_db(own)}, "
                                      f"vMix {_db(float(fb) * 100)} → le fader suit vMix")
            if not (a and a.step):
                self.values[cid] = float(fb)
            if c.kind == FADER:
                raw = round(float(fb) * 127)
                cur = self._sent.get(cid)
                if isinstance(cur, int) and abs(raw - cur) <= 1:
                    return   # déjà en place à un pas près : pas de micro-mouvement du moteur
                self._send_out(c, raw)
            else:
                self._send_out(c, self._ring_out(cid, a, float(fb)))
        else:
            if a is not None and not self._has_feedback(a):
                return  # LED momentanée gérée à l'appui
            on = bool(fb)
            self.leds[cid] = on
            blink = on and a is not None and a.key == "macro"
            self._send_led(c, LED_BLINK if blink else LED_ON if on else LED_OFF)

    # -- volumes : précision perdue par vMix -------------------------------------------------
    @staticmethod
    def _own_key(a: A.Action, m: dict):
        if a.key == "volume":
            return ("in", m.get("input", ""))
        if a.key == "master_volume":
            return ("master",)
        if a.key == "bus_volume":
            return ("bus", m.get("bus", "A"))
        return None

    def _confirmed(self, a: A.Action, m: dict) -> bool:
        """vMix publie-t-il exactement (à l'arrondi près) la dernière valeur qu'on lui a envoyée ?"""
        key = self._own_key(a, m) if a else None
        own = self._own.get(key) if key else None
        if own is None:
            return True
        amp = self._state_amp(key)
        sent = round(own, 1)   # la valeur part arrondie au dixième (« 49.6 ») : c'est elle que vMix applique
        return amp is not None and abs(amp - round((sent / 100) ** 4 * 100, 2)) <= 0.011

    def _state_amp(self, key: tuple):
        st = self.state
        if key[0] == "in":
            inp = st.input(key[1])
            return inp.volume_amp if inp else None
        if key[0] == "master":
            return st.master.volume_amp if st.online else None
        bus = st.buses.get(key[1])
        return bus.volume_amp if bus else None

    def _reconcile(self, a: A.Action, m: dict, fb):
        """vMix publie le volume en amplitude arrondie à 2 décimales : tout en bas de la course,
        la position relue est approximative (fader à 8 % relu « 0 »), et le moteur se « corrigeait »
        de quelques mm après un geste rapide. Si vMix affiche exactement l'arrondi de la position
        qu'on vient de lui envoyer, on garde notre valeur exacte."""
        key = self._own_key(a, m)
        own = self._own.get(key) if key else None
        if own is None or fb is None:
            return fb
        return own / 100 if self._confirmed(a, m) else fb

    @staticmethod
    def _has_feedback(a: A.Action) -> bool:
        return a.feedback is not None or a.key in ("scene_recall", "macro", "ducking_toggle")

    def _engine_feedback(self, a: A.Action, m: dict):
        if a.key == "scene_recall":
            return self.scenes.matches(m.get("scene", ""))
        if a.key == "macro":
            return m.get("macro", "") in self.macros.running
        if a.key == "ducking_toggle":
            return bool(self.ducker.cfg["enabled"])
        return None

    def is_meter_ring(self, cid: str) -> bool:
        c, a = self.controls[cid], self.action_of(cid)
        if c.kind != ENCODER or self.ring_mode(cid) != "cursor":
            return False
        return ((a is not None and a.key == "meter") or
                (a is None and self.profile.ring_meters and c.strip is not None and c.strip < 8))

    # -- anneau LED : curseur (rotation) ou état de l'appui -----------------------------------
    RING_MODES = ("auto", "cursor", "state", "state_blink", "off")

    def ring_mode(self, cid: str) -> str:
        """Ce qu'affiche l'anneau d'un encodeur. « Auto » : curseur si une action de rotation
        est associée, sinon l'état de l'appui (les appuis d'encodeur n'ont pas de LED)."""
        mode = self.profile.ring_modes.get(f"{self.page}:{cid}", "auto")
        if mode != "auto":
            return mode
        if self.action_of(cid) is None and self.action_of(cid.replace("enc", "push")) is not None:
            return "state"
        return "cursor"

    def set_ring_mode(self, cid: str, mode: str):
        key = f"{self.page}:{cid}"
        if mode == "auto":
            self.profile.ring_modes.pop(key, None)
        else:
            self.profile.ring_modes[key] = mode
        self._sent.pop(cid, None)
        self.touch_profile()
        self.mapping_changed.emit(cid)
        self.refresh_feedback()

    def push_state(self, cid: str) -> bool:
        """État de l'action associée à l'appui de l'encodeur `cid` (encN)."""
        push = cid.replace("enc", "push")
        a, m = self.action_of(push), self.mapping(push)
        if a is None:
            return False
        if a.engine:
            return bool(self._engine_feedback(a, m))
        if a.feedback:
            try:
                return bool(a.feedback(m, self.state))
            except Exception:
                return False
        return bool(self.leds.get(push))   # action sans état : allumé pendant l'appui

    def _ring_out(self, cid: str, a: A.Action | None, v: float) -> int:
        mode = self.ring_mode(cid)
        if mode == "off":
            return 0
        if mode in ("state", "state_blink"):
            # 27 = tout l'anneau allumé, 28 = tout l'anneau clignotant, 0 = éteint
            return (28 if mode == "state_blink" else 27) if self.push_state(cid) else 0
        return ring_value(v)

    def _ring_meter(self, c: ControlDef, a: A.Action | None, fb) -> float | None:
        """Anneau LED en vu-mètre : action « Vu-mètre », ou encodeur libre d'une tranche
        si l'option est activée. Retombée douce pour rester lisible."""
        if not self.is_meter_ring(c.id):
            return None
        if a is not None:
            target = fb
        else:
            fm = self.mapping(f"fader{c.strip + 1}") or {}
            inp = self.state.input(fm.get("input", ""))
            target = meter_norm(0.0 if inp.muted else inp.meter) if inp and inp.has_audio else 0.0
        if target is None:
            return None
        cur = max(float(target), self._ring.get(c.id, 0.0) - 0.12)
        self._ring[c.id] = cur
        return cur

    # -- envoi vers la console (canal global, numéros de retour) ---------------------------------
    def _tx(self, msg: mido.Message):
        self.stats["midi_out"] += 1
        if self.log_midi_out:
            self.log.emit(f"→ X-Touch  {_describe(msg)}")
        self.midi.send(msg)

    @property
    def _out_ch(self) -> int:
        return max(1, min(16, int(self.profile.global_channel))) - 1

    def _send_out(self, c: ControlDef, raw: int, key: str | None = None):
        """Moteur de fader ou valeur d'anneau, sans renvoyer une valeur déjà envoyée."""
        if c.out is None or self.console_mode == "mc":
            return
        key = key or c.id
        if self._sent.get(key) == raw:
            return
        self._sent[key] = raw
        self._tx(mido.Message("control_change", channel=self._out_ch, control=c.out.num, value=raw))


    def _send_ring_mode(self, c: ControlDef, a: A.Action | None):
        # À distance, la console n'allume qu'une LED (1-13) quel que soit le mode : le mode ne règle
        # que son propre dessin pendant qu'on tourne. En « Single », cet éclair local est aussi un point.
        mode = RING_SINGLE
        key = f"{c.id}:mode"
        if c.ring_mode is None or self.console_mode == "mc" or self._sent.get(key) == mode:
            return
        self._sent[key] = mode
        self._tx(mido.Message("control_change", channel=self._out_ch, control=c.ring_mode, value=mode))

    def _send_led(self, c: ControlDef, state: int | bool):
        state = int(state)
        if c.out is None or self.console_mode == "mc" or self._sent.get(c.id) == ("led", state):
            return
        self._sent[c.id] = ("led", state)
        if c.out.type == "cc":   # LED du footswitch
            self._tx(mido.Message("control_change", channel=self._out_ch, control=c.out.num,
                                        value=127 if state else 0))
            return
        vel = {LED_OFF: self.profile.led_off, LED_ON: self.profile.led_on,
               LED_BLINK: self.profile.led_blink}[state]
        self._tx(mido.Message("note_on", channel=self._out_ch, note=c.out.num, velocity=vel))

    def set_console_mode(self, mode: str):
        """Bascule la console en mode MIDI standard (celui de MixPanel) ou en mode Mackie
        Control (pour piloter un autre logiciel, ex. un logiciel de musique)."""
        mc = mode == "mc"
        self._tx(mido.Message("control_change", channel=self._out_ch, control=127, value=1 if mc else 0))
        self.console_mode = "mc" if mc else "standard"
        self._mc_warned = False
        self.log.emit("X-Touch passée en mode Mackie (MC) : elle ne répond plus à MixPanel"
                      if mc else "X-Touch passée en mode MIDI standard")
        self.console_mode_changed.emit(self.console_mode)
        if not mc:
            QTimer.singleShot(300, self.resend_all)   # laisser la console changer de mode

    def _reassert_leds(self):
        """Oublie l'état des LED qu'on croit avoir envoyé et renvoie l'état réel."""
        for cid, c in self.controls.items():
            if not c.continuous and isinstance(self._sent.get(cid), tuple):
                self._sent.pop(cid, None)
        self.refresh_feedback()

    def resend_all(self):
        """Renvoie tout l'état à la console (après connexion ou changement de réglages)."""
        self._sent.clear()
        if self.console_mode == "mc":
            return   # console volontairement en Mackie pour un autre logiciel : ne rien lui envoyer
        if self.profile.force_standard:
            # si un autre logiciel l'a laissée en mode MC, la console repasse en mode standard
            self._tx(mido.Message("control_change", channel=self._out_ch, control=127, value=0))
        # sélection du Layer : la console le demande au démarrage pour des valeurs fiables
        self._tx(mido.Message("program_change", channel=self._out_ch,
                                    program=0 if self.page == "A" else 1))
        self.refresh_feedback()

    def _on_midi_status(self, connected: bool, name: str):
        self.log.emit(f"X-Touch {'connectée : ' + name if connected else 'déconnectée'}")
        if connected:
            self._enc_last.clear()
            self._pedal_ok = False
            self.resend_all()
        self.changed.emit()
