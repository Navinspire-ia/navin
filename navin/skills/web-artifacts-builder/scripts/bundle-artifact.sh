#!/usr/bin/env bash
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
#
# bundle-artifact.sh <dir> [--skip-build]
#
# Builds the project in <dir> (npm run build) and verifies that dist/ holds a
# single self-contained index.html: no external script or stylesheet, no
# extra files besides an optional favicon. Prints the size in KB.
#   --skip-build  verify an existing dist/ without rebuilding
#   -h, --help    show this help
set -euo pipefail

usage() { sed -n '5,11p' "$0" | sed 's/^# \{0,1\}//'; }

DIR=""
SKIP=0
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --skip-build) SKIP=1 ;;
    -*) echo "error: unknown option $arg" >&2; usage >&2; exit 2 ;;
    *) if [ -n "$DIR" ]; then echo "error: only one directory is accepted" >&2; exit 2; fi; DIR="$arg" ;;
  esac
done
if [ -z "$DIR" ]; then echo "error: project directory is required" >&2; usage >&2; exit 2; fi
if [ ! -d "$DIR" ]; then echo "error: $DIR is not a directory" >&2; exit 1; fi

fail() { echo "error: $*" >&2; exit 1; }

if [ "$SKIP" = 0 ]; then
  [ -f "$DIR/package.json" ] || fail "$DIR/package.json not found; run init-artifact.sh first"
  grep -q '"vite-plugin-singlefile"' "$DIR/package.json" || fail "vite-plugin-singlefile is not a dependency of $DIR; the build cannot inline assets"
  [ -d "$DIR/node_modules" ] || fail "$DIR/node_modules missing; run npm install first"
  rm -rf "$DIR/dist"
  echo "Building $DIR ..."
  (cd "$DIR" && npm run build) || fail "npm run build failed in $DIR"
fi

OUT="$DIR/dist/index.html"
[ -f "$OUT" ] || fail "$OUT does not exist"

# dist must contain only index.html (plus an optional favicon).
EXTRA=$(find "$DIR/dist" -type f ! -name index.html ! -name 'favicon.*' | sort)
if [ -n "$EXTRA" ]; then
  printf 'error: dist/ contains files that were not inlined:\n%s\n' "$EXTRA" >&2
  echo "hint: check vite.config.ts uses viteSingleFile() and inlineDynamicImports" >&2
  exit 1
fi

# No external script or stylesheet references.
if grep -Eiq '<script[^>]*\bsrc=' "$OUT"; then
  echo "error: $OUT still references an external script:" >&2
  grep -Eio '<script[^>]*\bsrc="[^"]*"' "$OUT" >&2 || true
  exit 1
fi
if grep -Eiq '<link[^>]*rel="stylesheet"[^>]*\bhref=' "$OUT" || grep -Eiq '<link[^>]*\bhref=[^>]*rel="stylesheet"' "$OUT"; then
  echo "error: $OUT still references an external stylesheet:" >&2
  grep -Eio '<link[^>]*stylesheet[^>]*>' "$OUT" >&2 || true
  exit 1
fi

BYTES=$(wc -c < "$OUT")
KB=$(( (BYTES + 1023) / 1024 ))
echo "OK: $OUT is self-contained ($KB KB)"
if [ "$KB" -gt 5120 ]; then
  echo "warning: above 5 MB; trim icons, fonts or embedded data" >&2
fi
