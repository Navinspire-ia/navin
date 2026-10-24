#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Validate a marketing campaign brief for required senior-desk fields.

Accepts a markdown file. Looks for required section keywords (case-insensitive).
Exit 0 if all present; exit 1 with missing list otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED = [
    ("objective", re.compile(r"objective|objectif|kpi", re.I)),
    ("audience", re.compile(r"audience|persona|cible|icp", re.I)),
    ("offer_cta", re.compile(r"\boffer\b|offre|\bcta\b|call to action", re.I)),
    ("message", re.compile(r"key message|message house|message cl[eé]|promise", re.I)),
    ("channels", re.compile(r"channel|canal|canaux", re.I)),
    ("dates", re.compile(r"date|timeline|start|end|calendrier", re.I)),
    ("assets", re.compile(r"asset|livrable|deliverable", re.I)),
    ("tracking", re.compile(r"utm|tracking|mesure|measurement|analytics", re.I)),
]


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: check_campaign_brief.py brief.md", file=sys.stderr)
        raise SystemExit(2)
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    missing = [name for name, pattern in REQUIRED if not pattern.search(text)]
    if missing:
        print("BRIEF INCOMPLETE - missing:")
        for name in missing:
            print(f"- {name}")
        raise SystemExit(1)
    print(f"BRIEF OK: {path} (all required fields detected)")


if __name__ == "__main__":
    main()
