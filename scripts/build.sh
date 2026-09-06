#!/bin/sh
# Build the production WebUI (navin/web/dist) and/or the Python backend.
#
# Usage:
#   sh scripts/build.sh              # front + backend
#   sh scripts/build.sh front        # Vite production -> navin/web/dist
#   sh scripts/build.sh backend      # pip install -e . (venv)
#
# Make: make build   /   make build front   /   make build backend
# Prod UI: make start-prod   or   make start backend (gateway sert le build)

set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=dev-lib.sh
. "$SCRIPT_DIR/dev-lib.sh"
cd "$ROOT"

if ! parse_dev_args "$@"; then
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "front" ]; then
    build_frontend
fi
if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "backend" ]; then
    build_backend
fi

ok "Build termine ($SCOPE)."
if webui_dist_ready; then
    printf "  Prod WebUI: %s\n" "$WEBUI_DIST"
    printf "  Servir:     make start-prod   ou   make start backend\n"
    printf "  URL:        http://localhost:%s/\n" "$(webui_api_port)"
fi
