#!/usr/bin/env bash
# PreToolUse hook (matcher: write_file|edit_file|apply_patch) driven by
# .navin/code-guard.local.md. Missing file = defaults (hook does nothing).
set -euo pipefail

input=$(cat)
settings="${NAVIN_PROJECT_DIR:-.}/.navin/code-guard.local.md"
[[ -f "$settings" ]] || exit 0

parser="${NAVIN_PLUGIN_ROOT:-$(dirname "$0")/..}/scripts/parse-frontmatter.sh"
[[ -f "$parser" ]] || parser="$(dirname "$0")/../scripts/parse-frontmatter.sh"

enabled=$(bash "$parser" "$settings" enabled)
[[ "$enabled" == "false" ]] && exit 0
mode=$(bash "$parser" "$settings" mode)
mode=${mode:-block}

path=$(python3 -c '
import json, sys
ti = (json.loads(sys.argv[1] or "{}").get("tool_input") or {})
print(ti.get("path") or ti.get("file_path") or "")
' "$input")
[[ -z "$path" ]] && exit 0

while IFS= read -r prefix; do
  [[ -z "$prefix" ]] && continue
  if [[ "$path" == "$prefix"* || "$path" == "./$prefix"* ]]; then
    message="$path is protected by code-guard settings ($prefix)."
    if [[ "$mode" == "warn" ]]; then
      python3 -c 'import json,sys; print(json.dumps({"systemMessage": sys.argv[1]}))' "$message"
      exit 0
    fi
    echo "$message Edit .navin/code-guard.local.md to change the list." >&2
    exit 2
  fi
done < <(bash "$parser" "$settings" protected_paths)

exit 0
