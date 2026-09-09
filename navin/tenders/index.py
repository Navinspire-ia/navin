# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Local tenders index: on-disk book the agent can search and read in full."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from navin.tenders.normalize import looks_like_notice
from navin.tenders.profile import filed_documents, normalize_references, public_profile


def local_paths(store: Any) -> dict[str, str]:
    root = store.root
    return {
        "root": str(root),
        "profile": str(store.profile_path),
        "tenders": str(store.tenders_path),
        "journal": str(store.journal_path),
        "loop": str(store.loop_path),
        "loop_intent": str(store.loop_intent_path),
        "discoveries": str(store.discoveries_path),
        "files": str(store.files_dir),
        "index_json": str(root / "index.json"),
        "index": str(root / "INDEX.md"),
        "book": str(root / "book.md"),
        "dossier": str(root / "dossier.md"),
        "knowledge": str(root / "knowledge.md"),
        "notices": str(root / "notices.md"),
    }


def notice_card(row: dict[str, Any]) -> dict[str, Any]:
    response = row.get("response") if isinstance(row.get("response"), dict) else {}
    mail = row.get("mail") if isinstance(row.get("mail"), list) else []
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "buyer": str(row.get("buyer") or ""),
        "country": str(row.get("country") or ""),
        "sector": str(row.get("sector") or ""),
        "reference": str(row.get("reference") or ""),
        "score": row.get("score"),
        "go": row.get("go"),
        "go_reason": str(row.get("go_reason") or ""),
        "stage": str(row.get("stage") or ""),
        "deadline": str(row.get("deadline") or ""),
        "budget": row.get("budget"),
        "currency": str(row.get("currency") or ""),
        "source_url": str(row.get("source_url") or ""),
        "source_id": str(row.get("source_id") or ""),
        "publication_date": str(row.get("publication_date") or ""),
        "fetched_at": row.get("fetched_at"),
        "favorite": bool(row.get("favorite")),
        "archived": bool(row.get("archived")),
        "cpv": str(row.get("cpv") or ""),
        "has_response": bool(response),
        "has_mail": bool(mail),
        "crm_opportunity_id": str(row.get("crm_opportunity_id") or ""),
    }


def _haystack(row: dict[str, Any]) -> str:
    parts = [
        row.get("id"),
        row.get("title"),
        row.get("buyer"),
        row.get("country"),
        row.get("sector"),
        row.get("reference"),
        row.get("cpv"),
        row.get("description"),
        row.get("eligibility"),
        row.get("stage"),
        row.get("go_reason"),
        row.get("go_note"),
        row.get("source_url"),
        row.get("source_id"),
        row.get("score"),
    ]
    for blob in (row.get("analysis"), row.get("response")):
        if not isinstance(blob, dict):
            continue
        for value in blob.values():
            if isinstance(value, (str, int, float)):
                parts.append(value)
    return " ".join(str(part or "") for part in parts).lower()


def _csv_tokens(value: Any, *, upper: bool = False) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = str(value or "").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        token = item.strip()
        if upper:
            token = token.upper()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token)
    return out


def _iso_day(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    return dt.datetime.fromtimestamp(stamp, tz=dt.UTC).strftime("%Y-%m-%d")


def _in_iso_range(day: str, start: str, end: str) -> bool:
    if not start and not end:
        return True
    if not day:
        return False
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool_or_none(value: Any) -> bool | None:
    if value in (True, False):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "go", "1", "yes", "on"}:
        return True
    if text in {"false", "no-go", "nogo", "0", "no", "off"}:
        return False
    return None


def search_notices(
    rows: list[dict[str, Any]],
    query: str = "",
    *,
    stage: str = "",
    country: str = "",
    countries: Any = None,
    sector: str = "",
    source_id: str = "",
    buyer: str = "",
    go: bool | None = None,
    favorite: bool | None = None,
    include_archived: bool = True,
    deadline_from: str = "",
    deadline_to: str = "",
    published_from: str = "",
    published_to: str = "",
    arrived_from: str = "",
    arrived_to: str = "",
    min_score: Any = None,
    max_score: Any = None,
    min_budget: Any = None,
    max_budget: Any = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """Filter the local book. Default: every matching notice, archive included."""
    needle = str(query or "").strip().lower()
    stage_key = str(stage or "").strip().lower()
    country_keys = {item.upper() for item in _csv_tokens(countries, upper=True)}
    single = str(country or "").strip().upper()
    if single:
        country_keys.add(single)
    sectors = {item.lower() for item in _csv_tokens(sector)}
    sources = {item.lower() for item in _csv_tokens(source_id)}
    buyers = {item.lower() for item in _csv_tokens(buyer)}
    dead_from = _iso_day(deadline_from)
    dead_to = _iso_day(deadline_to)
    pub_from = _iso_day(published_from)
    pub_to = _iso_day(published_to)
    arr_from = _iso_day(arrived_from)
    arr_to = _iso_day(arrived_to)
    low_score = _as_float(min_score)
    high_score = _as_float(max_score)
    low_budget = _as_float(min_budget)
    high_budget = _as_float(max_budget)
    cap = int(limit or 0)
    hits: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not looks_like_notice(row):
            continue
        archived = bool(row.get("archived"))
        if stage_key == "archived":
            if not archived:
                continue
        else:
            if archived and not include_archived:
                continue
            if stage_key and str(row.get("stage") or "").strip().lower() != stage_key:
                continue
        if country_keys and str(row.get("country") or "").strip().upper() not in country_keys:
            continue
        if sectors:
            blob = " ".join(
                str(row.get(key) or "") for key in ("sector", "cpv", "title", "description")
            ).lower()
            if not any(token in blob for token in sectors):
                continue
        if sources and str(row.get("source_id") or "").strip().lower() not in sources:
            continue
        if buyers and str(row.get("buyer") or "").strip().lower() not in buyers:
            continue
        if go is True and row.get("go") is not True:
            continue
        if go is False and row.get("go") is not False:
            continue
        if favorite is True and not row.get("favorite"):
            continue
        if favorite is False and row.get("favorite"):
            continue
        if not _in_iso_range(_iso_day(row.get("deadline")), dead_from, dead_to):
            continue
        if not _in_iso_range(_iso_day(row.get("publication_date")), pub_from, pub_to):
            continue
        if not _in_iso_range(_iso_day(row.get("fetched_at")), arr_from, arr_to):
            continue
        score = _as_float(row.get("score"))
        if low_score is not None and (score is None or score < low_score):
            continue
        if high_score is not None and (score is None or score > high_score):
            continue
        budget = _as_float(row.get("budget"))
        if low_budget is not None and (budget is None or budget < low_budget):
            continue
        if high_budget is not None and (budget is None or budget > high_budget):
            continue
        if needle and needle not in _haystack(row):
            continue
        hits.append(notice_card(row))
        if cap > 0 and len(hits) >= cap:
            break
    return hits


def _join(values: Any, sep: str = ", ") -> str:
    if isinstance(values, list):
        return sep.join(str(item).strip() for item in values if str(item).strip())
    return str(values or "").strip()


def format_dossier(profile: dict[str, Any]) -> str:
    channels = profile.get("channels") if isinstance(profile.get("channels"), dict) else {}
    on_channels = [name for name in ("telegram", "whatsapp", "email", "teams", "slack") if channels.get(name)]
    dest = [f"- {name}: {channels.get(f'{name}_to') or 'on'}" for name in on_channels]
    refs = normalize_references(profile.get("references"))
    sites = profile.get("sites") if isinstance(profile.get("sites"), list) else []
    partners = profile.get("partners") if isinstance(profile.get("partners"), list) else []
    team = profile.get("team") if isinstance(profile.get("team"), list) else []
    price_book = profile.get("price_book") if isinstance(profile.get("price_book"), list) else []
    lines = [
        "# Tenders company dossier",
        "",
        f"- Name: {profile.get('name') or '-'}",
        f"- Legal name: {profile.get('legal_name') or '-'}",
        f"- Specialty: {profile.get('specialty') or '-'}",
        f"- Country: {profile.get('country') or '-'}",
        f"- Currency: {profile.get('currency') or '-'}",
        f"- Locale: {profile.get('locale') or '-'}",
        f"- Email: {profile.get('email') or '-'}",
        f"- Phone: {profile.get('phone') or '-'}",
        f"- Website: {profile.get('website') or '-'}",
        f"- Crafts: {_join(profile.get('crafts')) or '-'}",
        f"- Search countries: {_join(profile.get('countries')) or '-'}",
        f"- Languages: {_join(profile.get('languages')) or '-'}",
        f"- Certifications: {_join(profile.get('certifications')) or '-'}",
        f"- Tender types: {_join(profile.get('tender_types')) or '-'}",
        f"- Project types: {_join(profile.get('project_types')) or '-'}",
        f"- Strengths: {_join(profile.get('strengths')) or '-'}",
        f"- Headcount: {profile.get('headcount') or 0}",
        f"- Min budget: {profile.get('min_budget') or 0}",
        f"- Max budget: {profile.get('max_budget') or 0}",
        f"- Min deadline days: {profile.get('min_deadline_days') or 0}",
        f"- Min score: {profile.get('min_score') or 0}",
        f"- Turnover: {profile.get('turnover') or 0}",
        f"- Send mode: {profile.get('send_mode') or 'approval'}",
        f"- Source ids: {_join(profile.get('source_ids')) or '-'}",
        f"- Wizard complete: {bool(profile.get('wizard_complete'))}",
        f"- Wizard step: {profile.get('wizard_step') or 1}",
        f"- Brief: {profile.get('brief') or '-'}",
    ]
    if dest:
        lines.extend(["", "## Channels", "", *dest])
    else:
        lines.extend(["", "## Channels", "", "none"])
    if sites:
        lines.extend(["", "## Sites", ""])
        for row in sites:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('kind') or 'branch'} | {row.get('name') or '-'} | "
                f"{row.get('city') or ''} {row.get('country') or ''}".strip()
            )
    if partners:
        lines.extend(["", "## Partners", ""])
        for row in partners:
            if not isinstance(row, dict):
                continue
            lines.append(f"- {row.get('name') or '-'} | {row.get('role') or ''} | {row.get('country') or ''}")
    if refs:
        lines.extend(["", "## References", ""])
        for row in refs:
            excerpt = str(row.get("excerpt") or "").strip()
            lines.append(
                f"- {row.get('title') or '-'} | {row.get('client') or ''} | "
                f"{row.get('year') or ''} | {row.get('country') or ''}"
            )
            if excerpt:
                lines.append(f"  excerpt: {excerpt[:800]}")
    if team:
        lines.extend(["", "## Team", ""])
        for row in team:
            if isinstance(row, dict):
                lines.append(f"- {row.get('name') or row.get('role') or row}")
            else:
                lines.append(f"- {row}")
    if price_book:
        lines.extend(["", "## Price book", ""])
        for row in price_book:
            if isinstance(row, dict):
                lines.append(f"- {row.get('item') or row.get('name') or row} | {row.get('price') or ''}")
            else:
                lines.append(f"- {row}")
    if profile.get("methodology"):
        lines.extend(["", "## Methodology", "", str(profile.get("methodology") or "")])
    if profile.get("legal_clauses"):
        lines.extend(["", "## Legal clauses", "", str(profile.get("legal_clauses") or "")])
    docs = filed_documents(profile)
    word = [row for row in docs if row.get("bucket") == "word"]
    ppt = [row for row in docs if row.get("bucket") == "ppt"]
    slides = [row for row in docs if row.get("bucket") == "reuse_slides"]
    lines.extend(["", "## Templates", ""])
    lines.append(f"- Word models: {len(word)}")
    for row in word:
        lines.append(f"  - {row.get('name') or '-'} | {row.get('file_id') or ''} | {row.get('chars') or 0} chars")
        if row.get("excerpt"):
            lines.append(f"    excerpt: {str(row.get('excerpt') or '')[:800]}")
    lines.append(f"- PowerPoint models: {len(ppt)}")
    for row in ppt:
        lines.append(f"  - {row.get('name') or '-'} | {row.get('file_id') or ''} | {row.get('chars') or 0} chars")
        if row.get("excerpt"):
            lines.append(f"    excerpt: {str(row.get('excerpt') or '')[:800]}")
    lines.append(f"- Reuse slides: {len(slides)}")
    for row in slides:
        lines.append(f"  - {row.get('name') or '-'} | {row.get('file_id') or ''}")
        if row.get("excerpt"):
            lines.append(f"    excerpt: {str(row.get('excerpt') or '')[:800]}")
    if docs:
        lines.extend(
            [
                "",
                "## Knowledge extracts (mandatory for write)",
                "",
                "If a file is listed here, tenders action=write must reuse its extract. Never invent a page.",
                "Read one file with tenders action=file id=<file_id>.",
            ]
        )
        for row in docs:
            lines.append(
                f"- {row.get('label')}: {row.get('name') or row.get('title') or '-'} "
                f"| id={row.get('file_id') or '-'} | extract={row.get('extract') or '-'}"
            )
    return "\n".join(lines).strip() + "\n"


def format_notices_md(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Tenders notices",
        "",
        "Backup table. The live book is `tenders action=status`.",
        "",
        "| Id | Score | GO | Stage | Country | Deadline | Buyer | Title |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        card = notice_card(row)
        go = "yes" if card.get("go") is True else "no" if card.get("go") is False else "-"
        title = str(card.get("title") or "").replace("|", "/")[:80]
        buyer = str(card.get("buyer") or "").replace("|", "/")[:40]
        lines.append(
            "| "
            + " | ".join(
                [
                    str(card.get("id") or ""),
                    str(card.get("score") if card.get("score") is not None else "-"),
                    go,
                    str(card.get("stage") or "-"),
                    str(card.get("country") or "-"),
                    str(card.get("deadline") or "-"),
                    buyer,
                    title,
                ]
            )
            + " |"
        )
    if len(lines) == 6:
        lines.append("| - | - | - | - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def format_agent_status(snap: dict[str, Any], store: Any | None = None) -> str:
    """Full local book for the tenders tool. The agent must treat this as source of truth."""
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    kpis = snap.get("kpis") if isinstance(snap.get("kpis"), dict) else {}
    files = snap.get("files") if isinstance(snap.get("files"), dict) else {}
    paths = files or (local_paths(store) if store is not None else {})
    rows = snap.get("tenders") if isinstance(snap.get("tenders"), list) else []
    notices = [row for row in rows if isinstance(row, dict) and looks_like_notice(row)]
    open_n = kpis.get("open", sum(1 for row in notices if row.get("go") is not False and row.get("stage") != "no-go"))
    lines = [
        "TENDERS LOCAL BOOK (source of truth). Do not invent a notice missing here.",
        "The chat may discuss every notice below: live, favorite, archive, GO and no-go. Nothing is hidden.",
        "On-file Word/PPT models and references are mandatory for tenders action=write.",
        "Read one extract with tenders action=file id=<file_id>.",
        (
            f"Open {open_n}, new {kpis.get('new', 0)}, qualified {kpis.get('qualified', 0)}, "
            f"deadline<7d {kpis.get('deadline_7d', 0)}. {kpis.get('headline') or ''}"
        ).strip(),
        f"Total notices {len(notices)}. Wizard ready: {bool(snap.get('wizard_ready'))}.",
    ]
    loop = snap.get("loop") if isinstance(snap.get("loop"), dict) else None
    if loop is None and store is not None and hasattr(store, "load_loop"):
        from navin.tenders.loop import peek_loop

        loop = peek_loop(store)
    if loop:
        from navin.tenders.loop import format_loop_status

        lines.append(format_loop_status(loop))
    lines.append(
        f"Company: {profile.get('name') or '-'} | crafts {_join(profile.get('crafts')) or '-'} | "
        f"types {_join(profile.get('tender_types')) or '-'} | "
        f"projects {_join(profile.get('project_types')) or '-'} | "
        f"countries {_join(profile.get('countries')) or '-'}."
    )

    if paths:
        lines.append("Local files: " + ", ".join(f"{key}={value}" for key, value in paths.items()))
    lines.append("")
    lines.append(format_dossier(profile).rstrip())
    lines.append("")
    if notices:
        ranked = sorted(
            notices,
            key=lambda row: float(row.get("score") or 0),
            reverse=True,
        )
        lines.append("## Pipeline")
        lines.append("")
        for row in ranked:
            card = notice_card(row)
            go = "GO" if card.get("go") is True else "NO-GO" if card.get("go") is False else "-"
            flags = []
            if card.get("favorite"):
                flags.append("FAV")
            if card.get("archived"):
                flags.append("ARCH")
            mark = f" | {' '.join(flags)}" if flags else ""
            lines.append(
                f"- {card['id']} | {card.get('score') if card.get('score') is not None else '-'} | "
                f"{go} | {card.get('stage') or '-'} | {card.get('country') or '-'} | "
                f"{card.get('sector') or '-'} | {card.get('deadline') or '-'} | "
                f"{card.get('buyer') or '-'} | {card.get('title') or '-'}{mark}"
            )
        lines.append("")
        lines.append("Use tenders action=get id=tn-... for the full notice, drafts and mail.")
        lines.append(
            "Use tenders action=search query=... country=FR sector=Cloud deadline_from=2026-01-01 "
            "to filter this book. Default search returns the full match set, archive included."
        )
    else:
        lines.append("## Pipeline")
        lines.append("")
        lines.append(
            "Empty. The desk loop collects on its schedule (tenders action=start). "
            "One-shot: tenders action=collect after the wizard is complete."
        )
    journal = snap.get("journal") if isinstance(snap.get("journal"), list) else []
    if journal:
        lines.extend(["", "## Journal", ""])
        for event in journal[-12:]:
            if isinstance(event, dict):
                lines.append(f"- {event.get('kind') or 'event'}: {event.get('text') or ''}")
    return "\n".join(lines).strip() + "\n"


def format_knowledge(profile: dict[str, Any]) -> str:
    docs = filed_documents(profile)
    lines = [
        "# Tenders knowledge extracts",
        "",
        "These extracts come from imported Word, PowerPoint, PDF and reference files.",
        "tenders action=write must reuse them when they exist. Never invent a page.",
        "",
    ]
    if not docs:
        lines.append("No models or references on file.")
        return "\n".join(lines).strip() + "\n"
    for row in docs:
        lines.append(f"## {row.get('label')}: {row.get('name') or row.get('title') or '-'}")
        lines.append(f"id: {row.get('file_id') or '-'}")
        lines.append(f"extract: {row.get('extract') or '-'}")
        lines.append("")
        lines.append(str(row.get("excerpt") or "no extract"))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _index_loop(store: Any) -> dict[str, Any]:
    if not hasattr(store, "load_loop"):
        return {}
    from navin.tenders.loop import peek_loop

    return peek_loop(store)


def build_index_payload(store: Any, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    data = profile if isinstance(profile, dict) else store.load_profile()
    rows = [row for row in store.load_tenders() if looks_like_notice(row)]
    secrets_map = store.load_secrets()
    custom_keys = {
        key.split(":", 1)[1]
        for key in secrets_map
        if str(key).startswith("custom:") and ":" in str(key)
    }
    files = []
    if store.files_dir.is_dir():
        files = sorted(path.name for path in store.files_dir.iterdir() if path.is_file())
    return {
        "schema": 1,
        "generated_at": time.time(),
        "root": str(store.root),
        "files": local_paths(store),
        "uploads": files,
        "profile": public_profile(
            data,
            has_sam_key=bool(store.get_secret("sam_gov") or secrets_map.get("sam_gov")),
            custom_keys=custom_keys,
        ),
        "notices": [notice_card(row) for row in rows],
        "knowledge": filed_documents(data),
        "count": len(rows),
        "journal": store.load_journal()[-40:],
        "discoveries": store.load_discoveries(),
        "loop": _index_loop(store),
    }


def write_local_index(store: Any, profile: dict[str, Any] | None = None) -> dict[str, str]:
    """Write index.json, INDEX.md, book.md, dossier.md and notices.md next to the JSON store."""
    data = profile if isinstance(profile, dict) else store.load_profile()
    paths = local_paths(store)
    rows = [row for row in store.load_tenders() if looks_like_notice(row)]
    payload = build_index_payload(store, data)
    _atomic_write_text(store.root / "index.json", json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    dossier = format_dossier(data)
    _atomic_write_text(store.root / "dossier.md", dossier)
    _atomic_write_text(store.root / "knowledge.md", format_knowledge(data))
    _atomic_write_text(store.root / "notices.md", format_notices_md(rows))
    from navin.tenders.desk import _kpis
    from navin.tenders.profile import wizard_ready

    book = format_agent_status(
        {
            "profile": payload["profile"],
            "kpis": _kpis(rows, data),
            "files": paths,
            "tenders": rows,
            "wizard_ready": wizard_ready(data),
            "journal": payload.get("journal") or [],
            "loop": payload.get("loop"),
        },
        store,
    )
    _atomic_write_text(store.root / "book.md", book)
    index = "\n".join(
        [
            "# Tenders local index",
            "",
            "All Tenders facts live here. The agent must read this book via `tenders action=status`.",
            "Search with `tenders action=search`. Open one notice with `tenders action=get`.",
            "The desk loop is `loop.json` (Studio Start loop, Tauri, navin tenders). Heartbeat is follow only.",
            "",
            *[f"- {key}: `{value}`" for key, value in paths.items()],
            "",
            f"- Notices indexed: {len(rows)}",
            f"- Uploads: {len(payload.get('uploads') or [])}",
            "",
        ]
    )
    _atomic_write_text(store.root / "INDEX.md", index)
    return paths


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-tenders-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            if text and not text.endswith("\n"):
                handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
