"""Automatisations : scènes audio, macros et ducking."""
from __future__ import annotations

import time
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

from .vmix import VmixState, amp_to_db, xml_to_position

DUCK_DEFAULTS = {
    "enabled": False,
    "triggers": [],        # [{"input": clé, "input_title": nom}] : les micros
    "targets": [],         # sources à baisser (musique...)
    "threshold_db": -38,   # au-dessus : quelqu'un parle
    "depth_db": -12,       # baisse appliquée à la musique, en dB
    "attack_ms": 250,      # durée de la baisse
    "hold_ms": 700,        # silence avant de remonter
    "release_ms": 1500,    # durée de la remontée
}
MAX_MACRO_DEPTH = 4


def migrate_scenes(profile) -> bool:
    """Scènes enregistrées avant la 1.0.5 : volumes en amplitude (échelle du XML vMix).
    On les convertit une fois en position de fader, l'échelle de SetVolume."""
    changed = False
    for s in profile.scenes:
        if s.get("scale") == "position":
            continue
        for snap in list(s.get("inputs", {}).values()) + [s.get("master", {})] + list(s.get("buses", {}).values()):
            if "volume" in snap:
                snap["volume"] = round(xml_to_position(float(snap["volume"])), 2)
        s["scale"] = "position"
        changed = True
    return changed


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def resolve(state: VmixState, key: str, title: str = ""):
    """Source actuelle par sa clé, sinon par son nom (preset vMix rechargé...)."""
    inp = state.input(key)
    if inp is None and title:
        found = [i for i in state.inputs.values() if i.title == title]
        inp = found[0] if len(found) == 1 else None
    return inp


def _fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".")


# --- Scènes -------------------------------------------------------------------------


class SceneBook:
    def __init__(self, engine):
        self.e = engine

    @property
    def scenes(self) -> list:
        return self.e.profile.scenes

    def get(self, sid: str) -> dict | None:
        return next((s for s in self.scenes if s["id"] == sid), None)

    def _snapshot(self) -> dict:
        st, ducker = self.e.state, self.e.ducker
        inputs = {}
        for inp in st.ordered_inputs():
            if inp.has_audio:
                inputs[inp.key] = {"title": inp.title,
                                   "volume": ducker.base.get(inp.key, inp.volume),
                                   "muted": inp.muted, "buses": sorted(inp.buses)}
        return {"inputs": inputs,
                "master": {"volume": st.master.volume, "muted": st.master.muted},
                "buses": {b: {"volume": bs.volume, "muted": bs.muted} for b, bs in st.buses.items()}}

    def capture(self, name: str | None = None) -> dict | None:
        if not self.e.state.online:
            return None
        scene = {"id": new_id(), "name": name or f"Scène {len(self.scenes) + 1}", "fade_ms": 0,
                 "scale": "position", **self._snapshot()}
        self.scenes.append(scene)
        self.e.profile_changed()
        self.e.log.emit(f"Scène « {scene['name']} » enregistrée ({len(scene['inputs'])} sources)")
        return scene

    def update(self, sid: str):
        s = self.get(sid)
        if s and self.e.state.online:
            s.update(self._snapshot())
            self.e.profile_changed()
            self.e.log.emit(f"Scène « {s['name']} » mise à jour")

    def delete(self, sid: str):
        self.e.profile.scenes = [s for s in self.scenes if s["id"] != sid]
        self.e.profile_changed()

    def recall(self, sid: str):
        s = self.get(sid)
        if not s:
            return
        st, send, fade = self.e.state, self.e.vmix.send, int(s.get("fade_ms", 0))
        existing = ["M"] + sorted(st.buses)
        for key, snap in s["inputs"].items():
            inp = resolve(st, key, snap.get("title", ""))
            if inp is None:
                continue
            vol = float(snap["volume"])
            if self.e.ducker.rebase(inp.key, vol):
                pass  # musique baissée : elle remontera au niveau de la scène
            elif abs(inp.volume - vol) > 0.3:
                self.e.fade_volume(inp.key, vol, fade)
            if inp.muted != snap["muted"]:
                send("AudioOff" if snap["muted"] else "AudioOn", {"Input": inp.key})
            want = set(snap.get("buses", []))
            for bus in existing:
                if (bus in want) != (bus in inp.buses):
                    send("AudioBusOn" if bus in want else "AudioBusOff", {"Input": inp.key, "Value": bus})
        m = s.get("master", {})
        if m:
            if abs(st.master.volume - m["volume"]) > 0.3:
                send("SetMasterVolume", {"Value": _fmt(m["volume"])})
            if st.master.muted != m["muted"]:
                send("MasterAudioOff" if m["muted"] else "MasterAudioOn")
        for bus, b in s.get("buses", {}).items():
            cur = st.buses.get(bus)
            if cur is None:
                continue
            if abs(cur.volume - b["volume"]) > 0.3:
                send(f"SetBus{bus}Volume", {"Value": _fmt(b["volume"])})
            if cur.muted != b["muted"]:
                send("BusXAudioOff" if b["muted"] else "BusXAudioOn", {"Value": bus})
        self.e.vmix.request_refresh()
        self.e.log.emit(f"Scène « {s['name']} » rappelée" + (f" (fondu {fade} ms)" if fade else ""))

    def matches(self, sid: str) -> bool:
        """La console est-elle actuellement dans cette scène ? (LED du bouton)"""
        s, st = self.get(sid), self.e.state
        if not s or not st.online:
            return False
        for key, snap in s["inputs"].items():
            inp = resolve(st, key, snap.get("title", ""))
            if inp is None:
                continue
            vol = self.e.ducker.base.get(inp.key, inp.volume)
            if abs(vol - snap["volume"]) > 1.0 or inp.muted != snap["muted"]:
                return False
            known = {"M"} | set(st.buses)
            if (set(snap.get("buses", [])) & known) != (inp.buses & known):
                return False
        m = s.get("master")
        if m and (abs(st.master.volume - m["volume"]) > 1.0 or st.master.muted != m["muted"]):
            return False
        return True


# --- Fondus de volume ----------------------------------------------------------------


class SoftFades(QObject):
    """Fondus de volume faits par MixPanel : une suite de SetVolume (~25 par seconde),
    la commande qu'utilisent déjà les faders. On ne dépend pas de SetVolumeFade, qui
    selon les versions de vMix (et l'encodage de sa virgule) ne fait rien."""
    TICK_MS = 40

    def __init__(self, engine):
        super().__init__()
        self.e = engine
        self.active: dict[str, tuple] = {}   # clé -> (départ, arrivée, t0, durée s)
        self._timer = QTimer(self, interval=self.TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self, key: str, target: float, ms: int):
        inp = self.e.state.input(key)
        if ms <= 0 or inp is None:
            self.active.pop(key, None)
            self._set(key, target)
            return
        self.active[key] = (inp.volume, float(target), time.monotonic(), ms / 1000)
        self._tick()
        self._timer.start()

    def cancel(self, key: str):
        self.active.pop(key, None)

    def _set(self, key: str, pos: float):
        self.e.vmix.send("SetVolume", {"Input": key, "Value": _fmt(max(0.0, min(100.0, pos)))}, f"vol:{key}")

    def _tick(self):
        now = time.monotonic()
        for key, (a, b, t0, dur) in list(self.active.items()):
            t = min(1.0, (now - t0) / dur)
            self._set(key, a + (b - a) * t)
            if t >= 1.0:
                self.active.pop(key, None)
        if not self.active:
            self._timer.stop()


# --- Macros -------------------------------------------------------------------------


class MacroRunner:
    def __init__(self, engine):
        self.e = engine
        self.running: dict[str, int] = {}   # id -> étapes restantes

    @property
    def macros(self) -> list:
        return self.e.profile.macros

    def get(self, mid: str) -> dict | None:
        return next((m for m in self.macros if m["id"] == mid), None)

    def create(self, name: str | None = None) -> dict:
        m = {"id": new_id(), "name": name or f"Macro {len(self.macros) + 1}", "steps": []}
        self.macros.append(m)
        self.e.profile_changed()
        return m

    def delete(self, mid: str):
        self.e.profile.macros = [m for m in self.macros if m["id"] != mid]
        self.e.profile_changed()

    def duplicate(self, mid: str) -> dict | None:
        src = self.get(mid)
        if not src:
            return None
        m = {"id": new_id(), "name": f"{src['name']} (copie)",
             "steps": [dict(st) for st in src["steps"]]}
        self.macros.append(m)
        self.e.profile_changed()
        return m

    def run(self, mid: str, depth: int = 0):
        macro = self.get(mid)
        if macro:
            self.run_steps(mid, macro["name"], macro["steps"], depth)

    def run_steps(self, mid: str, name: str, steps: list, depth: int = 0):
        if depth > MAX_MACRO_DEPTH:
            self.e.log.emit(f"Macro « {name} » : trop d'imbrications, arrêt")
            return
        t, plan = 0, []
        for step in steps:
            if step.get("action") == "wait":
                t += int(step.get("ms", 0))
            else:
                plan.append((t, step))
        if not plan:
            return
        self.running[mid] = len(plan)
        self.e.refresh_feedback()
        self.e.log.emit(f"Macro « {name} » : {len(plan)} étape(s)")
        for delay, step in plan:
            QTimer.singleShot(delay, lambda s=step: self._do(mid, s, depth))

    def _do(self, mid: str, step: dict, depth: int):
        self.e.execute(step, depth + 1)
        left = self.running.get(mid, 1) - 1
        if left <= 0:
            self.running.pop(mid, None)
        else:
            self.running[mid] = left
        self.e.refresh_feedback()


# --- Ducking ------------------------------------------------------------------------


class Ducker(QObject):
    changed = Signal()

    def __init__(self, engine):
        super().__init__()
        self.e = engine
        self.active = False
        self.level_db = -120.0
        self.base: dict[str, float] = {}      # source baissée -> niveau à restaurer
        self._expected: dict[str, float] = {}  # source baissée -> niveau bas attendu
        self._settle = 0.0                     # fin du fondu de baisse
        self._last_voice = 0.0

    @property
    def cfg(self) -> dict:
        d = self.e.profile.ducking
        for k, v in DUCK_DEFAULTS.items():
            d.setdefault(k, list(v) if isinstance(v, list) else v)
        return d

    def set_enabled(self, on: bool):
        self.cfg["enabled"] = bool(on)
        if not on and self.active:
            self.release()
        self.e.profile_changed()
        self.changed.emit()

    def tick(self, st: VmixState):
        cfg = self.cfg
        level = -120.0
        for t in cfg["triggers"]:
            inp = resolve(st, t.get("input", ""), t.get("input_title", ""))
            if inp and inp.has_audio and not inp.muted:
                level = max(level, amp_to_db(inp.meter))
        self.level_db = level
        if not cfg["enabled"] or not st.online:
            if self.active:
                self.release()
            self.changed.emit()
            return
        now = time.monotonic()
        if level >= cfg["threshold_db"]:
            self._last_voice = now
        want = now - self._last_voice < cfg["hold_ms"] / 1000
        if self.active and now > self._settle:
            self._detect_manual(st)
        if want and not self.active:
            self.duck(st)
        elif not want and self.active:
            self.release()
        self.changed.emit()

    def duck(self, st: VmixState):
        cfg = self.cfg
        self.active = True
        for t in cfg["targets"]:
            inp = resolve(st, t.get("input", ""), t.get("input_title", ""))
            if not inp or not inp.has_audio:
                continue
            self.base[inp.key] = inp.volume
            self._lower(inp.key, inp.volume)
        self.e.log.emit("Ducking : musique baissée")

    def _lower(self, key: str, vol: float):
        cfg = self.cfg
        # position vMix : dB = 80·log10(pos/100), donc -X dB = position × 10^(-X/80)
        low = vol * 10 ** (min(0.0, float(cfg["depth_db"])) / 80)
        self._expected[key] = low
        self._settle = time.monotonic() + cfg["attack_ms"] / 1000 + 0.6
        self.e.fade_volume(key, low, int(cfg["attack_ms"]))

    def _detect_manual(self, st: VmixState):
        """Volume changé ailleurs (dans vMix, par une macro…) pendant la baisse :
        on respecte ce nouveau niveau et on ne le restaurera pas."""
        for key in list(self.base):
            inp = st.input(key)
            if inp and abs(inp.volume - self._expected.get(key, inp.volume)) > 2:
                self.base.pop(key, None)
                self._expected.pop(key, None)
                self.e.log.emit(f"Ducking : « {inp.title} » réglée à la main, niveau conservé")

    def release(self):
        rel = int(self.cfg["release_ms"])
        for key, vol in self.base.items():
            self.e.fade_volume(key, vol, rel)
        if self.base:
            self.e.log.emit("Ducking : musique rétablie")
        self.base.clear()
        self._expected.clear()
        self.active = False

    def user_moved(self, key: str):
        """Le fader d'une source baissée a été bougé à la main : on n'y touche plus."""
        self.base.pop(key, None)
        self._expected.pop(key, None)

    def rebase(self, key: str, vol: float) -> bool:
        """Une scène fixe le niveau d'une source baissée : il sera restauré à la fin."""
        if key in self.base:
            self.base[key] = vol
            self._lower(key, vol)   # reste baissée, mais par rapport au niveau de la scène
            return True
        return False
