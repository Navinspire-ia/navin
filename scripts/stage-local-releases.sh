#!/usr/bin/env bash
# Stage a local /releases tree for:
#   curl http://localhost:3100/install -fsS | bash
#
# Prefers a real PyInstaller sidecar (os/linux/bin/x64/navin-dist) when
# present. Otherwise wraps the repo .venv so you can test the one-liner
# (and `navin-cli`) without a full desktop build.
#
# Production (navin.live) never reads this folder: /install keeps S3 defaults.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  VERSION="$(python3 -c "import json; print(json.load(open('${ROOT}/desktop/src-tauri/tauri.conf.json'))['version'])" 2>/dev/null || echo 2.0.0)"
fi
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ARCH_LABEL="x64" ;;
  aarch64|arm64) ARCH_LABEL="arm64" ;;
  *) echo "unsupported arch ${ARCH}" >&2; exit 1 ;;
esac
OS="$(uname -s)"
case "$OS" in
  Linux) OS_LABEL="linux" ;;
  Darwin) OS_LABEL="macos" ;;
  *) echo "unsupported OS ${OS}" >&2; exit 1 ;;
esac

DEST="${ROOT}/site/front/public/downloads"
PREFIX="${DEST}/v${VERSION}"
NAME="navin-cli-${VERSION}-${OS_LABEL}-${ARCH_LABEL}.tar.gz"
SIDECAR=""
case "$OS_LABEL" in
  linux) SIDECAR="${ROOT}/os/linux/bin/${ARCH_LABEL}/navin-dist" ;;
  macos) SIDECAR="${ROOT}/os/macos/${ARCH_LABEL}/navin-dist" ;;
esac

rm -rf "$PREFIX"
mkdir -p "$PREFIX"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

if [[ -n "$SIDECAR" && -x "${SIDECAR}/navin" ]]; then
  echo "-> packing sidecar ${SIDECAR}"
  cp -a "$SIDECAR" "${STAGE}/navin-dist"
  KIND="sidecar"
else
  VENV_NAVIN="${ROOT}/.venv/bin/navin"
  [[ -x "$VENV_NAVIN" ]] || {
    echo "missing ${VENV_NAVIN} (run uv sync / pip install -e .) and no sidecar in os/" >&2
    exit 1
  }
  echo "-> packing repo .venv wrapper (dev bundle, not the production sidecar)"
  mkdir -p "${STAGE}/navin-dist"
  cat > "${STAGE}/navin-dist/navin" <<EOF
#!/bin/sh
exec "${VENV_NAVIN}" "\$@"
EOF
  chmod +x "${STAGE}/navin-dist/navin"
  printf 'dev-venv\n' > "${STAGE}/navin-dist/NAVIN_BUNDLE_KIND"
  KIND="dev-venv"
fi

tar -C "$STAGE" -czf "${PREFIX}/${NAME}" navin-dist
(cd "$PREFIX" && sha256sum "$NAME" > SHA256SUMS.txt)

python3 - "$DEST/releases.json" "$VERSION" "$NAME" <<'PY'
import datetime
import json
import sys

path, version, filename = sys.argv[1:]
entry = {
    "version": version,
    "date": datetime.date.today().isoformat(),
    "files": [filename],
}
with open(path, "w", encoding="utf-8") as fh:
    json.dump([entry], fh, indent=2)
    fh.write("\n")
PY

echo
echo "Local releases staged (${KIND}):"
echo "  ${PREFIX}/${NAME}"
echo "  ${DEST}/releases.json"
echo
echo "Start the site (from site/front):  npm run dev"
echo "Then install without touching production S3:"
echo "  curl http://localhost:3100/install -fsS | bash"
echo "Or keep your current navin and install into a temp prefix:"
echo "  NAVIN_PREFIX=/tmp/navin-local curl http://localhost:3100/install -fsS | bash"
echo "  /tmp/navin-local/bin/navin-cli"
echo
echo "Production is unchanged: curl https://navin.live/install still reads S3."
