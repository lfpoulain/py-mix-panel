"""Mise à jour depuis la dernière release GitHub (dépôt public, sans jeton).

Pas d'interface ici : seulement la détection, le téléchargement vérifié et le lancement de
l'installateur. Garde-fous : release stable uniquement, installateur du même dépôt GitHub
(URL contrôlée), taille et empreinte SHA-256 (fournie par GitHub) vérifiées avant exécution.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

REPO = "lfpoulain/py-mix-panel"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
USER_AGENT = "MixPanel-updater"
MAX_SIZE = 500 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass
class Update:
    version: str
    notes: str
    url: str
    name: str
    size: int
    sha256: str = ""   # vide si GitHub ne fournit pas l'empreinte (la taille reste vérifiée)


def parse_version(text: str) -> tuple[int, ...]:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    if not m:
        raise ValueError(text)
    return tuple(int(x) for x in m.groups())


def update_from_release(rel: dict, current: str = __version__) -> Update | None:
    """Une mise à jour si la release est stable, plus récente, et porte un installateur valide."""
    if rel.get("draft") or rel.get("prerelease"):
        return None
    try:
        new = parse_version(str(rel.get("tag_name", "")))
        if new <= parse_version(current):
            return None
    except ValueError:
        return None
    version = ".".join(map(str, new))
    wanted = f"MixPanel-{version}-setup.exe"
    for a in rel.get("assets") or []:
        url = str(a.get("browser_download_url", ""))
        if a.get("name") == wanted and url.startswith(DOWNLOAD_PREFIX) and url.endswith("/" + wanted):
            digest = str(a.get("digest") or "")
            sha = digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else ""
            return Update(version, str(rel.get("body") or "").strip(), url, wanted, int(a.get("size") or 0), sha)
    return None


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def fetch_latest(current: str = __version__) -> Update | None:
    """Interroge GitHub. None = déjà à jour ; UpdateError = GitHub injoignable ou release introuvable."""
    try:
        with _open(RELEASES_API, 10) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            raise UpdateError("Aucune release accessible (le dépôt est-il public ?)") from ex
        raise UpdateError(f"GitHub a répondu {ex.code}") from ex
    except (OSError, ValueError) as ex:
        raise UpdateError(f"GitHub est injoignable : {ex}") from ex
    return update_from_release(rel, current)


def download(up: Update, progress=None) -> Path:
    """Télécharge l'installateur dans un dossier temporaire et le vérifie. progress(fait, total)."""
    if not up.url.startswith(DOWNLOAD_PREFIX):
        raise UpdateError("Adresse de téléchargement refusée")
    dest = Path(tempfile.mkdtemp(prefix="mixpanel-update-")) / up.name
    part = dest.with_suffix(".part")
    h = hashlib.sha256()
    done = 0
    try:
        with _open(up.url, 60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or up.size or 0)
            while chunk := r.read(256 * 1024):
                done += len(chunk)
                if done > MAX_SIZE:
                    raise UpdateError("Fichier trop volumineux")
                h.update(chunk)
                f.write(chunk)
                if progress:
                    progress(done, total)
        if up.size and done != up.size:
            raise UpdateError(f"Téléchargement incomplet ({done} / {up.size} octets)")
        if up.sha256 and h.hexdigest() != up.sha256:
            raise UpdateError("Empreinte SHA-256 différente de celle publiée par GitHub : fichier refusé")
        part.replace(dest)
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except OSError as ex:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Téléchargement impossible : {ex}") from ex
    return dest


def can_self_update() -> bool:
    """Seule l'app installée (compilée) se remplace elle-même ; depuis les sources, on ouvre la page."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def launch_installer(path: Path):
    """Lance l'installateur en mode discret ; il ferme MixPanel s'il tourne encore, puis le relance."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(path), "/SILENT", "/NORESTART", "/RELAUNCH=1"], creationflags=flags, close_fds=True)
