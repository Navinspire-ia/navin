#!/usr/bin/env bash
# Run a hook script with a realistic event and report what Navin would see.
# Usage: test-hook.sh <Event> <script> [tool_input-json | prompt-text]
#   test-hook.sh PreToolUse ./validate-bash.sh '{"command": "rm -rf /"}'
#   test-hook.sh UserPromptSubmit ./check-prompt.sh 'deploy to prod'
#   test-hook.sh Stop ./require-tests.sh
set -uo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <Event> <script> [tool_input-json | prompt]" >&2
  exit 2
fi
event=$1
script=$2
arg=${3:-}
tool=${TOOL_NAME:-}

payload=$(python3 - "$event" "$arg" "$tool" <<'PY'
import json, os, sys
event, arg, tool = sys.argv[1], sys.argv[2], sys.argv[3]
body = {"hook_event_name": event, "session_id": "test-session", "cwd": os.getcwd()}
if event in ("PreToolUse", "PostToolUse"):
    try:
        ti = json.loads(arg) if arg else {}
    except ValueError:
        ti = {"command": arg}
    guess = "exec" if "command" in ti else ("write_file" if "content" in ti else "edit_file")
    body.update(tool_name=tool or guess, tool_input=ti)
    if event == "PostToolUse":
        body["tool_response"] = "ok"
elif event == "UserPromptSubmit":
    body["prompt"] = arg or "hello"
elif event == "SessionStart":
    body["source"] = "startup"
elif event in ("Stop", "SubagentStop"):
    body.update(stop_hook_active=arg == "active", last_assistant_message="Done.")
print(json.dumps(body))
PY
)

export NAVIN_PROJECT_DIR=${NAVIN_PROJECT_DIR:-$PWD}
export NAVIN_PLUGIN_ROOT=${NAVIN_PLUGIN_ROOT:-$(cd "$(dirname "$script")/../.." 2>/dev/null && pwd)}
out=$(mktemp)
err=$(mktemp)
start=$(date +%s%N 2>/dev/null || date +%s)
printf '%s' "$payload" | bash "$script" >"$out" 2>"$err"
code=$?
end=$(date +%s%N 2>/dev/null || date +%s)
ms=$(( (end - start) / 1000000 ))

echo "input:  $payload"
echo "exit:   $code ($( [[ $code -eq 0 ]] && echo allow || { [[ $code -eq 2 ]] && echo BLOCK || echo 'hook error'; } ))"
echo "time:   ${ms} ms"
echo "stdout: $(cat "$out")"
echo "stderr: $(cat "$err")"
if [[ $code -eq 0 && -s "$out" ]] && head -c 1 "$out" | grep -q '{'; then
  python3 -c 'import json,sys; json.load(open(sys.argv[1])); print("json:   valid")' "$out" 2>/dev/null || echo "json:   INVALID (Navin treats it as plain text)"
fi
rm -f "$out" "$err"
exit 0
