"""Leads desk snapshots and mutations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.leads.errors import LeadsError
from navin.leads.lock import leads_desk_lock
from navin.leads.qualify import apply_qualification
from navin.leads.sequence import due_steps, mark_step_sent, start_sequence
from navin.leads.sources import STAGES, catalog
from navin.leads.store import LeadsStore, lead_key
from navin.leads.waterfall import enrich_row, hunt_companies, is_public_body


def _is_live(row: dict[str, Any]) -> bool:
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return not row.get("archived") and not extra.get("archived")


def _require_live(store: LeadsStore, lead_id: str) -> dict[str, Any]:
    row = store.get_lead(lead_id)
    if not _is_live(row):
        raise LeadsError("lead is archived", status=409)
    return row


def _buying_signals(row: dict[str, Any]) -> list[dict[str, Any]]:
    signals = row.get("signals") if isinstance(row.get("signals"), list) else []
    return [
        item
        for item in signals
        if isinstance(item, dict) and item.get("kind") in {"funding", "hiring", "news"}
    ]


def _has_sequence(row: dict[str, Any]) -> bool:
    seq = row.get("sequence") if isinstance(row.get("sequence"), dict) else {}
    steps = seq.get("steps") if isinstance(seq.get("steps"), list) else []
    return bool(steps)


def in_outreach(row: dict[str, Any]) -> bool:
    """Contacted+ stages, or any lead already on a j0/j3/j7 sequence."""
    stage = str(row.get("stage") or "")
    if stage in {"contacted", "replied", "meeting", "opportunity", "won"}:
        return True
    return _has_sequence(row)


def _kpis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    live = [row for row in rows if _is_live(row)]
    strong = [row for row in live if int(row.get("score") or 0) >= 80]
    verified = [row for row in live if row.get("email_status") == "verified"]
    with_email = [row for row in live if row.get("email")]
    pipeline = [row for row in live if in_outreach(row)]
    due = [row for row in live if due_steps(row)]
    signals = [row for row in live if _buying_signals(row)]
    sources: dict[str, int] = {}
    for row in live:
        key = str(row.get("source") or "unknown")
        sources[key] = sources.get(key, 0) + 1
    sent_steps = sum(
        1
        for row in live
        for step in (
            (row.get("sequence") or {}).get("steps") or []
            if isinstance(row.get("sequence"), dict)
            else []
        )
        if isinstance(step, dict) and step.get("status") == "sent"
    )
    queue = sorted(
        [
            row
            for row in live
            if str(row.get("next_action") or "") == "contact now" or due_steps(row)
        ],
        key=lambda item: int(item.get("score") or 0),
        reverse=True,
    )[:8]
    return {
        "total": len(live),
        "strong": len(strong),
        "verified": len(verified),
        "with_email": len(with_email),
        "pipeline": len(pipeline),
        "due": len(due),
        "signals": len(signals),
        "sources": sources,
        "sent_steps": sent_steps,
        "headline": (
            f"{len(live)} leads, {len(strong)} tier A, {len(due)} follow-ups due, "
            f"{len(signals)} buying signals, {len(verified)} verified emails."
        ),
        "queue": [
            {
                "id": row.get("id"),
                "company": row.get("company"),
                "score": row.get("score"),
                "next_action": row.get("next_action"),
                "due": bool(due_steps(row)),
            }
            for row in queue
        ],
    }


def snapshot(store: LeadsStore, *, probe: bool = False) -> dict[str, Any]:
    profile = store.load_profile()
    if not store.profile_path.is_file():
        store.save_profile(profile)
    if not store.leads_path.is_file():
        store.save_leads([])
    rows = store.load_leads()
    providers = store.provider_status()
    if probe:
        providers = _attach_probes(providers)
    from navin.leads.loop import peek_loop

    loop = peek_loop(store)
    return {
        "profile": profile,
        "leads": rows,
        "loop": loop,
        "loop_brief": format_loop_brief(loop),
        "kpis": _kpis(rows),
        "stages": list(STAGES),
        "providers": providers,
        "catalog": catalog(),
        "channels": _channel_snapshot(profile),
        "wizard_ready": bool(profile.get("wizard_ready")),
        "tagline": "Discover. Qualify. Sequence. CRM. Outreach.",
        "files": {"root": str(store.root), "leads": str(store.leads_path), "profile": str(store.profile_path)},
    }


def _attach_probes(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from navin.leads.waterfall import probe_sirene

    sirene = probe_sirene()
    web_live = True
    try:
        from navin.agent.tools.web import WebSearchTool

        web_live = WebSearchTool is not None
    except Exception:
        web_live = False
    out: list[dict[str, Any]] = []
    for row in providers:
        item = dict(row)
        pid = str(item.get("id") or "")
        if pid == "sirene":
            item["live"] = bool(sirene.get("live"))
            item["error"] = str(sirene.get("error") or "")
        elif pid == "web":
            item["live"] = web_live
        elif pid == "linkedin":
            item["live"] = True
        out.append(item)
    return out


def _channel_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    from navin.leads.notify import channel_readiness
    from navin.leads.store import default_channels

    configured = profile.get("channels") if isinstance(profile.get("channels"), dict) else {}
    merged = {**default_channels(), **configured}
    ready = channel_readiness()
    return {"configured": merged, "ready": ready}


def save_profile(store: LeadsStore, incoming: dict[str, Any]) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        store.save_profile(incoming)
        store.append_journal({"kind": "profile", "text": "icp saved"})
        return snapshot(store)


def save_keys(store: LeadsStore, incoming: dict[str, Any]) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        store.save_secrets(incoming)
        store.append_journal({"kind": "keys", "text": "provider keys updated"})
        return snapshot(store)


def hunt(store: LeadsStore, body: dict[str, Any] | None = None) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=0) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        return _hunt(store, body)


def _hunt(store: LeadsStore, body: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = store.load_profile()
    incoming = body if isinstance(body, dict) else {}
    if incoming:
        merged = {**profile, **incoming, "wizard_ready": True}
        profile = store.save_profile(merged)
    from navin.leads.pipeline import run_hunt

    result = run_hunt(store, profile=profile, source="manual", hunt_fn=hunt_companies, enrich_fn=enrich_row)
    if not result.get("found"):
        raise LeadsError(
            "no companies yet. Refine the ICP (sector, countries, cities) or add a BYOK key (Apollo, Hunter, Pappers, Places).",
            status=422,
        )
    payload = snapshot(store)
    payload["hunt"] = result
    return payload


def enrich(store: LeadsStore, lead_id: str) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        row = _require_live(store, lead_id)
        next_row = enrich_row(row, store.load_profile(), store.load_secrets(), deep=True)
        store.patch_lead(lead_id, next_row)
        store.append_journal({"kind": "enrich", "id": lead_id})
        return snapshot(store)


def rescore(store: LeadsStore) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        profile = store.load_profile()
        rows = []
        for row in store.load_leads():
            next_row = apply_qualification(row, profile)
            if is_public_body(str(next_row.get("company") or "")):
                next_row["archived"] = True
            rows.append(next_row)
        store.save_leads(rows)
        return snapshot(store)


def sequence_start(store: LeadsStore, lead_id: str) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        return _sequence_start(store, lead_id)


def _sequence_start(store: LeadsStore, lead_id: str) -> dict[str, Any]:
    from navin.leads.draft import draft_sequence

    row = _require_live(store, lead_id)
    if store.is_opted_out(row):
        raise LeadsError("this contact opted out. The sequence will not start.", status=409)
    profile = store.load_profile()
    seq = start_sequence(row)
    drafted = draft_sequence({**row, "sequence": seq}, profile)
    seq = drafted.get("sequence") or seq
    store.patch_lead(lead_id, {"sequence": seq})
    store.append_journal({"kind": "sequence", "id": lead_id, "name": seq.get("name")})
    payload = snapshot(store)
    payload["sequence"] = seq
    return payload


def draft(
    store: LeadsStore, lead_id: str, *, step: int = 0, refresh: bool = False
) -> dict[str, Any]:
    """(Re)write the drafts of a lead's sequence; a step of 0 means every unsent step."""
    from navin.leads.draft import ai_draft, draft_sequence

    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError(
                "desk is busy. Wait for the current hunt or watch to finish.", status=409
            )
        row = _require_live(store, lead_id)
        profile = store.load_profile()
        if not isinstance(row.get("sequence"), dict):
            preview = ai_draft(row, profile, max(1, step or 1))
            payload = snapshot(store)
            payload["draft"] = preview
            return payload
        if step:
            steps = []
            for item in row["sequence"].get("steps") or []:
                if (
                    isinstance(item, dict)
                    and int(item.get("n") or 0) == step
                    and item.get("status") != "sent"
                ):
                    item = {**item, "draft": ai_draft(row, profile, step)}
                steps.append(item)
            seq = {**row["sequence"], "steps": steps}
        else:
            seq = (
                draft_sequence(row, profile, only_missing=not refresh).get("sequence")
                or row["sequence"]
            )
        store.patch_lead(lead_id, {"sequence": seq})
        payload = snapshot(store)
        payload["sequence"] = seq
        return payload


def sequences_run(store: LeadsStore) -> dict[str, Any]:
    """Draft (and, in autonomous mode, send) every due step now. Same rules as the loop."""
    from navin.leads.pipeline import auto_enroll, run_sequences

    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError(
                "desk is busy. Wait for the current hunt or watch to finish.", status=409
            )
        profile = store.load_profile()
        enrolled = auto_enroll(store, profile=profile)
        result = run_sequences(store, profile=profile)
        payload = snapshot(store)
        payload["sequences"] = {**result, "enrolled": enrolled}
        return payload


def optout(store: LeadsStore, value: str) -> dict[str, Any]:
    """Email or domain. Stops every sequence for that contact; the hunt never re-adds it."""
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError(
                "desk is busy. Wait for the current hunt or watch to finish.", status=409
            )
        blocked = store.add_optout(value)
        rows = store.load_leads()
        stopped = 0
        for index, row in enumerate(rows):
            if not store.is_opted_out(row):
                continue
            patch = {"stage": "lost" if row.get("stage") != "won" else row.get("stage")}
            if isinstance(row.get("sequence"), dict):
                patch["sequence"] = {**row["sequence"], "stopped": "opt-out"}
            rows[index] = {**row, **patch}
            stopped += 1
        if stopped:
            store.save_leads(rows)
        store.append_journal(
            {"kind": "optout", "value": str(value).strip().casefold(), "stopped": stopped}
        )
        payload = snapshot(store)
        payload["optout"] = {"count": len(blocked), "stopped": stopped}
        return payload


EXPORT_COLUMNS = (
    "company",
    "person",
    "role",
    "email",
    "email_status",
    "phone",
    "website",
    "domain",
    "country",
    "sector",
    "size",
    "source",
    "signal",
    "score",
    "tier",
    "stage",
    "next_action",
    "linkedin_url",
)


def export_csv(store: LeadsStore, *, tier: str = "", stage: str = "") -> str:
    """CSV for any CRM or spreadsheet. Archived rows and opt-outs are left out."""
    import csv
    import io

    wanted_tier = str(tier or "").strip().upper()
    wanted_stage = str(stage or "").strip().lower()
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for row in store.load_leads():
        if not _is_live(row) or store.is_opted_out(row):
            continue
        if wanted_tier and str(row.get("tier") or "").upper() != wanted_tier:
            continue
        if wanted_stage and str(row.get("stage") or "").lower() != wanted_stage:
            continue
        writer.writerow(
            [str(row.get(key) if row.get(key) is not None else "") for key in EXPORT_COLUMNS]
        )
    return buffer.getvalue()


def lookalike(store: LeadsStore, lead_id: str) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=0) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        return _lookalike(store, lead_id)


def _lookalike(store: LeadsStore, lead_id: str) -> dict[str, Any]:
    seed = _require_live(store, lead_id)
    profile = dict(store.load_profile())
    if seed.get("sector"):
        profile["sector"] = seed["sector"]
    country = str(seed.get("country") or "").upper()[:2]
    if country:
        profile["countries"] = [country]
    profile["icp_name"] = f"Lookalike {seed.get('company') or ''}".strip()
    # A lookalike pass narrows on the seed's city when we know it.
    extra = seed.get("extra") if isinstance(seed.get("extra"), dict) else {}
    city = str(extra.get("city") or seed.get("city") or "").strip()
    if city:
        profile["cities"] = [city]
    limit = min(25, int(profile.get("count") or 20))
    secrets = store.load_secrets()
    found = hunt_companies(profile, secrets, limit, cursor=store.load_cursor())
    seed_key = lead_key(seed)
    found = [row for row in found if lead_key(row) != seed_key]
    if not found:
        raise LeadsError(
            "no lookalikes yet. Enrich the seed (sector / country) or add a BYOK key.",
            status=422,
        )
    enriched = [enrich_row(row, profile, secrets) for row in found]
    rows, added = store.upsert_leads(enriched)
    store.append_journal({"kind": "lookalike", "id": lead_id, "added": added, "count": len(found)})
    payload = snapshot(store)
    payload["lookalike"] = {"seed": seed.get("id"), "found": len(found), "added": added, "kept": len(rows)}
    return payload


def set_stage(store: LeadsStore, lead_id: str, stage: str) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        _require_live(store, lead_id)
        store.patch_lead(lead_id, {"stage": stage})
        return snapshot(store)


def delete_lead(store: LeadsStore, lead_id: str) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        wanted = (lead_id or "").strip()
        if not wanted:
            raise LeadsError("id is required")
        row = store.get_lead(wanted)
        store.delete_lead(wanted)
        store.append_journal(
            {"kind": "delete", "id": wanted, "company": str(row.get("company") or "")}
        )
        payload = snapshot(store)
        payload["deleted"] = {"id": wanted}
        return payload


def watch(store: LeadsStore) -> dict[str, Any]:
    from navin.leads.watch import run_watch

    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        result = run_watch(store, already_locked=True)
        payload = snapshot(store)
    payload["watch"] = result
    return payload


def format_loop_brief(loop: dict[str, Any] | None) -> str:
    """One line the agent, Studio and CLI all show. No cron, no surprise."""
    row = loop if isinstance(loop, dict) else {}
    from navin.loop_schedule import describe_schedule, format_due

    raw = row.get("schedule") if isinstance(row.get("schedule"), dict) else None
    when = describe_schedule(raw)
    try:
        due_ts = float(row.get("next_due") or 0)
    except (TypeError, ValueError):
        due_ts = 0.0
    next_slot = format_due(due_ts, raw) if due_ts > 0 and row.get("enabled") else "not armed"
    try:
        cycle = int(row.get("cycle") or 0)
    except (TypeError, ValueError):
        cycle = 0
    last = str(row.get("last_result") or "no cycle yet").strip()
    state = "ON" if row.get("enabled") else "PAUSED"
    phase = str(row.get("phase") or "idle")
    return (
        f"Loop: {state} · {phase} · {when} · next {next_slot} · cycle {cycle} · {last}"
    )


def format_agent_status(payload: dict[str, Any]) -> str:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    kpis = payload.get("kpis") if isinstance(payload.get("kpis"), dict) else {}
    rows = payload.get("leads") if isinstance(payload.get("leads"), list) else []
    loop = payload.get("loop") if isinstance(payload.get("loop"), dict) else {}
    lines = [
        "Navin Leads (Studio #/leads). Local book is the source of truth.",
        f"ICP: {profile.get('icp_name') or '-'} · {profile.get('sector') or '-'} · "
        f"{','.join(profile.get('countries') or []) or 'FR'}",
        str(kpis.get("headline") or ""),
        format_loop_brief(loop),
        "Autonomy: leads action=start / stop / schedule / tick "
        "(same store as Studio #/leads, Tauri, navin leads, python -m navin.leads.desk_cli). "
        "If the loop is ON, do not hunt again and do not create a chat cron. "
        "Heartbeat is leads action=watch only (alerts, never send, never hunt).",
        f"Execution: {profile.get('execution_mode') or 'approval'}"
        + (
            f" (loop sends due steps, cap {profile.get('daily_send_cap') or 0}/day)"
            if profile.get("execution_mode") == "autonomous"
            else " (loop drafts due steps, a human sends)"
        )
        + f" · sources {','.join(profile.get('sources') or []) or '-'}",
        "Manual: hunt, enrich, rescore, sequence, draft, sequences, optout, lookalike, delete, outreach "
        "channel=email|whatsapp|telegram|teams. Never scrape LinkedIn people pages.",
    ]
    channels = profile.get("channels") if isinstance(profile.get("channels"), dict) else {}
    on = [name for name in ("email", "whatsapp", "telegram", "teams") if channels.get(name)]
    if on:
        dest = [f"{name}:{channels.get(f'{name}_to') or 'on'}" for name in on]
        lines.append("Alert channels: " + ", ".join(dest))
    live_rows = [row for row in rows if isinstance(row, dict) and _is_live(row)]
    for row in live_rows[:12]:
        bits = [
            str(row.get("company") or ""),
            str(row.get("person") or ""),
            str(row.get("role") or ""),
            f"score {row.get('score') or 0}",
            str(row.get("tier") or ""),
            str(row.get("next_action") or ""),
            str(row.get("stage") or "new"),
        ]
        lines.append(" - " + " · ".join(part for part in bits if part))
    if len(live_rows) > 12:
        lines.append(f" - ... +{len(live_rows) - 12} more")
    return "\n".join(line for line in lines if line)


def _crm_project(raw: str | Path | None) -> Path:
    text = str(raw or "").strip()
    if text:
        root = Path(text).expanduser()
        if not root.is_dir():
            raise LeadsError("project root not found", status=404)
        if root.name.lower() == "navinprojects":
            raise LeadsError(
                "NavinProjects is a folder of projects, not a CRM. Open a real project.",
                status=400,
            )
        return root.resolve()
    try:
        from navin.tenders.crm_sync import project_root

        return project_root()
    except Exception as exc:
        raise LeadsError(str(exc) or "open a project to push into the CRM", status=400) from exc


def push_crm(store: LeadsStore, lead_id: str, project: str | Path | None = None) -> dict[str, Any]:
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        return _push_crm(store, lead_id, project)


def _push_crm(store: LeadsStore, lead_id: str, project: str | Path | None = None) -> dict[str, Any]:
    row = _require_live(store, lead_id)
    try:
        from navin.crm.store import CrmError, create_record
    except Exception as exc:
        raise LeadsError(f"CRM is not available: {exc}", status=400) from exc
    root = _crm_project(project)
    try:
        # Desk writes stay local to the chosen project folder. Do not send
        # actor=leads-desk: that handle is not a CRM member once a human owner exists.
        created = create_record(
            root,
            "leads",
            {
                "name": str(row.get("person") or row.get("company") or "Lead")[:120],
                "company": str(row.get("company") or "")[:120],
                "email": str(row.get("email") or "")[:120],
                "phone": str(row.get("phone") or "")[:40],
                "country": str(row.get("country") or "")[:8],
                "source": "leads-desk",
                "score": int(row.get("score") or 0),
                "status": "qualifie" if int(row.get("score") or 0) >= 80 else "nouveau",
            },
            actor="",
        )
    except CrmError as exc:
        raise LeadsError(str(exc) or "CRM write failed", status=getattr(exc, "status", 400) or 400) from exc
    store.patch_lead(lead_id, {"crm_lead_id": created.get("id")})
    payload = snapshot(store)
    payload["crm"] = {"id": created.get("id"), "name": created.get("name")}
    return payload


def _default_outreach_dest(row: dict[str, Any], channel: str, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    if channel == "email":
        return str(row.get("email") or "").strip()
    if channel == "whatsapp":
        return str(row.get("phone") or "").strip()
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    return str(extra.get(f"{channel}_to") or row.get(channel) or "").strip()


def outreach(
    store: LeadsStore,
    lead_id: str,
    *,
    channel: str,
    to: str = "",
    subject: str = "",
    body: str = "",
    send: bool = False,
) -> dict[str, Any]:
    """Draft or send on Email / WhatsApp / Telegram / Teams. Heartbeat must not call this."""
    from navin.leads.notify import CHANNEL_NAMES

    kind = (channel or "email").strip().lower()
    if kind == "msteams":
        kind = "teams"
    if kind not in CHANNEL_NAMES:
        raise LeadsError("channel must be email, whatsapp, telegram or teams", status=400)
    with leads_desk_lock(store, wait_s=8) as owned:
        if not owned:
            raise LeadsError("desk is busy. Wait for the current hunt or watch to finish.", status=409)
        return _outreach(store, lead_id, kind=kind, dest_in=to, title_in=subject, text_in=body, send=send)


def _outreach(
    store: LeadsStore,
    lead_id: str,
    *,
    kind: str,
    dest_in: str,
    title_in: str,
    text_in: str,
    send: bool,
) -> dict[str, Any]:
    from navin.leads.notify import channel_readiness, send_channel

    row = _require_live(store, lead_id)
    dest = _default_outreach_dest(row, kind, dest_in)
    title = (title_in or "").strip() or f"{row.get('company') or 'Lead'}"
    text = (text_in or "").strip()
    if not text:
        who = str(row.get("person") or row.get("company") or "there")
        text = f"Hello {who},\n\nI would like to connect about {row.get('company') or 'your company'}.\n"
    status = channel_readiness().get(kind) or {}
    sent = False
    if send:
        if not dest:
            raise LeadsError(
                f"no destination for {kind}. Set the lead email/phone or pass to=.",
                status=422,
            )
        sent = send_channel(kind, dest, subject=title, body=text)
        if not sent:
            hint = str(status.get("hint") or "Settings > Channels")
            raise LeadsError(
                f"{kind} did not send. Connect the channel first. {hint}",
                status=409,
            )
        patch: dict[str, Any] = {"stage": "contacted"}
        if isinstance(row.get("sequence"), dict) and (row.get("sequence") or {}).get("steps"):
            next_row = mark_step_sent(row, channel=kind)
            patch["sequence"] = next_row.get("sequence")
        store.patch_lead(lead_id, patch)
        store.append_journal({"kind": "outreach", "id": lead_id, "channel": kind, "to": dest})
    payload = snapshot(store)
    payload["outreach"] = {
        "sent": sent,
        "prepared": not send,
        "channel": kind,
        "to": dest,
        "subject": title,
        "body": text,
        "status": status,
    }
    return payload
