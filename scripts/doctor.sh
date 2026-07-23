#!/bin/sh
# navin doctor — check that the system tools used by skills and studio
# modules are available. Informative only: nothing here is fatal.
set -u

ok=0
miss=0
missing_apt=""

check() {
  # $1 = command, $2 = purpose, $3 = apt package name (empty = not apt-installable)
  if command -v "$1" >/dev/null 2>&1; then
    printf 'OK    %-10s %s\n' "$1" "$2"
    ok=$((ok + 1))
  else
    printf 'MISS  %-10s %s\n' "$1" "$2"
    miss=$((miss + 1))
    [ -n "$3" ] && missing_apt="$missing_apt $3"
  fi
}

echo "== Required =="
check python3 "runtime (>= 3.11)" "python3"
check git "version control, checkpoints, packs" "git"
check curl "web fetch, installers" "curl"

echo ""
echo "== Recommended (used by skills) =="
check node "npx-based MCP servers and CLIs (clawhub, packs)" "nodejs"
check npx "on-demand Node tools" ""
check ffmpeg "video/audio processing (marketing, transcription)" "ffmpeg"
check jq "JSON processing in shell workflows" "jq"
check rg "fast code search (dev module)" "ripgrep"
check sqlite3 "local databases (dev module)" "sqlite3"
check convert "image conversion — ImageMagick (marketing)" "imagemagick"
check pandoc "document format conversion (documents)" "pandoc"
check soffice "Office → PDF conversion — LibreOffice (documents)" "libreoffice"
check docker "sandboxed execution, MCP servers in containers" ""

echo ""
if [ "$miss" -eq 0 ]; then
  echo "All tools present."
else
  echo "$miss tool(s) missing. On Debian/Ubuntu/WSL install with:"
  [ -n "$missing_apt" ] && echo "  sudo apt-get install -y$missing_apt"
  echo "(docker: https://docs.docker.com/engine/install/ — node: https://nodejs.org)"
fi
exit 0
