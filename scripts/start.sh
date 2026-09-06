#!/bin/sh
# navin start - lance tout le projet (backend + frontend) en une commande.
#
# Usage:
#   sh scripts/start.sh              # backend (gateway :8765) + frontend dev (:5173), en background
#   sh scripts/start.sh --backend    # gateway seul (background)
#   sh scripts/start.sh --front      # frontend dev seul (background)
#   sh scripts/start.sh --fg         # gateway seul, au premier plan (Ctrl+C pour arrêter)
#   sh scripts/start.sh --install    # installe backend + frontend, puis démarre tout
#
# Arrêt : sh scripts/stop.sh
#
# Équivalents Make :
#   make install / make start-bg / make status / make logs      (backend, racine)
#   cd webui && make install / make start-bg / make logs        (frontend)

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

MODE="all"
INSTALL=0
for arg in "$@"; do
    case "$arg" in
        --backend) MODE="backend" ;;
        --front|--frontend) MODE="front" ;;
        --fg) MODE="fg" ;;
        --install) INSTALL=1 ;;
        -h|--help)
            sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf "%bOption inconnue: %s%b\n" "$RED" "$arg" "$NC"; exit 1 ;;
    esac
done

# --- Installation (si demandée ou si manquante) -----------------------------

if [ "$INSTALL" = "1" ] || [ ! -x "$ROOT/.venv/bin/navin" ]; then
    printf "%bInstallation backend...%b\n" "$CYAN" "$NC"
    make install
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "front" ]; then
    if [ "$INSTALL" = "1" ] || [ ! -d "$ROOT/webui/node_modules" ]; then
        printf "%bInstallation frontend...%b\n" "$CYAN" "$NC"
        make -C webui install
    fi
fi

# --- Lancement ---------------------------------------------------------------

case "$MODE" in
    fg)
        exec make start
        ;;
    backend)
        make start-bg
        ;;
    front)
        make -C webui start-bg
        ;;
    all)
        make start-bg
        make -C webui start-bg
        ;;
esac

printf "\n%b✓ Navin est lancé.%b\n" "$GREEN" "$NC"
WEBUI_URL="$(python3 -c "import json, pathlib; p=pathlib.Path.home()/'.navin'/'config.json'; print((json.load(open(p)).get('channels') or {}).get('websocket', {}).get('port', 8765) if p.is_file() else 8765)" 2>/dev/null || echo 8765)"
printf "  Gateway / WebUI intégrée : %bhttp://127.0.0.1:%s%b\n" "$CYAN" "$WEBUI_URL" "$NC"
if [ "$MODE" = "all" ] || [ "$MODE" = "front" ]; then
    printf "  WebUI dev (hot reload)   : %bhttp://127.0.0.1:5173%b\n" "$CYAN" "$NC"
fi
printf "  Logs backend  : make logs\n"
printf "  Logs frontend : make -C webui logs\n"
printf "  Tout arrêter  : sh scripts/stop.sh\n"
