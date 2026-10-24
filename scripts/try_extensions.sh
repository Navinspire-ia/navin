#!/usr/bin/env bash
# Try the hookify, security-guidance and end-of-turn checks features from the CLI.
#
#   scripts/try_extensions.sh setup [dir]     create a small demo project (default /tmp/navin-try)
#   scripts/try_extensions.sh selftest [dir]  run every hook path offline, without any model call
#   scripts/try_extensions.sh scenarios [dir] print the prompts to try with `navin agent`
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
NAVIN="$REPO/.venv/bin/navin"
[ -x "$NAVIN" ] || NAVIN="navin"
ACTION="${1:-setup}"
DEMO="${2:-/tmp/navin-try}"

setup() {
  if [ -e "$DEMO" ]; then
    echo "$DEMO already exists. Remove it first (rm -r \"$DEMO\") or pass another folder." >&2
    exit 1
  fi
  mkdir -p "$DEMO/.navin" "$DEMO/tests" "$DEMO/web"
  printf '__pycache__/\n.pytest_cache/\n.ruff_cache/\n' >"$DEMO/.gitignore"
  cat >"$DEMO/pyproject.toml" <<'EOF'
[project]
name = "calc-demo"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF
  cat >"$DEMO/calc.py" <<'EOF'
"""Tiny module used to try the end-of-turn checks."""


def add(a: int, b: int) -> int:
    return a + b
EOF
  cat >"$DEMO/tests/conftest.py" <<'EOF'
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
EOF
  cat >"$DEMO/tests/test_calc.py" <<'EOF'
from calc import add


def test_add():
    assert add(2, 3) == 5
EOF
  cat >"$DEMO/web/app.js" <<'EOF'
export function greet(name) {
  return `Hello ${name}`;
}
EOF
  cp "$REPO/navin/skills/hookify-rules/examples/dangerous-rm.local.md" "$DEMO/.navin/hookify.dangerous-rm.local.md"
  cp "$REPO/navin/skills/hookify-rules/examples/console-log-warning.local.md" "$DEMO/.navin/hookify.console-log.local.md"
  cat >"$DEMO/.navin/security-patterns.yaml" <<'EOF'
patterns:
  - rule_name: no-md5
    reminder: MD5 is broken for security use. Use hashlib.sha256 or a password hasher.
    regex: hashlib\.md5
    paths: ["*.py"]
EOF
  cat >"$DEMO/.navin/security-guidance.md" <<'EOF'
- Database access goes through db.query(sql, params); any other execute() is a finding.
EOF
  (
    cd "$DEMO"
    git init -q
    git add -A
    git -c user.email=demo@navin.local -c user.name=demo commit -q -m "demo project"
  )
  echo "Demo project ready in $DEMO"
}

selftest() {
  [ -d "$DEMO/.navin" ] || setup
  "$PY" - "$DEMO" <<'EOF'
import asyncio
import json
import os
import sys
from pathlib import Path

from navin.agent import security_guidance, stop_checks, user_hooks

ws = Path(sys.argv[1])
os.environ["NAVIN_STOP_CHECKS"] = "1"
os.environ.pop("NAVIN_SECURITY_REVIEW", None)
results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"[{'OK' if ok else 'KO'}] {label}" + (f" - {detail}" if detail and not ok else ""))


async def main():
    notices = []

    async def notify(text):
        notices.append(text)

    reason = await user_hooks.pre_tool_use_block(ws, "try", "exec", {"command": "rm -rf build"}, notify=notify)
    check("hookify blocks rm -rf", bool(reason) and "Dangerous rm" in reason, str(reason))
    reason = await user_hooks.pre_tool_use_block(ws, "try", "exec", {"command": "ls"})
    check("hookify lets ls run", reason is None, str(reason))

    notices.clear()
    text = "export function greet(name) {\n  console.log(name);\n}\n"
    feedback = await user_hooks.post_tool_use_feedback(
        ws, "try", "write_file", {"path": "web/app.js", "content": text}, "ok", notify=notify,
    )
    check("hookify warns on console.log", bool(feedback) and "console.log" in feedback, str(feedback))

    notices.clear()
    code = "import yaml\n\nconfig = yaml.load(open('settings.yml'))\n"
    feedback = await user_hooks.post_tool_use_feedback(
        ws, "try", "write_file", {"path": "loader.py", "content": code}, "ok", notify=notify,
    )
    check("security pattern warns on yaml.load", bool(feedback) and "unsafe_yaml_load" in feedback, str(feedback))
    check("user sees a security notice", any("Security reminder" in n for n in notices), str(notices))
    feedback = await user_hooks.post_tool_use_feedback(
        ws, "try", "write_file", {"path": "hashes.py", "content": "hashlib.md5(b'x')"}, "ok",
    )
    check("project pattern no-md5 warns", bool(feedback) and "no-md5" in feedback, str(feedback))

    calc = ws / "calc.py"
    original = calc.read_text("utf-8")
    try:
        broken = original.replace("a + b", "a - b")
        calc.write_text(broken, "utf-8")
        await user_hooks.post_tool_use_feedback(
            ws, "stop", "edit_file", {"path": "calc.py", "content": broken}, "ok",
        )
        reason = await user_hooks.stop_hook_reason(ws, "stop", "done", stop_hook_active=False)
        check("end-of-turn checks block a broken test", bool(reason) and "test_add" in reason, str(reason)[:300])
        calc.write_text(original, "utf-8")
        await user_hooks.post_tool_use_feedback(
            ws, "stop", "edit_file", {"path": "calc.py", "content": original}, "ok",
        )
        notices.clear()
        reason = await user_hooks.stop_hook_reason(ws, "stop", "done", stop_hook_active=False, notify=notify)
        check("end-of-turn checks pass once fixed", reason is None and any("passed" in n for n in notices), str(notices))
    finally:
        calc.write_text(original, "utf-8")

    reason = await security_guidance.stop_review(ws, "try", stop_hook_active=False)
    check("AI review stays off by default", reason == ("", ""), str(reason))


asyncio.run(main())
print()
print(user_hooks.hooks_summary(ws))
print()
ok = all(results)
print(f"{sum(results)}/{len(results)} checks passed")
sys.exit(0 if ok else 1)
EOF
  rm -f "$DEMO/loader.py" "$DEMO/hashes.py"
  git -C "$DEMO" checkout -q -- web/app.js 2>/dev/null || true
}

scenarios() {
  cat <<EOF
Demo project: $DEMO
Navin command: $NAVIN

1. Hookify block (expected: the command is refused with "Dangerous rm command detected")
   $NAVIN agent -w $DEMO -m "Run rm -rf build in the terminal"

2. Hookify warning (expected: a "console.log detected" notice)
   $NAVIN agent -w $DEMO -m "Add console.log(name) at the start of greet in web/app.js"

3. Security pattern (expected: "Security reminder: unsafe_yaml_load", then the agent switches to yaml.safe_load)
   $NAVIN agent -w $DEMO -m "Create loader.py that reads settings.yml with yaml.load"

4. End-of-turn checks, green (expected at the end: "End-of-turn checks passed ... test(s) passed")
   NAVIN_STOP_CHECKS=1 $NAVIN agent -w $DEMO -m "Add mul(a, b) to calc.py with a test in tests/test_calc.py"

5. End-of-turn checks, red (expected: "End-of-turn checks failed", then the agent fixes the code,
   updates the test or explains why; a second stop with no change never blocks again)
   NAVIN_STOP_CHECKS=1 $NAVIN agent -w $DEMO -m "In calc.py make add return a - b, do not run the tests yourself"

6. AI review at end of turn (expected: "Security review of this turn's changes found potential vulnerabilities")
   NAVIN_SECURITY_REVIEW=1 $NAVIN agent -w $DEMO -m "Create db.py with find_user(cursor, q) that runs cursor.execute('select * from users where name = ' + q)"

7. AI review after a commit (expected: findings appended after the git commit)
   NAVIN_SECURITY_REVIEW=1 $NAVIN agent -w $DEMO -m "Commit every change with git commit -m 'add db'"

8. Commands
   $NAVIN agent -w $DEMO -m "/security-review"
   $NAVIN agent -w $DEMO -m "/hookify-list"
   $NAVIN agent -w $DEMO -m "/hookify Block git push --force"

Kill switches: NAVIN_HOOKIFY=0, NAVIN_SECURITY_GUIDANCE=0, NAVIN_SECURITY_PATTERNS=0.
EOF
}

case "$ACTION" in
  setup) setup ;;
  selftest) selftest ;;
  scenarios) scenarios ;;
  *) echo "usage: $0 setup|selftest|scenarios [dir]" >&2; exit 2 ;;
esac
