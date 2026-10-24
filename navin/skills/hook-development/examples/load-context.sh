#!/usr/bin/env bash
# SessionStart hook. Plain stdout is added to the context of the session.
set -euo pipefail

cat >/dev/null  # the event JSON is not needed here
cd "${NAVIN_PROJECT_DIR:-.}"

echo "## Project context (from load-context hook)"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "- Branch: $(git branch --show-current 2>/dev/null || echo detached)"
  echo "- Uncommitted files: $(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  echo "- Last commit: $(git log -1 --pretty='%h %s' 2>/dev/null || echo none)"
fi
if [[ -f .navin/sprint.md ]]; then
  echo "- Sprint goal: $(head -n 1 .navin/sprint.md)"
fi
if [[ -f package.json ]]; then
  echo "- Node project; test command: $(python3 -c 'import json; print(json.load(open("package.json")).get("scripts",{}).get("test","none"))')"
fi
exit 0
