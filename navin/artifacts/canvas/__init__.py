# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Navin canvas: analytical HTML artifacts built from structured blocks.

The model sends data (a list of typed blocks), never hand-written HTML.
:func:`build_canvas` validates the blocks and returns one self-contained HTML
document: the kit CSS and JS (``kit.css`` / ``kit.js`` next to this file) are
inlined, the data rides in a JSON island, and a Content-Security-Policy of
``default-src 'none'`` guarantees the canvas never touches the network.

Block types (mirroring the kit components):

- ``section``    title, blocks, description?
- ``stats``      items[{label, value, unit?, delta?, delta_unit?, tone?, hint?}], title?
- ``table``      columns[{key, label, unit?, align?, format?, better?, mono?}], rows[{key: value}],
                 title?, caption?, sort?{key, dir}
- ``bar_chart``  title, x_label, y_label, unit, caption, categories[str],
                 series[{name, values[number], role?, color?}], orientation?
- ``line_chart`` title, x_label, y_label, unit, caption, x[str],
                 series[{name, values[number|null], role?, color?}]
- ``timeline``   items[{date, label, end?, status?, detail?, milestone?, critical?}], title?, caption?
- ``findings``   items[{severity, title, file?, line?, location?, evidence?, detail?, status?, fix?}],
                 title? (``location`` is a non-file anchor such as a video timestamp)
- ``diff``       before{label, text}, after{label, text}, title?, caption?
- ``badges``     items[{label, tone?}], title?
- ``text``       text, title?
"""

from __future__ import annotations

import html
import json
import math
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from navin.artifacts.store import ArtifactError

__all__ = [
    "BLOCK_TYPES",
    "CANVAS_CSP",
    "CANVAS_MARKER",
    "CanvasError",
    "build_canvas",
    "is_canvas_html",
    "kit_css",
    "kit_js",
    "validate_blocks",
]

_HERE = Path(__file__).resolve().parent

BLOCK_TYPES = (
    "section",
    "stats",
    "table",
    "bar_chart",
    "line_chart",
    "timeline",
    "findings",
    "diff",
    "badges",
    "text",
)
SEVERITIES = ("critical", "high", "medium", "low", "info")
TONES = ("good", "bad", "warn", "info", "accent", "neutral")
SERIES_COLORS = ("s1", "s2", "s3", "s4", "s5", "accent", "good", "warn", "bad", "info")
CANVAS_MARKER = '<meta name="navin-canvas" content="1">'
CANVAS_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'"
)

_MAX_DEPTH = 4
_MAX_ROWS = 5000
_MAX_POINTS = 2000


class CanvasError(ArtifactError):
    """Invalid canvas blocks (message lists every problem found)."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("invalid canvas: " + "; ".join(self.problems), status=400)


@lru_cache(maxsize=1)
def kit_css() -> str:
    return (_HERE / "kit.css").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def kit_js() -> str:
    return (_HERE / "kit.js").read_text(encoding="utf-8")


def is_canvas_html(content: str) -> bool:
    return CANVAS_MARKER in (content or "")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


class _Checker:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.rows = 0

    def fail(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def need_text(self, block: dict[str, Any], key: str, where: str, why: str = "") -> None:
        if not _text(block.get(key)):
            self.fail(where, f"'{key}' is required{why}")

    def need_items(self, block: dict[str, Any], key: str, where: str) -> list[Any]:
        items = block.get(key)
        if not isinstance(items, list) or not items:
            self.fail(where, f"'{key}' must be a non-empty list (canvases never render empty states)")
            return []
        return items

    def tone(self, value: Any, where: str) -> None:
        if value is not None and value not in TONES:
            self.fail(where, f"tone must be one of {', '.join(TONES)}")

    # -- blocks --------------------------------------------------------
    def blocks(self, blocks: Any, where: str, depth: int) -> None:
        if not isinstance(blocks, list) or not blocks:
            self.fail(where, "blocks must be a non-empty list")
            return
        if depth > _MAX_DEPTH:
            self.fail(where, f"sections nest deeper than {_MAX_DEPTH} levels")
            return
        for index, block in enumerate(blocks):
            self.block(block, f"{where}[{index}]", depth)

    def block(self, block: Any, where: str, depth: int) -> None:
        if not isinstance(block, dict):
            self.fail(where, "block must be an object")
            return
        kind = block.get("type")
        if kind not in BLOCK_TYPES:
            self.fail(where, f"unknown block type {kind!r} (allowed: {', '.join(BLOCK_TYPES)})")
            return
        where = f"{where} {kind}"
        getattr(self, f"_{kind}")(block, where, depth)

    def _section(self, block: dict[str, Any], where: str, depth: int) -> None:
        self.need_text(block, "title", where)
        self.blocks(block.get("blocks"), f"{where}.blocks", depth + 1)

    def _stats(self, block: dict[str, Any], where: str, depth: int) -> None:
        for i, item in enumerate(self.need_items(block, "items", where)):
            at = f"{where}.items[{i}]"
            if not isinstance(item, dict):
                self.fail(at, "must be an object")
                continue
            self.need_text(item, "label", at)
            value = item.get("value")
            if not (_is_number(value) or _text(value)):
                self.fail(at, "'value' must be a finite number or non-empty text")
            delta = item.get("delta")
            if delta is not None and not (_is_number(delta) or isinstance(delta, str)):
                self.fail(at, "'delta' must be a number or text")
            self.tone(item.get("tone"), at)

    def _table(self, block: dict[str, Any], where: str, depth: int) -> None:
        columns = self.need_items(block, "columns", where)
        keys: list[str] = []
        for i, col in enumerate(columns):
            at = f"{where}.columns[{i}]"
            if not isinstance(col, dict):
                self.fail(at, "must be an object")
                continue
            self.need_text(col, "key", at)
            self.need_text(col, "label", at)
            if col.get("align") not in (None, "left", "right"):
                self.fail(at, "align must be left or right")
            if col.get("better") not in (None, "lower", "higher"):
                self.fail(at, "better must be lower or higher")
            if col.get("format") not in (None, "delta", "delta_pct"):
                self.fail(at, "format must be delta or delta_pct")
            if isinstance(col.get("key"), str):
                keys.append(col["key"])
        if len(set(keys)) != len(keys):
            self.fail(where, "column keys must be unique")
        rows = self.need_items(block, "rows", where)
        self.rows += len(rows)
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                self.fail(f"{where}.rows[{i}]", "must be an object keyed by column key")
                continue
            for key, value in row.items():
                if isinstance(value, dict):
                    if not (_text(value.get("badge")) or "text" in value):
                        self.fail(f"{where}.rows[{i}].{key}", "object cells need 'badge' or 'text'")
                    self.tone(value.get("tone"), f"{where}.rows[{i}].{key}")
                elif isinstance(value, float) and not math.isfinite(value):
                    self.fail(f"{where}.rows[{i}].{key}", "numbers must be finite")
        sort = block.get("sort")
        if sort is not None:
            if not isinstance(sort, dict) or sort.get("key") not in keys:
                self.fail(where, "sort.key must name a column")
            elif sort.get("dir", "desc") not in ("asc", "desc"):
                self.fail(where, "sort.dir must be asc or desc")

    def _chart(self, block: dict[str, Any], where: str, axis_key: str, allow_gaps: bool) -> None:
        why = " (charts must be labelled)"
        for key in ("title", "x_label", "y_label", "caption"):
            self.need_text(block, key, where, why)
        self.need_text(block, "unit", where, " (value axis unit, e.g. ms, tokens, USD, count)")
        axis = self.need_items(block, axis_key, where)
        if axis and not all(isinstance(v, (str, int, float)) and not isinstance(v, bool) for v in axis):
            self.fail(where, f"'{axis_key}' entries must be text or numbers")
        if len(axis) > _MAX_POINTS:
            self.fail(where, f"'{axis_key}' has more than {_MAX_POINTS} points")
        series = self.need_items(block, "series", where)
        names: list[str] = []
        for i, s in enumerate(series):
            at = f"{where}.series[{i}]"
            if not isinstance(s, dict):
                self.fail(at, "must be an object")
                continue
            self.need_text(s, "name", at, " (legend label)")
            if _text(s.get("name")):
                names.append(s["name"].strip())
            if s.get("color") is not None and s.get("color") not in SERIES_COLORS:
                self.fail(at, f"color must be one of {', '.join(SERIES_COLORS)}")
            if s.get("role") not in (None, "baseline"):
                self.fail(at, "role must be baseline when set")
            values = s.get("values")
            if not isinstance(values, list) or not values:
                self.fail(at, "'values' must be a non-empty list")
                continue
            if axis and len(values) != len(axis):
                self.fail(at, f"'values' has {len(values)} entries but '{axis_key}' has {len(axis)}")
            numbers = [v for v in values if _is_number(v)]
            bad = [v for v in values if not _is_number(v) and not (allow_gaps and v is None)]
            if bad:
                self.fail(at, "values must be finite numbers" + (" or null" if allow_gaps else ""))
            elif not numbers:
                self.fail(at, "series has no data points")
        if len(set(names)) != len(names):
            self.fail(where, "series names must be unique (they form the legend)")
        if block.get("orientation") not in (None, "vertical", "horizontal"):
            self.fail(where, "orientation must be vertical or horizontal")

    def _bar_chart(self, block: dict[str, Any], where: str, depth: int) -> None:
        self._chart(block, where, "categories", allow_gaps=False)

    def _line_chart(self, block: dict[str, Any], where: str, depth: int) -> None:
        self._chart(block, where, "x", allow_gaps=True)

    def _timeline(self, block: dict[str, Any], where: str, depth: int) -> None:
        for i, item in enumerate(self.need_items(block, "items", where)):
            at = f"{where}.items[{i}]"
            if not isinstance(item, dict):
                self.fail(at, "must be an object")
                continue
            self.need_text(item, "label", at)
            self.need_text(item, "date", at)

    def _findings(self, block: dict[str, Any], where: str, depth: int) -> None:
        for i, item in enumerate(self.need_items(block, "items", where)):
            at = f"{where}.items[{i}]"
            if not isinstance(item, dict):
                self.fail(at, "must be an object")
                continue
            self.need_text(item, "title", at)
            if item.get("severity") not in SEVERITIES:
                self.fail(at, f"severity must be one of {', '.join(SEVERITIES)}")
            line = item.get("line")
            if line is not None and not (isinstance(line, int) and not isinstance(line, bool) and line > 0):
                self.fail(at, "line must be a positive integer")

    def _diff(self, block: dict[str, Any], where: str, depth: int) -> None:
        texts = []
        for side in ("before", "after"):
            part = block.get(side)
            if not isinstance(part, dict):
                self.fail(where, f"'{side}' must be an object with label and text")
                continue
            self.need_text(part, "label", f"{where}.{side}")
            if not isinstance(part.get("text"), str):
                self.fail(f"{where}.{side}", "'text' must be a string")
            else:
                texts.append(part["text"])
        if len(texts) == 2 and not any(t.strip() for t in texts):
            self.fail(where, "before and after are both empty")

    def _badges(self, block: dict[str, Any], where: str, depth: int) -> None:
        for i, item in enumerate(self.need_items(block, "items", where)):
            at = f"{where}.items[{i}]"
            if not isinstance(item, dict):
                self.fail(at, "must be an object")
                continue
            self.need_text(item, "label", at)
            self.tone(item.get("tone"), at)

    def _text(self, block: dict[str, Any], where: str, depth: int) -> None:
        self.need_text(block, "text", where)


def validate_blocks(blocks: Any) -> None:
    """Raise :class:`CanvasError` listing every problem in *blocks*."""
    checker = _Checker()
    checker.blocks(blocks, "blocks", 0)
    if checker.rows > _MAX_ROWS:
        checker.fail("blocks", f"tables hold {checker.rows} rows (max {_MAX_ROWS}); aggregate first")
    if checker.problems:
        raise CanvasError(checker.problems)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def _json_island(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Neutralise anything that could close the script element or open a comment.
    return encoded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def build_canvas(
    title: str,
    blocks: list[dict[str, Any]],
    *,
    subtitle: str | None = None,
    generated_at: str | None = None,
) -> str:
    """Validate *blocks* and return a self-contained canvas HTML document."""
    problems: list[str] = []
    if not _text(title):
        problems.append("title is required")
    try:
        validate_blocks(blocks)
    except CanvasError as exc:
        problems.extend(exc.problems)
    if problems:
        raise CanvasError(problems)

    clean_title = title.strip()[:200]
    doc: dict[str, Any] = {
        "title": clean_title,
        "blocks": blocks,
        "generated_at": generated_at or time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
    }
    if _text(subtitle):
        doc["subtitle"] = str(subtitle).strip()
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        f'<meta http-equiv="Content-Security-Policy" content="{CANVAS_CSP}">\n'
        f"{CANVAS_MARKER}\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(clean_title)}</title>\n"
        f"<style>\n{kit_css()}</style>\n"
        "</head>\n<body>\n"
        '<main id="navin-canvas" class="nv-canvas"></main>\n'
        f'<script type="application/json" id="navin-canvas-data">{_json_island(doc)}</script>\n'
        f"<script>\n{kit_js()}</script>\n"
        "</body>\n</html>\n"
    )
