# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Campaigns Studio HTTP actions (contract: webui/src/lib/campaigns-types.ts)."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

from navin.campaigns import (
    crm_source,
    dispatcher,
    importer,
    inbound,
    prospecting,
    render,
    sender,
    store,
)
from navin.campaigns.store import CampaignError


def _decode(data_b64: Any) -> bytes:
    raw = str(data_b64 or "")
    if "," in raw[:100] and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        return base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise CampaignError("The file could not be read") from exc


def _int(query: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(query.get(key) or default)
    except (TypeError, ValueError):
        return default


def dashboard() -> dict[str, Any]:
    totals = store.dashboard_totals()
    settings = store.get_settings()
    email = sender.email_ready()
    return {
        "totals": totals["totals"],
        "timeline": totals["timeline"],
        "replies_by_class": totals["replies_by_class"],
        "recent_campaigns": store.list_campaigns()[:6],
        "setup": {
            "email_ready": email["ready"], "email_hint": email["hint"], "imap_ready": email["imap_ready"],
            "whatsapp_ready": bool(settings["whatsapp"].get("configured")),
            "tracking_ready": bool(settings.get("tracking_enabled") and settings.get("tracking_base_url")),
        },
        "loop": dispatcher.status(),
    }


def settings_payload() -> dict[str, Any]:
    settings = store.get_settings()
    settings.pop("tracking_secret", None)
    settings["from_email"] = sender.email_ready()["from_email"]
    return settings


def _preview(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("campaign") if isinstance(body.get("campaign"), dict) else {}
    base = store.get_campaign(raw["id"]) if raw.get("id") else {}
    campaign = store.normalize_campaign(raw, base or None)
    contact: dict[str, Any] = {"first_name": "Alex", "last_name": "Martin", "company": "Acme", "title": "CEO",
                               "email": "alex@example.com", "city": "Paris", "country": "FR"}
    if body.get("contact_id"):
        contact = store.get_contact(str(body["contact_id"]))
    if campaign["channel"] == "whatsapp":
        wa = render.render_whatsapp(campaign, contact)
        text = wa["text"] or f"Template {wa['template']} ({wa['language']}): " + " | ".join(wa["params"])
        return {"subject": "", "preheader": "", "html": "", "text": text, "warnings": wa["warnings"]}
    rendered = render.render_email(campaign, contact, store.get_settings())
    return {k: rendered[k] for k in ("subject", "preheader", "html", "text", "warnings")}


def handle_campaigns_action(
    action: str,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    *,
    crm_project: Path | None = None,
) -> Any:
    body = body if isinstance(body, dict) else {}
    query = query or {}
    act = (action or "dashboard").strip().lower()

    if act == "dashboard":
        return dashboard()
    # contacts
    if act == "contacts.list":
        return store.list_contacts(q=str(query.get("q") or ""), list_id=str(query.get("list_id") or ""),
                                   status=str(query.get("status") or ""), offset=_int(query, "offset"),
                                   limit=_int(query, "limit", 50))
    if act == "contacts.save":
        return store.save_contact(body)
    if act == "contacts.delete":
        return {"deleted": store.delete_contacts([str(i) for i in body.get("ids") or []])}
    if act == "contacts.export":
        return _export(str(query.get("list_id") or ""))
    # imports
    if act == "import.preview":
        return importer.preview(str(body.get("filename") or ""), _decode(body.get("data_b64")))
    if act == "import.commit":
        mapping = body.get("mapping") if isinstance(body.get("mapping"), dict) else {}
        return importer.commit(str(body.get("import_id") or ""), {str(k): str(v) for k, v in mapping.items()},
                               list_id=str(body.get("list_id") or ""),
                               new_list_name=str(body.get("new_list_name") or ""),
                               tags=[str(t) for t in body.get("tags") or []])
    if act == "import.leads":
        return importer.from_leads(list_id=str(body.get("list_id") or ""),
                                   new_list_name=str(body.get("new_list_name") or ""))
    if act in ("crm.preview", "crm.import"):
        if crm_project is None:
            raise CampaignError("Open a project chat to use its CRM", status=409)
        kinds = [str(k) for k in body.get("kinds") or ["contacts"]]
        filters = body.get("filters") if isinstance(body.get("filters"), dict) else {}
        if act == "crm.preview":
            return crm_source.preview(crm_project, kinds, filters)
        people, _ = crm_source.audience(crm_project, kinds, filters)
        return crm_source.import_people(people, list_id=str(body.get("list_id") or ""),
                                        new_list_name=str(body.get("new_list_name") or ""),
                                        tags=[str(t) for t in body.get("tags") or []])
    # prospecting (Leads desk providers and keys)
    if act == "prospects.providers":
        return prospecting.providers()
    if act == "prospects.search":
        criteria = body.get("criteria") if isinstance(body.get("criteria"), dict) else body
        return prospecting.search(criteria)
    if act == "prospects.import":
        return prospecting.import_search(
            str(body.get("search_id") or ""),
            indexes=[int(i) for i in body.get("indexes") or [] if str(i).isdigit()] or None,
            only_with_email=bool(body.get("only_with_email")),
            list_id=str(body.get("list_id") or ""), new_list_name=str(body.get("new_list_name") or ""),
            tags=[str(t) for t in body.get("tags") or []],
        )
    if act == "contacts.enrich":
        return prospecting.enrich([str(i) for i in body.get("ids") or []],
                                  find_email=bool(body.get("find_email", True)),
                                  verify=bool(body.get("verify", True)), company=bool(body.get("company", True)))
    # lists
    if act == "lists.list":
        return store.list_lists()
    if act == "lists.save":
        return store.save_list(body)
    if act == "lists.delete":
        return {"deleted": store.delete_list(str(body.get("id") or ""))}
    if act == "lists.add":
        return store.add_to_list(str(body.get("list_id") or ""), [str(i) for i in body.get("contact_ids") or []])
    if act == "lists.remove":
        return store.remove_from_list(str(body.get("list_id") or ""), [str(i) for i in body.get("contact_ids") or []])
    # templates
    if act == "templates.list":
        return store.list_templates()
    if act == "templates.save":
        return store.save_template(body)
    if act == "templates.delete":
        return {"deleted": store.delete_template(str(body.get("id") or ""))}
    # campaigns
    if act == "campaigns.list":
        return store.list_campaigns(str(query.get("status") or ""))
    if act == "campaigns.get":
        return store.get_campaign(str(query.get("id") or body.get("id") or ""))
    if act == "campaigns.save":
        return store.save_campaign(body)
    if act == "campaigns.delete":
        return {"deleted": store.delete_campaign(str(body.get("id") or ""))}
    if act == "campaigns.duplicate":
        return store.duplicate_campaign(str(body.get("id") or ""))
    if act == "campaigns.preview":
        return _preview(body)
    if act == "campaigns.test":
        campaign = body.get("campaign") if isinstance(body.get("campaign"), dict) else {}
        return {"sent": dispatcher.send_test(campaign, [str(t) for t in body.get("to") or []])}
    if act == "campaigns.schedule":
        return dispatcher.schedule(str(body.get("id") or ""), str(body.get("schedule_at") or ""))
    if act == "campaigns.pause":
        return dispatcher.pause(str(body.get("id") or ""))
    if act == "campaigns.resume":
        return dispatcher.resume(str(body.get("id") or ""))
    if act == "campaigns.cancel":
        return dispatcher.cancel(str(body.get("id") or ""))
    if act == "campaigns.sends":
        return store.list_sends(str(query.get("id") or ""), status=str(query.get("status") or ""),
                                offset=_int(query, "offset"), limit=_int(query, "limit", 50))
    if act == "attachments.upload":
        return sender.save_attachment(str(body.get("filename") or ""), _decode(body.get("data_b64")))
    # replies
    if act == "replies.list":
        return store.list_replies(status=str(query.get("status") or ""),
                                  classification=str(query.get("classification") or ""),
                                  campaign_id=str(query.get("campaign_id") or ""),
                                  offset=_int(query, "offset"), limit=_int(query, "limit", 50))
    if act == "replies.update":
        return store.update_reply(body)
    if act == "replies.send":
        return inbound.send_reply(str(body.get("id") or ""), str(body.get("body") or ""),
                                  str(body.get("subject") or ""))
    # settings
    if act == "settings.get":
        return settings_payload()
    if act == "settings.save":
        store.save_settings(body)
        settings = store.get_settings()
        try:
            from navin.campaigns.heartbeat import ensure_heartbeat_task

            ensure_heartbeat_task(settings["auto_reply_mode"] != "off" and settings["loop_enabled"])
        except Exception:  # noqa: BLE001 - settings are saved even if HEARTBEAT.md is read-only
            pass
        return settings_payload()
    if act == "whatsapp.test":
        to = str(body.get("to") or "")
        settings = store.get_settings(with_secrets=True)
        message_id = sender.send_whatsapp(settings, to, {"template": "hello_world", "language": "en_US",
                                                         "params": []})
        return {"ok": True, "detail": f"Test template sent (id {message_id})."}
    if act == "whatsapp.templates":
        return sender.whatsapp_templates(store.get_settings(with_secrets=True))
    if act == "loop.run":
        return dispatcher.maybe_tick(force=True)
    raise CampaignError(f"Unknown campaigns action {action!r}", status=404)


def _export(list_id: str) -> dict[str, Any]:
    import csv
    import io

    rows = store.list_contacts(list_id=list_id, limit=500)
    total, offset, items = rows["total"], 500, list(rows["items"])
    while offset < total:
        items += store.list_contacts(list_id=list_id, offset=offset, limit=500)["items"]
        offset += 500
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    attr_keys = sorted({k for c in items for k in (c.get("attrs") or {})})
    writer.writerow(["email", "phone", "first_name", "last_name", "company", "title", "city", "country",
                     "tags", "status", *attr_keys])
    for c in items:
        writer.writerow([c["email"], c["phone"], c["first_name"], c["last_name"], c["company"], c["title"],
                         c["city"], c["country"], ";".join(c["tags"]), c["status"],
                         *[(c.get("attrs") or {}).get(k, "") for k in attr_keys]])
    name = store.get_list(list_id)["name"] if list_id else "contacts"
    return {"csv": buffer.getvalue(), "filename": f"{name}.csv".replace("/", "-")}


# -- public tracking (no auth: signed tokens) ---------------------------------

PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def track(kind: str, token: str, url: str = "") -> tuple[str, Any]:
    """Record an open / click / unsubscribe. Returns (response kind, payload)."""
    parts = render.verify(store.tracking_secret(), token.removesuffix(".gif"))
    if not parts or len(parts) != 2 or parts[0] != kind:
        return ("pixel", PIXEL) if kind == "o" else ("html", _page("This link is not valid."))
    send = store.get_send(parts[1])
    if send is None:
        return ("pixel", PIXEL) if kind == "o" else ("html", _page("This link has expired."))
    stamp = store.now_iso()
    if kind == "o":
        store.mark_send(send["id"], opened_at=send["opened_at"] or stamp, open_count=send["open_count"] + 1)
        return "pixel", PIXEL
    if kind == "c":
        store.mark_send(send["id"], clicked_at=send["clicked_at"] or stamp, click_count=send["click_count"] + 1,
                        opened_at=send["opened_at"] or stamp)
        store.record_event("click", campaign_id=send["campaign_id"], send_id=send["id"], data={"url": url[:500]})
        if url.startswith(("http://", "https://")):
            return "redirect", url
        return "html", _page("Link opened.")
    if kind == "u":
        store.set_contact_status(send["contact_id"], "unsubscribed")
        store.record_event("unsubscribe", campaign_id=send["campaign_id"], send_id=send["id"])
        return "html", _page("You are unsubscribed. You will not receive these emails anymore.")
    return "html", _page("Unknown link.")


def _page(message: str) -> str:
    import html

    return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
            "content='width=device-width,initial-scale=1'><title>Navin</title></head>"
            "<body style='font-family:Arial,sans-serif;display:flex;min-height:90vh;align-items:center;"
            f"justify-content:center;color:#111827'><p>{html.escape(message)}</p></body></html>")
