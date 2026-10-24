#!/usr/bin/env bash
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
# Validate a Navin agent definition file.
# Usage: validate-agent.sh <agents/name.md>
set -uo pipefail

file="${1:-}"
if [ -z "$file" ] || [ ! -f "$file" ]; then
  echo "Usage: $0 <agent-file.md>" >&2
  exit 2
fi

python3 - "$file" "$(dirname "$0")" <<'PY'
import re, sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
errors, warnings = [], []
# Agent descriptions hold unquoted <example> dialogue that strict YAML
# rejects; reuse the tolerant parser Navin itself applies at runtime.
try:
    from navin.agent.frontmatter import split_frontmatter
except Exception:  # noqa: BLE001 - standalone copy of the skill
    sys.path.insert(0, str(Path(sys.argv[2]).resolve().parents[1] / "plugin-structure" / "scripts"))
    from validate_plugin import parse_frontmatter as split_frontmatter
meta, body, error = split_frontmatter(text)
if error or meta is None:
    print(f"[CRITICAL] {path}: {error or 'missing frontmatter (--- name / description ---)'}")
    sys.exit(1)
body = body.strip()
name = str(meta.get("name") or path.stem)
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,48}[a-z0-9]", name):
    errors.append(f"name {name!r}: 3-50 chars, lowercase letters, digits, hyphens, no leading/trailing hyphen")
desc = meta.get("description")
if not isinstance(desc, str) or len(desc.strip()) < 20:
    errors.append("description is required (when to use the agent)")
else:
    n = desc.count("<example>")
    if n == 0:
        warnings.append("description has no <example> blocks (add 2-4)")
    elif n > 4:
        warnings.append(f"description has {n} examples (2-4 is enough)")
    if not re.search(r"use this agent|use when|when the user", desc, re.IGNORECASE):
        warnings.append("description should start with the trigger ('Use this agent when ...')")
    if len(desc) > 5000:
        warnings.append("description over 5000 characters")
tools = meta.get("tools")
if tools is not None and not isinstance(tools, (list, str)):
    errors.append("tools must be a list or a comma-separated string")
color = meta.get("color")
if color is not None and color not in ("blue", "cyan", "green", "yellow", "magenta", "red"):
    warnings.append(f"color {color!r} is not one of blue, cyan, green, yellow, magenta, red")
model = meta.get("model")
if model not in (None, "inherit") and not isinstance(model, str):
    errors.append("model must be a string ('inherit')")
words = len(body.split())
if words < 20:
    errors.append("system prompt (body) is empty or too short")
elif words < 80:
    warnings.append(f"system prompt has {words} words: add process and output format")
elif words > 4000:
    warnings.append(f"system prompt has {words} words: consider moving detail to a skill")
if body and not re.search(r"\byou\b", body, re.IGNORECASE):
    warnings.append("system prompt should be written in second person ('You are ...')")
if not re.search(r"output|report|return", body, re.IGNORECASE):
    warnings.append("system prompt does not define the output format")
if "\u2014" in text or "\u2013" in text:
    warnings.append("long dash found: use '-'")
for e in errors:
    print(f"[CRITICAL] {e}")
for w in warnings:
    print(f"[WARNING] {w}")
print(f"Agent '{name}': " + ("PASS" if not errors else f"FAIL ({len(errors)} critical)"))
sys.exit(1 if errors else 0)
PY
