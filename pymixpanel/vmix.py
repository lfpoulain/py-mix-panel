"""Client de l'API TCP vMix (port 8099).

Deux connexions indépendantes :
  * commandes : file d'attente avec fusion (un fader qui bouge n'envoie que
    sa dernière valeur, jamais une rafale en retard) ;
  * état : lecture du XML vMix à intervalle court, et immédiatement après
    chaque commande, pour un retour (LED / faders motorisés) quasi instantané.
"""
from __future__ import annotations

import itertools
import math
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from PySide6.QtCore import QObject, Signal

BUSES = ["A", "B", "C", "D", "E", "F", "G"]
MIN_XML_GAP = 0.04


@dataclass
class InputState:
    key: str
    number: int
    title: str
    type: str
    has_audio: bool = False
    muted: bool = False
    volume: float = 0.0          # position du fader vMix 0-100 (échelle de SetVolume)
    volume_amp: float = 0.0      # valeur brute du XML (amplitude, arrondie par vMix)
    buses: frozenset = frozenset()  # {"M", "A", ...}
    solo: bool = False
    balance: float = 0.0         # -1..1
    gain: float = 0.0            # 0-24 dB
    meter: float = 0.0           # niveau crête 0..1 (amplitude)


@dataclass
class BusState:
    volume: float = 100.0
    volume_amp: float = 100.0
    muted: bool = False
    meter: float = 0.0


@dataclass
class VmixState:
    online: bool = False
    version: str = ""
    inputs: dict = field(default_factory=dict)   # key -> InputState
    master: BusState = field(default_factory=BusState)
    headphones: float = 100.0
    buses: dict = field(default_factory=dict)    # "A" -> BusState
    active: int = 0
    preview: int = 0
    overlays: dict = field(default_factory=dict)  # 1..4 -> numéro d'input (0 = vide)
    transitions: dict = field(default_factory=dict)  # 1..4 -> (effet, durée ms)
    streaming: bool = False
    recording: bool = False
    external: bool = False
    fade_to_black: bool = False

    def input(self, key: str) -> InputState | None:
        return self.inputs.get(key) if key else None

    def ordered_inputs(self) -> list[InputState]:
        return sorted(self.inputs.values(), key=lambda i: i.number)


def xml_to_position(v: float) -> float:
    """Le XML de vMix donne le volume en amplitude (0-100) alors que SetVolume attend la
    position du fader (0-100) : amplitude = (position / 100)^4 × 100. Ex. position 50 -> « 6.25 »
    dans le XML (-24 dB). Toute l'app travaille en position de fader."""
    return 100 * (max(0.0, v) / 100) ** 0.25


def position_to_db(pos: float) -> float:
    """Position de fader vMix (0-100) -> dB : 100 = 0 dB, 50 = -24 dB, 0 = -inf."""
    return 80 * math.log10(pos / 100) if pos > 0.001 else float("-inf")


def amp_to_db(amp: float) -> float:
    return 20 * math.log10(amp) if amp > 1e-6 else -120.0


def meter_norm(amp: float) -> float:
    """Amplitude 0..1 -> position 0..1 sur une échelle -60..0 dB."""
    return min(1.0, max(0.0, (amp_to_db(amp) + 60) / 60))


def _buses(raw) -> frozenset:
    """« M,A,B » -> {"M", "A", "B"} (tolère espaces, minuscules et « Master »)."""
    out = set()
    for tok in re.split(r"[,;\s]+", raw or ""):
        tok = tok.strip().upper()
        if tok.startswith("MASTER"):
            out.add("M")
        elif tok.startswith("BUS") and len(tok) > 3:
            out.add(tok[-1])        # « BusA » -> « A »
        elif tok:
            out.add(tok[0])
    return frozenset(out)


def _b(v) -> bool:
    return str(v).strip().lower() == "true"


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_xml(data: bytes) -> VmixState:
    root = ET.fromstring(data)
    st = VmixState(online=True, version=root.findtext("version") or "")
    for el in root.iter("input"):
        a = el.attrib
        if "key" not in a:
            continue
        inp = InputState(
            key=a["key"], number=int(_f(a.get("number"), 0)),
            title=a.get("title") or (el.text or "").strip(), type=a.get("type", ""),
            has_audio="volume" in a, muted=_b(a.get("muted")), volume=xml_to_position(_f(a.get("volume"))), volume_amp=_f(a.get("volume")),
            buses=_buses(a.get("audiobusses")),
            solo=_b(a.get("solo")), balance=_f(a.get("balance")), gain=_f(a.get("gainDb")),
            meter=max(_f(a.get("meterF1")), _f(a.get("meterF2"))),
        )
        st.inputs[inp.key] = inp
    audio = root.find("audio")
    if audio is not None:
        for el in audio:
            bs = BusState(xml_to_position(_f(el.get("volume"), 100.0)), _f(el.get("volume"), 100.0),
                          _b(el.get("muted")),
                          max(_f(el.get("meterF1")), _f(el.get("meterF2"))))
            if el.tag == "master":
                st.master = bs
                st.headphones = xml_to_position(_f(el.get("headphonesVolume"), 100.0))
            elif el.tag.startswith("bus") and len(el.tag) == 4:
                st.buses[el.tag[3]] = bs
    st.active = int(_f(root.findtext("active"), 0))
    st.preview = int(_f(root.findtext("preview"), 0))
    ov = root.find("overlays")
    if ov is not None:
        for el in ov:
            st.overlays[int(_f(el.get("number"), 0))] = int(_f((el.text or "").strip(), 0))
    tr = root.find("transitions")
    if tr is not None:
        for el in tr:
            st.transitions[int(_f(el.get("number"), 0))] = (el.get("effect", ""),
                                                           int(_f(el.get("duration"), 0)))
    st.streaming = _b(root.findtext("streaming"))
    st.recording = _b(root.findtext("recording"))
    st.external = _b(root.findtext("external"))
    st.fade_to_black = _b(root.findtext("fadeToBlack"))
    return st


def _resolve(host: str) -> str:
    """Préférer l'IPv4. Sous Windows, « localhost » essaie d'abord ::1, et comme vMix
    n'écoute qu'en IPv4, chaque connexion perdait environ 2 secondes."""
    if host.strip().lower() in ("localhost", ""):
        return "127.0.0.1"
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    except OSError:
        return host


class _Conn:
    """Connexion TCP ligne par ligne au format vMix."""

    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((_resolve(host), port), timeout=1.5)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(3)
        self.f = self.sock.makefile("rb")

    def send(self, line: str):
        self.sock.sendall(line.encode("utf-8") + b"\r\n")

    def readline(self) -> str:
        raw = self.f.readline()
        if not raw:
            raise ConnectionError("vMix a fermé la connexion")
        return raw.decode("utf-8", "replace").rstrip("\r\n")

    def read(self, n: int) -> bytes:
        data = self.f.read(n)
        if len(data) < n:
            raise ConnectionError("réponse vMix incomplète")
        return data

    def close(self):
        for c in (self.f, self.sock):
            try:
                c.close()
            except Exception:
                pass


class VmixClient(QObject):
    state_changed = Signal(object)        # VmixState
    status_changed = Signal(bool, str)    # connecté, version
    log = Signal(str)

    def __init__(self, host="localhost", port=8099, poll_ms=100):
        super().__init__()
        self.host, self.port, self.poll_ms = host, port, poll_ms
        self._queue: OrderedDict = OrderedDict()
        self._cv = threading.Condition()
        self._refresh = threading.Event()
        self._stop = threading.Event()
        self._gen = 0  # incrémenté à chaque reconfiguration
        self._ids = itertools.count()
        self.online = False
        self.latency_ms: float | None = None   # aller-retour moyen d'une commande
        self.xml_ms: float | None = None       # durée moyenne d'une lecture de l'état (XML)
        self.stats = {"xml": 0, "cmd": 0}      # compteurs (lus par le diagnostic)
        self._last_state: VmixState | None = None

    # -- API publique -----------------------------------------------------
    def start(self):
        for target in (self._cmd_loop, self._state_loop):
            threading.Thread(target=target, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._refresh.set()
        with self._cv:
            self._cv.notify_all()

    def configure(self, host: str, port: int, poll_ms: int):
        if (host, port) != (self.host, self.port):
            self._gen += 1
        self.host, self.port, self.poll_ms = host, int(port), max(30, int(poll_ms))
        self._refresh.set()

    def send(self, function: str, params: dict | None = None, coalesce: str | None = None):
        # la virgule reste lisible (« 80,1000 ») : vMix ne décode pas toujours %2C
        query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")}, safe=",",
                          quote_via=quote)
        key = coalesce or f"#{next(self._ids)}"
        with self._cv:
            self._queue.pop(key, None)
            self._queue[key] = (function, query, time.monotonic())
            self._cv.notify()

    def request_refresh(self):
        self._refresh.set()

    # -- threads ----------------------------------------------------------
    def _cmd_loop(self):
        conn, gen = None, self._gen
        while not self._stop.is_set():
            with self._cv:
                while not self._queue and not self._stop.is_set():
                    self._cv.wait(0.5)
                if self._stop.is_set():
                    break
                key, (fn, query, t0) = self._queue.popitem(last=False)
            if time.monotonic() - t0 > 2.0:
                continue  # commande trop ancienne (vMix était hors ligne)
            if gen != self._gen and conn:
                conn.close()
                conn = None
            gen = self._gen
            line = f"FUNCTION {fn} {query}".rstrip()
            continuous = not key.startswith("#")
            for attempt in range(2):
                try:
                    if conn is None:
                        conn = _Conn(self.host, self.port)
                    t_send = time.perf_counter()
                    conn.send(line)
                    while True:
                        resp = conn.readline()
                        if resp.startswith("FUNCTION"):
                            break
                    rtt = (time.perf_counter() - t_send) * 1000
                    self.latency_ms = rtt if self.latency_ms is None else self.latency_ms * 0.8 + rtt * 0.2
                    self.stats["cmd"] += 1
                    if " ER " in resp:
                        self.log.emit(f"vMix ✗ {fn} {query} → {resp[12:]}")
                    elif not continuous:
                        # les flux de fader ne sont pas journalisés (des centaines par seconde)
                        self.log.emit(f"vMix ← {fn} {query}  ({rtt:.0f} ms)")
                    break
                except (OSError, ConnectionError, ValueError):
                    if conn:
                        conn.close()
                    conn = None
                    if attempt:
                        self.log.emit(f"vMix injoignable, commande perdue : {fn}")
            if not continuous:
                # bouton : retour LED immédiat. Fader : inutile, le moteur est protégé
                # pendant le mouvement, et relire le XML en boucle ralentirait vMix.
                self._refresh.set()
        if conn:
            conn.close()

    def _state_loop(self):
        conn, gen = None, self._gen
        while not self._stop.is_set():
            if gen != self._gen and conn:
                conn.close()
                conn = None
            gen = self._gen
            try:
                if conn is None:
                    conn = _Conn(self.host, self.port)
                t_xml = time.perf_counter()
                conn.send("XML")
                while True:
                    head = conn.readline()
                    if head.startswith("XML "):
                        break
                n = int(head.split()[1])
                state = parse_xml(conn.read(n))
                dt = (time.perf_counter() - t_xml) * 1000
                self.xml_ms = dt if self.xml_ms is None else self.xml_ms * 0.8 + dt * 0.2
                self.stats["xml"] += 1
                if not self.online:
                    self.online = True
                    self.status_changed.emit(True, state.version)
                if state != self._last_state:
                    self._last_state = state
                    self.state_changed.emit(state)
            except (OSError, ConnectionError, ValueError, ET.ParseError):
                if conn:
                    conn.close()
                conn = None
                if self.online:
                    self.online = False
                    self._last_state = None
                    self.status_changed.emit(False, "")
                    self.state_changed.emit(VmixState())
                self._stop.wait(1.5)
                continue
            self._stop.wait(MIN_XML_GAP)  # jamais plus de ~25 lectures XML par seconde
            self._refresh.wait(max(0.0, self.poll_ms / 1000 - MIN_XML_GAP))
            self._refresh.clear()
        if conn:
            conn.close()
