# Hook patterns

## 1. Block dangerous shell commands (PreToolUse, matcher `exec`)

Match `rm -rf /`, `mkfs`, `dd of=/dev/`, fork bombs, `curl | sh`. Exit 2 with
the pattern that matched. See `examples/validate-bash.sh`.

## 2. Protect paths (PreToolUse, matcher `write_file|edit_file|apply_patch`)

Refuse writes to `.env`, `*.pem`, `.git/`, lock files or anything outside the
workspace. See `examples/validate-write.sh`.

## 3. Lint after every edit (PostToolUse, same matcher)

Run the linter on the edited file and print
`{"decision": "block", "reason": "<lint output>"}` when it fails; the model
reads it with the tool result and fixes the file.

```bash
file=$(python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("path",""))' <<<"$input")
[[ "$file" == *.py ]] || exit 0
if ! out=$(ruff check "$file" 2>&1); then
  python3 -c 'import json,sys; print(json.dumps({"decision":"block","reason":sys.argv[1]}))' "$out"
fi
```

## 4. Inject project context (SessionStart)

Print the current sprint goal, the branch and open blockers; plain stdout
becomes context. See `examples/load-context.sh`.

## 5. Enrich prompts (UserPromptSubmit)

When the prompt mentions "deploy", add the deployment checklist as
`additionalContext`.

## 6. Refuse prompts with secrets (UserPromptSubmit)

Detect `AKIA[0-9A-Z]{16}` or private key headers in `prompt`; exit 2 with
"Remove the secret from your message".

## 7. Require tests before finishing (Stop)

```bash
active=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))' <<<"$input")
[[ "$active" == "True" ]] && exit 0
if [[ -f .navin/tests-dirty ]]; then
  echo '{"decision": "block", "reason": "Code changed since the last test run. Run the tests and report."}'
fi
```

Pair it with a PostToolUse hook that creates `.navin/tests-dirty` on edits and
removes it when the test tool runs.

## 8. Audit log (PostToolUse, matcher `*`)

Append `date, tool_name` to `.navin/tool-audit.log`. Never block; exit 0.

## 9. Time window (PreToolUse, matcher `exec|git`)

Refuse deploy commands outside working hours with a clear reason.

## 10. Settings-driven hooks

Read `.navin/<plugin>.local.md` and exit 0 at once when `enabled: false`. See
the `plugin-settings` skill.
