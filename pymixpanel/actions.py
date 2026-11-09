"""Catalogue des actions vMix associables aux contrôles.

Une action continue (fader, encodeur, pédale) reçoit une valeur 0..1 ;
une action « déclencheur » (bouton) s'exécute à l'appui. Chaque action sait
aussi lire son état dans vMix pour allumer la LED / placer le fader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .vmix import BUSES, VmixState, meter_norm

AUDIO, ROUTING, VIDEO, PROD, AUTO, CUSTOM = "audio", "routing", "video", "prod", "auto", "custom"

CATEGORIES = {
    AUDIO: ("Audio", "#2dd4bf"),
    ROUTING: ("Bus", "#a78bfa"),
    VIDEO: ("Vidéo", "#fbbf24"),
    PROD: ("Production", "#fb7185"),
    AUTO: ("Scènes & macros", "#a3e635"),
    CUSTOM: ("Avancé", "#60a5fa"),
}

TRANSITIONS = ["Cut", "Fade", "Merge", "Wipe", "Slide", "Fly", "CrossZoom", "FlyRotate",
               "Cube", "CubeZoom", "VerticalWipe", "VerticalSlide", "Zoom", "Stinger1",
               "Stinger2", "Transition1", "Transition2", "Transition3", "Transition4"]

BUS_NAMES = {"M": "Master", **{b: f"Bus {b}" for b in BUSES}}


@dataclass
class Param:
    name: str
    label: str
    type: str          # input | bus | route | transition | duration | overlay | slot | level
                       # | scene | macro | meter_source | text
    default: object = None


@dataclass
class Action:
    key: str
    label: str
    category: str
    continuous: bool
    params: list
    run: Callable      # (p, value, state) -> list[(fonction, params, clé de fusion)]
    feedback: Callable | None = None   # (p, state) -> float | bool | None
    short: Callable | None = None      # (p, titre) -> (ligne principale, ligne secondaire)
    help: str = ""
    step: bool = False   # encodeur cranté : run() reçoit +n / -n crans au lieu d'une valeur
    engine: bool = False  # exécutée par le moteur (scènes, macros, ducking), pas par vMix


def _num(state: VmixState, p: dict) -> int:
    inp = state.input(p.get("input", ""))
    return inp.number if inp else -1


def _input_fb(attr: Callable):
    def fb(p, st: VmixState):
        inp = st.input(p.get("input", ""))
        return attr(inp, p) if inp else None
    return fb


def _vol(v: float) -> str:
    return f"{v * 100:.1f}".rstrip("0").rstrip(".")


def _ellipsis(text: str, n: int = 12) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


ACTIONS: dict[str, Action] = {}


def _add(a: Action):
    ACTIONS[a.key] = a


IN = Param("input", "Source", "input")

# --- Audio -----------------------------------------------------------------
_add(Action(
    "volume", "Volume d'une source", AUDIO, True, [IN],
    lambda p, v, st: [("SetVolume", {"Input": p.get("input"), "Value": _vol(v)}, f"vol:{p.get('input')}")],
    _input_fb(lambda i, p: i.volume / 100 if i.has_audio else None),
    lambda p, t: (t, "Volume"),
    "Le fader règle le volume de la source dans le mixeur vMix.",
))
_add(Action(
    "master_volume", "Volume Master", AUDIO, True, [],
    lambda p, v, st: [("SetMasterVolume", {"Value": _vol(v)}, "vol:master")],
    lambda p, st: st.master.volume / 100 if st.online else None,
    lambda p, t: ("MASTER", "Volume"),
))
_add(Action(
    "bus_volume", "Volume d'un bus", AUDIO, True, [Param("bus", "Bus", "bus", "A")],
    lambda p, v, st: [(f"SetBus{p.get('bus', 'A')}Volume", {"Value": _vol(v)}, f"vol:bus{p.get('bus')}")],
    lambda p, st: (st.buses[p.get("bus", "A")].volume / 100) if p.get("bus", "A") in st.buses else None,
    lambda p, t: (f"BUS {p.get('bus', 'A')}", "Volume"),
))
_add(Action(
    "headphones_volume", "Volume casque", AUDIO, True, [],
    lambda p, v, st: [("SetHeadphonesVolume", {"Value": _vol(v)}, "vol:phones")],
    lambda p, st: st.headphones / 100 if st.online else None,
    lambda p, t: ("CASQUE", "Volume"),
))
_add(Action(
    "balance", "Balance d'une source", AUDIO, True, [IN],
    lambda p, v, st: [("SetBalance", {"Input": p.get("input"), "Value": f"{v * 2 - 1:.2f}"},
                       f"bal:{p.get('input')}")],
    _input_fb(lambda i, p: (i.balance + 1) / 2 if i.has_audio else None),
    lambda p, t: (t, "Balance"),
    "Idéal sur un encodeur : gauche ↔ droite, centre = 50 %.",
))
_add(Action(
    "gain", "Gain d'une source (0-24 dB)", AUDIO, True, [IN],
    lambda p, v, st: [("SetGain", {"Input": p.get("input"), "Value": round(v * 24)},
                       f"gain:{p.get('input')}")],
    _input_fb(lambda i, p: i.gain / 24 if i.has_audio else None),
    lambda p, t: (t, "Gain"),
    "Préampli de la source, pratique sur un encodeur pour un micro trop faible.",
))
_add(Action(
    "audio_toggle", "Activer / couper une source", AUDIO, False, [IN],
    lambda p, v, st: [("Audio", {"Input": p.get("input")}, None)],
    _input_fb(lambda i, p: not i.muted),
    lambda p, t: (t, "ON / OFF"),
    "LED allumée = la source est audible.",
))
_add(Action(
    "audio_on", "Activer une source", AUDIO, False, [IN],
    lambda p, v, st: [("AudioOn", {"Input": p.get("input")}, None)],
    _input_fb(lambda i, p: not i.muted),
    lambda p, t: (t, "ON"),
    "Toujours « allumer », jamais basculer : idéal dans une macro.",
))
_add(Action(
    "audio_off", "Couper une source", AUDIO, False, [IN],
    lambda p, v, st: [("AudioOff", {"Input": p.get("input")}, None)],
    _input_fb(lambda i, p: i.muted),
    lambda p, t: (t, "OFF"),
))


def _volume_set(p, v, st):
    lvl, fade = float(p.get("level", 100)), int(p.get("duration", 0))
    if fade:
        return [("SetVolumeFade", {"Input": p.get("input"), "Value": f"{lvl:g},{fade}"}, None)]
    return [("SetVolume", {"Input": p.get("input"), "Value": f"{lvl:g}"}, None)]


_add(Action(
    "volume_set", "Régler une source à un niveau (avec fondu)", AUDIO, False,
    [IN, Param("level", "Niveau", "level", 80), Param("duration", "Fondu", "duration", 1000)],
    _volume_set,
    _input_fb(lambda i, p: abs(i.volume - float(p.get("level", 100))) < 1),
    lambda p, t: (t, f"→ {p.get('level', 80)} %"),
    "Ex. : musique à 0 % en 2 s pour une fin de stream, ou micro à 85 %.",
))
_add(Action(
    "master_toggle", "Activer / couper le Master", AUDIO, False, [],
    lambda p, v, st: [("MasterAudio", {}, None)],
    lambda p, st: (not st.master.muted) if st.online else None,
    lambda p, t: ("MASTER", "ON / OFF"),
))
_add(Action(
    "bus_toggle", "Activer / couper un bus", AUDIO, False, [Param("bus", "Bus", "bus", "A")],
    lambda p, v, st: [("BusXAudio", {"Value": p.get("bus", "A")}, None)],
    lambda p, st: (not st.buses[p.get("bus", "A")].muted) if p.get("bus", "A") in st.buses else None,
    lambda p, t: (f"BUS {p.get('bus', 'A')}", "ON / OFF"),
))

# --- Bus -------------------------------------------------------------------
_add(Action(
    "bus_route", "Envoyer une source vers un bus", ROUTING, False,
    [IN, Param("bus", "Bus", "route", "M")],
    lambda p, v, st: [("AudioBus", {"Input": p.get("input"), "Value": p.get("bus", "M")}, None)],
    _input_fb(lambda i, p: p.get("bus", "M") in i.buses),
    lambda p, t: (f"→ {p.get('bus', 'M')}", t),
    "LED allumée = la source est envoyée sur ce bus.",
))

# --- Vidéo -----------------------------------------------------------------
_add(Action(
    "transition", "Transition principale", VIDEO, False,
    [Param("effect", "Effet", "transition", "Fade"), Param("duration", "Durée", "duration", 500)],
    lambda p, v, st: [(p.get("effect", "Fade"),
                       {} if p.get("effect") in ("Cut",) or str(p.get("effect", "")).startswith("Transition")
                       else {"Duration": int(p.get("duration", 500))}, None)],
    None,
    lambda p, t: (p.get("effect", "Fade").upper(), f"{p.get('duration', 500)} ms"
                  if p.get("effect") != "Cut" else "Transition"),
    "Lance la transition entre la preview et le programme.",
))
_add(Action(
    "preview_input", "Mettre une source en preview", VIDEO, False, [IN],
    lambda p, v, st: [("PreviewInput", {"Input": p.get("input")}, None)],
    lambda p, st: (st.preview == _num(st, p)) if st.input(p.get("input", "")) else None,
    lambda p, t: (t, "PREVIEW"),
))
_add(Action(
    "cut_input", "Passer une source au programme (cut)", VIDEO, False, [IN],
    lambda p, v, st: [("CutDirect", {"Input": p.get("input")}, None)],
    lambda p, st: (st.active == _num(st, p)) if st.input(p.get("input", "")) else None,
    lambda p, t: (t, "PROGRAM"),
))
_add(Action(
    "fade_input", "Fondu vers une source", VIDEO, False, [IN, Param("duration", "Durée", "duration", 500)],
    lambda p, v, st: [("Fade", {"Input": p.get("input"), "Duration": int(p.get("duration", 500))}, None)],
    lambda p, st: (st.active == _num(st, p)) if st.input(p.get("input", "")) else None,
    lambda p, t: (t, "FONDU"),
))
_add(Action(
    "overlay", "Overlay (afficher / masquer)", VIDEO, False,
    [Param("overlay", "Overlay", "overlay", 1), IN],
    lambda p, v, st: [(f"OverlayInput{int(p.get('overlay', 1))}", {"Input": p.get("input")}, None)],
    lambda p, st: (st.overlays.get(int(p.get("overlay", 1))) == _num(st, p)) if st.input(p.get("input", "")) else None,
    lambda p, t: (t, f"OVERLAY {p.get('overlay', 1)}"),
))

# --- Vu-mètre sur un anneau LED -----------------------------------------------------


def _meter_fb(p, st: VmixState):
    src = str(p.get("source", "master"))
    if src == "master":
        return meter_norm(0.0 if st.master.muted else st.master.meter) if st.online else None
    if src.startswith("bus:"):
        b = st.buses.get(src[4:])
        return meter_norm(b.meter) if b else None
    inp = st.input(src)
    return meter_norm(0.0 if inp.muted else inp.meter) if inp and inp.has_audio else None


_add(Action(
    "meter", "Vu-mètre sur l'anneau LED", AUDIO, True,
    [Param("source", "Niveau de", "meter_source", "master")],
    lambda p, v, st: [],
    _meter_fb,
    lambda p, t: (t or "VU", "Vu-mètre"),
    "L'anneau de l'encodeur affiche le niveau audio en direct (tourner n'a pas d'effet).",
))

# --- Encodeurs crantés --------------------------------------------------------


def _cycle_effect(p, delta, st: VmixState):
    n = int(p.get("slot", 1))
    cur = st.transitions.get(n, ("Fade", 0))[0]
    effects = [t for t in TRANSITIONS if not t.startswith("Transition")]
    i = effects.index(cur) if cur in effects else 0
    return [(f"SetTransitionEffect{n}", {"Value": effects[(i + delta) % len(effects)]}, f"teff:{n}")]


def _step_duration(p, delta, st: VmixState):
    n = int(p.get("slot", 1))
    cur = st.transitions.get(n, ("", 500))[1]
    return [(f"SetTransitionDuration{n}", {"Value": max(0, min(10000, cur + 100 * delta))}, f"tdur:{n}")]


SLOT = Param("slot", "Transition", "slot", 1)
_add(Action(
    "preview_nav", "Parcourir les sources en preview", VIDEO, True, [],
    lambda p, d, st: [("PreviewInputNext" if d > 0 else "PreviewInputPrevious", {}, None)
                      for _ in range(abs(d))],
    None,
    lambda p, t: ("◀ PVW ▶", "Parcourir"),
    "Tourne pour faire défiler la preview, appuie (avec une action Transition) pour l'envoyer.",
    step=True,
))
_add(Action(
    "transition_effect", "Choisir l'effet d'une transition", VIDEO, True, [SLOT],
    _cycle_effect, None,
    lambda p, t: ((t or "Effet").upper(), f"Transition {p.get('slot', 1)}"),
    "Tourne pour changer l'effet du bouton de transition vMix 1-4. "
    "Mets « Transition N » sur l'appui pour la lancer.",
    step=True,
))
_add(Action(
    "transition_duration", "Régler la durée d'une transition", VIDEO, True, [SLOT],
    _step_duration, None,
    lambda p, t: (t or "Durée", f"Transition {p.get('slot', 1)}"),
    "Chaque cran ajoute ou retire 100 ms.",
    step=True,
))

# --- Production --------------------------------------------------------------
for _k, _lbl, _fn, _attr, _s in [
    ("streaming", "Démarrer / arrêter le stream", "StartStopStreaming", "streaming", "STREAM"),
    ("recording", "Démarrer / arrêter l'enregistrement", "StartStopRecording", "recording", "REC"),
    ("external", "Sortie externe on / off", "StartStopExternal", "external", "EXTERNAL"),
    ("ftb", "Fondu au noir", "FadeToBlack", "fade_to_black", "FTB"),
]:
    _add(Action(
        _k, _lbl, PROD, False, [],
        (lambda fn: lambda p, v, st: [(fn, {}, None)])(_fn),
        (lambda attr: lambda p, st: getattr(st, attr) if st.online else None)(_attr),
        (lambda s: lambda p, t: (s, ""))(_s),
    ))

# --- Scènes, macros, ducking (exécutés par le moteur) ----------------------------------
_add(Action(
    "scene_recall", "Rappeler une scène audio", AUTO, False, [Param("scene", "Scène", "scene")],
    None, None, lambda p, t: (t or "Scène", "SCÈNE"),
    "Restaure volumes, mutes et envois de bus. LED allumée = la console est dans cette scène.",
    engine=True,
))
_add(Action(
    "scene_save", "Enregistrer l'état dans une scène", AUTO, False, [Param("scene", "Scène", "scene")],
    None, None, lambda p, t: (t or "Scène", "ENREGISTRER"),
    "Écrase la scène avec les niveaux actuels.",
    engine=True,
))
_add(Action(
    "macro", "Lancer une macro", AUTO, False, [Param("macro", "Macro", "macro")],
    None, None, lambda p, t: (t or "Macro", "MACRO"),
    "Enchaîne plusieurs actions. LED allumée pendant l'exécution.",
    engine=True,
))
_add(Action(
    "ducking_toggle", "Ducking automatique on / off", AUTO, False, [],
    None, None, lambda p, t: ("DUCKING", "ON / OFF"),
    "Active ou coupe la baisse automatique de la musique quand un micro parle.",
    engine=True,
))

# --- Avancé ----------------------------------------------------------------


def _custom_params(p: dict, value: str) -> dict:
    out = {}
    for part in str(p.get("query", "")).split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip().replace("{value}", value)
    return out


_add(Action(
    "custom", "Fonction vMix personnalisée", CUSTOM, False,
    [Param("function", "Fonction", "text", ""), Param("query", "Paramètres", "text", "")],
    lambda p, v, st: [(p.get("function", ""), _custom_params(p, ""), None)] if p.get("function") else [],
    None,
    lambda p, t: (_ellipsis(p.get("function", "") or "Fonction", 10), "vMix"),
    "N'importe quelle fonction de l'API vMix. Paramètres au format Input=3&Value=50.",
))
_add(Action(
    "custom_value", "Fonction vMix continue personnalisée", CUSTOM, True,
    [Param("function", "Fonction", "text", ""), Param("query", "Paramètres", "text", "Value={value}")],
    lambda p, v, st: [(p.get("function", ""), _custom_params(p, _vol(v)),
                       f"custom:{p.get('function')}:{p.get('query')}")] if p.get("function") else [],
    None,
    lambda p, t: (_ellipsis(p.get("function", "") or "Fonction", 10), "vMix"),
    "{value} est remplacé par la position du contrôle (0 à 100).",
))


def actions_for(continuous: bool, encoder: bool = False) -> list[Action]:
    return [a for a in ACTIONS.values() if a.continuous == continuous and (encoder or not a.step)]


def macro_actions() -> list[Action]:
    """Actions utilisables comme étape de macro (déclencheurs)."""
    return [a for a in ACTIONS.values() if not a.continuous]


def needs_input(action: Action) -> bool:
    return any(p.type == "input" for p in action.params)


def defaults(action: Action) -> dict:
    return {p.name: p.default for p in action.params if p.default is not None}
