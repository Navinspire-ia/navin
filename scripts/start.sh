#!/bin/sh
# Start gateway (backend) and/or Vite (front), background by default.
#
# Usage:
#   sh scripts/start.sh                 # backend + front
#   sh scripts/start.sh backend
#   sh scripts/start.sh front
#   sh scripts/start.sh --fg            # gateway only, foreground
#   sh scripts/start.sh --install       # install then start
#
# Make: make start   /   make start backend   /   make start front
# Stop: sh scripts/stop.sh

set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=dev-lib.sh
. "$SCRIPT_DIR/dev-lib.sh"
cd "$ROOT"

if ! parse_dev_args "$@"; then
    sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

if [ "$DO_INSTALL" = "1" ]; then
    sh "$SCRIPT_DIR/install.sh" "$SCOPE"
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
