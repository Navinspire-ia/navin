#!/bin/sh
# navin stop — arrête tout le projet (gateway backend + frontend dev).
#
# Usage:
#   sh scripts/stop.sh              # arrête backend + frontend
#   sh scripts/stop.sh --backend    # gateway seul
#   sh scripts/stop.sh --front      # frontend seul

set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; NC='\033[0m'

MODE="all"
case "${1:-}" in
    --backend) MODE="backend" ;;
    --front|--frontend) MODE="front" ;;
    "") ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
esac

if [ "$MODE" = "all" ] || [ "$MODE" = "front" ]; then
    make -C webui stop || true
fi
if [ "$MODE" = "all" ] || [ "$MODE" = "backend" ]; then
    make stop || true
fi

printf "%b✓ Arrêt terminé.%b\n" "$GREEN" "$NC"
