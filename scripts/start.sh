#!/bin/sh
# Start gateway (backend) and/or Vite (front), background by default.
#
# Dev:  Vite http://localhost:5173/ (hot reload) + gateway
# Prod: gateway sert le build navin/web/dist sur http://localhost:<webui>/
#
# Usage:
#   sh scripts/start.sh                 # DEV: backend + Vite
#   sh scripts/start.sh backend         # gateway seul (prod UI = le build)
#   sh scripts/start.sh front           # Vite seul
#   sh scripts/start.sh --prod          # prod: build si besoin + gateway, sans Vite
#   sh scripts/start.sh --fg            # gateway only, foreground
#   sh scripts/start.sh --install       # install then start
#
# Make: make start   /   make start-prod   /   make start backend   /   make start front
# Stop: sh scripts/stop.sh

set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=dev-lib.sh
. "$SCRIPT_DIR/dev-lib.sh"
cd "$ROOT"

if ! parse_dev_args "$@"; then
    sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

if [ "$PROD" = "1" ]; then
    if [ "$SCOPE" = "front" ]; then
        die "--prod sert le build via le gateway. Utilisez: make start-prod"
    fi
    SCOPE="backend"
fi

if [ "$DO_INSTALL" = "1" ]; then
    sh "$SCRIPT_DIR/install.sh" "$SCOPE"
fi

if [ "$PROD" = "1" ] && ! webui_dist_ready; then
    info "Pas de build WebUI. Construction de navin/web/dist ..."
    build_frontend
fi

if [ "$FG" = "1" ]; then
    if [ "$SCOPE" = "front" ]; then
        die "--fg s'applique au gateway, pas au frontend. Utilisez: sh scripts/start.sh front"
    fi
    if [ ! -x "$NAVIN" ]; then
        sh "$SCRIPT_DIR/install.sh" backend
    fi
    if [ "$SCOPE" = "all" ]; then
        if [ ! -d "${WEBUI_DIR}/node_modules" ]; then
            sh "$SCRIPT_DIR/install.sh" front
        fi
        start_front_bg || true
    fi
    start_backend_fg
fi

err=0
if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "backend" ]; then
    if [ ! -x "$NAVIN" ]; then
        sh "$SCRIPT_DIR/install.sh" backend
    fi
    start_backend_bg || err=1
fi

if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "front" ]; then
    if [ ! -d "${WEBUI_DIR}/node_modules" ]; then
        sh "$SCRIPT_DIR/install.sh" front
    fi
    start_front_bg || err=1
fi

printf "\n"
if [ "$err" -ne 0 ]; then
    warn "Navin: demarrage partiel ($SCOPE). Voir les messages ci-dessus."
    print_urls
    exit "$err"
fi
ok "Navin est lance ($SCOPE)."
print_urls
