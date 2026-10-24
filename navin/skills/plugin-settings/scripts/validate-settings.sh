#!/usr/bin/env bash
# Validate a plugin settings file (.navin/<plugin>.local.md).
# Usage: validate-settings.sh <file> [required-key ...]
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <file> [required-key ...]" >&2
  exit 2
fi

python3 - "$@" <<'PY'
import re, sys
from pathlib import Path

path = Path(sys.argv[1])
required = sys.argv[2:]
errors, warnings = [], []
if not path.is_file():
    print(f"[CRITICAL] {path}: file not found")
    sys.exit(1)
if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.local\.md", path.name):
    warnings.append("name should be <plugin-name>.local.md")
if path.parent.name != ".navin":
    warnings.append("settings files live in <workspace>/.navin/")
text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
if not text.startswith("---"):
    print(f"[CRITICAL] {path}: no frontmatter (first line must be ---)")
    sys.exit(1)
match = re.match(r"^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)", text, re.S)
if not match:
    print(f"[CRITICAL] {path}: frontmatter is never closed with ---")
    sys.exit(1)
raw = match.group(1)
try:
    import yaml
    meta = yaml.safe_load(raw) or {}
except ImportError:
    meta = {l.partition(":")[0].strip(): l.partition(":")[2].strip() for l in raw.splitlines() if ":" in l and not l.startswith((" ", "\t", "#"))}
except Exception as exc:  # yaml.YAMLError
    print(f"[CRITICAL] {path}: invalid YAML: {exc}")
    sys.exit(1)
if not isinstance(meta, dict):
    print(f"[CRITICAL] {path}: frontmatter must be key: value pairs")
    sys.exit(1)
for key, value in meta.items():
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(key)):
        warnings.append(f"key {key!r}: use lower_case_with_underscores")
    if isinstance(value, str) and value.lower() in ("yes", "no", "on", "off", "true", "false"):
        warnings.append(f"{key}: {value!r} is a string; write true or false without quotes")
    if isinstance(value, str) and re.search(r"(AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|-----BEGIN)", value):
        errors.append(f"{key}: looks like a secret; use an environment variable")
    if re.search(r"(token|secret|password|api_key)", str(key)) and value:
        warnings.append(f"{key}: secrets do not belong in settings files")
for key in required:
    if key not in meta:
        errors.append(f"required key {key!r} is missing")
gitignore = path.parent.parent / ".gitignore"
if gitignore.is_file() and ".local.md" not in gitignore.read_text(encoding="utf-8", errors="replace"):
    warnings.append(".gitignore does not ignore .navin/*.local.md")
for w in warnings:
    print(f"[WARNING] {path}: {w}")
for e in errors:
    print(f"[CRITICAL] {path}: {e}")
print(f"{path}: {len(meta)} key(s), {len(errors)} error(s), {len(warnings)} warning(s)")
sys.exit(1 if errors else 0)
PY
