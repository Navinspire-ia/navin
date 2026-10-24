#!/usr/bin/env bash
# Read YAML frontmatter of a settings file.
# Usage:
#   parse-frontmatter.sh <file>          print every top-level key as key=value
#   parse-frontmatter.sh <file> <key>    print one value (lists: one item per line)
#   parse-frontmatter.sh <file> --body   print the Markdown body
# A missing file or key prints nothing and exits 0.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <file> [key|--body]" >&2
  exit 2
fi
file=$1
key=${2:-}
[[ -f "$file" ]] || exit 0

python3 - "$file" "$key" <<'PY'
import json, re, sys

path, key = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
match = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", text, re.S)
if key == "--body":
    sys.stdout.write(text[match.end():] if match else text)
    sys.exit(0)
if not match:
    sys.exit(0)
raw = match.group(1)
try:
    import yaml
    meta = yaml.safe_load(raw) or {}
except Exception:
    meta = {}
    current = None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1] in (" ", "\t") and current and line.strip().startswith("- "):
            meta.setdefault(current, [])
            if isinstance(meta[current], list):
                meta[current].append(line.strip()[2:].strip().strip('"').strip("'"))
            continue
        k, sep, v = line.partition(":")
        if sep:
            current = k.strip()
            v = v.split(" #", 1)[0].strip()
            if v.startswith("[") and v.endswith("]"):
                meta[current] = [i.strip().strip('"').strip("'") for i in v[1:-1].split(",") if i.strip()]
            else:
                meta[current] = v.strip('"').strip("'") if v else []
if not isinstance(meta, dict):
    sys.exit(0)

def fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict,)):
        return json.dumps(value)
    return str(value)

if key:
    value = meta.get(key)
    if isinstance(value, list):
        for item in value:
            print(fmt(item))
    elif value is not None:
        print(fmt(value))
else:
    for k, value in meta.items():
        if isinstance(value, list):
            print(f"{k}=" + ",".join(fmt(i) for i in value))
        else:
            print(f"{k}={fmt(value)}")
PY
