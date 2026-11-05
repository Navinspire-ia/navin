#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Detect exfiltration anomalies in a CSV of outbound transfers.

Usage:
    egress_baseline.py <transfers.csv> [--json] [--z-threshold 3.0] [--min-history 5]
                       [--work-hours 7-20] [--personal-domains gmail.com,...] [--output report.md]

CSV columns (header required): timestamp (ISO 8601), user, destination
(domain, IP or channel target), bytes, channel (email, saas_upload, usb,
cloud_storage, llm_prompt, git_push, print, dns, other). Optional column:
filename.

Per user, the daily byte volume is compared to that user's own history
(z-score on the earlier days, population standard deviation, at least
--min-history days or the first days are only learning). Other signals per
transfer: new destination never seen before for that user, off-hours or
weekend, personal cloud or webmail domain, archive-then-encrypt pattern
(filename ending .zip/.7z/.rar/.gpg/.enc larger than 10 MB), DNS channel with
volume above 1 MB per day (tunneling), and a burst of more than 20 transfers
in one hour. Each anomaly carries the reasons and a score.

Exit codes: 0 no anomaly, 1 at least one anomaly, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PERSONAL_DEFAULT = {
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.fr", "live.com", "yahoo.com",
    "yahoo.fr", "proton.me", "protonmail.com", "icloud.com", "me.com", "laposte.net", "orange.fr",
    "free.fr", "sfr.fr", "wanadoo.fr", "gmx.com", "gmx.fr", "drive.google.com", "dropbox.com",
    "wetransfer.com", "mega.nz", "mega.io", "onedrive.live.com", "1drv.ms", "box.com", "pcloud.com",
    "transfer.sh", "file.io", "anonfiles.com", "gofile.io", "send.vis.ee", "swisstransfer.com",
    "telegram.org", "t.me", "discord.com", "cdn.discordapp.com", "pastebin.com", "paste.ee",
    "chat.openai.com", "chatgpt.com", "claude.ai", "gemini.google.com", "poe.com", "character.ai",
}
ARCHIVE_EXT = (".zip", ".7z", ".rar", ".tar.gz", ".tgz", ".gpg", ".enc", ".pgp", ".aes")


@dataclass
class Transfer:
    when: datetime
    user: str
    destination: str
    size: int
    channel: str
    filename: str
    line: int


def parse_time(value: str) -> datetime | None:
    text = value.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            parsed = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def load(path: Path) -> tuple[list[Transfer], list[str]]:
    rows: list[Transfer] = []
    errors: list[str] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "user", "destination", "bytes", "channel"}
        fields = {f.strip().lower() for f in (reader.fieldnames or [])}
        missing = required - fields
        if missing:
            raise ValueError(f"missing columns: {', '.join(sorted(missing))}")
        for number, raw in enumerate(reader, start=2):
            row = {str(k).strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            when = parse_time(row["timestamp"])
            if when is None:
                errors.append(f"line {number}: bad timestamp {row['timestamp']!r}")
                continue
            try:
                size = int(float(row["bytes"]))
            except ValueError:
                errors.append(f"line {number}: bad bytes {row['bytes']!r}")
                continue
            rows.append(Transfer(when, row["user"].lower(), row["destination"].lower(), size, row["channel"].lower() or "other", row.get("filename", ""), number))
    rows.sort(key=lambda t: t.when)
    return rows, errors


def registrable(domain: str) -> str:
    parts = domain.split("/")[0].split(":")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def analyse(rows: list[Transfer], z_threshold: float, min_history: int, work_start: int, work_end: int, personal: set[str]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in rows:
        daily[t.user][t.when.strftime("%Y-%m-%d")] += t.size

    # Volume z-score per user-day against that user's previous days.
    for user, days in daily.items():
        ordered = sorted(days.items())
        for idx, (day, total) in enumerate(ordered):
            history = [v for _, v in ordered[:idx]]
            if len(history) < min_history:
                continue
            mean = sum(history) / len(history)
            std = math.sqrt(sum((v - mean) ** 2 for v in history) / len(history))
            z = (total - mean) / std if std > 0 else (float("inf") if total > mean * 1.5 else 0.0)
            if z >= z_threshold:
                anomalies.append({
                    "kind": "volume_spike", "user": user, "day": day, "destination": "-", "channel": "-",
                    "bytes": total, "score": 40 + min(40, int(z * 5)) if z != float("inf") else 60,
                    "reasons": [f"daily volume {human(total)} vs mean {human(mean)} over {len(history)} days (z={'inf' if z == float('inf') else f'{z:.1f}'})"],
                    "lines": [],
                })

    seen_dest: dict[str, set[str]] = defaultdict(set)
    hourly: dict[tuple[str, str], list[Transfer]] = defaultdict(list)
    dns_daily: dict[tuple[str, str], int] = defaultdict(int)
    first_day_seen: dict[str, str] = {}
    for t in rows:
        day = t.when.strftime("%Y-%m-%d")
        first_day_seen.setdefault(t.user, day)
        reasons: list[str] = []
        score = 0
        dest = registrable(t.destination)
        if dest not in seen_dest[t.user] and day != first_day_seen[t.user]:
            reasons.append(f"new destination {t.destination} for this user")
            score += 20
        seen_dest[t.user].add(dest)
        hour = t.when.hour
        if t.when.weekday() >= 5:
            reasons.append("weekend")
            score += 15
        elif hour < work_start or hour >= work_end:
            reasons.append(f"off-hours ({t.when.strftime('%H:%M')} UTC)")
            score += 15
        if dest in personal or t.destination in personal:
            reasons.append(f"personal cloud, webmail or public LLM destination ({dest})")
            score += 25
        if t.channel in ("llm_prompt",) and t.size > 50_000:
            reasons.append(f"large prompt to an LLM service ({human(t.size)})")
            score += 20
        if t.filename.lower().endswith(ARCHIVE_EXT) and t.size > 10 * 1024 * 1024:
            reasons.append(f"archive or encrypted container {t.filename} ({human(t.size)})")
            score += 25
        if t.channel == "usb" and t.size > 100 * 1024 * 1024:
            reasons.append(f"USB copy above 100 MB ({human(t.size)})")
            score += 20
        if t.channel == "dns":
            dns_daily[(t.user, day)] += t.size
            if dns_daily[(t.user, day)] > 1024 * 1024:
                reasons.append(f"DNS volume above 1 MB in a day ({human(dns_daily[(t.user, day)])}): tunneling pattern")
                score += 30
        hourly[(t.user, t.when.strftime("%Y-%m-%dT%H"))].append(t)
        if len(reasons) >= 2 or score >= 25:
            anomalies.append({
                "kind": "transfer", "user": t.user, "day": day, "destination": t.destination, "channel": t.channel,
                "bytes": t.size, "score": min(100, score), "reasons": reasons, "lines": [t.line],
            })
    for (user, hour_key), items in hourly.items():
        if len(items) > 20:
            anomalies.append({
                "kind": "burst", "user": user, "day": hour_key[:10], "destination": ", ".join(sorted({i.destination for i in items})[:3]),
                "channel": ", ".join(sorted({i.channel for i in items})), "bytes": sum(i.size for i in items), "score": 35,
                "reasons": [f"{len(items)} transfers within hour {hour_key[11:]}:00 UTC"], "lines": [i.line for i in items][:10],
            })
    return sorted(anomalies, key=lambda a: (-a["score"], a["user"], a["day"]))


def to_markdown(anomalies: list[dict[str, Any]], stats: dict[str, Any]) -> str:
    lines = [
        "# Egress baseline report", "",
        f"Transfers: {stats['transfers']}, users: {stats['users']}, period: {stats['from']} to {stats['to']}, parse errors: {stats['errors']}, anomalies: {len(anomalies)}.", "",
        "| Score | Kind | User | Day | Destination | Channel | Volume | Reasons | CSV lines |", "|---|---|---|---|---|---|---|---|---|",
    ]
    for a in anomalies:
        lines.append(f"| {a['score']} | {a['kind']} | {a['user']} | {a['day']} | {a['destination']} | {a['channel']} | {human(a['bytes'])} | {'; '.join(a['reasons'])} | {', '.join(str(x) for x in a['lines']) or '-'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find exfiltration anomalies in a transfers CSV.")
    parser.add_argument("csv", help="transfers CSV: timestamp,user,destination,bytes,channel[,filename]")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--z-threshold", type=float, default=3.0, help="z-score above which a daily volume is a spike (default 3.0)")
    parser.add_argument("--min-history", type=int, default=5, help="days of history required before scoring volume (default 5)")
    parser.add_argument("--work-hours", default="7-20", help="working hours in UTC as start-end (default 7-20)")
    parser.add_argument("--personal-domains", help="comma-separated extra personal/webmail domains")
    parser.add_argument("--output", help="write the report to this file")
    args = parser.parse_args(argv)

    path = Path(args.csv)
    if not path.is_file():
        print(f"error: {path} is not a file", file=sys.stderr)
        return 2
    try:
        start_text, end_text = args.work_hours.split("-")
        work_start, work_end = int(start_text), int(end_text)
    except ValueError:
        print("error: --work-hours must look like 7-20", file=sys.stderr)
        return 2
    if not (0 <= work_start < work_end <= 24) or args.z_threshold <= 0 or args.min_history < 1:
        print("error: invalid thresholds", file=sys.stderr)
        return 2
    personal = set(PERSONAL_DEFAULT)
    if args.personal_domains:
        personal |= {d.strip().lower() for d in args.personal_domains.split(",") if d.strip()}
    try:
        rows, errors = load(path)
    except (ValueError, csv.Error, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("error: no valid transfer rows", file=sys.stderr)
        return 2

    anomalies = analyse(rows, args.z_threshold, args.min_history, work_start, work_end, personal)
    stats = {"transfers": len(rows), "users": len({r.user for r in rows}), "from": rows[0].when.strftime("%Y-%m-%d"), "to": rows[-1].when.strftime("%Y-%m-%d"), "errors": len(errors), "anomalies": len(anomalies)}
    text = json.dumps({"stats": stats, "parse_errors": errors, "anomalies": anomalies}, indent=2) if args.json else to_markdown(anomalies, stats)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"{len(anomalies)} anomalies -> {args.output}")
    else:
        print(text)
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
