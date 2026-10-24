#!/usr/bin/env bash
# Lint hook shell scripts for common mistakes.
# Usage: hook-linter.sh <script.sh> [more.sh ...]
set -uo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <script.sh> [more.sh ...]" >&2
  exit 2
fi

status=0
for script in "$@"; do
  issues=()
  warns=()
  if [[ ! -f "$script" ]]; then
    echo "[CRITICAL] $script: not found"
    status=1
    continue
  fi
  first=$(head -n 1 "$script")
  [[ "$first" == "#!"* ]] || issues+=("missing shebang (#!/usr/bin/env bash)")
  [[ -x "$script" ]] || warns+=("not executable (chmod +x), or call it with 'bash <script>'")
  grep -Eq 'set -[a-z]*e[a-z]*' "$script" || warns+=("no 'set -euo pipefail'")
  grep -Eq '\$\(cat\)|read -r|json\.load\(sys\.stdin\)|cat >/dev/null|jq ' "$script" \
    || warns+=("never reads stdin: the event JSON is ignored")
  grep -Eq '\beval\b' "$script" && issues+=("uses eval on hook input")
  grep -Eq 'exit 1([^0-9]|$)' "$script" && warns+=("exit 1 is a hook error, not a block; use exit 2 to block")
  if grep -Eq 'exit 2' "$script" && ! grep -Eq '>&2' "$script"; then
    warns+=("exits 2 without writing a reason to stderr")
  fi
  grep -Eq '(^|[^"])\$(input|command|file|path)([^"A-Za-z_]|$)' "$script" \
    && warns+=("unquoted variable expansion; quote \"\$var\"")
  grep -Eq '/(home|Users)/[A-Za-z]' "$script" && warns+=("hard-coded home path; use \$NAVIN_PLUGIN_ROOT or \$NAVIN_PROJECT_DIR")
  if command -v bash >/dev/null && ! bash -n "$script" 2>/dev/null; then
    issues+=("bash syntax error (bash -n)")
  fi
  for w in "${warns[@]}"; do echo "[WARNING] $script: $w"; done
  for i in "${issues[@]}"; do echo "[CRITICAL] $script: $i"; done
  [[ ${#issues[@]} -eq 0 ]] || status=1
  echo "$script: ${#issues[@]} error(s), ${#warns[@]} warning(s)"
done
exit $status
