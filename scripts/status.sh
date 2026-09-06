#!/bin/sh
# Status of gateway and/or Vite.
#
# Usage:
#   sh scripts/status.sh
#   sh scripts/status.sh backend
#   sh scripts/status.sh front
#
# Make: make status   /   make status backend   /   make status front

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

code=0
if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "backend" ]; then
    status_backend || code=1
fi
if [ "$SCOPE" = "all" ] || [ "$SCOPE" = "front" ]; then
    status_front || code=1
fi
exit "$code"
