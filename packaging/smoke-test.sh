#!/bin/sh
# Zero-config boot test for a frozen Navin build on Linux or macOS.
#
#   sh packaging/smoke-test.sh os/linux/bin/x64/navin-dist/navin
#   sh packaging/smoke-test.sh /Applications/Navin.app/Contents/MacOS/Navin
#
# Starts the WebUI against throwaway ports, a throwaway config and a throwaway
# workspace, then checks that it serves and that it created what it needed.
# `--version` proves the executable links; only this proves the product runs.
set -eu

binary="${1:?Usage: smoke-test.sh /path/to/navin-executable}"
free_port() {
  "$binary" python -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()"
}
webui_port="${NAVIN_TEST_WEBUI_PORT:-$(free_port)}"
gateway_port="${NAVIN_TEST_GATEWAY_PORT:-$(free_port)}"
if [ "$webui_port" = "$gateway_port" ]; then
  gateway_port="$(free_port)"
fi
root="$(mktemp -d "${TMPDIR:-/tmp}/navin-zero-config.XXXXXX")"
pid=""

# Job control, so the binary gets a process group of its own. Signalling the
# single pid reaches the PyInstaller bootloader and leaves the unpacked app -
# and the gateway it started - running against the test ports long after the
# test is over. Signalling the group reaches all of them.
set -m

cleanup() {
  if [ -n "$pid" ]; then
    kill -TERM "-$pid" >/dev/null 2>&1 || kill -TERM "$pid" >/dev/null 2>&1 || true
    attempt=0
    while [ "$attempt" -lt 20 ] && kill -0 "$pid" >/dev/null 2>&1; do
      attempt=$((attempt + 1))
      sleep 0.5
    done
    kill -KILL "-$pid" >/dev/null 2>&1 || kill -KILL "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
  fi
  rm -rf "$root"
}
trap cleanup EXIT HUP INT TERM

"$binary" webui \
  --port "$webui_port" \
  --gateway-port "$gateway_port" \
  --workspace "$root/workspace" \
  --config "$root/config.json" \
  --no-open \
  --yes \
  >"$root/stdout.log" 2>"$root/stderr.log" &
pid="$!"

ready=0
attempt=0
while [ "$attempt" -lt 90 ]; do
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    cat "$root/stdout.log" "$root/stderr.log" >&2
    printf 'Navin exited before the WebUI became ready.\n' >&2
    exit 1
  fi
  if curl -fsS "http://127.0.0.1:$webui_port/" >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ "$ready" != "1" ]; then
  cat "$root/stdout.log" "$root/stderr.log" >&2
  printf 'WebUI did not become ready within 90 seconds.\n' >&2
  exit 1
fi

curl -fsS "http://127.0.0.1:$webui_port/manifest.webmanifest" >/dev/null
curl -fsS "http://127.0.0.1:$webui_port/health" >/dev/null
[ -f "$root/config.json" ]
[ -d "$root/workspace" ]

# Parity with a source install, checked on the artifact itself. Each of these was
# broken in the shipped builds while passing from a checkout, which is where the
# rest of the test suite runs.

# The OS command jail. Linux and macOS sidecars without it brick every exec.
# Windows has no OS sandbox backend, so the helper is not shipped there.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *)
    dist="$(CDPATH= cd -- "$(dirname -- "$binary")" && pwd)"
    sandbox_bin="$(find "$dist" "$(dirname "$dist")" -name navin-sandbox -type f 2>/dev/null | head -n 1)"
    [ -n "$sandbox_bin" ] || {
      printf 'The sidecar ships no navin-sandbox.\n' >&2
      exit 1
    }
    ;;
esac

# The embedded interpreter, and the libraries every document skill is told it has.
"$binary" python -c "import Cryptodome, docx, openpyxl, pptx, pandas, reportlab, pdfplumber" \
  || { printf 'The build cannot run Python with its own libraries.\n' >&2; exit 1; }

# Studio desks must import in the frozen sidecar (Linux / Windows / macOS).
"$binary" python -c "import navin.career.desk_cli, navin.leads.desk_cli, navin.marketing.desk_cli, navin.tenders.desk_cli, navin.trading.desk_cli" \
  || { printf 'The build is missing a studio desk module.\n' >&2; exit 1; }

# TLS has two consumers of one bundled OpenSSL: python's ssl and cryptography's
# rust binding. A build that kept the wrong copy boots but loses the websocket
# channel and the WebUI, so load both against the shipped libraries.
"$binary" python -c "import ssl, cryptography.hazmat.bindings._rust" \
  || { printf 'The bundled OpenSSL does not satisfy cryptography.\n' >&2; exit 1; }

# The document converters ship as scripts and are copied into the workspace.
"$binary" python -c "
import sys
from pathlib import Path
from navin.utils.document_templates import converter_command
command = converter_command(Path(sys.argv[1]), 'html2docx')
if not command:
    raise SystemExit('the document converters are missing from this build')
" "$root/workspace" \
  || { printf 'The document converters are not usable in this build.\n' >&2; exit 1; }

# Channels report as shipped rather than as missing dependencies. Running the
# doctor while this sidecar is live also checks ports and local HTTP health.
"$binary" doctor --config "$root/config.json" --workspace "$root/workspace" \
  >"$root/doctor.log" 2>&1
grep -q "bundled extras" "$root/doctor.log" \
  || { cat "$root/doctor.log" >&2; printf 'The build does not declare its extras.\n' >&2; exit 1; }
for diagnostic in "Connectivity" "recommended URL" "webui port" "gateway port" "ffmpeg" "chromium"; do
  grep -q "$diagnostic" "$root/doctor.log" \
    || { cat "$root/doctor.log" >&2; printf 'Doctor output is missing: %s\n' "$diagnostic" >&2; exit 1; }
done

printf 'Navin zero-config smoke test passed: %s\n' "$binary"
