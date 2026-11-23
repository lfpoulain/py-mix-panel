"""L'API Stream Deck commune à l'écosystème (``/api/deck``, contrat d'orchestream-v2).

py-livedeck parle un seul protocole à toutes les apps :

* ``GET /api/deck/state`` : tout l'état, plus ``commands`` et ``keys`` ;
* ``GET /api/deck/events`` : flux SSE, ``state`` à l'ouverture puis ``patch`` à chaque changement ;
* ``GET /api/deck/commands`` : les commandes, avec les valeurs de leurs arguments ;
* ``POST /api/deck/commands/<id>`` avec ``{"args": {...}}`` (ou un corps vide) :
  répond ``{"ok", "message", "data"}``.

MixPanel décrit lui-même chaque touche, prête à dessiner (``id`` stable, ``group``,
``title``, ``subtitle``, ``active``, ``tone``, ``available``/``reason``) : une scène ou une
macro créée dans MixPanel apparaît toute seule sur le Stream Deck.

On n'y expose que ce qui est propre à MixPanel (scènes, macros, ducking, page, verrou,
mode de la console) : les sources vMix se pilotent déjà directement depuis LiveDeck.

Les fonctions de construction sont pures (dict d'état en entrée, dicts en sortie) et
testables sans Qt. :class:`DeckControl` lit l'état du moteur et exécute les commandes ;
il doit être appelé sur le fil de l'interface, qui possède le moteur.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from . import APP_NAME, __version__


class ApiError(Exception):
    """Une réponse exploitable par le pupitre : statut HTTP, code machine, message en français."""

    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details

    def body(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


# -- commandes ------------------------------------------------------------------------
# ``target`` dit à DeckControl quoi faire, ``mode`` comment (on / off / toggle).

_ON_OFF = (("start", "on", "Activer"), ("stop", "off", "Désactiver"), ("toggle", "toggle", "Basculer"))

COMMANDS: list[dict] = [
    {"id": "scene.recall", "label": "Rappeler une scène", "argNames": ["scene"], "target": "scene"},
    {"id": "scene.update", "label": "Enregistrer l'état actuel dans une scène", "argNames": ["scene"],
     "target": "scene_update"},
    {"id": "macro.run", "label": "Lancer une macro", "argNames": ["macro"], "target": "macro"},
]
COMMANDS += [{"id": f"ducking.{verb}", "label": f"{text} le ducking", "argNames": [],
              "target": "ducking", "mode": mode} for verb, mode, text in _ON_OFF]
COMMANDS += [
    {"id": "page.set", "label": "Afficher une page (A ou B)", "argNames": ["page"], "target": "page"},
    {"id": "page.toggle", "label": "Basculer entre les pages A et B", "argNames": [], "target": "page",
     "mode": "toggle"},
]
COMMANDS += [{"id": f"lock.{verb}", "label": f"{text} le verrouillage", "argNames": [],
              "target": "lock", "mode": mode} for verb, mode, text in _ON_OFF]
COMMANDS += [
    {"id": "console.standard", "label": "X-Touch en mode standard (MixPanel)", "argNames": [],
     "target": "console", "mode": "standard"},
    {"id": "console.mackie", "label": "X-Touch en mode Mackie (autre logiciel)", "argNames": [],
     "target": "console", "mode": "mc"},
    {"id": "console.modeToggle", "label": "Basculer le mode de la X-Touch", "argNames": [],
     "target": "console", "mode": "toggle"},
]
_BY_ID = {c["id"]: c for c in COMMANDS}

NO_VMIX = "vMix n'est pas connecté."
NO_XTOUCH = "X-Touch non connectée."
NO_DUCKING = "Ducking non configuré : choisis les micros et les sources à baisser."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _key(key_id: str, group: str, command: str | None, args: dict, title: str, subtitle: str | None,
         active: bool, tone: str, reason: str | None = None) -> dict:
    return {"id": key_id, "group": group, "command": command, "args": args, "title": title,
            "subtitle": subtitle or None, "active": bool(active), "tone": tone,
            "available": reason is None, "reason": reason}


# -- disponibilité ------------------------------------------------------------------------


def _command_reason(command: dict, status: dict) -> str | None:
    """Pourquoi une commande ne peut pas partir maintenant (indépendamment de ses arguments)."""
    target, mode = command["target"], command.get("mode")
    if target in ("scene", "scene_update"):
        if not status["scenes"]:
            return "Aucune scène enregistrée."
        if not status["vmix"]["connected"]:
            return NO_VMIX
    if target == "macro" and not status["macros"]:
        return "Aucune macro."
    if target == "ducking" and mode != "off" and not status["ducking"]["configured"] \
            and not status["ducking"]["enabled"]:
        return NO_DUCKING
    if target == "console" and not status["xtouch"]["connected"]:
        return NO_XTOUCH
    return None


def _macro_reason(macro: dict) -> str | None:
    return None if macro["steps"] else "Macro vide."


# -- touches ------------------------------------------------------------------------------


def build_keys(status: dict) -> list[dict]:
    """Toutes les touches que le pupitre peut placer, dans l'ordre d'affichage."""
    vmix, xtouch, duck = status["vmix"], status["xtouch"], status["ducking"]
    mc = xtouch["mode"] == "mc"
    keys = [
        _key("status.vmix", "État", None, {}, "vMix",
             (f"v{vmix['version']}" if vmix["version"] else "Connecté") if vmix["connected"] else "Déconnecté",
             vmix["connected"], "success" if vmix["connected"] else "danger"),
        _key("status.xtouch", "État", None, {}, "X-Touch",
             ("Mode Mackie (en pause)" if mc else "Connectée") if xtouch["connected"] else "Déconnectée",
             xtouch["connected"], ("warning" if mc else "success") if xtouch["connected"] else "danger"),
    ]
    no_vmix = None if vmix["connected"] else NO_VMIX
    for scene in status["scenes"]:
        keys.append(_key(f"scene.recall:{scene['id']}", "Scènes", "scene.recall", {"scene": scene["id"]},
                         scene["name"], "En place" if scene["active"] else "Rappeler",
                         scene["active"], "success" if scene["active"] else "neutral", no_vmix))
    for macro in status["macros"]:
        running = macro["running"]
        steps = macro["steps"]
        keys.append(_key(f"macro.run:{macro['id']}", "Macros", "macro.run", {"macro": macro["id"]},
                         macro["name"], "En cours" if running else f"{steps} étape{'s' if steps > 1 else ''}",
                         running, "info" if running else "neutral", _macro_reason(macro)))

    if duck["active"]:
        duck_sub, duck_tone = "Musique baissée", "warning"
    elif duck["enabled"]:
        duck_sub, duck_tone = "Activé", "success"
    else:
        duck_sub, duck_tone = "Désactivé", "neutral"
    keys.append(_key("ducking.toggle", "Automatisation", "ducking.toggle", {}, "Ducking", duck_sub,
                     duck["enabled"], duck_tone,
                     None if duck["configured"] or duck["enabled"] else NO_DUCKING))

    locked = status["locked"]
    keys += [
        _key("page.toggle", "Console", "page.toggle", {}, "Page", f"Page {status['page']}",
             status["page"] == "B", "accent" if status["page"] == "B" else "neutral"),
        _key("lock.toggle", "Console", "lock.toggle", {}, "Verrouillage",
             "Verrouillé" if locked else "Déverrouillé", locked, "danger" if locked else "neutral"),
        _key("console.modeToggle", "Console", "console.modeToggle", {}, "Mode X-Touch",
             "Mackie (MC)" if mc else "Standard", mc, "warning" if mc else "neutral",
             None if xtouch["connected"] else NO_XTOUCH),
    ]
    return keys


# -- état, commandes, patchs ----------------------------------------------------------------


def build_commands(status: dict, with_values: bool = False) -> list[dict]:
    """Commandes avec leur disponibilité ; avec ``with_values``, les valeurs permises des arguments."""
    out = []
    for command in COMMANDS:
        reason = _command_reason(command, status)
        item = {"id": command["id"], "label": command["label"], "argNames": list(command["argNames"]),
                "available": reason is None, "reason": reason}
        if with_values:
            item["args"] = [_arg_values(name, status) for name in command["argNames"]]
        out.append(item)
    return out


def _arg_values(name: str, status: dict) -> dict:
    if name == "scene":
        values = [{"value": s["id"], "label": s["name"]} for s in status["scenes"]]
    elif name == "macro":
        values = [{"value": m["id"], "label": m["name"]} for m in status["macros"]]
    else:  # page
        values = [{"value": "A", "label": "Page A"}, {"value": "B", "label": "Page B"}]
    return {"name": name, "type": "enum", "values": values}


def build_state(status: dict, token_set: bool) -> dict:
    """``GET /api/deck/state`` : les sections d'état, plus les commandes et les touches."""
    state = {"token": {"name": "jeton API" if token_set else "accès libre", "permissions": []}}
    state.update(copy.deepcopy(status))   # copie : le prochain patch se compare à elle
    state["commands"] = build_commands(status)
    state["keys"] = build_keys(status)
    state["updatedAt"] = _now()
    return state


def build_patch(old: dict, new: dict) -> dict | None:
    """Ce qui a changé entre deux états, en ``patch`` SSE (None si rien n'a changé)."""
    changed = {k: v for k, v in new.items() if k not in ("keys", "updatedAt") and old.get(k) != v}
    old_keys = {k["id"]: k for k in old.get("keys", [])}
    new_keys = {k["id"]: k for k in new.get("keys", [])}
    keys = [k for kid, k in new_keys.items() if old_keys.get(kid) != k]
    removed = [kid for kid in old_keys if kid not in new_keys]
    if not (changed or keys or removed):
        return None
    return {"changed": changed, "keys": keys, "removedKeys": removed,
            "updatedAt": new.get("updatedAt") or _now()}


# -- exécution ----------------------------------------------------------------------------


def _invalid(arg: str, message: str) -> ApiError:
    return ApiError(422, "VALIDATION_FAILED", message, {f"args.{arg}": message})


def _arg(args: dict, name: str) -> str:
    value = args.get(name)
    if value is None or str(value).strip() == "":
        raise _invalid(name, f"argument « {name} » manquant")
    return str(value).strip()


def _refused(message: str) -> ApiError:
    return ApiError(409, "REFUSED", message)


def run_command(control: "DeckControl", command_id: str, args) -> dict:
    """``POST /api/deck/commands/<id>`` : exécute la commande, répond comme orchestream."""
    command = _BY_ID.get(command_id)
    if command is None:
        raise ApiError(404, "COMMAND_NOT_FOUND", f"commande « {command_id} » inconnue")
    if not isinstance(args, dict):
        raise _invalid("args", "« args » doit être un objet")
    status = control.status()
    target, mode = command["target"], command.get("mode")

    if target in ("scene", "scene_update"):
        sid = _arg(args, "scene")
        scene = next((s for s in status["scenes"] if s["id"] == sid), None)
        if scene is None:   # cible inconnue : 422, jamais 404 (lu comme « pas d'API deck »)
            raise _invalid("scene", f"scène « {sid} » introuvable")
        if not status["vmix"]["connected"]:
            raise _refused(NO_VMIX)
        data = control.recall_scene(sid) if target == "scene" else control.update_scene(sid)
        done = f"Scène « {scene['name']} » " + ("rappelée" if target == "scene" else "mise à jour")
    elif target == "macro":
        mid = _arg(args, "macro")
        macro = next((m for m in status["macros"] if m["id"] == mid), None)
        if macro is None:
            raise _invalid("macro", f"macro « {mid} » introuvable")
        if not macro["steps"]:
            raise _refused(f"La macro « {macro['name']} » est vide.")
        data = control.run_macro(mid)
        done = f"Macro « {macro['name']} » lancée"
    elif target == "ducking":
        on = (not status["ducking"]["enabled"]) if mode == "toggle" else mode == "on"
        if on and not status["ducking"]["configured"]:
            raise _refused(NO_DUCKING)
        data = control.set_ducking(on)
        done = "Ducking " + ("activé" if on else "désactivé")
    elif target == "page":
        if mode == "toggle":
            page = "B" if status["page"] == "A" else "A"
        else:
            page = _arg(args, "page").upper()
            if page not in ("A", "B"):
                raise _invalid("page", f"page « {page} » inconnue (A ou B)")
        data = control.set_page(page)
        done = f"Page {page}"
    elif target == "lock":
        on = (not status["locked"]) if mode == "toggle" else mode == "on"
        data = control.set_locked(on)
        done = "Verrouillé" if on else "Déverrouillé"
    else:  # console
        if not status["xtouch"]["connected"]:
            raise _refused(NO_XTOUCH)
        if mode == "toggle":
            mode = "standard" if status["xtouch"]["mode"] == "mc" else "mc"
        data = control.set_console_mode(mode)
        done = "X-Touch en mode " + ("Mackie (MC)" if mode == "mc" else "standard")
    return {"ok": True, "message": done, "data": data}


class DeckControl:
    """Lit l'état du moteur et exécute les commandes. Toujours sur le fil de l'interface."""

    def __init__(self, engine, token=lambda: "", on_lock=None):
        self.engine = engine
        self.token = token          # jeton actuel (lu à chaque état, pour « token.name »)
        self.on_lock = on_lock      # verrou changé à distance : la fenêtre l'enregistre

    def status(self) -> dict:
        e = self.engine
        duck = e.ducker.cfg
        return {
            "app": {"name": APP_NAME, "version": __version__},
            "profile": {"name": e.profile.name},
            "vmix": {"connected": bool(e.state.online), "version": e.state.version or ""},
            "xtouch": {"connected": bool(getattr(e.midi, "connected", False)),
                       "port": getattr(e.midi, "port_name", "") or "",
                       "mode": e.console_mode},
            "page": e.page,
            "locked": bool(e.locked),
            "ducking": {"enabled": bool(duck["enabled"]), "active": bool(e.ducker.active),
                        "configured": bool(duck["triggers"] and duck["targets"])},
            "scenes": [{"id": s["id"], "name": s.get("name", ""), "active": bool(e.scenes.matches(s["id"]))}
                       for s in e.profile.scenes],
            "macros": [{"id": m["id"], "name": m.get("name", ""),
                        "steps": sum(1 for st in m.get("steps", []) if st.get("action") != "wait"),
                        "running": m["id"] in e.macros.running}
                       for m in e.profile.macros],
        }

    def deck_state(self) -> dict:
        return build_state(self.status(), token_set=bool(self.token()))

    def deck_commands(self) -> list[dict]:
        return build_commands(self.status(), with_values=True)

    # -- verbes (renvoient l'état à jour, pour que la touche se redessine sans relire) --
    def recall_scene(self, sid: str) -> dict:
        self.engine.scenes.recall(sid)
        return self.status()

    def update_scene(self, sid: str) -> dict:
        self.engine.scenes.update(sid)
        return self.status()

    def run_macro(self, mid: str) -> dict:
        self.engine.macros.run(mid)
        return self.status()

    def set_ducking(self, on: bool) -> dict:
        if bool(self.engine.ducker.cfg["enabled"]) != on:
            self.engine.ducker.set_enabled(on)
        return self.status()

    def set_page(self, page: str) -> dict:
        self.engine.set_page(page)
        return self.status()

    def set_locked(self, on: bool) -> dict:
        self.engine.set_locked(on)
        if self.on_lock:
            self.on_lock(on)
        return self.status()

    def set_console_mode(self, mode: str) -> dict:
        if self.engine.console_mode != mode:
            self.engine.set_console_mode(mode)
        return self.status()
