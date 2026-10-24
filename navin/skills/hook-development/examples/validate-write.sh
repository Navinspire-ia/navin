#!/usr/bin/env bash
# PreToolUse hook (matcher: write_file|edit_file|apply_patch).
# Refuses writes to protected paths and content that looks like a secret.
set -euo pipefail

input=$(cat)
verdict=$(python3 - "$input" <<'PY'
import json, os, re, sys

data = json.loads(sys.argv[1] or "{}")
ti = data.get("tool_input") or {}
path = str(ti.get("path") or ti.get("file_path") or "")
text = " ".join(str(ti.get(k) or "") for k in ("content", "new_text", "patch"))
for edit in ti.get("edits") or []:
    if isinstance(edit, dict):
        path = path or str(edit.get("path") or "")
        text += " " + str(edit.get("new_text") or "")

project = os.path.realpath(os.environ.get("NAVIN_PROJECT_DIR") or os.getcwd())
if path:
    full = os.path.realpath(os.path.join(project, path))
    name = os.path.basename(full)
    if not (full == project or full.startswith(project + os.sep)):
        print(f"Write outside the workspace refused: {path}")
        sys.exit()
    if name == ".env" or name.startswith(".env.") or name.endswith((".pem", ".key")):
        print(f"{name} holds secrets; edit it manually.")
        sys.exit()
    if "/.git/" in full + "/" and full.startswith(os.path.join(project, ".git")):
        print("Direct writes inside .git are refused.")
        sys.exit()
if re.search(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{36}", text):
    print("The content contains what looks like a secret; use an environment variable instead.")
PY
)

if [[ -n "$verdict" ]]; then
  echo "$verdict" >&2
  exit 2
fi
exit 0
