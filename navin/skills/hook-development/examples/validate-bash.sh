#!/usr/bin/env bash
# PreToolUse hook (matcher: exec). Blocks destructive shell commands.
# Exit 2 with the reason on stderr to refuse the call.
set -euo pipefail

input=$(cat)
command=$(python3 -c '
import json, sys
data = json.loads(sys.argv[1] or "{}")
print((data.get("tool_input") or {}).get("command", ""))
' "$input")

[[ -z "$command" ]] && exit 0

patterns=(
  'rm[[:space:]]+-[a-zA-Z]*r[a-zA-Z]*f?[a-zA-Z]*[[:space:]]+(/|~|\$HOME)([[:space:]]|$)'
  'mkfs(\.|[[:space:]])'
  'dd[[:space:]].*of=/dev/(sd|nvme|disk)'
  ':\(\)[[:space:]]*\{[[:space:]]*:\|:&[[:space:]]*\};:'
  '(curl|wget)[^|]*\|[[:space:]]*(sudo[[:space:]]+)?(ba|z)?sh'
  'chmod[[:space:]]+-R[[:space:]]+777[[:space:]]+/'
  'git[[:space:]]+push[[:space:]].*--force([[:space:]]|$).*(main|master)'
)

for pattern in "${patterns[@]}"; do
  if [[ "$command" =~ $pattern ]]; then
    echo "Blocked by validate-bash: the command matches a destructive pattern ($pattern). Ask the user to run it manually if it is intended." >&2
    exit 2
  fi
done

exit 0
