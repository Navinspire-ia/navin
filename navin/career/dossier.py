"""Local career dossier: files on disk + full text for the agent."""

from __future__ import annotations

from typing import Any

from navin.career.store import CareerStore, _atomic_write_text


def local_paths(store: CareerStore) -> dict[str, str]:
    return {
        "root": str(store.root),
        "profile": str(store.profile_path),
        "cv": str(store.root / "cv.md"),
        "dossier": str(store.root / "dossier.md"),
        "index": str(store.root / "INDEX.md"),
        "book": str(store.root / "book.md"),
        "opportunities": str(store.jobs_path),
        "applications": str(store.apps_path),
        "inbox": str(store.inbox_path),
        "journal": str(store.journal_path),
        "applications_md": str(store.root / "applications.md"),
    }


def _api_key_line(profile: dict[str, Any]) -> str:
    """Flags only. Never print the secret values."""
    ids = {str(item).strip().lower() for item in (profile.get("source_ids") or []) if str(item).strip()}
    flags = profile.get("api_keys") if isinstance(profile.get("api_keys"), dict) else {}
    parts = []
    for name in ("adzuna", "jooble", "usajobs"):
        enabled = name in ids
        ready = bool(flags.get(name))
        if not enabled and not ready:
            continue
        state = "live" if enabled and ready else "on, key missing" if enabled else "key on file, source off"
        parts.append(f"{name} ({state})")
    return _join(parts) or "none enabled"


def _join(values: Any, sep: str = ", ") -> str:
    if isinstance(values, list):
        return sep.join(str(item).strip() for item in values if str(item).strip())
    return str(values or "").strip()


def _block(title: str, body: str) -> str:
    text = (body or "").strip()
    if not text:
        return ""
    return f"## {title}\n\n{text}\n"


def format_dossier(profile: dict[str, Any]) -> str:
    """Human-readable competence dossier. Facts only. No invented lines."""
    kind = "company" if profile.get("account_kind") == "company" else "solo"
    track = str(profile.get("track") or "freelance")
    pay = (
        f"TJM {profile.get('min_rate') or '-'} - {profile.get('max_rate') or '-'} {profile.get('currency') or ''}"
        if track == "freelance"
        else f"Salary {profile.get('min_salary') or '-'} - {profile.get('max_salary') or '-'} {profile.get('currency') or ''}"
    )
    company = profile.get("company") if isinstance(profile.get("company"), dict) else {}
    channels = profile.get("channels") if isinstance(profile.get("channels"), dict) else {}
    parts = [
        "# Career dossier",
        "",
        f"- Kind: {kind}",
        f"- Track: {track}",
        f"- Name: {profile.get('display_name') or '-'}",
        f"- Headline: {profile.get('headline') or '-'}",
        f"- Email: {profile.get('email') or '-'}",
        f"- Phone: {profile.get('phone') or '-'}",
        f"- Residence: {profile.get('residence_country') or '-'}",
        f"- Search countries: {_join(profile.get('countries_primary')) or '-'}",
        f"- Titles: {_join(profile.get('titles')) or '-'}",
        f"- Stack: {_join(profile.get('stack')) or '-'}",
        f"- Work mode: {profile.get('work_mode') or '-'}",
        f"- Hybrid days: {profile.get('hybrid_days_min') or 0}-{profile.get('hybrid_days_max') or 0}",
        f"- Pay: {pay.strip()}",
        f"- Available: {profile.get('available_from') or '-'}",
        f"- Languages: {_join(profile.get('languages')) or '-'}",
        f"- Visa: {profile.get('visa') or 'none'}",
        f"- Wizard complete: {bool(profile.get('wizard_complete'))}",
        f"- Prospect email approved: {bool(profile.get('prospect_email_approved'))}",
        f"- Source ids: {_join(profile.get('source_ids')) or '(live defaults)'}",
        f"- Official APIs: {_api_key_line(profile)}",
    ]
    if kind == "company":
        parts.extend(
            [
                "",
                "## Company",
                "",
                f"- Name: {company.get('name') or '-'}",
                f"- Email: {company.get('email') or '-'}",
                f"- Phone: {company.get('phone') or '-'}",
                f"- Address: {company.get('address') or '-'}",
                f"- City: {company.get('city') or '-'}",
                f"- Country: {company.get('country') or '-'}",
            ]
        )
    parts.append(_block("Master CV", str(profile.get("master_cv") or "")))
    experiences = profile.get("experiences") if isinstance(profile.get("experiences"), list) else []
    if experiences:
        lines = []
        for row in experiences:
            if not isinstance(row, dict):
                continue
            head = " - ".join(item for item in (row.get("title"), row.get("company")) if item)
            when = f" ({row.get('period')})" if row.get("period") else ""
            lines.append(f"- {head}{when}".strip())
            if row.get("facts"):
                lines.append(f"  {row['facts']}")
        parts.append(_block("Experience", "\n".join(lines)))
    education = profile.get("education") if isinstance(profile.get("education"), list) else []
    if education:
        lines = []
        for row in education:
            if not isinstance(row, dict):
                continue
            head = " - ".join(item for item in (row.get("diploma"), row.get("school")) if item)
            when = f" ({row.get('year')})" if row.get("year") else ""
            lines.append(f"- {head}{when}".strip())
        parts.append(_block("Education", "\n".join(lines)))
    if profile.get("strengths"):
        parts.append(_block("Strengths", "\n".join(f"- {item}" for item in profile["strengths"] if str(item).strip())))
    if profile.get("weaknesses"):
        parts.append(_block("Gaps", "\n".join(f"- {item}" for item in profile["weaknesses"] if str(item).strip())))
    if profile.get("highlights"):
        parts.append(_block("Values", "\n".join(f"- {item}" for item in profile["highlights"] if str(item).strip())))
    projects = profile.get("projects") if isinstance(profile.get("projects"), list) else []
    if projects:
        lines = []
        for row in projects:
            if isinstance(row, dict) and (row.get("title") or row.get("result")):
                lines.append(f"- {row.get('title') or ''}: {row.get('result') or ''}".strip(": "))
        parts.append(_block("Projects", "\n".join(lines)))
    if profile.get("prospect_email"):
        parts.append(_block("Prospecting email", str(profile.get("prospect_email") or "")))
    on_channels = [name for name in ("email", "teams", "whatsapp", "telegram") if channels.get(name)]
    dest = []
    for name in on_channels:
        dest.append(f"- {name}: {channels.get(f'{name}_to') or 'on'}")
    parts.append(_block("Channels", "\n".join(dest) if dest else "none"))
    talents = profile.get("talents") if isinstance(profile.get("talents"), list) else []
    if kind == "company" and talents:
        lines = []
        for row in talents:
            if not isinstance(row, dict):
                continue
            lines.append(f"### {row.get('name') or 'Untitled'}")
            if row.get("headline"):
                lines.append(row["headline"])
            if row.get("master_cv"):
                lines.append(str(row["master_cv"]))
            if row.get("strengths"):
                lines.append("Strengths: " + _join(row.get("strengths")))
            lines.append("")
        parts.append(_block("Talents", "\n".join(lines)))
    return "\n".join(part for part in parts if part).strip() + "\n"


def format_applications_md(apps: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    by_id = {str(row.get("id") or ""): row for row in rows if isinstance(row, dict)}
    lines = [
        "# Career applications",
        "",
        "Backup table. The live book is `career action=status`.",
        "",
        "| Company | Role | Pack | Stage | Next action |",
        "| --- | --- | --- | --- | --- |",
    ]
    for app in apps:
        if not isinstance(app, dict):
            continue
        job = by_id.get(str(app.get("opportunity_id") or "")) or {}
        lines.append(
            "| "
            + " | ".join(
                [
                    str(job.get("company") or "").replace("|", "/"),
                    str(job.get("title") or "").replace("|", "/"),
                    str(app.get("cv_name") or "").replace("|", "/"),
                    str(app.get("stage") or "").replace("|", "/"),
                    str(app.get("next_action") or "").replace("|", "/"),
                ]
            )
            + " |"
        )
    if len(lines) == 6:
        lines.append("| - | - | - | - | - |")
    return "\n".join(lines) + "\n"


def _offer_flags(row: dict[str, Any]) -> str:
    flags = []
    if row.get("favorite"):
        flags.append("FAV")
    if row.get("archived"):
        flags.append("ARCH")
    return f" | {' '.join(flags)}" if flags else ""


def _offer_domain(row: dict[str, Any]) -> str:
    stack = row.get("stack") if isinstance(row.get("stack"), list) else []
    for item in stack:
        token = str(item or "").strip()
        if token:
            return token
    return "-"


def format_agent_status(snap: dict[str, Any], store: CareerStore | None = None) -> str:
    """Full local book for the career tool. The agent must treat this as source of truth."""
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    kpis = snap.get("kpis") if isinstance(snap.get("kpis"), dict) else {}
    files = snap.get("files") if isinstance(snap.get("files"), dict) else {}
    paths = files or (local_paths(store) if store is not None else {})
    rows = snap.get("opportunities") if isinstance(snap.get("opportunities"), list) else []
    apps = snap.get("applications") if isinstance(snap.get("applications"), list) else []
    inbox = snap.get("inbox") if isinstance(snap.get("inbox"), list) else []
    offers = [row for row in rows if isinstance(row, dict)]
    live_n = sum(1 for row in offers if not row.get("archived"))
    fav_n = sum(1 for row in offers if row.get("favorite") and not row.get("archived"))
    arch_n = sum(1 for row in offers if row.get("archived"))
    counts = {
        "opportunities": kpis.get("opportunities", live_n),
        "strong": kpis.get("strong", sum(1 for row in offers if float(row.get("match_score") or 0) >= 80)),
        "applications": kpis.get("applications", len(apps)),
        "interviews": kpis.get("interviews", sum(1 for row in offers if row.get("stage") == "interview")),
    }
    lines = [
        "CAREER LOCAL BOOK (source of truth). Do not invent facts missing here.",
        "The chat may discuss every offer below: live, favorite and archive. "
        "Studio filters are UI-only. Nothing is hidden.",
    ]
    if kpis.get("headline"):
        lines.append(f"KPI: {kpis['headline']}")
    lines.append(
        f"Opportunities {counts['opportunities']} | strong {counts['strong']} | "
        f"applications {counts['applications']} | interviews {counts['interviews']}."
    )
    lines.append(f"Book lists {len(offers)} offer(s): live {live_n}, favorite {fav_n}, archive {arch_n}.")
    if paths:
        lines.append("Local files: " + ", ".join(f"{key}={value}" for key, value in paths.items()))
    lines.append("")
    lines.append(format_dossier(profile))
    if offers:
        ranked = sorted(
            offers,
            key=lambda row: float(row.get("match_score") or 0),
            reverse=True,
        )
        lines.append("## Pipeline")
        lines.append("")
        for row in ranked:
            posted = str(row.get("posted_at") or "").strip() or "-"
            pay = row.get("compensation")
            money = f"{pay} {row.get('currency') or ''}".strip() if pay not in (None, "") else "-"
            lines.append(
                f"- {row.get('id')} | {row.get('title') or '-'} | {row.get('company') or '-'} | "
                f"{row.get('country') or '-'} | {_offer_domain(row)} | {row.get('remote') or '-'} | "
                f"{posted} | {money} | {row.get('match_score') if row.get('match_score') is not None else '-'}% | "
                f"{row.get('stage') or '-'} | {row.get('source') or '-'}{_offer_flags(row)}"
            )
        lines.append("")
        lines.append("Use career action=status to reload this full book. Use career action=read file=book for the same list.")
        lines.append("Discuss any id above. UI country / domain / date filters never restrict this chat.")
    else:
        lines.append("## Pipeline")
        lines.append("")
        lines.append("Empty. Run career action=search after the wizard is complete.")
    if apps:
        lines.append("")
        lines.append("## Applications")
        lines.append("")
        for row in apps:
            if not isinstance(row, dict):
                continue
            lines.append(f"- {row.get('id')} | {row.get('opportunity_id')} | {row.get('cv_name') or ''} | {row.get('stage') or ''}")
        lines.append("")
    if inbox:
        lines.append("")
        lines.append("## Inbox")
        lines.append("")
        for row in inbox:
            if not isinstance(row, dict):
                continue
            lines.append(f"- {row.get('classification') or 'waiting'}: {row.get('subject') or ''}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_local_index(store: CareerStore, profile: dict[str, Any] | None = None) -> dict[str, str]:
    """Write cv.md, dossier.md and INDEX.md next to the JSON store."""
    data = dict(profile) if isinstance(profile, dict) else store.load_profile()
    from navin.career.stack import api_key_flags

    data["api_keys"] = api_key_flags(store.load_secrets())
    paths = local_paths(store)
    cv = str(data.get("master_cv") or "").strip()
    _atomic_write_text(store.root / "cv.md", cv)
    dossier = format_dossier(data)
    _atomic_write_text(store.root / "dossier.md", dossier)
    rows = store.load_opportunities()
    apps = store.load_applications()
    book = format_agent_status(
        {
            "profile": data,
            "kpis": {},
            "files": paths,
            "opportunities": rows,
            "applications": apps,
            "inbox": store.load_inbox(),
        }
    )
    _atomic_write_text(store.root / "book.md", book)
    apps_md = format_applications_md(apps, rows)
    _atomic_write_text(store.root / "applications.md", apps_md)
    index = "\n".join(
        [
            "# Career local index",
            "",
            "All Career facts live here. The agent must read this book via `career action=status`.",
            "",
            *[f"- {key}: `{value}`" for key, value in paths.items()],
            "",
        ]
    )
    _atomic_write_text(store.root / "INDEX.md", index)
    return paths
