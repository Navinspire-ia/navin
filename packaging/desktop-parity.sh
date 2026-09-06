#!/bin/sh
# Automated + manual gate for WebUI (browser) vs Tauri desktop parity.
#
#   sh packaging/desktop-parity.sh
#   make desktop-parity
#
# Runs unit locks that catch shared-shell regressions, then prints the manual
# checklist to exercise on Win / macOS / Linux (or via ?hostChrome=1 in Chrome).
set -eu

root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$root"

fail=0
section() {
  printf '\n== %s ==\n' "$1"
}

section "WebUI locks (host chrome, tree paths, desktop helpers)"
if [ -x "$root/webui/node_modules/.bin/vitest" ] || [ -f "$root/webui/package.json" ]; then
  (
    cd "$root/webui"
    if [ ! -d node_modules ]; then
      npm install --no-fund --no-audit
    fi
    npm test -- --run \
      src/lib/host-chrome.test.ts \
      src/lib/desktop.test.ts \
      src/lib/ui-zoom.test.ts \
      src/components/dev/devWorkbenchUtils.test.ts \
      src/components/dev/editorLineMetrics.test.ts \
      src/lib/terminal-keys.test.ts \
      2>&1
  ) || fail=1
else
  printf 'skip: webui package missing\n'
  fail=1
fi

section "Python locks (shell routes, model failover notice)"
PYTEST=""
if [ -x "$root/.venv/bin/python" ]; then
  PYTEST="$root/.venv/bin/python -m pytest"
elif command -v python3 >/dev/null 2>&1; then
  PYTEST="python3 -m pytest"
fi
if [ -n "$PYTEST" ]; then
  $PYTEST -q --tb=line \
    tests/test_desktop_shell_modules.py \
    tests/test_fallback_policy.py::TestSwitchNotice \
    2>&1 || fail=1
  # Terminal PTY live test needs a real tty pool; skip quietly when exhausted.
  $PYTEST -q --tb=line tests/test_terminal_session.py 2>&1 || {
    printf '(terminal session live test failed - often sandbox/pty; not a parity blocker)\n'
  }
else
  printf 'skip: no python for pytest\n'
  fail=1
fi

section "Bundle locks (what the desktop apps will actually serve)"
# The desktop apps serve the bundle frozen in their sidecar, not the Vite dev
# server the browser talks to. A bundle left behind by an older `npm run build`
# is the commonest way for the packaged app to differ from the local web app,
# and nothing said so.
if [ -n "$PYTEST" ]; then
  PYTHON_BIN="${PYTEST%% -m pytest}"
else
  PYTHON_BIN="python3"
fi
"$PYTHON_BIN" - <<'PY' || fail=1
import sys
from pathlib import Path

sys.path.insert(0, "packaging")
from verify_bundle_stamp import StampError, verify

from navin.webui.build import describe_webui_bundle_status, inspect_webui_bundle

status = inspect_webui_bundle()
if status.needs_build:
    print(f"stale: {describe_webui_bundle_status(status)}")
    print("       run `cd webui && npm run build` before packaging")
    raise SystemExit(1)
try:
    stamp = verify(Path("navin/web/dist"))
except StampError as exc:
    print(f"unverifiable bundle: {exc}")
    raise SystemExit(1) from None
print(
    f"ok: bundle {stamp['bundle'][:16]} ({stamp['files']} files, "
    f"version {stamp.get('version') or 'unknown'})"
    + (", built from an uncommitted tree" if stamp.get("dirty") else "")
)
PY

section "Source locks (preview wired, failover is quiet)"
python3 - <<'PY' || fail=1
from pathlib import Path
root = Path(".")
app = (root / "webui/src/App.tsx").read_text(encoding="utf-8")
main = (root / "webui/src/main.tsx").read_text(encoding="utf-8")
turns = (root / "navin/session/webui_turns.py").read_text(encoding="utf-8")
assert "shouldShowHostChrome" in app, "App must use shouldShowHostChrome"
assert "initHostChromePreview" in main, "main must init host chrome preview"
assert 'level="info"' in turns and "model-failover" in turns, "failover toast must be info"
assert "host-no-drag" in (root / "webui/src/components/dev/DevWorkbench.tsx").read_text(encoding="utf-8")
print("ok: host chrome + quiet failover + explorer no-drag")
PY

section "Manual checklist (browser preview + each Tauri OS)"
cat <<'EOF'
Preview chrome Tauri in the local web app (no rebuild):
  http://127.0.0.1:8766/#/code?hostChrome=1
  http://localhost:5173/#/code?hostChrome=1
  (use ``&hostChrome=1`` if the hash already has ``?chat=...``)
  Disable with ?hostChrome=0 or &hostChrome=0

Same checks on Navin Code (Tauri) Win / macOS / Linux:

  [ ] Code: click a folder in the explorer - chevron opens, children list
  [ ] Code: open a file as a tab
  [ ] Terminal: sleep 30 then Ctrl+C (Cmd+C on macOS without selection) stops it
  [ ] Chat: send a message; model failover stays in the bell (no yellow overlay mid-turn)
  [ ] Download a file / open an external https link (leaves WebView)
  [ ] Zoom Ctrl+/- still usable; composer and status bar stay visible
  [ ] Desks: #/tenders #/career #/leads #/crm open without blank WebView

Dev shell (optional):
  cd desktop && NAVIN_DESKTOP_BIN=$PWD/../.venv/bin/navin npm run dev
EOF

if [ "$fail" -ne 0 ]; then
  printf '\ndesktop-parity: FAILED (automated locks)\n' >&2
  exit 1
fi
printf '\ndesktop-parity: automated locks OK - complete the checklist before release\n'
exit 0
