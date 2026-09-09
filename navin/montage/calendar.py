# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Structured content calendar for Montage (propose-only, no publish)."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR
from navin.montage.analyze import ProjectKit, analyze_project

_DEFAULT_DAYS = 14
_ALLOWED_DAYS = frozenset({7, 14, 30})

# Rotating channel / format patterns for a starter calendar.
_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    ("LinkedIn", "carousel/image", "Problem → product proof", "Try the product"),
    ("X", "short post + visual", "One sharp insight", "Link in reply"),
    ("Instagram", "1:1 feed", "Behind-the-build visual", "Save for later"),
    ("TikTok", "9:16 reel 15-30s", "Hook in 1s + demo", "Comment for access"),
    ("LinkedIn", "text + screenshot", "Customer pain story", "Book a walkthrough"),
    ("Product Hunt", "launch teaser", "What's shipping this week", "Follow the launch"),
    ("Instagram", "9:16 story", "Feature tip of the day", "Swipe up / link"),
)


@dataclass(slots=True)
class CalendarRow:
    day: int
    date: str
    channel: str
    format: str
    hook: str
    cta: str
    theme: str
    asset_path: str
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_calendar(
    kit: ProjectKit,
    *,
    days: int = _DEFAULT_DAYS,
    start: date | None = None,
) -> list[CalendarRow]:
    if days not in _ALLOWED_DAYS:
        days = _DEFAULT_DAYS
    start = start or date.today()
    rows: list[CalendarRow] = []
    themes = [
        f"Introduce {kit.name}",
        "Core problem you solve",
        "Product walkthrough",
        "Social proof / credibility",
        "Feature deep-dive",
        "Behind the scenes",
        "CTA / conversion push",
    ]
    for i in range(days):
        channel, fmt, hook_tpl, cta = _PATTERNS[i % len(_PATTERNS)]
        theme = themes[i % len(themes)]
        hook = f"{hook_tpl} - {kit.name}"
        asset = f"{WORKSPACE_MONTAGE_DIR}/creatives/day-{i + 1:02d}"
        rows.append(
            CalendarRow(
                day=i + 1,
                date=(start + timedelta(days=i)).isoformat(),
                channel=channel,
                format=fmt,
                hook=hook,
                cta=cta,
                theme=theme,
                asset_path=asset,
            )
        )
    return rows


def write_calendar(
    root: Path,
    rows: list[CalendarRow] | None = None,
    *,
    kit: ProjectKit | None = None,
    days: int = _DEFAULT_DAYS,
) -> dict[str, Path]:
    kit = kit or analyze_project(root)
    rows = rows or build_calendar(kit, days=days)
    out_dir = root / WORKSPACE_MONTAGE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "calendar.md"
    csv_path = out_dir / "calendar.csv"
    json_path = out_dir / "calendar.json"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    md = [
        f"# Content calendar - {kit.name}",
        "",
        f"_Proposed {stamp} - {len(rows)} days. Not published._",
        "",
        "| Day | Date | Channel | Format | Theme | Hook | CTA | Asset |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        md.append(
            f"| {row.day} | {row.date} | {row.channel} | {row.format} | "
            f"{row.theme} | {row.hook} | {row.cta} | `{row.asset_path}` |"
        )
    md.extend(
        [
            "",
            "## Rules",
            "",
            "- Status is `proposed` until the user validates.",
            "- Generate creatives only after validation for costly video batches.",
            "- Never auto-publish; hand off to /ads or connected MCP only if asked.",
            "",
        ]
    )
    md_path.write_text("\n".join(md), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "day",
                "date",
                "channel",
                "format",
                "theme",
                "hook",
                "cta",
                "asset_path",
                "status",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_dict())

    payload = {
        "project": kit.name,
        "days": len(rows),
        "generated_at": stamp,
        "rows": [r.to_dict() for r in rows],
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"markdown": md_path, "csv": csv_path, "json": json_path}


def render_calendar_result(root: Path, paths: dict[str, Path], days: int) -> str:
    return "\n".join(
        [
            f"Content calendar ({days} days) written:",
            f"  markdown: {paths['markdown'].relative_to(root)}",
            f"  csv: {paths['csv'].relative_to(root)}",
            f"  json: {paths['json'].relative_to(root)}",
            "",
            "Ask the user to validate themes/channels before generate_image /",
            "generate_video or HyperFrames render batches.",
        ]
    )
