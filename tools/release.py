"""Publie une nouvelle version de MixPanel.

    python tools/release.py 1.1.0            # prépare, commite, tague et pousse
    python tools/release.py 1.1.0 --dry-run  # vérifie seulement

Étapes :
  1. vérifie que le dépôt est propre et que CHANGELOG.md contient une section « ## 1.1.0 » ;
  2. met à jour pymixpanel/__init__.py ;
  3. lance les tests ;
  4. crée le commit « Version 1.1.0 » et le tag v1.1.0, puis pousse les deux.

Le tag déclenche le workflow GitHub « Release », qui compile l'installateur et publie la release.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "pymixpanel" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"


def git(*args: str, capture=True) -> str:
    r = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=capture)
    if r.returncode:
        sys.exit(f"git {' '.join(args)} a échoué :\n{r.stderr}")
    return (r.stdout or "").strip()


def main():
    ap = argparse.ArgumentParser(description="Publie une nouvelle version de MixPanel.")
    ap.add_argument("version", help="ex. 1.1.0")
    ap.add_argument("--dry-run", action="store_true", help="vérifier sans rien modifier")
    args = ap.parse_args()
    v = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        sys.exit("La version doit être de la forme 1.2.3")

    if git("status", "--porcelain"):
        sys.exit("Le dépôt contient des modifications non commitées : commite-les d'abord.")
    if git("tag", "--list", f"v{v}"):
        sys.exit(f"Le tag v{v} existe déjà.")
    if not re.search(rf"^## {re.escape(v)}\b", CHANGELOG.read_text(encoding="utf-8"), re.M):
        sys.exit(f"Ajoute d'abord une section « ## {v} — AAAA-MM-JJ » dans CHANGELOG.md.")

    print("Tests…")
    if subprocess.run([sys.executable, "tests/test_core.py"], cwd=ROOT, capture_output=True).returncode:
        sys.exit("Les tests échouent : release annulée.")

    if args.dry_run:
        print(f"Tout est prêt pour la version {v} (dry-run : rien n'a été modifié).")
        return

    text = INIT.read_text(encoding="utf-8")
    new = re.sub(r'__version__ = "[^"]+"', f'__version__ = "{v}"', text)
    if new != text:
        INIT.write_text(new, encoding="utf-8")
        git("commit", "-am", f"Version {v}")
    git("tag", "-a", f"v{v}", "-m", f"MixPanel {v}")
    git("push", "--follow-tags", capture=False)
    print(f"\nv{v} poussée. Le workflow « Release » compile l'installateur et publie la release sur GitHub.")


if __name__ == "__main__":
    main()
