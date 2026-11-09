"""Faux serveur vMix (API TCP) pour tester MixPanel sans vMix.

    python tools/fake_vmix.py [port]      (8099 par défaut)
"""
from __future__ import annotations

import random
import socketserver
import sys
import time
import threading
from urllib.parse import parse_qsl
from xml.sax.saxutils import quoteattr

LOCK = threading.Lock()
VERBOSE = True   # les tests le coupent : afficher chaque commande ralentit le serveur
INPUTS = [
    # key, title, type, audio
    ("3f6c1a52-8d4e-4b7a-9c21-5e0d7b4a9f13", "Caméra 1", "Capture", True),
    ("a81d47c9-2e3b-4f60-8d95-1c7e6b0a4d28", "Micro Animateur", "Audio", True),
    ("c5e92b10-7f4a-4d3c-b816-9a2f0e5d7c41", "Micro Invité", "Audio", True),
    ("6b0f8e37-41d2-4c9a-a7e5-3d18c2f6b905", "Musique", "AudioFile", True),
    ("e2a74d68-9c1f-4b05-8e3a-7f6d0b2c1a59", "Micro Régie", "Audio", True),
    ("19d3c7a4-5b8e-4f21-9d06-2e4a8c7f3b60", "Partage écran", "Capture", True),
    ("8c4e1f2b-6a9d-4e37-b5c0-4f2d9a1e8b73", "NDI Caméra 2", "NDI", True),
    ("d7b25a90-3e6c-4a18-9f4d-8b1c5e2a6f07", "NDI PC Jeu", "NDI", True),
    ("5a9e3c61-0d7b-4f84-a2c5-6e8f1b4d9c32", "Audio IN 3", "Audio", True),
    ("0b8f6a3e-1111-4c1d-9a55-000000000010", "Logo", "Image", False),
]
S = {
    "inputs": {k: {"n": i + 1, "title": t, "type": ty, "audio": a, "muted": False,
                   "volume": 80.0, "buses": {"M"}, "balance": 0.0, "gain": 0}
               for i, (k, t, ty, a) in enumerate(INPUTS)},
    "transitions": {n: ["Fade", 500] for n in range(1, 5)},
    "meters": {},   # clé -> niveau imposé (tests), sinon aléatoire
    "apply_delay": 0.0,  # s : simule un vMix chargé qui applique les volumes en retard
    "fade_broken": False,  # SetVolumeFade acquitté mais sans effet (constaté sur certains vMix)
    "ignore_until": 0.0,  # time.monotonic() : SetVolume répondu OK mais ignoré (commande perdue)
    "master": [100.0, False], "phones": 100.0,
    "buses": {b: [100.0, False] for b in "AB"},
    "active": 1, "preview": 2, "overlays": {n: 0 for n in range(1, 5)},
    "streaming": False, "recording": False, "external": False, "ftb": False,
}


def _find(ref: str):
    for k, inp in S["inputs"].items():
        if ref in (k, inp["title"], str(inp["n"])):
            return k, inp
    return None, None


def _amp(position: float) -> str:
    """Comme le vrai vMix : le XML donne l'amplitude, (position / 100)^4 × 100, à 2 décimales."""
    return f"{(max(0.0, position) / 100) ** 4 * 100:.2f}"


def xml() -> bytes:
    out = ["<vmix><version>28.0.0.39</version><edition>4K</edition><inputs>"]
    for k, i in S["inputs"].items():
        audio = ""
        if i["audio"]:
            lvl = 0.0 if i["muted"] else S["meters"].get(k, random.uniform(0.05, 0.6)) * i["volume"] / 100
            audio = (f' muted="{i["muted"]}" volume="{_amp(i["volume"])}" balance="{i["balance"]:g}"'
                     f' gainDb="{i["gain"]}" meterF1="{lvl:.4f}" meterF2="{lvl * 0.9:.4f}" solo="False"'
                     f' audiobusses="{",".join(sorted(i["buses"], key="MABCDEFG".index))}"')
        out.append(f'<input key="{k}" number="{i["n"]}" type="{i["type"]}" title={quoteattr(i["title"])}'
                   f' state="Paused"{audio}>{i["title"]}</input>')
    out.append("</inputs><overlays>")
    out += [f'<overlay number="{n}">{v or ""}</overlay>' for n, v in S["overlays"].items()]
    out.append("</overlays><transitions>")
    out += [f'<transition number="{n}" effect="{e}" duration="{d}"/>' for n, (e, d) in S["transitions"].items()]
    out.append(f"</transitions><preview>{S['preview']}</preview><active>{S['active']}</active>")
    out.append(f"<fadeToBlack>{S['ftb']}</fadeToBlack><recording>{S['recording']}</recording>"
               f"<external>{S['external']}</external><streaming>{S['streaming']}</streaming>")
    out.append(f'<audio><master volume="{_amp(S["master"][0])}" muted="{S["master"][1]}" '
               f'headphonesVolume="{_amp(S["phones"])}"/>')
    out += [f'<bus{b} volume="{_amp(v)}" muted="{m}"/>' for b, (v, m) in S["buses"].items()]
    out.append("</audio></vmix>")
    return "".join(out).encode("utf-8")


# Noms exacts de la référence officielle vMix (Shortcut Function Reference) : le faux vMix
# refuse tout le reste, comme le vrai. Les effets de transition (Fade, Cube…) sont aussi des fonctions.
TRANSITION_EFFECTS = ["Cut", "Fade", "Merge", "Wipe", "Slide", "Fly", "CrossZoom", "FlyRotate", "Cube",
                      "CubeZoom", "VerticalWipe", "VerticalSlide", "Zoom"]
REAL_FUNCTIONS = {
    "Audio", "AudioOn", "AudioOff", "AudioBus", "AudioBusOn", "AudioBusOff",
    "BusXAudio", "BusXAudioOn", "BusXAudioOff", "MasterAudio", "MasterAudioOn", "MasterAudioOff",
    "SetVolume", "SetVolumeFade", "SetBalance", "SetGain", "SetMasterVolume", "SetMasterVolumeFade",
    "SetHeadphonesVolume", "Solo", "SoloOn", "SoloOff",
    *[f"SetBus{b}Volume" for b in "ABCDEFG"], *[f"SetBus{b}VolumeFade" for b in "ABCDEFG"],
    "PreviewInput", "PreviewInputNext", "PreviewInputPrevious", "CutDirect", "QuickPlay", "FadeToBlack",
    *[f"OverlayInput{n}" for n in range(1, 5)], *[f"Transition{n}" for n in range(1, 5)],
    *[f"Stinger{n}" for n in range(1, 5)], *[f"SetTransitionEffect{n}" for n in range(1, 5)],
    *[f"SetTransitionDuration{n}" for n in range(1, 5)],
    "StartStopStreaming", "StartStopRecording", "StartStopExternal", "StartStopMultiCorder",
    *TRANSITION_EFFECTS,
}


def function(fn: str, q: dict) -> str | None:
    if fn not in REAL_FUNCTIONS:
        return "Function not found"
    f = fn.lower()
    k, inp = _find(q.get("Input", ""))
    val = q.get("Value", "")
    if f == "setvolume" and inp and time.monotonic() < S["ignore_until"]:
        pass   # vMix sous charge : la commande est acquittée mais pas appliquée
    elif f == "setvolume" and inp:
        if S["apply_delay"]:
            def later(inp=inp, v=float(val)):
                with LOCK:
                    inp["volume"] = v
            threading.Timer(S["apply_delay"], later).start()
        else:
            inp["volume"] = float(val)
    elif f == "setvolumefade" and inp and S["fade_broken"]:
        pass
    elif f == "setvolumefade" and inp:
        inp["volume"] = float(val.split(",")[0])
    elif f == "audiobuson" and inp:
        inp["buses"] |= {val}
    elif f == "audiobusoff" and inp:
        inp["buses"] -= {val}
    elif f in ("busxaudioon", "busxaudiooff"):
        S["buses"].setdefault(val.upper(), [100.0, False])[1] = f.endswith("off")
    elif f == "setbalance" and inp:
        inp["balance"] = float(val)
    elif f == "setgain" and inp:
        inp["gain"] = int(float(val))
    elif f in ("previewinputnext", "previewinputprevious"):
        n = len(S["inputs"])
        S["preview"] = (S["preview"] % n) + 1 if f.endswith("next") else ((S["preview"] - 2) % n) + 1
    elif f.startswith("settransitioneffect"):
        S["transitions"][int(f[-1])][0] = val
    elif f.startswith("settransitionduration"):
        S["transitions"][int(f[-1])][1] = int(float(val))
    elif f == "audio" and inp:
        inp["muted"] = not inp["muted"]
    elif f == "audioon" and inp:
        inp["muted"] = False
    elif f == "audiooff" and inp:
        inp["muted"] = True
    elif f == "audiobus" and inp:
        inp["buses"] ^= {val}
    elif f in ("setmastervolume", "setmastervolumefade"):
        S["master"][0] = float(val.split(",")[0])
    elif f == "setheadphonesvolume":
        S["phones"] = float(val)
    elif f == "masteraudioon":
        S["master"][1] = False
    elif f == "masteraudiooff":
        S["master"][1] = True
    elif f == "masteraudio":
        S["master"][1] = not S["master"][1]
    elif f.startswith("setbus") and "volume" in f:
        S["buses"].setdefault(fn[6].upper(), [100.0, False])[0] = float(val.split(",")[0])
    elif f == "busxaudio":
        b = S["buses"].setdefault(val.upper(), [100.0, False])
        b[1] = not b[1]
    elif f == "previewinput" and inp:
        S["preview"] = inp["n"]
    elif f in ("cutdirect",) and inp:
        S["active"] = inp["n"]
    elif f.startswith("overlayinput") and inp:
        n = int(f[-1])
        S["overlays"][n] = 0 if S["overlays"][n] == inp["n"] else inp["n"]
    elif f == "startstopstreaming":
        S["streaming"] = not S["streaming"]
    elif f == "startstoprecording":
        S["recording"] = not S["recording"]
    elif f == "startstopexternal":
        S["external"] = not S["external"]
    elif f == "fadetoblack":
        S["ftb"] = not S["ftb"]
    elif f in ("cut", "fade", "merge", "wipe", "slide", "fly", "crosszoom", "flyrotate", "cube",
               "cubezoom", "verticalwipe", "verticalslide", "zoom", "stinger1", "stinger2",
               "transition1", "transition2", "transition3", "transition4"):
        if inp:
            S["active"] = inp["n"]
        else:
            S["active"], S["preview"] = S["preview"], S["active"]
    else:
        return "Function not found or invalid parameters"
    return None


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        if VERBOSE:
            print(f"+ client {self.client_address}")
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            cmd, _, rest = line.partition(" ")
            cmd = cmd.upper()
            with LOCK:
                if cmd == "XML":
                    data = xml()
                    self.wfile.write(f"XML {len(data)}\r\n".encode() + data)
                elif cmd == "FUNCTION":
                    fn, _, query = rest.partition(" ")
                    err = function(fn, dict(parse_qsl(query)))
                    if VERBOSE:
                        print(f"  FUNCTION {fn} {query}" + (f"  -> ER {err}" if err else ""))
                    self.wfile.write((f"FUNCTION ER {err}" if err else "FUNCTION OK Completed").encode() + b"\r\n")
                elif cmd == "TALLY":
                    t = "".join("1" if i["n"] == S["active"] else "2" if i["n"] == S["preview"] else "0"
                                for i in S["inputs"].values())
                    self.wfile.write(f"TALLY OK {t}\r\n".encode())
                elif cmd == "QUIT":
                    return
                else:
                    self.wfile.write(f"{cmd} ER Unknown command\r\n".encode())
        if VERBOSE:
            print(f"- client {self.client_address}")


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    print(f"Faux vMix à l'écoute sur le port {port}")
    Server(("127.0.0.1", port), Handler).serve_forever()
