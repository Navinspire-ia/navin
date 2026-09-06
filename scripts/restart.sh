#!/bin/sh
# Restart gateway and/or Vite.
#
# Usage:
#   sh scripts/restart.sh
#   sh scripts/restart.sh backend
#   sh scripts/restart.sh front
#
# Make: make restart   /   make restart backend   /   make restart front

set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=dev-lib.sh
. "$SCRIPT_DIR/dev-lib.sh"
cd "$ROOT"

if ! parse_dev_args "$@"; then
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
fi

sh "$SCRIPT_DIR/stop.sh" "$SCOPE"
# Leave the previous listener time to release the port.
sleep 1
sh "$SCRIPT_DIR/start.sh" "$SCOPE"
