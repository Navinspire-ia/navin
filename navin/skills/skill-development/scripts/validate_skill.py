#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Lint one Navin skill folder.

Usage:
    validate_skill.py <skill-dir> [--json]

Reports critical issues (exit 1) and warnings: frontmatter, description quality,
body size and style, cited files that do not exist, files nobody cites.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)
RESOURCE_DIRS = ("references", "examples", "scripts", "assets")
SECOND_PERSON = re.compile(r"\b(you should|you can|you need to|you will|you might)\b", re.IGNORECASE)


def lint(folder: Path) -> dict:
    critical: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int] = {}
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        return {"critical": ["SKILL.md not found"], "warnings": [], "stats": {}, "passed": False}
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    match = FRONTMATTER_RE.match(text) if text.startswith("---") else None
    if not match:
        critical.append("missing YAML frontmatter (--- name / description ---)")
        meta: dict = {}
        body = text
    else:
        body = text[match.end():]
        try:
            meta = (yaml.safe_load(match.group(1)) if yaml else {}) or {}
        except Exception as exc:  # noqa: BLE001
            critical.append(f"invalid YAML frontmatter: {exc}")
            meta = {}
        if not isinstance(meta, dict):
            critical.append("frontmatter is not a mapping")
            meta = {}
    name = str(meta.get("name") or "").strip()
    desc = str(meta.get("description") or "").strip()
    if not name:
        critical.append("frontmatter 'name' is required")
    elif name != folder.name:
        warnings.append(f"name {name!r} differs from folder {folder.name!r}")
    if not desc:
        critical.append("frontmatter 'description' is required")
    else:
        stats["description_chars"] = len(desc)
        if len(desc) > 1024:
            warnings.append(f"description is {len(desc)} characters (keep it under 1024)")
        if len(desc) < 80:
            warnings.append("description is short: say what the skill does and when to use it")
        phrases = re.findall(r'"[^"]{3,80}"', desc)
        stats["trigger_phrases"] = len(phrases)
        if len(phrases) < 3:
            warnings.append(f"description has {len(phrases)} quoted trigger phrases (aim for 3 or more)")
        if not re.search(r"\b(use when|use for|use this|when the user|trigger)\b", desc, re.IGNORECASE):
            warnings.append("description does not say when to use the skill ('Use when ...')")
        if re.search(r"\byou\b", desc, re.IGNORECASE):
            warnings.append("description uses 'you'; write it in third person")
    words = len(body.split())
    stats["body_words"] = words
    if words > 5000:
        critical.append(f"body has {words} words (limit 5000): move detail to references/")
    elif words > 3000:
        warnings.append(f"body has {words} words (target 1500-2000)")
    elif words < 150:
        warnings.append(f"body has only {words} words: is the workflow complete?")
    second = SECOND_PERSON.findall(body)
    if len(second) > 3:
        warnings.append(f"{len(second)} second-person phrases ('you should' ...): use imperative form")
    if any(ch in text for ch in ("\u2014", "\u2013")):
        warnings.append("long dash found: use '-' instead")
    # A citation is a path that starts the reference (not the tail of
    # "hooks/scripts/x.sh" or "${ROOT}/scripts/x.py") and names a file.
    cited = set(re.findall(r"(?<![\w/.${}-])((?:references|examples|scripts|assets)/[\w./-]*\w\.\w+)", body))
    for ref in sorted(cited):
        if not (folder / ref).exists():
            critical.append(f"body cites {ref} but the file does not exist")
    for sub in RESOURCE_DIRS:
        base = folder / sub
        if not base.is_dir():
            continue
        files = [p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        stats[sub] = len(files)
        for path in files:
            rel = path.relative_to(folder).as_posix()
            if rel not in body and path.name not in body:
                warnings.append(f"{rel} is never mentioned in SKILL.md, so it will not be used")
            if sub == "scripts" and path.suffix in (".sh", ".py") and not path.stat().st_mode & 0o111:
                warnings.append(f"{rel} is not executable (chmod +x)")
    return {"critical": critical, "warnings": warnings, "stats": stats, "passed": not critical}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if len(args) != 1:
        print(__doc__.strip())
        return 2
    folder = Path(args[0]).expanduser().resolve()
    if folder.is_file() and folder.name == "SKILL.md":
        folder = folder.parent
    result = lint(folder)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Skill lint: {folder}")
        for key, value in result["stats"].items():
            print(f"  {key}: {value}")
        for item in result["critical"]:
            print(f"[CRITICAL] {item}")
        for item in result["warnings"]:
            print(f"[WARNING] {item}")
        print("Result: " + ("PASS" if result["passed"] else "FAIL"))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
