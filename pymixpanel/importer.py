"""Import d'un show Central Control (.ctrl) vers un profil MixPanel."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Profile

# Fonctions Central Control -> action MixPanel
_FUNCS = {
    "Set Mixer Level": "volume",
    "Toggle Audio Input": "audio_toggle",
    "Set Master Level": "master_volume",
    "Toggle Master": "master_toggle",
    "Toggle Input To Bus": "bus_route",
    "Main Transition": "transition",
    "Preview Input": "preview_input",
    "Cut Direct": "cut_input",
    "Toggle Streaming": "streaming",
    "Toggle Recording": "recording",
    "Fade To Black": "ftb",
}


def _control_id(name: str, control_type: int) -> str | None:
    n = name.strip()
    if m := re.fullmatch(r"Fader (\d)", n):
        return f"fader{m[1]}"
    if n == "Main Fader":
        return "main"
    if m := re.fullmatch(r"Fader Button (\d)", n):
        return f"fbtn{m[1]}"
    if n == "Master Fader Button":
        return "mainbtn"
    if m := re.fullmatch(r"Matrix (\d),(\d)", n):
        return f"m{m[1]}_{m[2]}"
    if m := re.fullmatch(r"vPot(?: Push)? (\d+)", n):
        # Central Control nomme mal les encodeurs de droite : on se fie au type.
        return f"{'push' if control_type == 0 else 'enc'}{m[1]}"
    simple = {"Rewind": "rew", "Forward": "ff", "Loop": "loop", "Record": "rec",
              "Stop": "stop", "Play": "play", "Footpedal 1": "exp", "Footpedal 2": "foot"}
    return simple.get(n)


def _value(param: dict):
    v = param.get("Value")
    if isinstance(v, dict) and "SelectedItem" in v:
        s = v["SelectedItem"] or {}
        return s.get("value"), s.get("name")
    return v, v


def import_ctrl(path: str | Path) -> tuple[Profile, list[str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    prof = Profile(name=data.get("ShowName") or Path(path).stem)
    notes: list[str] = []

    for el in data.get("ShowElements", []):
        dev = el.get("Device", {})
        params = dev.get("DeviceParameters", {})
        t = dev.get("$type", "")
        if "VMix" in t:
            prof.vmix_host = str(params.get("IP Address", {}).get("Value") or "localhost")
            prof.vmix_port = int(params.get("Port", {}).get("Value") or 8099)
            rate, _ = _value(params.get("Refresh Rate", {}))
            if isinstance(rate, (int, float)) and rate > 0:
                prof.poll_ms = int(rate)
        elif "XTouch" in t:
            _, in_name = _value(params.get("MIDI In", {}))
            _, out_name = _value(params.get("MIDI Out", {}))
            if in_name:
                prof.midi_in = in_name
            if out_name:
                prof.midi_out = out_name

    for me in data.get("Mapping", {}).get("MappedElements", []):
        for mc in me.get("MappedControls", []):
            cid = _control_id(mc.get("ID", ""), mc.get("ControlType", 0))
            funcs = [f["Function"] for f in mc.get("MappedFunctions", []) if f.get("Function")]
            if not funcs:
                continue
            if cid is None:
                notes.append(f"Contrôle inconnu ignoré : {mc.get('ID')}")
                continue
            fn = funcs[0]
            action = _FUNCS.get(fn.get("ID", ""))
            if action is None or fn.get("DestID") != "vMix":
                notes.append(f"{mc.get('ID')} : fonction « {fn.get('ID')} » non reprise")
                continue
            if len(funcs) > 1:
                notes.append(f"{mc.get('ID')} : seule la 1re fonction a été reprise")
            m: dict = {"action": action}
            fp = fn.get("Parameters", {})
            if "Input" in fp:
                key, title = _value(fp["Input"])
                m["input"], m["input_title"] = key, title
            if "Audio Bus" in fp:
                m["bus"] = _value(fp["Audio Bus"])[0] or "M"
            if "Transition" in fp:
                m["effect"] = _value(fp["Transition"])[0] or "Fade"
            if "Duration" in fp:
                try:
                    m["duration"] = int(_value(fp["Duration"])[0])
                except (TypeError, ValueError):
                    m["duration"] = 500
            prof.mappings[cid] = m

    trig = sum(len(me.get("MappedTriggers", [])) for me in data.get("Mapping", {}).get("MappedElements", []))
    if trig:
        notes.append(f"{trig} déclencheur(s) Central Control non repris (retours vers d'autres appareils)")
    return prof, notes
