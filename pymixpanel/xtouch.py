"""Description de la surface X-Touch Compact (mode MIDI standard, Layer A).

D'après la table MIDI officielle (Quick Start Guide Behringer, pages « MIDI Map » et
« RX MIDI Data »). Attention : la console n'utilise PAS les mêmes numéros dans les deux sens.

  * Entrée (TX, canal 1) : faders CC 1-9, tactile CC 101-109, encodeurs CC 10-25,
    appuis Notes 0-15, boutons Notes 16-54, pédales CC 26/27.
  * Retours (RX, canal GLOBAL de la console) : moteurs CC 1-9, LED des boutons
    Notes 0-38, mode des anneaux CC 10-25, valeur des anneaux CC 26-41 (0-13).

Les identifiants d'entrée se réapprennent depuis l'interface (« Apprendre ») ; les retours
dépendent de la position physique du contrôle et ne changent pas.
"""
from __future__ import annotations

from dataclasses import dataclass

import mido


@dataclass(frozen=True)
class MidiId:
    type: str  # "cc" | "note" | "pitch"
    ch: int    # 0-15
    num: int   # 0-127 (0 pour pitch)

    def to_dict(self) -> dict:
        return {"type": self.type, "ch": self.ch, "num": self.num}

    @classmethod
    def from_dict(cls, d: dict) -> "MidiId":
        return cls(str(d["type"]), int(d.get("ch", 0)), int(d.get("num", 0)))

    def label(self) -> str:
        if self.type == "pitch":
            return f"Pitch bend · canal {self.ch + 1}"
        kind = "CC" if self.type == "cc" else "Note"
        return f"{kind} {self.num} · canal {self.ch + 1}"


FADER, ENCODER, BUTTON, PEDAL = "fader", "encoder", "button", "pedal"


@dataclass
class ControlDef:
    id: str
    name: str
    kind: str
    midi: MidiId                     # ce que la console envoie
    touch: MidiId | None = None      # capteur tactile (faders)
    strip: int | None = None         # 0-7 pour les tranches, 8 pour le master
    out: MidiId | None = None        # retour : moteur, LED ou valeur d'anneau (canal global)
    ring_mode: int | None = None     # CC du mode d'affichage de l'anneau (encodeurs)
    midi_b: MidiId | None = None     # ce que la console envoie en Layer B
    touch_b: MidiId | None = None

    @property
    def continuous(self) -> bool:
        return self.kind in (FADER, ENCODER, PEDAL)


TRANSPORT = [("rew", "Rewind"), ("ff", "Forward"), ("loop", "Loop"),
             ("rec", "Record"), ("stop", "Stop"), ("play", "Play")]

# Modes d'affichage des anneaux LED (RX CC 10-25)
RING_SINGLE, RING_PAN, RING_FAN, RING_SPREAD, RING_TRIM = range(5)


def default_controls(ch: int = 0) -> dict[str, ControlDef]:
    c: dict[str, ControlDef] = {}

    def add(cd: ControlDef):
        c[cd.id] = cd

    def led(n: int) -> MidiId:
        return MidiId("note", 0, n)

    for i in range(9):
        name = f"Fader {i + 1}" if i < 8 else "Fader principal"
        add(ControlDef(f"fader{i + 1}" if i < 8 else "main", name, FADER,
                       MidiId("cc", ch, 1 + i), MidiId("cc", ch, 101 + i), strip=i,
                       out=MidiId("cc", 0, 1 + i)))
    for i in range(16):
        add(ControlDef(f"enc{i + 1}", f"Encodeur {i + 1}", ENCODER,
                       MidiId("cc", ch, 10 + i), strip=i if i < 8 else None,
                       out=MidiId("cc", 0, 26 + i), ring_mode=10 + i))
        # les appuis d'encodeur n'ont pas de LED
        add(ControlDef(f"push{i + 1}", f"Appui encodeur {i + 1}", BUTTON,
                       MidiId("note", ch, i), strip=i if i < 8 else None))
    for r in range(3):
        for col in range(8):
            add(ControlDef(f"m{r + 1}_{col + 1}", f"Bouton ligne {r + 1} · {col + 1}", BUTTON,
                           MidiId("note", ch, 16 + r * 8 + col), strip=col, out=led(r * 8 + col)))
    for i in range(8):
        add(ControlDef(f"fbtn{i + 1}", f"Bouton fader {i + 1}", BUTTON,
                       MidiId("note", ch, 40 + i), strip=i, out=led(24 + i)))
    add(ControlDef("mainbtn", "Bouton fader principal", BUTTON, MidiId("note", ch, 48), strip=8,
                   out=led(32)))
    for i, (cid, name) in enumerate(TRANSPORT):
        add(ControlDef(cid, name, BUTTON, MidiId("note", ch, 49 + i), out=led(33 + i)))
    add(ControlDef("exp", "Pédale d'expression", PEDAL, MidiId("cc", ch, 26)))
    add(ControlDef("foot", "Footswitch", BUTTON, MidiId("cc", ch, 27), out=MidiId("cc", 0, 42)))
    for cd in c.values():
        cd.midi_b = layer_b(cd.midi)
        cd.touch_b = layer_b(cd.touch) if cd.touch else None
    return c


def layer_b(mid: MidiId) -> MidiId:
    """Équivalent Layer B d'un message Layer A (table officielle « Instrument Control ») :
    notes +55 (appuis 55-70, boutons 71-109), CC 1-25 +27 (faders 28-36, encodeurs 37-52),
    pédales CC 63 / 64, capteurs tactiles CC 111-119."""
    if mid.type == "note":
        return MidiId("note", mid.ch, mid.num + 55)
    if mid.type == "cc":
        if 1 <= mid.num <= 25:
            return MidiId("cc", mid.ch, mid.num + 27)
        if mid.num in (26, 27):
            return MidiId("cc", mid.ch, mid.num + 37)
        if 101 <= mid.num <= 109:
            return MidiId("cc", mid.ch, mid.num + 10)
    return mid


def ring_value(v: float) -> int:
    """Valeur 0..1 -> valeur d'anneau : 0 = éteint, 1-13 = LED de gauche à droite."""
    v = min(1.0, max(0.0, v))
    return 0 if v < 0.005 else 1 + round(v * 12)


def strip_controls(i: int) -> dict[str, str]:
    """Identifiants des contrôles d'une tranche (0-7)."""
    n = i + 1
    return {"fader": f"fader{n}", "button": f"fbtn{n}", "enc": f"enc{n}", "push": f"push{n}",
            "row1": f"m1_{n}", "row2": f"m2_{n}", "row3": f"m3_{n}"}


def decode(msg: mido.Message) -> tuple[MidiId, float] | None:
    """Message MIDI -> (identifiant, valeur normalisée 0..1)."""
    t = msg.type
    if t == "control_change":
        return MidiId("cc", msg.channel, msg.control), msg.value / 127
    if t == "note_on":
        return MidiId("note", msg.channel, msg.note), msg.velocity / 127
    if t == "note_off":
        return MidiId("note", msg.channel, msg.note), 0.0
    if t == "pitchwheel":
        return MidiId("pitch", msg.channel, 0), (msg.pitch + 8192) / 16383
    return None
