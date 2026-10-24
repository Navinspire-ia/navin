# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Campaigns book: contacts, lists, templates, campaigns, sends, replies.

Plain functions over :mod:`navin.campaigns.db`. JSON columns (tags, ids,
attachments) are decoded here so callers only see dicts and lists, shaped like
``webui/src/lib/campaigns-types.ts``.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from navin.campaigns.db import connect


class CampaignError(Exception):
    """User-facing campaigns failure with an HTTP status."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


CHANNELS = ("email", "whatsapp")
CAMPAIGN_STATUSES = ("draft", "scheduled", "sending", "paused", "sent", "failed", "cancelled")
EDITABLE_STATUSES = ("draft", "paused", "scheduled")
CONTACT_STATUSES = ("subscribed", "unsubscribed", "bounced")
REPLY_CLASSES = (
    "interested", "meeting", "question", "not_interested", "unsubscribe",
    "out_of_office", "bounce", "other",
)
REPLY_STATUSES = ("new", "drafted", "answered", "handed_off", "ignored")
CONTACT_FIELDS = ("email", "phone", "first_name", "last_name", "company", "title", "city", "country")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
_PHONE = re.compile(r"^\+?[0-9][0-9 ().-]{5,24}$")

DEFAULT_SETTINGS: dict[str, Any] = {
    "from_name": "",
    "reply_to": "",
    "throttle_per_minute": 30,
    "daily_cap": 500,
    "company_address": "",
    "footer_html": "",
    "unsubscribe_text": "Unsubscribe",
    "tracking_enabled": False,
    "tracking_base_url": "",
    "auto_reply_mode": "draft",
    "loop_enabled": True,
    # Business-hours sending, like a sales sequence tool (ISO weekdays, 1 = Monday).
    "send_window": {"enabled": False, "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00",
                    "timezone": "Europe/Paris"},
    # Skip addresses whose domain cannot receive mail (DNS MX check) before sending.
    "verify_domains": True,
    "whatsapp": {"phone_number_id": "", "waba_id": "", "token": "", "app_secret": "",
                 "verify_token": "", "webhook_enabled": False, "webhook_port": 18795},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _loads(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _clean(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def normalize_phone(value: str) -> str:
    raw = _clean(value, 40)
    if not raw:
        return ""
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    return digits


# -- settings ---------------------------------------------------------------


def get_settings(*, with_secrets: bool = False) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='settings'").fetchone()
    stored = _loads(row["value"] if row else "", {})
    settings = {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}
    settings["whatsapp"] = {**DEFAULT_SETTINGS["whatsapp"], **(stored.get("whatsapp") or {})}
    if not with_secrets:
        token = settings["whatsapp"].get("token") or ""
        secret = settings["whatsapp"].get("app_secret") or ""
        settings["whatsapp"] = {
            **settings["whatsapp"],
            "token": "",
            "app_secret": "",
            "token_hint": f"...{token[-4:]}" if len(token) > 8 else ("set" if token else ""),
            "app_secret_hint": f"...{secret[-4:]}" if len(secret) > 8 else ("set" if secret else ""),
            "configured": bool(token and settings["whatsapp"].get("phone_number_id")),
            "webhook_path": "/whatsapp/webhook",
        }
    return settings


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_settings(with_secrets=True)
    for key in ("from_name", "reply_to", "company_address", "unsubscribe_text", "tracking_base_url"):
        if key in patch:
            current[key] = _clean(patch[key], 400)
    if "footer_html" in patch:
        current["footer_html"] = str(patch["footer_html"] or "")[:8000]
    if current["reply_to"] and not _EMAIL.match(current["reply_to"]):
        raise CampaignError("Reply-to is not a valid email address")
    if current["tracking_base_url"] and not re.match(r"^https?://", current["tracking_base_url"]):
        raise CampaignError("The tracking URL must start with http:// or https://")
    for key, low, high in (("throttle_per_minute", 1, 600), ("daily_cap", 1, 100_000)):
        if key in patch:
            try:
                current[key] = max(low, min(high, int(patch[key])))
            except (TypeError, ValueError) as exc:
                raise CampaignError(f"{key} must be a number") from exc
    for key in ("tracking_enabled", "loop_enabled", "verify_domains"):
        if key in patch:
            current[key] = bool(patch[key])
    window = patch.get("send_window")
    if isinstance(window, dict):
        merged = {**current.get("send_window", DEFAULT_SETTINGS["send_window"]), **window}
        days = sorted({int(d) for d in merged.get("days") or [] if str(d).isdigit() and 1 <= int(d) <= 7})
        for key in ("start", "end"):
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(merged.get(key) or "")):
                raise CampaignError("Sending hours use HH:MM")
        if merged["start"] >= merged["end"]:
            raise CampaignError("The sending window must end after it starts")
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(str(merged.get("timezone") or "UTC"))
        except Exception as exc:
            raise CampaignError("Unknown time zone") from exc
        current["send_window"] = {"enabled": bool(merged.get("enabled")), "days": days or [1, 2, 3, 4, 5],
                                  "start": merged["start"], "end": merged["end"],
                                  "timezone": str(merged.get("timezone") or "UTC")}
    if "auto_reply_mode" in patch:
        if patch["auto_reply_mode"] not in ("off", "draft", "send"):
            raise CampaignError("auto_reply_mode is off, draft or send")
        current["auto_reply_mode"] = patch["auto_reply_mode"]
    wa = patch.get("whatsapp")
    if isinstance(wa, dict):
        for key in ("phone_number_id", "waba_id"):
            if key in wa:
                current["whatsapp"][key] = re.sub(r"\D", "", str(wa[key] or ""))[:40]
        if wa.get("token"):
            current["whatsapp"]["token"] = str(wa["token"]).strip()[:600]
        if wa.get("clear_token"):
            current["whatsapp"]["token"] = ""
        if wa.get("app_secret"):
            current["whatsapp"]["app_secret"] = str(wa["app_secret"]).strip()[:200]
        if "verify_token" in wa:
            current["whatsapp"]["verify_token"] = str(wa["verify_token"] or "").strip()[:200]
        if "webhook_enabled" in wa:
            current["whatsapp"]["webhook_enabled"] = bool(wa["webhook_enabled"])
        if "webhook_port" in wa:
            try:
                port = max(1024, min(65535, int(wa["webhook_port"])))
            except (TypeError, ValueError) as exc:
                raise CampaignError("The webhook port must be a number") from exc
            if port in _reserved_ports():
                raise CampaignError("This reception setting is already used by Navin itself; pick another one")
            current["whatsapp"]["webhook_port"] = port
        if current["whatsapp"]["webhook_enabled"] and not current["whatsapp"].get("verify_token"):
            current["whatsapp"]["verify_token"] = secrets.token_urlsafe(18)
    if not current.get("tracking_secret"):
        current["tracking_secret"] = secrets.token_hex(16)
    _write_settings(current)
    return get_settings()


def _reserved_ports() -> set[int]:
    """Ports Navin's own gateway and API already listen on."""
    try:
        from navin.config.loader import load_config

        config = load_config()
        return {int(config.gateway.port), int(getattr(config.api, "port", 0) or 0)} - {0}
    except Exception:  # noqa: BLE001 - no config: nothing reserved to check
        return set()


def _write_settings(value: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('settings', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (json.dumps(value, ensure_ascii=False),),
        )


def tracking_secret() -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='settings'").fetchone()
    stored = _loads(row["value"] if row else "", {})
    secret = stored.get("tracking_secret")
    if not secret:
        stored["tracking_secret"] = secret = secrets.token_hex(16)
        _write_settings({**DEFAULT_SETTINGS, **stored})
    return str(secret)


def get_state(key: str, default: Any = None) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (f"state:{key}",)).fetchone()
    return _loads(row["value"] if row else "", default)


def set_state(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (f"state:{key}", json.dumps(value, ensure_ascii=False)),
        )


# -- contacts ---------------------------------------------------------------


def _contact(row: Any, list_ids: list[str] | None = None) -> dict[str, Any]:
    data = dict(row)
    data["tags"] = _loads(data.get("tags"), [])
    data["attrs"] = _loads(data.get("attrs"), {})
    data["list_ids"] = list_ids or []
    return data


def _list_ids_for(conn: Any, ids: Iterable[str]) -> dict[str, list[str]]:
    ids = list(ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    out: dict[str, list[str]] = {}
    for row in conn.execute(f"SELECT contact_id, list_id FROM list_members WHERE contact_id IN ({marks})", ids):
        out.setdefault(row["contact_id"], []).append(row["list_id"])
    return out


def list_contacts(
    *, q: str = "", list_id: str = "", status: str = "", offset: int = 0, limit: int = 50,
) -> dict[str, Any]:
    where, args = [], []
    if list_id:
        where.append("id IN (SELECT contact_id FROM list_members WHERE list_id=?)")
        args.append(list_id)
    if status in CONTACT_STATUSES:
        where.append("status=?")
        args.append(status)
    if q.strip():
        like = f"%{q.strip().lower()}%"
        where.append(
            "(lower(email) LIKE ? OR lower(first_name||' '||last_name) LIKE ? OR lower(company) LIKE ? "
            "OR phone LIKE ? OR lower(tags) LIKE ?)"
        )
        args += [like, like, like, like, like]
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(500, int(limit or 50)))
    with connect() as conn:
        total = conn.execute(f"SELECT count(*) FROM contacts {clause}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM contacts {clause} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            [*args, limit, max(0, int(offset or 0))],
        ).fetchall()
        lists = _list_ids_for(conn, [r["id"] for r in rows])
    return {"items": [_contact(r, lists.get(r["id"])) for r in rows], "total": total}


def get_contact(contact_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        if row is None:
            raise CampaignError("Contact not found", status=404)
        return _contact(row, _list_ids_for(conn, [contact_id]).get(contact_id))


def find_contact(email: str = "", phone: str = "") -> dict[str, Any] | None:
    with connect() as conn:
        row = None
        if email:
            row = conn.execute("SELECT * FROM contacts WHERE lower(email)=lower(?)", (email,)).fetchone()
        if row is None and phone:
            row = conn.execute("SELECT * FROM contacts WHERE phone=?", (normalize_phone(phone),)).fetchone()
    return _contact(row) if row else None


def _normalize_contact(body: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(current or {})
    for field in CONTACT_FIELDS:
        if field in body:
            data[field] = _clean(body[field], 254 if field == "email" else 160)
    data["email"] = (data.get("email") or "").lower()
    data["phone"] = normalize_phone(data.get("phone") or "")
    if data["email"] and not _EMAIL.match(data["email"]):
        raise CampaignError(f"{data['email']} is not a valid email address")
    if data["phone"] and not _PHONE.match(data["phone"]):
        raise CampaignError(f"{data['phone']} is not a valid phone number")
    if not data["email"] and not data["phone"]:
        raise CampaignError("A contact needs an email or a phone number")
    if "tags" in body:
        tags = body["tags"]
        if isinstance(tags, str):
            tags = [t for t in re.split(r"[,;]", tags)]
        data["tags"] = sorted({_clean(t, 40) for t in tags or [] if _clean(t, 40)})
    if "attrs" in body and isinstance(body["attrs"], dict):
        data["attrs"] = {_clean(k, 60): _clean(v, 500) for k, v in body["attrs"].items() if _clean(k, 60)}
    if "status" in body and body["status"] in CONTACT_STATUSES:
        data["status"] = body["status"]
    if "source" in body:
        data["source"] = _clean(body["source"], 80)
    return data


def save_contact(body: dict[str, Any]) -> dict[str, Any]:
    contact_id = _clean(body.get("id"), 40)
    current = get_contact(contact_id) if contact_id else None
    if current is None:
        existing = find_contact(_clean(body.get("email"), 254), _clean(body.get("phone"), 40))
        if existing:
            current, contact_id = existing, existing["id"]
    data = _normalize_contact(body, current)
    stamp = now_iso()
    with connect() as conn:
        if current:
            conn.execute(
                "UPDATE contacts SET email=?, phone=?, first_name=?, last_name=?, company=?, title=?, city=?, "
                "country=?, tags=?, attrs=?, status=?, source=?, updated_at=? WHERE id=?",
                (*(data.get(f, "") for f in CONTACT_FIELDS), json.dumps(data.get("tags", [])),
                 json.dumps(data.get("attrs", {}), ensure_ascii=False), data.get("status", "subscribed"),
                 data.get("source", ""), stamp, contact_id),
            )
        else:
            contact_id = new_id("ct")
            conn.execute(
                "INSERT INTO contacts(id, email, phone, first_name, last_name, company, title, city, country, "
                "tags, attrs, status, source, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (contact_id, *(data.get(f, "") for f in CONTACT_FIELDS), json.dumps(data.get("tags", [])),
                 json.dumps(data.get("attrs", {}), ensure_ascii=False), data.get("status", "subscribed"),
                 data.get("source", "manual"), stamp, stamp),
            )
        for list_id in body.get("list_ids") or []:
            conn.execute("INSERT OR IGNORE INTO list_members(list_id, contact_id) "
                         "SELECT id, ? FROM lists WHERE id=?", (contact_id, list_id))
    return get_contact(contact_id)


def delete_contacts(ids: list[str]) -> int:
    ids = [i for i in ids if i]
    if not ids:
        return 0
    with connect() as conn:
        marks = ",".join("?" * len(ids))
        return conn.execute(f"DELETE FROM contacts WHERE id IN ({marks})", ids).rowcount


def set_contact_status(contact_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE contacts SET status=?, updated_at=? WHERE id=?", (status, now_iso(), contact_id))


# -- lists ------------------------------------------------------------------


def list_lists() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT l.*, (SELECT count(*) FROM list_members m WHERE m.list_id=l.id) AS count "
            "FROM lists l ORDER BY l.name COLLATE NOCASE"
        ).fetchall()
    return [dict(r) for r in rows]


def get_list(list_id: str) -> dict[str, Any]:
    for row in list_lists():
        if row["id"] == list_id:
            return row
    raise CampaignError("List not found", status=404)


def save_list(body: dict[str, Any]) -> dict[str, Any]:
    name = _clean(body.get("name"), 120)
    if not name:
        raise CampaignError("A list needs a name")
    list_id = _clean(body.get("id"), 40)
    with connect() as conn:
        if list_id and conn.execute("SELECT 1 FROM lists WHERE id=?", (list_id,)).fetchone():
            conn.execute("UPDATE lists SET name=?, description=? WHERE id=?",
                         (name, _clean(body.get("description"), 500), list_id))
        else:
            list_id = new_id("ls")
            conn.execute("INSERT INTO lists(id, name, description, created_at) VALUES(?,?,?,?)",
                         (list_id, name, _clean(body.get("description"), 500), now_iso()))
    return get_list(list_id)


def delete_list(list_id: str) -> int:
    with connect() as conn:
        return conn.execute("DELETE FROM lists WHERE id=?", (list_id,)).rowcount


def add_to_list(list_id: str, contact_ids: list[str]) -> dict[str, Any]:
    get_list(list_id)
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO list_members(list_id, contact_id) SELECT ?, id FROM contacts WHERE id=?",
            [(list_id, cid) for cid in contact_ids],
        )
    return get_list(list_id)


def remove_from_list(list_id: str, contact_ids: list[str]) -> dict[str, Any]:
    with connect() as conn:
        conn.executemany("DELETE FROM list_members WHERE list_id=? AND contact_id=?",
                         [(list_id, cid) for cid in contact_ids])
    return get_list(list_id)


# -- templates --------------------------------------------------------------


def _template(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["wa_params"] = _loads(data.get("wa_params"), [])
    return data


def list_templates() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM templates ORDER BY updated_at DESC").fetchall()
    return [_template(r) for r in rows]


def get_template(template_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
    if row is None:
        raise CampaignError("Template not found", status=404)
    return _template(row)


def save_template(body: dict[str, Any]) -> dict[str, Any]:
    name = _clean(body.get("name"), 120)
    if not name:
        raise CampaignError("A template needs a name")
    channel = body.get("channel") if body.get("channel") in CHANNELS else "email"
    template_id = _clean(body.get("id"), 40)
    values = (
        name, channel, _clean(body.get("subject"), 300), _clean(body.get("preheader"), 300),
        str(body.get("html") or "")[:400_000], str(body.get("text") or "")[:4096],
        _clean(body.get("wa_template"), 120), _clean(body.get("wa_language"), 20),
        json.dumps([str(p)[:500] for p in body.get("wa_params") or []][:20], ensure_ascii=False),
    )
    stamp = now_iso()
    with connect() as conn:
        if template_id and conn.execute("SELECT 1 FROM templates WHERE id=?", (template_id,)).fetchone():
            conn.execute(
                "UPDATE templates SET name=?, channel=?, subject=?, preheader=?, html=?, text=?, wa_template=?, "
                "wa_language=?, wa_params=?, updated_at=? WHERE id=?", (*values, stamp, template_id),
            )
        else:
            template_id = new_id("tp")
            conn.execute(
                "INSERT INTO templates(id, name, channel, subject, preheader, html, text, wa_template, "
                "wa_language, wa_params, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (template_id, *values, stamp, stamp),
            )
    return get_template(template_id)


def delete_template(template_id: str) -> int:
    with connect() as conn:
        return conn.execute("DELETE FROM templates WHERE id=?", (template_id,)).rowcount


# -- campaigns --------------------------------------------------------------

_JSON_CAMPAIGN = ("wa_params", "list_ids", "contact_ids", "attachments", "steps", "subject_variants")
STEP_CONDITIONS = ("no_reply", "no_open", "always")
MAX_STEPS = 6


def _campaign(row: Any, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(row)
    for key in _JSON_CAMPAIGN:
        data[key] = _loads(data.get(key), [])
    data["track_opens"] = bool(data["track_opens"])
    data["track_clicks"] = bool(data["track_clicks"])
    data["stats"] = stats if stats is not None else campaign_stats(data["id"])
    data["stats"]["steps"] = step_stats(data["id"]) if data.get("steps") else []
    data["stats"]["variants"] = variant_stats(data) if data.get("subject_variants") else []
    return data


def assign_variants(campaign: dict[str, Any], contact_ids: list[str]) -> dict[str, str]:
    """Balanced A/B(/C) split of the contacts a campaign sends to.

    Contacts are dealt round-robin in a stable pseudo-random order (a hash of
    campaign and contact): every subject gets an equal share, within one
    contact, even on a small list, where hashing each contact on its own can
    send a single subject to everyone. The same audience always gets the same
    split, and the list order (import date, name) does not bias a variant.
    """
    unique = list(dict.fromkeys(contact_ids))
    count = 1 + len(campaign.get("subject_variants") or [])
    if count == 1:
        return dict.fromkeys(unique, "")
    order = sorted(unique, key=lambda cid: uuid.uuid5(uuid.NAMESPACE_URL, f"{campaign['id']}:{cid}").hex)
    return {cid: "ABC"[rank % count] for rank, cid in enumerate(order)}


def subject_for(campaign: dict[str, Any], variant: str) -> str:
    variants = [campaign.get("subject") or "", *(campaign.get("subject_variants") or [])]
    index = "ABC".find(variant)
    return variants[index] if 0 <= index < len(variants) else variants[0]


def variant_stats(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT variant, sum(status='sent') AS sent, sum(opened_at!='') AS opened, "
            "sum(clicked_at!='') AS clicked, sum(replied_at!='') AS replied FROM sends "
            "WHERE campaign_id=? GROUP BY variant", (campaign["id"],),
        ).fetchall()
    out = []
    for row in rows:
        if not row["variant"]:
            continue
        sent = int(row["sent"] or 0)
        out.append({"variant": row["variant"], "subject": subject_for(campaign, row["variant"]), "sent": sent,
                    "opened": int(row["opened"] or 0), "clicked": int(row["clicked"] or 0),
                    "replied": int(row["replied"] or 0),
                    "open_rate": round((row["opened"] or 0) / sent, 4) if sent else 0.0,
                    "reply_rate": round((row["replied"] or 0) / sent, 4) if sent else 0.0})
    return sorted(out, key=lambda r: r["variant"])


def step_stats(campaign_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT step, sum(status='sent') AS sent, sum(status='failed') AS failed, "
            "sum(status='skipped') AS skipped, sum(opened_at!='') AS opened FROM followups "
            "WHERE campaign_id=? GROUP BY step ORDER BY step", (campaign_id,),
        ).fetchall()
    return [{"step": r["step"], "sent": int(r["sent"] or 0), "failed": int(r["failed"] or 0),
             "skipped": int(r["skipped"] or 0), "opened": int(r["opened"] or 0)} for r in rows]


def due_followups(campaign: dict[str, Any], step_index: int, now_iso_value: str) -> list[dict[str, Any]]:
    """Sends ready for follow-up *step_index* (1-based): delay passed, condition met, not done yet."""
    step = campaign["steps"][step_index - 1]
    with connect() as conn:
        if step_index == 1:
            base = ("SELECT s.id AS send_id, s.contact_id, s.address, s.message_id, s.variant, s.sent_at AS since, "
                    "s.opened_at, s.replied_at, s.bounced, c.status AS contact_status FROM sends s "
                    "JOIN contacts c ON c.id=s.contact_id WHERE s.campaign_id=? AND s.status='sent'")
        else:
            base = ("SELECT s.id AS send_id, s.contact_id, s.address, s.message_id, s.variant, f.sent_at AS since, "
                    "s.opened_at, s.replied_at, s.bounced, c.status AS contact_status FROM followups f "
                    "JOIN sends s ON s.id=f.send_id JOIN contacts c ON c.id=s.contact_id "
                    f"WHERE f.campaign_id=? AND f.step={step_index - 1} AND f.status='sent'")
        rows = conn.execute(
            base + f" AND NOT EXISTS (SELECT 1 FROM followups x WHERE x.send_id=s.id AND x.step={step_index})",
            (campaign["id"],),
        ).fetchall()
    out = []
    cutoff = datetime.fromisoformat(now_iso_value) - timedelta(days=int(step["delay_days"]))
    for row in rows:
        try:
            since = datetime.fromisoformat(row["since"])
        except (TypeError, ValueError):
            continue
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        if since > cutoff:
            continue
        out.append(dict(row))
    return out


def record_followup(campaign_id: str, row: dict[str, Any], step: int, *, status: str, error: str = "",
                    message_id: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO followups(id, campaign_id, send_id, contact_id, step, status, error, message_id, "
            "sent_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (new_id("fu"), campaign_id, row["send_id"], row["contact_id"], step, status, error[:300], message_id,
             now_iso() if status == "sent" else ""),
        )


def pending_sequences() -> list[dict[str, Any]]:
    """Campaigns whose follow-ups may still be due."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE status IN ('sending','sent') AND steps NOT IN ('', '[]')"
        ).fetchall()
    return [_campaign(r, _empty_stats()) for r in rows]


def list_campaigns(status: str = "") -> list[dict[str, Any]]:
    with connect() as conn:
        if status in CAMPAIGN_STATUSES:
            rows = conn.execute("SELECT * FROM campaigns WHERE status=? ORDER BY updated_at DESC", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM campaigns ORDER BY updated_at DESC").fetchall()
    stats = all_campaign_stats()
    return [_campaign(r, stats.get(r["id"]) or _empty_stats()) for r in rows]


def get_campaign(campaign_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if row is None:
        raise CampaignError("Campaign not found", status=404)
    return _campaign(row)


def normalize_campaign(body: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(current or {})
    for key, limit in (("name", 160), ("subject", 300), ("preheader", 300), ("from_name", 120),
                       ("reply_to", 254), ("wa_template", 120), ("wa_language", 20), ("template_id", 40)):
        if key in body:
            data[key] = _clean(body[key], limit)
    if "channel" in body:
        data["channel"] = body["channel"] if body["channel"] in CHANNELS else "email"
    data.setdefault("channel", "email")
    if "html" in body:
        data["html"] = str(body["html"] or "")[:400_000]
    if "text" in body:
        data["text"] = str(body["text"] or "")[:4096]
    if "wa_params" in body:
        data["wa_params"] = [str(p)[:500] for p in body["wa_params"] or []][:20]
    for key in ("list_ids", "contact_ids"):
        if key in body:
            data[key] = [str(v)[:40] for v in body[key] or [] if v][:5000]
    if "attachments" in body:
        data["attachments"] = [
            {"id": str(a.get("id"))[:60], "name": _clean(a.get("name"), 200),
             "size": int(a.get("size") or 0), "mime": _clean(a.get("mime"), 120)}
            for a in body["attachments"] or [] if isinstance(a, dict) and a.get("id")
        ][:10]
    if "steps" in body:
        steps = []
        for index, step in enumerate(body["steps"] or []):
            if not isinstance(step, dict):
                continue
            try:
                delay = max(1, min(60, int(step.get("delay_days") or 3)))
            except (TypeError, ValueError) as exc:
                raise CampaignError("Follow-up delays are numbers of days") from exc
            html = str(step.get("html") or "")[:200_000]
            if not html.strip():
                raise CampaignError(f"Follow-up {index + 1} is empty")
            steps.append({"id": _clean(step.get("id"), 40) or new_id("st"), "delay_days": delay,
                          "condition": step.get("condition") if step.get("condition") in STEP_CONDITIONS
                          else "no_reply",
                          "subject": _clean(step.get("subject"), 300), "html": html})
        if len(steps) > MAX_STEPS:
            raise CampaignError(f"A sequence has at most {MAX_STEPS} follow-ups")
        data["steps"] = steps
    if "subject_variants" in body:
        data["subject_variants"] = [_clean(v, 300) for v in body["subject_variants"] or [] if _clean(v, 300)][:2]
    if "throttle_per_minute" in body:
        try:
            data["throttle_per_minute"] = max(1, min(600, int(body["throttle_per_minute"])))
        except (TypeError, ValueError) as exc:
            raise CampaignError("throttle_per_minute must be a number") from exc
    for key in ("track_opens", "track_clicks"):
        if key in body:
            data[key] = bool(body[key])
    if data.get("reply_to") and not _EMAIL.match(data["reply_to"]):
        raise CampaignError("Reply-to is not a valid email address")
    if not data.get("name"):
        data["name"] = "Untitled campaign"
    return data


def save_campaign(body: dict[str, Any]) -> dict[str, Any]:
    campaign_id = _clean(body.get("id"), 40)
    current = get_campaign(campaign_id) if campaign_id else None
    if current and current["status"] not in EDITABLE_STATUSES:
        raise CampaignError(f"A {current['status']} campaign cannot be edited; duplicate it instead", status=409)
    data = normalize_campaign(body, current)
    stamp = now_iso()
    cols = ("name", "channel", "subject", "preheader", "from_name", "reply_to", "html", "text",
            "wa_template", "wa_language", "wa_params", "template_id", "list_ids", "contact_ids",
            "attachments", "throttle_per_minute", "track_opens", "track_clicks", "steps", "subject_variants")
    values = []
    for col in cols:
        value = data.get(col, [] if col in _JSON_CAMPAIGN else ("" if col not in (
            "throttle_per_minute", "track_opens", "track_clicks") else None))
        if col in _JSON_CAMPAIGN:
            value = json.dumps(value or [], ensure_ascii=False)
        elif col == "throttle_per_minute":
            value = int(value or get_settings()["throttle_per_minute"])
        elif col in ("track_opens", "track_clicks"):
            value = 1 if (value is None or value) else 0
        values.append(value)
    with connect() as conn:
        if current:
            sets = ", ".join(f"{c}=?" for c in cols)
            conn.execute(f"UPDATE campaigns SET {sets}, updated_at=? WHERE id=?", (*values, stamp, campaign_id))
        else:
            campaign_id = new_id("cp")
            conn.execute(
                f"INSERT INTO campaigns(id, {', '.join(cols)}, status, created_at, updated_at) "
                f"VALUES(?, {', '.join('?' * len(cols))}, 'draft', ?, ?)",
                (campaign_id, *values, stamp, stamp),
            )
    return get_campaign(campaign_id)


def set_campaign(campaign_id: str, **fields: Any) -> dict[str, Any]:
    if not fields:
        return get_campaign(campaign_id)
    fields["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE campaigns SET {sets} WHERE id=?", (*fields.values(), campaign_id))
    return get_campaign(campaign_id)


def delete_campaign(campaign_id: str) -> int:
    campaign = get_campaign(campaign_id)
    if campaign["status"] == "sending":
        raise CampaignError("Pause or cancel the campaign before deleting it", status=409)
    with connect() as conn:
        return conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,)).rowcount


def duplicate_campaign(campaign_id: str) -> dict[str, Any]:
    source = get_campaign(campaign_id)
    copy = {k: v for k, v in source.items() if k not in {"id", "status", "stats", "created_at", "updated_at",
                                                        "launched_at", "finished_at", "last_error", "schedule_at"}}
    copy["name"] = f"{source['name']} (copy)"[:160]
    return save_campaign(copy)


def audience_contacts(campaign: dict[str, Any]) -> list[dict[str, Any]]:
    """Every contact the campaign targets: its lists plus its single contacts."""
    ids: dict[str, None] = {}
    with connect() as conn:
        for list_id in campaign.get("list_ids") or []:
            for row in conn.execute("SELECT contact_id FROM list_members WHERE list_id=?", (list_id,)):
                ids[row["contact_id"]] = None
        for cid in campaign.get("contact_ids") or []:
            ids[cid] = None
        if not ids:
            return []
        out: list[dict[str, Any]] = []
        keys = list(ids)
        for start in range(0, len(keys), 500):
            chunk = keys[start:start + 500]
            rows = conn.execute(f"SELECT * FROM contacts WHERE id IN ({','.join('?' * len(chunk))})", chunk)
            out += [_contact(r) for r in rows]
    return out


# -- sends ------------------------------------------------------------------


def _empty_stats() -> dict[str, Any]:
    return {"recipients": 0, "queued": 0, "sent": 0, "failed": 0, "skipped": 0, "opened": 0,
            "clicked": 0, "replied": 0, "bounced": 0, "unsubscribed": 0,
            "open_rate": 0.0, "click_rate": 0.0, "reply_rate": 0.0}


def _stats_from(row: Any) -> dict[str, Any]:
    stats = _empty_stats()
    for key in ("recipients", "queued", "sent", "failed", "skipped", "opened", "clicked", "replied",
                "bounced", "unsubscribed"):
        stats[key] = int(row[key] or 0)
    delivered = max(1, stats["sent"])
    if stats["sent"]:
        stats["open_rate"] = round(stats["opened"] / delivered, 4)
        stats["click_rate"] = round(stats["clicked"] / delivered, 4)
        stats["reply_rate"] = round(stats["replied"] / delivered, 4)
    return stats


_STATS_SQL = """
SELECT s.campaign_id AS cid, count(*) AS recipients,
  sum(s.status='queued') AS queued, sum(s.status='sent') AS sent,
  sum(s.status='failed') AS failed, sum(s.status='skipped') AS skipped,
  sum(s.opened_at!='') AS opened, sum(s.clicked_at!='') AS clicked,
  sum(s.replied_at!='') AS replied, sum(s.bounced) AS bounced,
  sum(c.status='unsubscribed') AS unsubscribed
FROM sends s LEFT JOIN contacts c ON c.id=s.contact_id
"""


def campaign_stats(campaign_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(_STATS_SQL + " WHERE s.campaign_id=? GROUP BY s.campaign_id", (campaign_id,)).fetchone()
    return _stats_from(row) if row else _empty_stats()


def all_campaign_stats() -> dict[str, dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(_STATS_SQL + " GROUP BY s.campaign_id").fetchall()
    return {r["cid"]: _stats_from(r) for r in rows}


def queue_sends(campaign_id: str, rows: list[tuple[str, ...]]) -> int:
    """Insert (contact_id, address, status, error[, variant]) rows once per contact."""
    with connect() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO sends(id, campaign_id, contact_id, address, status, error, variant) "
            "VALUES(?,?,?,?,?,?,?)",
            [(new_id("sd"), campaign_id, *row[:4], row[4] if len(row) > 4 else "") for row in rows],
        )
        return conn.total_changes - before


def next_queued(campaign_id: str, limit: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sends WHERE campaign_id=? AND status='queued' ORDER BY rowid LIMIT ?",
            (campaign_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_send(send_id: str, **fields: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE sends SET {sets} WHERE id=?", (*fields.values(), send_id))


def get_send(send_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sends WHERE id=?", (send_id,)).fetchone()
    return dict(row) if row else None


def list_sends(campaign_id: str, *, status: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
    args: list[Any] = [campaign_id]
    clause = "WHERE s.campaign_id=?"
    if status in ("queued", "sent", "failed", "skipped"):
        clause += " AND s.status=?"
        args.append(status)
    elif status in ("opened", "clicked", "replied"):
        clause += f" AND s.{status}_at!=''"
    elif status == "bounced":
        clause += " AND s.bounced=1"
    limit = max(1, min(500, int(limit or 50)))
    with connect() as conn:
        total = conn.execute(f"SELECT count(*) FROM sends s {clause}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT s.*, trim(coalesce(c.first_name,'')||' '||coalesce(c.last_name,'')) AS contact_name "
            f"FROM sends s LEFT JOIN contacts c ON c.id=s.contact_id {clause} ORDER BY s.rowid LIMIT ? OFFSET ?",
            [*args, limit, max(0, int(offset or 0))],
        ).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["bounced"] = bool(item["bounced"])
        item.pop("message_id", None)
        items.append(item)
    return {"items": items, "total": total}


def sent_today() -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM sends WHERE status='sent' AND sent_at>=?", (start,)).fetchone()[0]


def sent_last_minute(campaign_id: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(seconds=60)).replace(microsecond=0).isoformat()
    with connect() as conn:
        return conn.execute("SELECT count(*) FROM sends WHERE campaign_id=? AND status='sent' AND sent_at>=?",
                            (campaign_id, since)).fetchone()[0]


def find_send_for_reply(message_ids: list[str], from_addr: str) -> dict[str, Any] | None:
    """The send a reply answers: by Message-ID threading, else by address."""
    with connect() as conn:
        for mid in message_ids:
            row = conn.execute("SELECT * FROM sends WHERE message_id=? AND message_id!=''", (mid,)).fetchone()
            if row:
                return dict(row)
            # A reply to a follow-up threads on the follow-up's Message-ID.
            row = conn.execute(
                "SELECT s.* FROM followups f JOIN sends s ON s.id=f.send_id WHERE f.message_id=? AND f.message_id!=''",
                (mid,),
            ).fetchone()
            if row:
                return dict(row)
        if from_addr:
            row = conn.execute(
                "SELECT * FROM sends WHERE lower(address)=lower(?) AND status='sent' ORDER BY sent_at DESC LIMIT 1",
                (from_addr,),
            ).fetchone()
            if row:
                return dict(row)
    return None


def find_send_by_message_id(message_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sends WHERE message_id=? AND message_id!=''", (message_id,)).fetchone()
    return dict(row) if row else None


def find_whatsapp_send(phone: str) -> dict[str, Any] | None:
    """The latest WhatsApp send to *phone* (digits, any formatting)."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    with connect() as conn:
        rows = conn.execute(
            "SELECT s.* FROM sends s JOIN campaigns c ON c.id=s.campaign_id "
            "WHERE c.channel='whatsapp' AND s.status='sent' ORDER BY s.sent_at DESC LIMIT 2000"
        ).fetchall()
    for row in rows:
        if re.sub(r"\D", "", row["address"]) == digits:
            return dict(row)
    return None


def contact_thread(contact_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """What was sent to and received from a contact, oldest first (the agent's context)."""
    with connect() as conn:
        sends = conn.execute(
            "SELECT s.sent_at AS at, c.name AS campaign, c.channel, c.subject, c.html, c.text, c.wa_template, "
            "c.wa_params FROM sends s JOIN campaigns c ON c.id=s.campaign_id "
            "WHERE s.contact_id=? AND s.status='sent' ORDER BY s.sent_at DESC LIMIT ?", (contact_id, limit),
        ).fetchall()
        replies = conn.execute(
            "SELECT received_at, answered_at, body, draft_reply, status, channel, classification FROM replies "
            "WHERE contact_id=? ORDER BY received_at DESC LIMIT ?", (contact_id, limit),
        ).fetchall()
    from navin.campaigns.render import html_to_text

    events: list[dict[str, Any]] = []
    for row in sends:
        content = (html_to_text(row["html"] or "") if row["channel"] == "email"
                   else row["text"] or f"template {row['wa_template']} {row['wa_params']}")
        events.append({"at": row["at"], "who": "us", "kind": "campaign", "channel": row["channel"],
                       "campaign": row["campaign"], "subject": row["subject"], "content": content[:1500]})
    for row in replies:
        events.append({"at": row["received_at"], "who": "them", "kind": "reply", "channel": row["channel"],
                       "classification": row["classification"], "content": row["body"][:2000]})
        if row["status"] == "answered" and row["answered_at"]:
            events.append({"at": row["answered_at"], "who": "us", "kind": "answer", "channel": row["channel"],
                           "content": (row["draft_reply"] or "")[:2000]})
    return sorted(events, key=lambda e: e["at"] or "")[-limit:]


def record_event(kind: str, *, campaign_id: str = "", send_id: str = "", data: dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO events(campaign_id, send_id, kind, at, data) VALUES(?,?,?,?,?)",
                     (campaign_id, send_id, kind, now_iso(), json.dumps(data or {}, ensure_ascii=False)))


# -- replies ----------------------------------------------------------------


def add_reply(body: dict[str, Any]) -> dict[str, Any] | None:
    """Store an inbound reply once (by Message-ID); None when already known."""
    reply_id = new_id("rp")
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO replies(id, campaign_id, send_id, contact_id, from_addr, subject, body, message_id, "
                "received_at, classification, confidence, summary, status, channel) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (reply_id, body.get("campaign_id", ""), body.get("send_id", ""), body.get("contact_id", ""),
                 _clean(body.get("from_addr"), 254), _clean(body.get("subject"), 300),
                 str(body.get("body") or "")[:20_000], _clean(body.get("message_id"), 300),
                 body.get("received_at") or now_iso(), body.get("classification", "other"),
                 float(body.get("confidence") or 0), _clean(body.get("summary"), 600), "new",
                 body.get("channel") if body.get("channel") in CHANNELS else "email"),
            )
        except Exception as exc:  # sqlite3.IntegrityError: same Message-ID already stored
            if "UNIQUE" in str(exc):
                return None
            raise
    return get_reply(reply_id)


def _reply(row: Any) -> dict[str, Any]:
    return dict(row)


def get_reply(reply_id: str) -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT r.*, coalesce(cp.name,'') AS campaign_name, "
            "trim(coalesce(c.first_name,'')||' '||coalesce(c.last_name,'')) AS contact_name "
            "FROM replies r LEFT JOIN campaigns cp ON cp.id=r.campaign_id LEFT JOIN contacts c ON c.id=r.contact_id "
            "WHERE r.id=?", (reply_id,),
        ).fetchone()
    if row is None:
        raise CampaignError("Reply not found", status=404)
    return _reply(row)


def list_replies(
    *, status: str = "", classification: str = "", campaign_id: str = "", offset: int = 0, limit: int = 50,
) -> dict[str, Any]:
    where, args = [], []
    if status in REPLY_STATUSES:
        where.append("r.status=?")
        args.append(status)
    if classification in REPLY_CLASSES:
        where.append("r.classification=?")
        args.append(classification)
    if campaign_id:
        where.append("r.campaign_id=?")
        args.append(campaign_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(200, int(limit or 50)))
    with connect() as conn:
        total = conn.execute(f"SELECT count(*) FROM replies r {clause}", args).fetchone()[0]
        rows = conn.execute(
            "SELECT r.*, coalesce(cp.name,'') AS campaign_name, "
            "trim(coalesce(c.first_name,'')||' '||coalesce(c.last_name,'')) AS contact_name "
            f"FROM replies r LEFT JOIN campaigns cp ON cp.id=r.campaign_id LEFT JOIN contacts c ON c.id=r.contact_id "
            f"{clause} ORDER BY r.received_at DESC LIMIT ? OFFSET ?",
            [*args, limit, max(0, int(offset or 0))],
        ).fetchall()
    return {"items": [_reply(r) for r in rows], "total": total}


def update_reply(body: dict[str, Any]) -> dict[str, Any]:
    reply_id = _clean(body.get("id"), 40)
    get_reply(reply_id)
    fields: dict[str, Any] = {}
    if body.get("classification") in REPLY_CLASSES:
        fields["classification"] = body["classification"]
        fields["confidence"] = float(body.get("confidence", 1.0))
    if body.get("status") in REPLY_STATUSES:
        fields["status"] = body["status"]
    for key, limit in (("assigned_to", 80), ("summary", 600)):
        if key in body:
            fields[key] = _clean(body[key], limit)
    for key in ("handoff_brief", "draft_reply"):
        if key in body:
            fields[key] = str(body[key] or "")[:8000]
    if "draft_reply" in fields and fields["draft_reply"] and "status" not in fields:
        fields["status"] = "drafted"
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields)
        with connect() as conn:
            conn.execute(f"UPDATE replies SET {sets} WHERE id=?", (*fields.values(), reply_id))
    return get_reply(reply_id)


# -- dashboard --------------------------------------------------------------


def dashboard_totals() -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
    with connect() as conn:
        one = lambda sql, *a: conn.execute(sql, a).fetchone()[0] or 0  # noqa: E731
        sent = one("SELECT count(*) FROM sends WHERE status='sent' AND sent_at>=?", since)
        opened = one("SELECT count(*) FROM sends WHERE status='sent' AND sent_at>=? AND opened_at!=''", since)
        clicked = one("SELECT count(*) FROM sends WHERE status='sent' AND sent_at>=? AND clicked_at!=''", since)
        replied = one("SELECT count(*) FROM sends WHERE status='sent' AND sent_at>=? AND replied_at!=''", since)
        bounced = one("SELECT count(*) FROM sends WHERE sent_at>=? AND bounced=1", since)
        totals = {
            "contacts": one("SELECT count(*) FROM contacts"),
            "subscribed": one("SELECT count(*) FROM contacts WHERE status='subscribed'"),
            "lists": one("SELECT count(*) FROM lists"),
            "campaigns": one("SELECT count(*) FROM campaigns"),
            "sent_30d": sent,
            "open_rate": round(opened / sent, 4) if sent else 0.0,
            "click_rate": round(clicked / sent, 4) if sent else 0.0,
            "reply_rate": round(replied / sent, 4) if sent else 0.0,
            "bounce_rate": round(bounced / sent, 4) if sent else 0.0,
            "unsubscribed": one("SELECT count(*) FROM contacts WHERE status='unsubscribed'"),
            "replies_new": one("SELECT count(*) FROM replies WHERE status='new'"),
        }
        days: dict[str, dict[str, Any]] = {}
        today = datetime.now(timezone.utc).date()
        for offset in range(29, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            days[day] = {"date": day, "sent": 0, "opened": 0, "clicked": 0, "replied": 0}
        for col, key in (("sent_at", "sent"), ("opened_at", "opened"), ("clicked_at", "clicked"),
                         ("replied_at", "replied")):
            for row in conn.execute(
                f"SELECT substr({col},1,10) AS d, count(*) AS n FROM sends WHERE {col}>=? GROUP BY d", (since,),
            ):
                if row["d"] in days:
                    days[row["d"]][key] = row["n"]
        by_class = {r["classification"]: r["n"] for r in conn.execute(
            "SELECT classification, count(*) AS n FROM replies GROUP BY classification")}
        scheduled = one("SELECT count(*) FROM campaigns WHERE status='scheduled'")
        sending = one("SELECT count(*) FROM campaigns WHERE status='sending'")
    return {"totals": totals, "timeline": list(days.values()), "replies_by_class": by_class,
            "scheduled": scheduled, "sending": sending}
