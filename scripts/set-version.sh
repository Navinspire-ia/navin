#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Tamponne la version distribuée dans TOUTES les sources de vérité :
#   - desktop/src-tauri/tauri.conf.json  (source lue par les builds)
#   - desktop/src-tauri/Cargo.toml
#   - desktop/package.json
#   - desktop-electron/package.json
#   - webui/package.json
#   - pyproject.toml
#   - navin/_version.py
#
# Usage :  scripts/set-version.sh 1.1.0
# Idempotent : si la version est déjà la bonne, ne réécrit rien.
# Appelé automatiquement par make linux/appimage/electron-linux/windows/macos/desktop-dmg
# quand VERSION=x.y.z est passé (défaut : version Tauri courante = no-op).
# ---------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:?usage: set-version.sh <version>}"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.]+)?$ ]]; then
  echo "set-version: version invalide '${VERSION}' (attendu x.y.z)" >&2
  exit 1
fi

python3 - "$ROOT" "$VERSION" <<'PY'
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
changed = []

def stamp(path: pathlib.Path, pattern: str, repl: str) -> None:
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if n == 0:
        raise SystemExit(f"set-version: motif version introuvable dans {path}")
    if new != text:
        path.write_text(new, encoding="utf-8")
        changed.append(str(path.relative_to(root)))

# JSON : on cible la clé "version" de tête pour préserver le formatage.
stamp(
    root / "desktop/src-tauri/tauri.conf.json",
    r'^(\s*"version"\s*:\s*")[^"]+(")',
    rf"\g<1>{version}\g<2>",
)
stamp(
    root / "desktop/package.json",
    r'^(\s*"version"\s*:\s*")[^"]+(")',
    rf"\g<1>{version}\g<2>",
)
stamp(
    root / "desktop-electron/package.json",
    r'^(\s*"version"\s*:\s*")[^"]+(")',
    rf"\g<1>{version}\g<2>",
)
# La WebUI n'est pas publiée comme paquet npm, mais sa version se retrouve dans
# le bundle et dans build-info.json : restée à 1.0.0 pendant que tout le reste
# passait en 1.3.0, elle faisait mentir toute trace venant du front.
stamp(
    root / "webui/package.json",
    r'^(\s*"version"\s*:\s*")[^"]+(")',
    rf"\g<1>{version}\g<2>",
)
stamp(
    root / "desktop/src-tauri/Cargo.toml",
    r'^(version\s*=\s*")[^"]+(")',
    rf"\g<1>{version}\g<2>",
)
stamp(
    root / "pyproject.toml",
    r'^(version\s*=\s*")[^"]+(")',
    rf"\g<1>{version}\g<2>",
)
# Frozen builds read this file; they cannot see pyproject.toml or dist-info.
stamp(
    root / "navin/_version.py",
    r'^(__version__\s*=\s*")[^"]+(")',
    rf"\g<1>{version}\g<2>",
)

# Contrôle : la conf Tauri doit maintenant porter la bonne version.
conf = json.loads((root / "desktop/src-tauri/tauri.conf.json").read_text())
assert conf["version"] == version, conf["version"]

if changed:
    print(f"set-version: {version} -> " + ", ".join(changed))
else:
    print(f"set-version: {version} (déjà à jour)")
PY
