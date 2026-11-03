#!/bin/sh
# Mark the edition of the next build: standard (Navin) or sm (Navin SM).
#
#   scripts/set-edition.sh sm         # before packaging Navin SM
#   scripts/set-edition.sh standard   # back to Navin
#
# navin/edition.py is read by the engine at runtime; desktop/src-tauri/edition.txt
# by the desktop shell at build time (desktop/src-tauri/build.rs). This script
# writes both. Navin SM never updates itself: the organization distributes its
# versions.
set -eu

edition="${1:-}"
case "$edition" in
    standard|sm) ;;
    *) echo "usage: $0 standard|sm" >&2; exit 2 ;;
esac

root="$(cd "$(dirname "$0")/.." && pwd)"
file="$root/navin/edition.py"
[ -f "$file" ] || { echo "set-edition: $file not found" >&2; exit 1; }

# grep -c prints 0 and exits 1 when nothing matches: the count is checked next.
count="$(grep -c '^EDITION = "[a-z]*"$' "$file" || true)"
if [ "$count" != 1 ]; then
    echo "set-edition: expected one EDITION line in $file, found $count" >&2
    exit 1
fi

tmp="$file.tmp.$$"
sed "s/^EDITION = \"[a-z]*\"\$/EDITION = \"$edition\"/" "$file" > "$tmp"
mv "$tmp" "$file"

# The desktop shell's copy (read by desktop/src-tauri/build.rs). The public
# tree has no desktop shell: nothing to stamp there.
desktop="$root/desktop/src-tauri"
if [ -d "$desktop" ]; then
    printf '%s\n' "$edition" > "$desktop/edition.txt"
fi
echo "edition: $edition"
