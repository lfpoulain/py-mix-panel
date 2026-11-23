"""API Stream Deck (/api/deck) pour py-livedeck : état, touches, commandes, erreurs, jeton, flux SSE.

    python -m pytest tests      ou      python tests/test_deck.py
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import app, setup_engine, wait  # noqa: E402

from pymixpanel import deck  # noqa: E402
from pymixpanel.api import ApiService, api_settings, check_token, dispatch  # noqa: E402

KEY_FIELDS = {"id", "group", "command", "args", "title", "subtitle", "active", "tone", "available", "reason"}


def _status(**over) -> dict:
    st = {
        "app": {"name": "MixPanel", "version": "x"},
        "profile": {"name": "Show"},
        "vmix": {"connected": True, "version": "27"},
        "xtouch": {"connected": True, "port": "X-TOUCH", "mode": "standard"},
        "page": "A",
        "locked": False,
        "ducking": {"enabled": False, "active": False, "configured": True},
        "scenes": [{"id": "s1", "name": "Jeu", "active": True}],
        "macros": [{"id": "m1", "name": "Fin", "steps": 2, "running": False},
                   {"id": "m2", "name": "Vide", "steps": 0, "running": False}],
    }
    st.update(over)
    return st


def _keys(state) -> dict:
    return {k["id"]: k for k in state["keys"]}


def test_state_keys_and_commands():
    state = deck.build_state(_status(), token_set=False)
    assert isinstance(state["commands"], list) and state["updatedAt"].endswith("Z")
    assert state["token"] == {"name": "accès libre", "permissions": []}
    keys = _keys(state)
    for k in keys.values():
        assert set(k) == KEY_FIELDS, k["id"]
    assert list(keys)[:2] == ["status.vmix", "status.xtouch"]
    assert keys["status.vmix"]["command"] is None and keys["status.vmix"]["subtitle"] == "v27"
    scene = keys["scene.recall:s1"]
    assert (scene["command"], scene["args"], scene["active"], scene["tone"]) == \
        ("scene.recall", {"scene": "s1"}, True, "success")
    assert keys["macro.run:m1"]["subtitle"] == "2 étapes" and keys["macro.run:m1"]["available"]
    assert keys["macro.run:m2"]["available"] is False and keys["macro.run:m2"]["reason"] == "Macro vide."
    assert keys["ducking.toggle"]["subtitle"] == "Désactivé"
    assert keys["page.toggle"]["subtitle"] == "Page A"
    for cid in ("ducking.toggle", "page.toggle", "lock.toggle", "console.modeToggle"):
        assert keys[cid]["command"] == cid
    cmds = {c["id"]: c for c in deck.build_commands(_status(), with_values=True)}
    assert cmds["scene.recall"]["args"] == [{"name": "scene", "type": "enum",
                                             "values": [{"value": "s1", "label": "Jeu"}]}]
    assert cmds["page.set"]["args"][0]["values"][1] == {"value": "B", "label": "Page B"}

    # indisponibilités, avec la raison en français
    off = deck.build_state(_status(vmix={"connected": False, "version": ""},
                                   xtouch={"connected": False, "port": "", "mode": "standard"},
                                   ducking={"enabled": False, "active": False, "configured": False}), False)
    keys = _keys(off)
    assert keys["scene.recall:s1"]["reason"] == deck.NO_VMIX
    assert keys["console.modeToggle"]["reason"] == deck.NO_XTOUCH
    assert keys["ducking.toggle"]["reason"] == deck.NO_DUCKING
    assert keys["status.vmix"]["tone"] == "danger"


def test_patch_only_on_change():
    a = deck.build_state(_status(), False)
    b = deck.build_state(_status(), False)
    assert deck.build_patch(a, b) is None
    c = deck.build_state(_status(page="B", macros=[{"id": "m1", "name": "Fin", "steps": 2, "running": True}]),
                         False)
    patch = deck.build_patch(a, c)
    assert patch["changed"]["page"] == "B"
    assert {k["id"] for k in patch["keys"]} == {"page.toggle", "macro.run:m1"}
    assert patch["removedKeys"] == ["macro.run:m2"]
    assert "commands" not in patch["changed"]   # les commandes ne dépendent pas de ces changements


class _FakeControl(deck.DeckControl):
    def __init__(self, status):
        super().__init__(engine=None)
        self._st, self.calls = status, []

    def status(self):
        return self._st

    def __getattribute__(self, name):
        if name in ("recall_scene", "update_scene", "run_macro", "set_ducking", "set_page", "set_locked",
                    "set_console_mode"):
            return lambda *a: (self.calls.append((name, *a)), {"ok": 1})[1]
        return super().__getattribute__(name)


def test_routes_and_errors():
    ctl = _FakeControl(_status())
    code, body = dispatch("POST", "/api/deck/commands/scene.recall", ctl, b'{"args": {"scene": "s1"}}')
    assert code == 200 and body["ok"] and "Jeu" in body["message"] and ctl.calls[-1] == ("recall_scene", "s1")
    code, body = dispatch("POST", "/api/deck/commands/page.toggle", ctl, b"")
    assert code == 200 and ctl.calls[-1] == ("set_page", "B")
    dispatch("POST", "/api/deck/commands/lock.toggle", ctl, b"{}")
    assert ctl.calls[-1] == ("set_locked", True)
    dispatch("POST", "/api/deck/commands/console.modeToggle", ctl)
    assert ctl.calls[-1] == ("set_console_mode", "mc")

    def err(method, path, body=b""):
        code, data = dispatch(method, path, ctl, body)
        return code, data["error"]["code"], data["error"]

    assert err("POST", "/api/deck/commands/nope")[:2] == (404, "COMMAND_NOT_FOUND")
    code, c, e = err("POST", "/api/deck/commands/scene.recall", b'{"args": {"scene": "zz"}}')
    assert (code, c) == (422, "VALIDATION_FAILED") and "args.scene" in e["details"]   # jamais 404 NOT_FOUND
    code, c, e = err("POST", "/api/deck/commands/macro.run")
    assert (code, c) == (422, "VALIDATION_FAILED") and e["details"] == {"args.macro": "argument « macro » manquant"}
    assert err("POST", "/api/deck/commands/macro.run", b'{"args": {"macro": "m2"}}')[:2] == (409, "REFUSED")
    assert err("POST", "/api/deck/commands/page.set", b'{"args": {"page": "C"}}')[:2] == (422, "VALIDATION_FAILED")
    assert err("POST", "/api/deck/commands/page.set", b"[1]")[:2] == (422, "VALIDATION_FAILED")
    assert err("POST", "/api/deck/commands/page.set", b"{oops")[:2] == (422, "VALIDATION_FAILED")
    assert err("GET", "/api/deck/commands/page.toggle")[:2] == (405, "METHOD_NOT_ALLOWED")
    assert err("POST", "/api/deck/state")[:2] == (405, "METHOD_NOT_ALLOWED")
    assert err("GET", "/api/deck/nope")[:2] == (404, "ROUTE_NOT_FOUND")
    assert err("GET", "/status")[:2] == (404, "ROUTE_NOT_FOUND")
    off = _FakeControl(_status(vmix={"connected": False, "version": ""},
                               ducking={"enabled": False, "active": False, "configured": False}))
    code, data = dispatch("POST", "/api/deck/commands/scene.recall", off, b'{"args": {"scene": "s1"}}')
    assert code == 409 and data["error"]["message"] == deck.NO_VMIX
    assert dispatch("POST", "/api/deck/commands/ducking.start", off)[0] == 409
    assert dispatch("POST", "/api/deck/commands/ducking.stop", off)[0] == 200


def test_token_and_settings():
    assert check_token("", None) is None
    assert check_token("abc", "Bearer abc") is None
    assert check_token("abc", "bearer  abc ") is None
    for bad in (None, "", "Bearer abd", "Basic abc", "abc"):
        e = check_token("abc", bad)
        assert e.status == 401 and e.code == "INVALID_TOKEN"
    assert api_settings({}) == {"enabled": False, "port": 5085, "localOnly": True, "token": ""}
    assert api_settings({"api": {"port": "x", "token": " t ", "enabled": True}})["port"] == 5085
    assert api_settings({"api": {"token": " t "}})["token"] == "t"


def _request(url, method="GET", body=None, token=None):
    req = urllib.request.Request(url, data=body, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    out = {}

    def go():
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                out["status"], out["body"] = r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            out["status"], out["body"] = e.code, json.loads(e.read())

    t = threading.Thread(target=go)
    t.start()
    assert wait(lambda: not t.is_alive(), 6)   # le fil principal traite les requêtes (fil de l'interface)
    return out["status"], out["body"]


def test_engine_live_sse(tmp_path):
    eng, midi = setup_engine(tmp_path)
    eng.profile.scenes.clear()
    eng.profile.macros[:] = [{"id": "m1", "name": "Fin", "steps": [{"action": "wait", "ms": 10}]}]
    eng.scenes.capture("Jeu")
    saved = []
    svc = ApiService(eng, on_lock=saved.append)
    try:
        assert svc.apply({"enabled": True, "port": 0, "localOnly": True, "token": "s3cret"}) == ""
        base = svc.url()
        assert base.startswith("http://127.0.0.1:")

        assert _request(base + "/api/deck/state")[0] == 401
        assert _request(base + "/api/deck/state?token=s3cret")[0] == 401   # jamais lu dans l'URL
        code, state = _request(base + "/api/deck/state", token="s3cret")
        assert code == 200 and state["token"]["name"] == "jeton API"
        assert state["vmix"]["connected"] and state["page"] == "A"
        sid = state["scenes"][0]["id"]
        assert _keys(state)[f"scene.recall:{sid}"]["active"] is True
        assert _keys(state)["macro.run:m1"]["reason"] == "Macro vide."   # une pause seule ne fait rien

        # flux SSE : state à l'ouverture, puis un patch après une commande
        events, stop = [], threading.Event()

        def listen():
            req = urllib.request.Request(base + "/api/deck/events")
            req.add_header("Authorization", "Bearer s3cret")
            with urllib.request.urlopen(req, timeout=10) as r:
                events.append(("ctype", r.headers["Content-Type"]))
                name = None
                for raw in r:
                    line = raw.decode().rstrip("\n")
                    if line.startswith("event: "):
                        name = line[7:]
                    elif line.startswith("data: "):
                        events.append((name, json.loads(line[6:])))
                    if stop.is_set():
                        break

        t = threading.Thread(target=listen, daemon=True)
        t.start()
        assert wait(lambda: any(e[0] == "state" for e in events), 5)
        assert events[0] == ("ctype", "text/event-stream; charset=utf-8")

        code, body = _request(base + "/api/deck/commands/page.toggle", "POST", b"", token="s3cret")
        assert code == 200 and body["ok"] and body["data"]["page"] == "B" and eng.page == "B"
        assert wait(lambda: any(e[0] == "patch" and e[1]["changed"].get("page") == "B" for e in events), 5)
        patch = next(e[1] for e in events if e[0] == "patch")
        assert any(k["id"] == "page.toggle" and k["subtitle"] == "Page B" for k in patch["keys"])

        code, body = _request(base + "/api/deck/commands/lock.start", "POST", b"{}", token="s3cret")
        assert code == 200 and eng.locked and saved == [True]
        _request(base + "/api/deck/commands/lock.stop", "POST", token="s3cret")
        assert not eng.locked and saved == [True, False]

        code, body = _request(base + "/api/deck/commands/scene.recall", "POST",
                              json.dumps({"args": {"scene": "inconnue"}}).encode(), token="s3cret")
        assert code == 422 and body["error"]["code"] == "VALIDATION_FAILED"
        code, body = _request(base + "/api/deck/commands", token="s3cret")
        assert code == 200 and any(c["id"] == "scene.recall" for c in body["commands"])
        stop.set()

        # API fermée : plus rien n'écoute
        svc.apply({"enabled": False, "port": 0, "localOnly": True, "token": ""})
        assert svc.url() == "" and not svc.server.running
    finally:
        svc.stop()
        eng.set_page("A")
        eng.vmix.stop()
    app.processEvents()


if __name__ == "__main__":
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                fn(d) if fn.__code__.co_argcount else fn()
            print("OK ", name)
