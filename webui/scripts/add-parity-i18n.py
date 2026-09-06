#!/usr/bin/env python3
"""Add i18n keys to the EN and FR bundles without disturbing what is there.

The two bundles must stay key-for-key identical, and several agents edit them
at once. Hand-editing invites both problems: a key added on one side only, and
a whole-file reformat that turns a two-line change into an unreviewable diff.

This script reads both files immediately before writing, adds only the keys
that are missing, and re-serialises with the exact formatting the files
already use (2-space indent, no ASCII escaping, trailing newline) - verified
by a byte-identical round trip on an unchanged file. Running it twice changes
nothing the second time, and an existing translation is never overwritten.

Usage::

    python webui/scripts/add-parity-i18n.py additions.json

where ``additions.json`` maps dotted key paths to their two texts::

    {
      "dev.checkpoints.errors.gitMissing": {
        "en": "git is not available.",
        "fr": "git n'est pas disponible."
      }
    }
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

LOCALES = ("en", "fr")
BUNDLE = Path(__file__).resolve().parent.parent / "src" / "i18n" / "locales"


def _load(locale: str) -> dict[str, Any]:
    return json.loads((BUNDLE / locale / "common.json").read_text(encoding="utf-8"))


def _dump(locale: str, data: dict[str, Any]) -> None:
    path = BUNDLE / locale / "common.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _insert(tree: dict[str, Any], dotted: str, value: str) -> bool:
    """Add ``dotted`` if absent. True when the tree changed."""
    parts = dotted.split(".")
    node: dict[str, Any] = tree
    for part in parts[:-1]:
        child = node.get(part)
        if child is None:
            child = {}
            node[part] = child
        elif not isinstance(child, dict):
            raise SystemExit(f"{dotted}: '{part}' already holds a string")
        node = child
    leaf = parts[-1]
    if leaf in node:
        return False
    node[leaf] = value
    return True


def _keys(node: Any, prefix: str = "") -> list[str]:
    if not isinstance(node, dict):
        return [prefix]
    out: list[str] = []
    for key, value in node.items():
        out.extend(_keys(value, f"{prefix}.{key}" if prefix else key))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    additions = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    added = 0
    for locale in LOCALES:
        tree = _load(locale)
        changed = False
        for dotted, texts in additions.items():
            text = texts.get(locale)
            if not isinstance(text, str):
                raise SystemExit(f"{dotted}: missing '{locale}' text")
            if _insert(tree, dotted, text):
                changed = True
                added += 1
        if changed:
            _dump(locale, tree)

    sets = {locale: set(_keys(_load(locale))) for locale in LOCALES}
    only_en = sorted(sets["en"] - sets["fr"])
    only_fr = sorted(sets["fr"] - sets["en"])
    if only_en or only_fr:
        print(f"parity broken: en-only={only_en} fr-only={only_fr}")
        return 1
    print(f"{added} key(s) added, {len(sets['en'])} keys in each bundle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
