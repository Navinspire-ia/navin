# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tolerant YAML frontmatter for agent, command and skill markdown files.

Agent descriptions carry multi-line ``<example>`` blocks with ``user:`` /
``assistant:`` lines, written as a plain (unquoted) YAML value. Strict YAML
rejects that ("mapping values are not allowed here"), which used to drop the
whole frontmatter silently: the agent lost its name and description. This
parser keeps strict YAML when it works and falls back to a line-based reading
where a new key only starts on a top-level ``key:`` line outside an example.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", re.DOTALL)
_KEY_RE = re.compile(r"^([A-Za-z][\w-]*):(?:\s+(.*)|\s*)$")
# Labels used inside examples; never top-level keys of a definition.
_DIALOGUE_LABELS = frozenset({"user", "assistant", "context", "commentary", "human"})
_EXAMPLE_BLOCK_RE = re.compile(r"<example>.*?</example>", re.DOTALL | re.IGNORECASE)


def _scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in "[{\"'" or value.lower() in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", value):
        try:
            return yaml.safe_load(value)
        except yaml.YAMLError:
            return value
    return value


def lenient_yaml(raw: str) -> dict[str, Any]:
    """Line-based fallback: top-level ``key: value`` lines, the rest continues."""
    meta: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    in_example = False
    for line in raw.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if "<example>" in lowered:
            in_example = True
        match = None if (in_example or line[:1].isspace()) else _KEY_RE.match(line)
        if match and match.group(1).lower() not in _DIALOGUE_LABELS:
            current = match.group(1)
            if current not in meta:
                order.append(current)
            meta[current] = [match.group(2) or ""]
        elif current is not None:
            meta[current].append(line)
        if "</example>" in lowered:
            in_example = False
    result: dict[str, Any] = {}
    for key in order:
        lines = meta[key]
        first, rest = lines[0], lines[1:]
        if not first.strip() and rest and all(
            not item.strip() or item.lstrip().startswith("- ") for item in rest
        ):
            result[key] = [str(_scalar(item.lstrip()[2:])) for item in rest if item.strip()]
            continue
        if not rest:
            result[key] = _scalar(first)
            continue
        text = "\n".join([first, *rest]).strip()
        if first.strip() in ("|", ">", "|-", ">-"):
            text = "\n".join(item[2:] if item.startswith("  ") else item for item in rest).strip()
        result[key] = text.strip("\"'") if text[:1] in "\"'" and text[-1:] == text[:1] else text
    return result


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return ``(meta, body, error)``; ``meta`` is None without frontmatter."""
    if not text.startswith("---"):
        return None, text, None
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None, text, "frontmatter opened with --- but never closed"
    raw, body = match.group(1), text[match.end():]
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError:
        meta = lenient_yaml(raw)
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        return None, body, "frontmatter is not a mapping"
    return meta, body, None


def strip_examples(description: str) -> str:
    """Routing sentence of a description, without its ``<example>`` blocks."""
    text = _EXAMPLE_BLOCK_RE.sub("", description or "")
    text = re.sub(r"\s*\bExamples?:\s*$", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()
