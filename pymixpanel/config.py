"""Profils (associations + réglages) enregistrés en JSON dans le dossier utilisateur.

Les données vivent dans %APPDATA%\\MixPanel, indépendamment de l'exe : on peut
remplacer l'exe par une nouvelle version (ou le lancer depuis les sources) et
retrouver exactement la même configuration. MIXPANEL_HOME permet de choisir un
autre dossier (clé USB, tests…).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _data_dir() -> Path:
    if os.environ.get("MIXPANEL_HOME"):
        return Path(os.environ["MIXPANEL_HOME"])
    return Path(os.environ.get("APPDATA") or Path.home()) / "MixPanel"


def _legacy_dirs() -> list[Path]:
    """Anciens emplacements : à côté de l'exe (v1.0.0) ou à la racine du projet."""
    if getattr(sys, "frozen", False):
        return [Path(sys.executable).resolve().parent]
    return [Path(__file__).resolve().parent.parent]


APP_DIR = _data_dir()
PROFILES_DIR = APP_DIR / "profiles"
SETTINGS_FILE = APP_DIR / "settings.json"


def migrate_legacy() -> list[str]:
    """Copie (sans rien écraser) les profils d'un ancien emplacement vers le dossier
    utilisateur, et met à jour le dernier profil ouvert. Renvoie les profils copiés."""
    copied: list[str] = []
    for old in _legacy_dirs():
        old_profiles = old / "profiles"
        if old.resolve() == APP_DIR.resolve() or not old_profiles.is_dir():
            continue
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        for src in old_profiles.glob("*.json"):
            dst = PROFILES_DIR / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                copied.append(src.stem)
        old_settings = old / "settings.json"
        if not SETTINGS_FILE.exists() and old_settings.exists():
            try:
                last = json.loads(old_settings.read_text(encoding="utf-8")).get("last_profile")
                if last:
                    save_settings({"last_profile": str(PROFILES_DIR / Path(last).name)})
            except (OSError, ValueError):
                pass
    return copied


@dataclass
class Profile:
    name: str = "Mon profil"
    vmix_host: str = "localhost"
    vmix_port: int = 8099
    poll_ms: int = 100
    midi_in: str = "X-TOUCH"
    midi_out: str = "X-TOUCH"
    global_channel: int = 1         # canal global de la X-Touch : ses retours y sont écoutés
    led_on: int = 2                 # vélocités LED (firmware récent : 0 éteint, 2 allumé, 3 clignote)
    led_off: int = 0
    led_blink: int = 3
    feedback_rev: int = 2           # 2 = table de retours officielle (v1.0.2)
    hold_ms: int = 350              # protection fader après un mouvement manuel
    strip_template: list = field(default_factory=lambda: ["M", "A", "B"])
    midi_overrides: dict = field(default_factory=dict)  # cid -> {"midi": {...}, "touch": {...}}
    mappings: dict = field(default_factory=dict)        # page A : cid -> {"action": ..., params...}
    mappings_b: dict = field(default_factory=dict)      # page B (Layer B de la console)
    scenes: list = field(default_factory=list)          # instantanés audio rappelables
    macros: list = field(default_factory=list)          # suites d'actions
    ducking: dict = field(default_factory=dict)         # voir automation.DUCK_DEFAULTS
    ring_meters: bool = False                           # anneaux libres = vu-mètre de la tranche
    force_standard: bool = True                         # remettre la console en mode MIDI standard
    ring_modes: dict = field(default_factory=dict)      # "A:enc1" -> auto | cursor | state | state_blink | off

    path: Path | None = field(default=None, compare=False, repr=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("path", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "path"}
        if "feedback_rev" not in d:
            # profils < 1.0.2 : la vélocité 127 est ignorée par la X-Touch
            if known.get("led_on", 127) == 127:
                known["led_on"] = 2
            known["feedback_rev"] = 2
        return cls(**known)

    def save(self, path: Path | None = None):
        path = path or self.path or profile_path(self.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        self.path = path

    @classmethod
    def load(cls, path: Path) -> "Profile":
        p = cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
        p.path = Path(path)
        return p


def profile_path(name: str) -> Path:
    safe = re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "profil"
    return PROFILES_DIR / f"{safe}.json"


def list_profiles() -> list[Path]:
    if not PROFILES_DIR.exists():
        return []
    return sorted(PROFILES_DIR.glob("*.json"), key=lambda p: p.stem.lower())


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
