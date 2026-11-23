"""API locale de pilotage : l'API Stream Deck commune (``/api/deck``) pour py-livedeck.

* HTTP simple sur le réseau local, JSON en entrée et en sortie, sans dépendance ;
* fermée par défaut ; port réglable ; « ce poste uniquement » écoute sur 127.0.0.1 ;
* jeton facultatif : vide, l'API est ouverte ; rempli, chaque requête doit porter
  ``Authorization: Bearer <jeton>`` (jamais lu dans l'URL), sinon ``401 INVALID_TOKEN`` ;
* erreurs ``{"error": {"code", "message", "details?"}}`` en français. Jamais ``404 NOT_FOUND``
  sous ``/api/deck`` : py-livedeck le lit comme « adresse publique refusée » ;
* les commandes ne s'exécutent jamais sur le fil HTTP : elles passent au fil de
  l'interface, qui possède le moteur (voir :class:`ApiService`).

Le routage est une fonction pure, testable sans ouvrir de socket.
"""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from PySide6.QtCore import QObject, Signal

from . import APP_NAME, __version__
from . import deck
from .deck import ApiError

log = logging.getLogger(__name__)

DEFAULT_PORT = 5085
API_DEFAULTS = {"enabled": False, "port": DEFAULT_PORT, "localOnly": True, "token": ""}
DECK_HELP = ("GET /api/deck/state, GET /api/deck/events, GET /api/deck/commands, "
             "POST /api/deck/commands/<id> {\"args\": {...}}")
MAX_STREAMS = 16
POLL_SECONDS = 1.0
KEEPALIVE_SECONDS = 25.0
CALL_TIMEOUT = 10.0


def check_token(expected: str, authorization: str | None) -> ApiError | None:
    """None si la requête peut passer : pas de jeton réglé, ou le bon jeton."""
    if not expected:
        return None
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(value.strip().encode(), expected.encode()):
        return None
    return ApiError(401, "INVALID_TOKEN", "jeton API absent ou incorrect : MixPanel › Réglages › "
                                          "Pilotage à distance, en-tête « Authorization: Bearer <jeton> »")


def _json_body(body: bytes) -> dict:
    """``{}`` pour un corps vide, l'objet sinon."""
    if not body or not body.strip():
        return {}
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ApiError(422, "VALIDATION_FAILED", "corps JSON illisible",
                       {"body": "JSON attendu, ou corps vide"}) from None
    if not isinstance(data, dict):
        raise ApiError(422, "VALIDATION_FAILED", "le corps doit être un objet JSON")
    return data


def dispatch(method: str, path: str, control, body: bytes = b"") -> tuple[int, dict]:
    """Route une requête. ``control`` est un :class:`deck.DeckControl`."""
    method = method.upper()
    parts = [unquote(p) for p in path.split("?", 1)[0].strip("/").split("/") if p]
    try:
        if not parts:
            return 200, {"app": APP_NAME, "version": __version__, "deck": DECK_HELP}
        if parts[:2] != ["api", "deck"]:
            raise ApiError(404, "ROUTE_NOT_FOUND", f"route inconnue. Routes : {DECK_HELP}")
        rest = parts[2:]
        if rest in (["state"], ["commands"]):
            if method != "GET":
                raise ApiError(405, "METHOD_NOT_ALLOWED", f"utilisez GET /api/deck/{rest[0]}")
            return 200, (control.deck_state() if rest == ["state"] else {"commands": control.deck_commands()})
        if len(rest) == 2 and rest[0] == "commands":
            if method != "POST":
                raise ApiError(405, "METHOD_NOT_ALLOWED", "une commande exige POST")
            args = _json_body(body).get("args")
            return 200, deck.run_command(control, rest[1], {} if args is None else args)
        if rest == ["events"]:
            raise ApiError(405, "METHOD_NOT_ALLOWED", "utilisez GET /api/deck/events (flux SSE)")
        raise ApiError(404, "ROUTE_NOT_FOUND", f"route inconnue. Routes : {DECK_HELP}")
    except ApiError as exc:
        return exc.status, exc.body()


# -- serveur HTTP ------------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = APP_NAME
    sys_version = ""

    def do_GET(self):  # noqa: N802
        if self._refused():
            return
        if self.path.split("?", 1)[0].rstrip("/") == "/api/deck/events":
            self._events()
            return
        self._answer("GET")

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(min(length, 65536)) if length > 0 else b""
        if self._refused():
            return
        self._answer("POST", body)
        self.server.changed()   # une commande est partie : les flux ouverts regardent tout de suite

    def _bad_verb(self):
        if not self._refused():
            self._send_json(405, ApiError(405, "METHOD_NOT_ALLOWED", "utilisez GET ou POST").body())

    do_PUT = do_DELETE = do_PATCH = _bad_verb  # noqa: N815

    def _refused(self) -> bool:
        error = check_token(self.server.token(), self.headers.get("Authorization"))
        if error is not None:
            self._send_json(error.status, error.body())
            return True
        return False

    def _answer(self, method: str, body: bytes = b""):
        try:
            status, answer = self.server.call(method, self.path, body)
        except Exception as exc:   # ne jamais laisser le pupitre sans réponse
            log.exception("Erreur API")
            status, answer = 500, {"error": {"code": "INTERNAL", "message": str(exc)}}
        self._send_json(status, answer)

    def _events(self):
        """``GET /api/deck/events`` : ``state``, puis un ``patch`` à chaque changement.

        L'état est relu chaque seconde, et aussitôt après une commande ; un commentaire
        toutes les 25 s au plus garde la connexion (py-livedeck la juge perdue après 45 s)."""
        server = self.server
        if not server.open_stream():
            self._send_json(429, ApiError(429, "TOO_MANY_STREAMS",
                                          f"déjà {MAX_STREAMS} flux en direct ouverts").body())
            return
        try:
            status, state = server.call("GET", "/api/deck/state", b"")
            if status != 200:
                self._send_json(status, state)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._event("state", state)
            last, last_write, seen = state, time.monotonic(), server.generation
            while not server.closing.is_set():
                seen = server.wait_change(seen, POLL_SECONDS)
                if server.closing.is_set():
                    break
                status, state = server.call("GET", "/api/deck/state", b"")
                if status == 200:
                    patch = deck.build_patch(last, state)
                    if patch is not None:
                        self._event("patch", patch)
                        last, last_write = state, time.monotonic()
                        continue
                if time.monotonic() - last_write >= KEEPALIVE_SECONDS:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    last_write = time.monotonic()
        except OSError:
            pass   # le pupitre est parti
        finally:
            server.close_stream()

    def _event(self, name: str, data: dict):
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode())
        self.wfile.flush()

    def _send_json(self, status: int, body: dict):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            pass

    def log_message(self, fmt: str, *args):
        log.debug("API %s", fmt % args)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False   # Windows : sinon deux apps pourraient prendre le même port

    def __init__(self, address, call, token):
        super().__init__(address, _Handler)
        self.call, self.token = call, token
        self.closing = threading.Event()
        self.generation = 0
        self._cond = threading.Condition()
        self._streams = 0

    def changed(self):
        with self._cond:
            self.generation += 1
            self._cond.notify_all()

    def wait_change(self, seen: int, timeout: float) -> int:
        with self._cond:
            if self.generation == seen and not self.closing.is_set():
                self._cond.wait(timeout)
            return self.generation

    def open_stream(self) -> bool:
        with self._cond:
            if self._streams >= MAX_STREAMS:
                return False
            self._streams += 1
            return True

    def close_stream(self):
        with self._cond:
            self._streams -= 1

    def shutdown(self):
        self.closing.set()
        self.changed()   # réveille les flux pour qu'ils se terminent
        super().shutdown()


class ApiServer:
    """Fait tourner l'API HTTP sur un fil d'arrière-plan."""

    def __init__(self, call):
        self._call = call
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self.error = ""
        self.host = ""
        self.port = 0
        self.token = ""   # lu à chaque requête : un changement s'applique aussitôt

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, host: str, port: int) -> str:
        """Démarre (ou redémarre sur une nouvelle adresse). Renvoie un message d'erreur, ou ""."""
        if self.running and (host, int(port)) == (self.host, self.port):
            return ""
        self.stop()
        try:
            self._server = _Server((host, int(port)), self._call, lambda: self.token)
        except OSError as exc:
            self.error = f"port {port} indisponible ({exc.strerror or exc})"
            log.warning("API non démarrée : %s", self.error)
            return self.error
        self.host, self.port, self.error = host, self._server.server_address[1], ""
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.2},
                                        daemon=True, name="api")
        self._thread.start()
        return ""

    def changed(self):
        if self._server is not None:
            self._server.changed()

    def stop(self):
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=2.0)
        self.host, self.port = "", 0


# -- service Qt : réglages, fil de l'interface ------------------------------------------------


def api_settings(settings: dict) -> dict:
    """Réglages de l'API (dans settings.json, communs à tous les profils), complétés."""
    cfg = dict(API_DEFAULTS)
    raw = settings.get("api")
    if isinstance(raw, dict):
        cfg.update({k: raw[k] for k in API_DEFAULTS if k in raw})
    try:
        cfg["port"] = max(1, min(65535, int(cfg["port"])))
    except (TypeError, ValueError):
        cfg["port"] = DEFAULT_PORT
    cfg["token"] = str(cfg.get("token") or "").strip()
    return cfg


class ApiService(QObject):
    """Relie le serveur HTTP au moteur : chaque requête est exécutée sur le fil de l'interface."""
    _job = Signal(object)
    status_changed = Signal(str)   # adresse ("" = fermée), ou message d'erreur préfixé « ⚠ »

    def __init__(self, engine, on_lock=None):
        super().__init__()
        self.engine = engine
        self.server = ApiServer(self._run_request)
        self.control = deck.DeckControl(engine, lambda: self.server.token, on_lock)
        self._job.connect(self._run_job)
        # changements ponctuels : les flux ouverts regardent tout de suite (le reste : chaque seconde)
        for sig in (engine.automation_changed, engine.page_changed, engine.locked_changed,
                    engine.console_mode_changed, engine.midi.status_changed, engine.vmix.status_changed):
            sig.connect(lambda *_: self.server.changed())

    def apply(self, cfg: dict) -> str:
        """Ouvre, déplace ou ferme l'API selon les réglages. Renvoie une erreur, ou ""."""
        self.server.token = cfg["token"]
        if not cfg["enabled"]:
            self.server.stop()
            self.status_changed.emit("")
            return ""
        host = "127.0.0.1" if cfg["localOnly"] else "0.0.0.0"  # noqa: S104 - choix de l'utilisateur
        error = self.server.start(host, cfg["port"])
        self.status_changed.emit(f"⚠ {error}" if error else self.url())
        if not error:
            self.engine.log.emit(f"API Stream Deck ouverte : {self.url()}/api/deck")
        return error

    def url(self) -> str:
        if not self.server.running:
            return ""
        host = "127.0.0.1" if self.server.host.startswith("127.") else local_ip()
        return f"http://{host}:{self.server.port}"

    def stop(self):
        self.server.stop()

    def _run_request(self, method: str, path: str, body: bytes = b""):
        """Appelé depuis un fil HTTP : exécute la route sur le fil de l'interface."""
        done = threading.Event()
        box: dict = {}

        def job():
            try:
                box["result"] = dispatch(method, path, self.control, body)
            except Exception as exc:   # renvoyé à l'appelant
                box["error"] = exc
            finally:
                done.set()

        self._job.emit(job)
        if not done.wait(timeout=CALL_TIMEOUT):
            return 503, ApiError(503, "BUSY", f"{APP_NAME} n'a pas répondu à temps").body()
        if "error" in box:
            raise box["error"]
        return box["result"]

    def _run_job(self, job):
        job()   # méthode liée : la connexion est mise en file vers le fil de l'interface


def local_ip() -> str:
    """Adresse IPv4 du poste sur le réseau local (sans rien envoyer)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
