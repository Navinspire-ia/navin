#!/bin/sh
# Install backend (.venv) and/or WebUI (npm ci).
# Also installs missing system deps via apt, dnf/yum, pacman, zypper or brew.
#
# Usage:
#   sh scripts/install.sh              # system + backend + frontend
#   sh scripts/install.sh backend
#   sh scripts/install.sh front
#   sh scripts/install.sh --no-system  # skip apt/dnf/pacman/brew
#
# Make: make install   /   make install backend   /   make install front

set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=dev-lib.sh
. "$SCRIPT_DIR/dev-lib.sh"
cd "$ROOT"

if ! parse_dev_args "$@"; then
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "backend" ]; then
    ensure_system_deps
fi
if [ "$SCOPE" = "front" ]; then
    ensure_system_deps
fi

if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "backend" ]; then
    install_backend
fi
if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "front" ]; then
    install_frontend
fi

if [ -x "$NAVIN" ]; then
    "$NAVIN" doctor || true
fi

ok "Install terminee ($SCOPE)."
printf "  Demarrer: make start   ou   sh scripts/start.sh\n"
printf "  CLI:      %s\n" "$NAVIN_CLI"
