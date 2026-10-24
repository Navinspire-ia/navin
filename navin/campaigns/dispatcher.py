# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Schedule, launch and deliver campaigns, a batch per tick.

The gateway runs :func:`maybe_tick` every ~20 seconds. A due campaign is
expanded once into one ``sends`` row per reachable contact (unsubscribed,
bounced and address-less contacts are recorded as skipped, never silently
dropped), then delivered within its per-minute throttle and the daily cap.
Pause, resume and cancel act on the status the tick reads before each batch.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from navin.campaigns import deliverability, render, sender, store
from navin.campaigns.store import CampaignError

_LAST_TICK: dict[str, float] = {}


def _parse_when(value: str) -> datetime | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CampaignError("schedule_at must be an ISO date-time") from exc
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def check_ready(campaign: dict[str, Any], settings: dict[str, Any]) -> None:
    """Refuse a launch that cannot work, with what to fix."""
    if not (campaign.get("list_ids") or campaign.get("contact_ids")):
        raise CampaignError("Choose at least one list or contact")
    if campaign["channel"] == "email":
        if not campaign.get("subject", "").strip():
            raise CampaignError("The subject is empty")
        if not render.html_to_text(campaign.get("html") or "").strip():
            raise CampaignError("The message is empty")
        if not sender.email_ready()["ready"]:
            raise CampaignError("Email is not configured: Settings > Channels > Email (SMTP).", status=409)
    else:
        if not (campaign.get("wa_template") or campaign.get("text")):
            raise CampaignError("Pick an approved WhatsApp template")
        wa = settings.get("whatsapp") or {}
        if not (wa.get("phone_number_id") and wa.get("token")):
            raise CampaignError("WhatsApp is not configured in Campaigns settings.", status=409)


def schedule(campaign_id: str, schedule_at: str = "") -> dict[str, Any]:
    campaign = store.get_campaign(campaign_id)
    if campaign["status"] not in ("draft", "scheduled", "paused"):
        raise CampaignError(f"A {campaign['status']} campaign cannot be scheduled", status=409)
    check_ready(campaign, store.get_settings(with_secrets=True))
    when = _parse_when(schedule_at)
    stamp = when.astimezone(timezone.utc).replace(microsecond=0).isoformat() if when else store.now_iso()
    store.record_event("scheduled", campaign_id=campaign_id, data={"at": stamp})
    return store.set_campaign(campaign_id, status="scheduled", schedule_at=stamp, last_error="")


def pause(campaign_id: str) -> dict[str, Any]:
    campaign = store.get_campaign(campaign_id)
    if campaign["status"] not in ("sending", "scheduled"):
        raise CampaignError("Only a sending or scheduled campaign can be paused", status=409)
    return store.set_campaign(campaign_id, status="paused")


def resume(campaign_id: str) -> dict[str, Any]:
    campaign = store.get_campaign(campaign_id)
    if campaign["status"] != "paused":
        raise CampaignError("Only a paused campaign can be resumed", status=409)
    status = "sending" if campaign.get("launched_at") else "scheduled"
    return store.set_campaign(campaign_id, status=status)


def cancel(campaign_id: str) -> dict[str, Any]:
    campaign = store.get_campaign(campaign_id)
    if campaign["status"] in ("sent", "cancelled"):
        raise CampaignError(f"The campaign is already {campaign['status']}", status=409)
    from navin.campaigns.db import connect

    with connect() as conn:
        conn.execute("UPDATE sends SET status='skipped', error='campaign cancelled' "
                     "WHERE campaign_id=? AND status='queued'", (campaign_id,))
    return store.set_campaign(campaign_id, status="cancelled", finished_at=store.now_iso())


def expand(campaign: dict[str, Any]) -> int:
    """One sends row per targeted contact; unreachable ones are skipped with a reason."""
    rows: list[tuple[str, ...]] = []
    queued: list[int] = []
    for contact in store.audience_contacts(campaign):
        address = contact["email"] if campaign["channel"] == "email" else contact["phone"]
        if contact["status"] != "subscribed":
            rows.append((contact["id"], address or "-", "skipped", contact["status"]))
        elif not address:
            rows.append((contact["id"], "-", "skipped",
                         "no email address" if campaign["channel"] == "email" else "no phone number"))
        elif campaign["channel"] == "email" and (contact.get("attrs") or {}).get("email_status") == "invalid":
            rows.append((contact["id"], address, "skipped", "email verified as invalid"))
        else:
            queued.append(len(rows))
            rows.append((contact["id"], address, "queued", ""))
    # The A/B split is dealt over the reachable contacts only, so each subject
    # really goes out to its share.
    variants = store.assign_variants(campaign, [rows[i][0] for i in queued])
    for i in queued:
        rows[i] = (*rows[i], variants[rows[i][0]])
    return store.queue_sends(campaign["id"], rows)


def _contact_or_blank(contact_id: str) -> dict[str, Any]:
    try:
        return store.get_contact(contact_id)
    except CampaignError:
        return {}


def deliver_batch(campaign: dict[str, Any], settings: dict[str, Any], budget: int) -> int:
    """Send up to *budget* queued messages; return how many went out."""
    batch = store.next_queued(campaign["id"], budget)
    if not batch:
        return 0
    secret = store.tracking_secret()
    sent = 0
    from_email = sender.email_ready()["from_email"]
    if campaign["channel"] == "email":
        with sender.SmtpSession() as smtp:
            for row in batch:
                if store.get_campaign(campaign["id"])["status"] != "sending":
                    break
                contact = _contact_or_blank(row["contact_id"])
                if settings.get("verify_domains", True):
                    domain = row["address"].rsplit("@", 1)[-1]
                    if deliverability.domain_accepts_mail(domain) is False:
                        store.mark_send(row["id"], status="skipped", error="this domain does not receive email")
                        continue
                variant_campaign = campaign
                if row.get("variant"):
                    variant_campaign = {**campaign, "subject": store.subject_for(campaign, row["variant"])}
                rendered = render.render_email(variant_campaign, contact, settings, send_id=row["id"], secret=secret)
                try:
                    msg = sender.build_email(
                        rendered=rendered, to=row["address"],
                        from_name=campaign.get("from_name") or settings.get("from_name") or "",
                        from_email=from_email,
                        reply_to=campaign.get("reply_to") or settings.get("reply_to") or "",
                        attachments=campaign.get("attachments"), campaign_id=campaign["id"],
                    )
                    smtp.send(msg)
                except CampaignError as exc:
                    store.mark_send(row["id"], status="failed", error=exc.message[:300])
                    continue
                except Exception as exc:  # noqa: BLE001 - one bad address must not stop the batch
                    store.mark_send(row["id"], status="failed", error=str(exc)[:300] or "send failed",
                                    bounced=1 if "recipient" in str(exc).lower() else 0)
                    continue
                store.mark_send(row["id"], status="sent", sent_at=store.now_iso(), message_id=msg["Message-ID"])
                sent += 1
    else:
        import httpx

        with httpx.Client(timeout=30) as client:
            for row in batch:
                if store.get_campaign(campaign["id"])["status"] != "sending":
                    break
                contact = _contact_or_blank(row["contact_id"])
                rendered = render.render_whatsapp(campaign, contact)
                try:
                    message_id = sender.send_whatsapp(settings, row["address"], rendered, client=client)
                except CampaignError as exc:
                    store.mark_send(row["id"], status="failed", error=exc.message[:300])
                    continue
                store.mark_send(row["id"], status="sent", sent_at=store.now_iso(), message_id=message_id)
                sent += 1
    return sent


def tick_campaign(campaign: dict[str, Any], settings: dict[str, Any]) -> int:
    now = datetime.now(timezone.utc)
    if not deliverability.in_send_window(settings.get("send_window")):
        return 0  # outside the business-hours window: picked up at the next open slot
    if campaign["status"] == "scheduled":
        when = _parse_when(campaign.get("schedule_at") or "")
        if when and when > now:
            return 0
        try:
            check_ready(campaign, settings)
        except CampaignError as exc:
            store.set_campaign(campaign["id"], status="failed", last_error=exc.message)
            return 0
        expand(campaign)
        campaign = store.set_campaign(campaign["id"], status="sending", launched_at=store.now_iso())
        store.record_event("launched", campaign_id=campaign["id"])
    if campaign["status"] != "sending":
        return 0
    per_minute = int(campaign.get("throttle_per_minute") or settings.get("throttle_per_minute") or 30)
    room_minute = per_minute - store.sent_last_minute(campaign["id"])
    room_day = int(settings.get("daily_cap") or 500) - store.sent_today()
    budget = min(room_minute, room_day, 200)
    if room_day <= 0:
        store.set_campaign(campaign["id"], last_error="Daily sending cap reached; resumes tomorrow.")
        return 0
    if budget <= 0:
        return 0
    try:
        sent = deliver_batch(campaign, settings, budget)
    except CampaignError as exc:
        store.set_campaign(campaign["id"], last_error=exc.message)
        logger.warning("Campaign {} batch failed: {}", campaign["id"], exc.message)
        return 0
    stats = store.campaign_stats(campaign["id"])
    current = store.get_campaign(campaign["id"])
    if current["status"] == "sending" and not stats["queued"]:
        final = "failed" if stats["sent"] == 0 and stats["failed"] else "sent"
        store.set_campaign(campaign["id"], status=final, finished_at=store.now_iso(),
                           last_error="" if final == "sent" else "Every send failed; see the recipients list.")
        store.record_event("finished", campaign_id=campaign["id"], data=stats)
    return sent


def maybe_tick(*, force: bool = False) -> dict[str, Any]:
    """Advance every due campaign, then poll replies. Safe to call often."""
    settings = store.get_settings(with_secrets=True)
    if not settings.get("loop_enabled", True) and not force:
        return status()
    sent = 0
    for campaign in store.list_campaigns():
        if campaign["status"] in ("scheduled", "sending"):
            try:
                sent += tick_campaign(campaign, settings)
            except Exception:  # noqa: BLE001 - one campaign must not stall the others
                logger.exception("Campaign {} tick failed", campaign["id"])
    if deliverability.in_send_window(settings.get("send_window")):
        for campaign in store.pending_sequences():
            try:
                sent += tick_followups(campaign, settings)
            except Exception:  # noqa: BLE001 - one sequence must not stall the others
                logger.exception("Campaign {} follow-ups failed", campaign["id"])
    store.set_state("last_tick", store.now_iso())
    last_poll = _LAST_TICK.get("imap", 0.0)
    if force or time.monotonic() - last_poll > 60:
        _LAST_TICK["imap"] = time.monotonic()
        try:
            from navin.campaigns.inbound import poll_replies

            poll_replies()
        except Exception:  # noqa: BLE001 - replies are retried next tick
            logger.exception("Campaign reply poll failed")
    result = status()
    result["sent_now"] = sent
    return result


def tick_followups(campaign: dict[str, Any], settings: dict[str, Any], *, budget: int = 100) -> int:
    """Send the follow-ups that are due, threaded on the first email.

    A follow-up goes only to people still worth writing to: no reply, not
    bounced, still subscribed, and meeting the step's condition (no reply / not
    opened / always). A reply at any point stops the rest of the sequence.
    """
    if campaign["channel"] != "email" or not campaign.get("steps"):
        return 0
    from_email = sender.email_ready()["from_email"]
    secret = store.tracking_secret()
    sent = 0
    now_value = store.now_iso()
    for index, step in enumerate(campaign["steps"], start=1):
        due = store.due_followups(campaign, index, now_value)
        if not due:
            continue
        with sender.SmtpSession() as smtp:
            for row in due[:budget - sent]:
                if row["replied_at"] or row["bounced"] or row["contact_status"] != "subscribed":
                    store.record_followup(campaign["id"], row, index, status="skipped",
                                          error="replied" if row["replied_at"] else row["contact_status"])
                    continue
                if step["condition"] == "no_open" and row["opened_at"]:
                    store.record_followup(campaign["id"], row, index, status="skipped", error="opened")
                    continue
                contact = _contact_or_blank(row["contact_id"])
                first_subject = store.subject_for(campaign, row.get("variant") or "")
                subject = step.get("subject") or (first_subject if first_subject.lower().startswith("re:")
                                                  else f"Re: {first_subject}")
                rendered = render.render_email(
                    {**campaign, "subject": subject, "preheader": "", "html": step["html"]},
                    contact, settings, send_id=row["send_id"], secret=secret,
                )
                try:
                    msg = sender.build_email(
                        rendered=rendered, to=row["address"],
                        from_name=campaign.get("from_name") or settings.get("from_name") or "",
                        from_email=from_email,
                        reply_to=campaign.get("reply_to") or settings.get("reply_to") or "",
                        campaign_id=campaign["id"],
                    )
                    if row.get("message_id"):
                        msg["In-Reply-To"] = row["message_id"]
                        msg["References"] = row["message_id"]
                    smtp.send(msg)
                except Exception as exc:  # noqa: BLE001 - one address must not stop the sequence
                    store.record_followup(campaign["id"], row, index, status="failed", error=str(exc) or "failed")
                    continue
                store.record_followup(campaign["id"], row, index, status="sent", message_id=msg["Message-ID"])
                sent += 1
                if sent >= budget:
                    return sent
    return sent


def status() -> dict[str, Any]:
    totals = store.dashboard_totals()
    return {"last_tick": store.get_state("last_tick", "") or "",
            "last_imap_poll": store.get_state("last_imap_poll", "") or "",
            "sending": totals["sending"], "scheduled": totals["scheduled"],
            "wa_webhook_error": store.get_state("wa_webhook_error", "") or ""}


def send_test(campaign: dict[str, Any], to: list[str]) -> list[str]:
    """Send the campaign as it stands to test addresses (not counted in stats)."""
    targets = [t.strip() for t in to if t and t.strip()][:10]
    if not targets:
        raise CampaignError("Give at least one test address")
    settings = store.get_settings(with_secrets=True)
    campaign = store.normalize_campaign(campaign, None) if "id" not in campaign else {
        **store.get_campaign(campaign["id"]), **store.normalize_campaign(campaign, store.get_campaign(campaign["id"]))}
    sample = {"first_name": "Alex", "last_name": "Martin", "company": "Acme", "title": "CEO",
              "email": targets[0], "city": "Paris", "country": "FR"}
    if campaign.get("channel") == "whatsapp":
        rendered = render.render_whatsapp(campaign, sample)
        for target in targets:
            sender.send_whatsapp(settings, target, rendered)
        return targets
    rendered = render.render_email(campaign, sample, settings)
    rendered["subject"] = f"[Test] {rendered['subject']}"
    from_email = sender.email_ready()["from_email"]
    with sender.SmtpSession() as smtp:
        for target in targets:
            if "@" not in target:
                raise CampaignError(f"{target} is not an email address")
            smtp.send(sender.build_email(
                rendered=rendered, to=target,
                from_name=campaign.get("from_name") or settings.get("from_name") or "",
                from_email=from_email, reply_to=campaign.get("reply_to") or settings.get("reply_to") or "",
                attachments=campaign.get("attachments"),
            ))
    return targets
