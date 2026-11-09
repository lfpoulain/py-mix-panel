"""Extrait la section d'une version de CHANGELOG.md (notes de la release GitHub).

    python tools/release_notes.py 1.0.0 > notes.md
"""
import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

FOOTER = """
---

### Installation

1. Télécharge **`{exe}`** ci-dessous et lance-le (aucun droit administrateur requis).
2. Ferme Central Control avant le premier lancement de MixPanel (il bloque le port MIDI de la X-Touch).

Mise à jour : MixPanel te la propose dans son en-tête, ou relance simplement le nouvel installateur. Ta configuration est dans `%APPDATA%\\MixPanel` et n'est jamais touchée.
Windows SmartScreen peut afficher un avertissement au premier lancement (installateur non signé) : « Informations complémentaires » → « Exécuter quand même ».
"""


def notes(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        sys.exit(f"Aucune section « ## {version} » dans CHANGELOG.md")
    return m.group(1).strip() + "\n" + FOOTER.format(exe=f"MixPanel-{version}-setup.exe")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1].lstrip("v")))
