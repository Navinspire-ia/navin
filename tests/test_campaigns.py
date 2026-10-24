"""Campaigns Studio: lists, imports, CRM audience, sending, replies, tracking."""

import base64
import io
import json
from email.message import EmailMessage

import httpx
import pytest

from navin.campaigns import (
    db,
    deliverability,
    dispatcher,
    heartbeat,
    importer,
    inbound,
    render,
    sender,
    store,
)
from navin.campaigns.store import CampaignError
from navin.webui import campaigns_api

_REAL_MX = deliverability.domain_accepts_mail


@pytest.fixture(autouse=True)
def book(tmp_path, monkeypatch):
    root = tmp_path / "campaigns"
    root.mkdir()
    monkeypatch.setattr(db, "campaigns_root", lambda: root)
    monkeypatch.setattr(importer, "campaigns_root", lambda: root)
    # No real DNS in tests: "could not tell" never skips anyone.
    from navin.campaigns import deliverability

    monkeypatch.setattr(deliverability, "domain_accepts_mail", lambda domain: None)
    return root


class FakeSMTP:
    sent: list[EmailMessage] = []
    fail_for: set[str] = set()

    def __init__(self, *args, **kwargs):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        if msg["To"] in FakeSMTP.fail_for:
            raise OSError("recipient rejected")
        FakeSMTP.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.fail_for = set()
    monkeypatch.setattr(sender, "smtp_config", lambda: {
        "host": "smtp.example.com", "port": 587, "user": "news@acme.io", "password": "x",
        "from": "news@acme.io", "tls": True, "ssl": False,
    })
    monkeypatch.setattr(sender, "email_ready", lambda: {
        "ready": True, "imap_ready": True, "from_email": "news@acme.io", "hint": "",
    })
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    return FakeSMTP


def _people(n=3):
    list_id = store.save_list({"name": "Prospects"})["id"]
    ids = []
    for i in range(n):
        ids.append(store.save_contact({"email": f"p{i}@client.com", "first_name": f"P{i}", "company": "Client",
                                       "list_ids": [list_id]})["id"])
    return list_id, ids


def test_contacts_lists_and_dedupe():
    list_id, ids = _people(2)
    again = store.save_contact({"email": "P0@client.com", "title": "CTO"})
    assert again["id"] == ids[0] and again["title"] == "CTO"
    assert store.get_list(list_id)["count"] == 2
    page = store.list_contacts(q="p1@", list_id=list_id)
    assert page["total"] == 1 and page["items"][0]["list_ids"] == [list_id]
    with pytest.raises(CampaignError, match="email or a phone"):
        store.save_contact({"first_name": "Nobody"})
    with pytest.raises(CampaignError, match="valid email"):
        store.save_contact({"email": "not-an-email"})


def test_csv_and_xlsx_import_with_auto_mapping():
    csv_bytes = "Prénom;Nom;E-mail;Société;Plan\nAna;Diaz;ana@x.com;Xco;pro\nBob;;bad;Yco;free\n".encode()
    preview = importer.preview("leads.csv", csv_bytes)
    assert preview["rows"] == 2
    assert preview["mapping"]["E-mail"] == "email" and preview["mapping"]["Société"] == "company"
    assert preview["mapping"]["Prénom"] == "first_name" and preview["mapping"]["Plan"] == "attrs.plan"
    result = importer.commit(preview["import_id"], preview["mapping"], new_list_name="Salon 2026", tags=["salon"])
    assert result["created"] == 1 and result["skipped"] == 1 and "bad" in result["errors"][0]
    ana = store.find_contact("ana@x.com")
    assert ana["attrs"]["plan"] == "pro" and ana["tags"] == ["salon"] and len(store.get_contact(ana["id"])["list_ids"]) == 1
    assert store.get_list(result["list_id"])["count"] == 1

    from openpyxl import Workbook

    wb = Workbook()
    wb.active.append(["Email", "Phone", "Full name"])
    wb.active.append(["zoe@y.com", "+33 6 12 34 56 78", "Zoe Lee"])
    buffer = io.BytesIO()
    wb.save(buffer)
    preview = importer.preview("contacts.xlsx", buffer.getvalue())
    assert preview["mapping"] == {"Email": "email", "Phone": "phone", "Full name": "full_name"}
    importer.commit(preview["import_id"], preview["mapping"])
    zoe = store.find_contact("zoe@y.com")
    assert zoe["first_name"] == "Zoe" and zoe["phone"] == "+33612345678"
    with pytest.raises(CampaignError, match="Email or Phone"):
        p = importer.preview("x.csv", b"a,b\n1,2\n")
        importer.commit(p["import_id"], {"a": "company", "b": "ignore"})


def test_crm_audience_with_filters(tmp_path):
    from navin.campaigns import crm_source
    from navin.crm.store import create_record

    project = tmp_path / "project"
    project.mkdir()
    acme = create_record(project, "companies", {"name": "Acme", "industry": "SaaS", "country": "FR"})
    ana = create_record(project, "contacts", {"firstName": "Ana", "lastName": "Diaz", "email": "ana@acme.io",
                                              "companyId": acme["id"], "tags": ["vip"]})
    create_record(project, "contacts", {"firstName": "Bo", "email": "bo@other.io", "status": "inactif"})
    create_record(project, "leads", {"name": "Cy Ray", "email": "cy@lead.io", "score": 85, "company": "Lead Co"})
    create_record(project, "leads", {"name": "Low Score", "email": "low@lead.io", "score": 10})
    create_record(project, "opportunities", {"name": "Big deal", "amount": 50000, "stage": "proposition",
                                             "contactIds": [ana["id"]]})

    preview = crm_source.preview(project, ["contacts"], {"tags": ["vip"]})
    assert preview["count"] == 1 and preview["sample"][0]["company"] == "Acme"
    assert "vip" in preview["facets"]["tags"]
    leads = crm_source.preview(project, ["leads"], {"score_min": 50})
    assert [s["email"] for s in leads["sample"]] == ["cy@lead.io"]
    opps = crm_source.audience(project, ["opportunities"], {"amount_min": 10000})[0]
    assert opps[0]["attrs"]["stage"] == "proposition" and opps[0]["email"] == "ana@acme.io"
    result = campaigns_api.handle_campaigns_action(
        "crm.import", {"kinds": ["opportunities", "leads"], "filters": {"amount_min": 1000, "score_min": 50},
                       "new_list_name": "Hot CRM"}, crm_project=project)
    assert result["created"] == 2 and store.get_list(result["list_id"])["count"] == 2
    with pytest.raises(CampaignError, match="Open a project chat"):
        campaigns_api.handle_campaigns_action("crm.preview", {})


def test_render_merge_tags_footer_and_signed_tracking():
    settings = {**store.get_settings(), "company_address": "1 rue de Paris", "tracking_enabled": True,
                "tracking_base_url": "https://navin.example.com"}
    campaign = {"subject": "Hi {{first_name|there}}", "preheader": "For {{company}}", "track_opens": True,
                "track_clicks": True,
                "html": '<p>Hello {{first_name}}</p><a href="https://acme.io/offer">Offer</a><script>x()</script>'}
    out = render.render_email(campaign, {"first_name": "", "company": "Xco"}, settings, send_id="sd_1", secret="s3")
    assert out["subject"] == "Hi there"
    assert "<script" not in out["html"] and "1 rue de Paris" in out["html"]
    assert "/api/campaigns/t/c/" in out["html"] and "u=https%3A%2F%2Facme.io%2Foffer" in out["html"]
    assert "/api/campaigns/t/o/" in out["html"] and out["unsubscribe_url"]
    assert any("first_name" in w for w in out["warnings"])
    token = out["unsubscribe_url"].rsplit("/", 1)[1]
    assert render.verify("s3", token) == ["u", "sd_1"]
    assert render.verify("other", token) is None


def test_campaign_sends_throttled_skips_unsubscribed_and_finishes(smtp):
    list_id, ids = _people(4)
    store.set_contact_status(ids[3], "unsubscribed")
    store.save_settings({"company_address": "1 rue X", "throttle_per_minute": 2})
    campaign = store.save_campaign({"name": "Launch", "subject": "Hello {{first_name}}", "list_ids": [list_id],
                                    "html": "<p>Hi {{first_name}}</p>", "throttle_per_minute": 2})
    store.set_campaign(campaign["id"], status="scheduled")
    dispatcher.maybe_tick()
    stats = store.campaign_stats(campaign["id"])
    assert stats["sent"] == 2 and stats["queued"] == 1 and stats["skipped"] == 1   # throttle 2/min
    assert {m["To"] for m in smtp.sent} <= {"p0@client.com", "p1@client.com", "p2@client.com"}
    first = smtp.sent[0]
    assert first["Subject"] == f"Hello P{first['To'][1]}" and first.get_body(("html",)) is not None
    skipped = store.list_sends(campaign["id"], status="skipped")["items"][0]
    assert skipped["error"] == "unsubscribed"
    store.save_settings({"throttle_per_minute": 100})
    store.set_campaign(campaign["id"], throttle_per_minute=100)
    dispatcher.maybe_tick()
    final = store.get_campaign(campaign["id"])
    assert final["status"] == "sent" and final["stats"]["sent"] == 3
    with pytest.raises(CampaignError, match="cannot be edited"):
        store.save_campaign({"id": campaign["id"], "name": "x"})


def test_schedule_validates_and_future_waits(smtp):
    list_id, _ = _people(1)
    draft = store.save_campaign({"name": "Empty", "list_ids": [list_id]})
    with pytest.raises(CampaignError, match="subject"):
        dispatcher.schedule(draft["id"])
    ok = store.save_campaign({"id": draft["id"], "subject": "S", "html": "<p>Body</p>"})
    scheduled = dispatcher.schedule(ok["id"], "2999-01-01T09:00:00+00:00")
    assert scheduled["status"] == "scheduled"
    dispatcher.maybe_tick()
    assert store.get_campaign(ok["id"])["stats"]["sent"] == 0
    dispatcher.pause(ok["id"])
    assert store.get_campaign(ok["id"])["status"] == "paused"
    assert dispatcher.cancel(ok["id"])["status"] == "cancelled"


def test_failures_attachments_and_test_send(smtp):
    list_id, _ = _people(2)
    smtp.fail_for = {"p1@client.com"}
    attachment = sender.save_attachment("brochure.pdf", b"%PDF-1.4 data")
    campaign = store.save_campaign({"name": "Doc", "subject": "Our brochure", "html": "<p>See attached</p>",
                                    "list_ids": [list_id], "attachments": [attachment]})
    sent = dispatcher.send_test({"id": campaign["id"]}, ["me@acme.io"])
    assert sent == ["me@acme.io"] and smtp.sent[-1]["Subject"].startswith("[Test]")
    assert store.campaign_stats(campaign["id"])["recipients"] == 0
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    stats = store.campaign_stats(campaign["id"])
    assert stats["sent"] == 1 and stats["failed"] == 1
    delivered = [m for m in smtp.sent if m["To"] == "p0@client.com"][0]
    assert [p.get_filename() for p in delivered.iter_attachments()] == ["brochure.pdf"]


def test_whatsapp_cloud_api_template_send(monkeypatch):
    store.save_settings({"whatsapp": {"phone_number_id": "123456", "waba_id": "999", "token": "EAAtoken123456"}})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/message_templates"):
            return httpx.Response(200, json={"data": [{"name": "promo", "language": "fr", "status": "APPROVED",
                                                       "category": "MARKETING",
                                                       "components": [{"type": "BODY", "text": "Bonjour {{1}}, -{{2}}%"}]}]})
        body = json.loads(request.content)
        if body["to"] == "33600000000":
            return httpx.Response(400, json={"error": {"message": "Invalid parameter"}})
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
    settings = store.get_settings(with_secrets=True)
    templates = sender.whatsapp_templates(settings)
    assert templates[0]["params"] == 2 and templates[0]["status"] == "APPROVED"
    store.save_contact({"phone": "+33 6 11 22 33 44", "first_name": "Ana"})
    store.save_contact({"phone": "+33 6 00 00 00 00", "first_name": "Bad"})
    list_id = store.save_list({"name": "WA"})["id"]
    store.add_to_list(list_id, [c["id"] for c in store.list_contacts()["items"]])
    campaign = store.save_campaign({"name": "WA promo", "channel": "whatsapp", "wa_template": "promo",
                                    "wa_language": "fr", "wa_params": ["{{first_name}}", "20"], "list_ids": [list_id]})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    stats = store.campaign_stats(campaign["id"])
    assert stats["sent"] == 1 and stats["failed"] == 1
    sent_body = json.loads([c for c in calls if c.url.path.endswith("/messages")][0].content)
    assert sent_body["template"]["name"] == "promo"
    assert sent_body["template"]["components"][0]["parameters"][0]["text"] in {"Ana", "Bad"}
    assert calls[-1].headers["authorization"] == "Bearer EAAtoken123456"
    failed = store.list_sends(campaign["id"], status="failed")["items"][0]
    assert "Invalid parameter" in failed["error"]
    assert store.get_settings()["whatsapp"]["token"] == ""   # never returned


def _reply(to_message_id, sender_addr, subject, body, msg_id):
    msg = EmailMessage()
    msg["From"] = sender_addr
    msg["To"] = "news@acme.io"
    msg["Subject"] = subject
    msg["Message-ID"] = msg_id
    if to_message_id:
        msg["In-Reply-To"] = to_message_id
    msg.set_content(body)
    return msg.as_bytes()


def test_replies_threaded_classified_and_unsubscribe(smtp):
    list_id, ids = _people(3)
    campaign = store.save_campaign({"name": "Q4", "subject": "Offer", "html": "<p>Offer</p>", "list_ids": [list_id]})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    sent = {m["To"]: m["Message-ID"] for m in smtp.sent}

    interested = inbound.ingest_message(_reply(sent["p0@client.com"], "P0 <p0@client.com>", "Re: Offer",
                                               "Yes, interested! What is the pricing?\n\nOn Mon, X wrote:\n> Offer",
                                               "<r1@client.com>"))
    assert interested["classification"] == "interested" and "On Mon" not in interested["body"]
    assert inbound.ingest_message(_reply(sent["p0@client.com"], "p0@client.com", "Re: Offer", "dup",
                                         "<r1@client.com>")) is None          # same Message-ID once
    stop = inbound.ingest_message(_reply("", "p1@client.com", "Re: Offer", "STOP", "<r2@client.com>"))
    assert stop["classification"] == "unsubscribe"
    assert store.get_contact(ids[1])["status"] == "unsubscribed"
    away = inbound.ingest_message(_reply(sent["p2@client.com"], "p2@client.com", "Automatic reply: Offer",
                                         "I am out of office until Monday.", "<r3@client.com>"))
    assert away["classification"] == "out_of_office" and away["status"] == "ignored"
    assert inbound.ingest_message(_reply("", "stranger@else.com", "Hello", "hi", "<r4@else.com>")) is None
    stats = store.campaign_stats(campaign["id"])
    assert stats["replied"] == 3 and stats["unsubscribed"] == 1

    answered = inbound.send_reply(interested["id"], "Thanks! Our pro plan is 49 EUR/month.")
    assert answered["status"] == "answered"
    answer = smtp.sent[-1]
    assert answer["In-Reply-To"] == "<r1@client.com>" and answer["Subject"] == "Re: Offer"

    note = heartbeat.heartbeat_prompt_note(heartbeat.tick_watch())
    assert note == ""  # interested was answered; nothing new left except handled ones
    store.save_settings({"auto_reply_mode": "send"})
    inbound.ingest_message(_reply(sent["p2@client.com"], "p2@client.com", "Re: Offer", "Can we book a call?",
                                  "<r5@client.com>"))
    note = heartbeat.heartbeat_prompt_note(heartbeat.tick_watch())
    assert "Campaign replies waiting: 1" in note and "[meeting]" in note and "reply_send" in note


def test_bounce_marks_contact(smtp):
    list_id, ids = _people(1)
    campaign = store.save_campaign({"name": "B", "subject": "S", "html": "<p>x</p>", "list_ids": [list_id]})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    mid = smtp.sent[0]["Message-ID"]
    bounce = inbound.ingest_message(_reply("", "MAILER-DAEMON@mx.client.com", "Undeliverable: S",
                                           f"Delivery failed for p0@client.com\nMessage-ID: {mid}", "<b1@mx>"))
    assert bounce["classification"] == "bounce"
    assert store.get_contact(ids[0])["status"] == "bounced"
    assert store.campaign_stats(campaign["id"])["bounced"] == 1


def test_public_tracking_open_click_unsubscribe(smtp):
    store.save_settings({"tracking_enabled": True, "tracking_base_url": "https://navin.example.com"})
    list_id, ids = _people(1)
    campaign = store.save_campaign({"name": "T", "subject": "S", "list_ids": [list_id],
                                    "html": '<p><a href="https://acme.io/x">x</a></p>'})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    html = smtp.sent[0].get_body(("html",)).get_content()
    import re

    open_token = re.search(r"/t/o/([^\"]+)\.gif", html).group(1)
    click_token = re.search(r"/t/c/([^\"?]+)\?", html).group(1)
    unsub_token = re.search(r"/t/u/([^\"]+)\"", html).group(1)
    assert campaigns_api.track("o", open_token + ".gif") == ("pixel", campaigns_api.PIXEL)
    assert campaigns_api.track("c", click_token, "https://acme.io/x") == ("redirect", "https://acme.io/x")
    kind, page = campaigns_api.track("u", unsub_token)
    assert kind == "html" and "unsubscribed" in page
    assert store.get_contact(ids[0])["status"] == "unsubscribed"
    stats = store.campaign_stats(campaign["id"])
    assert stats["opened"] == 1 and stats["clicked"] == 1
    assert campaigns_api.track("u", "forged" + unsub_token[6:])[0] == "html"
    assert smtp.sent[0]["List-Unsubscribe"].startswith("<https://navin.example.com/api/campaigns/t/u/")


def test_api_dashboard_settings_and_heartbeat_task(smtp, tmp_path):
    _people(2)
    dash = campaigns_api.handle_campaigns_action("dashboard")
    assert dash["totals"]["contacts"] == 2 and len(dash["timeline"]) == 30 and dash["setup"]["email_ready"]
    workspace = tmp_path / "ws"
    (workspace / ".navin").mkdir(parents=True)
    (workspace / ".navin" / "HEARTBEAT.md").write_text("# Heartbeat Tasks\n\n## Active Tasks\n\n### Other\nx\n")
    assert heartbeat.ensure_heartbeat_task(True, workspace)
    text = (workspace / ".navin" / "HEARTBEAT.md").read_text()
    assert "Campaign replies" in text and "### Other" in text
    assert not heartbeat.ensure_heartbeat_task(True, workspace)
    assert heartbeat.ensure_heartbeat_task(False, workspace)
    assert "Campaign replies" not in (workspace / ".navin" / "HEARTBEAT.md").read_text()
    saved = campaigns_api.handle_campaigns_action("settings.save", {"from_name": "Acme", "whatsapp": {"token": "T" * 20}})
    assert saved["from_name"] == "Acme" and saved["whatsapp"]["token_hint"] == "...TTTT"
    assert "tracking_secret" not in saved
    with pytest.raises(CampaignError, match="Unknown"):
        campaigns_api.handle_campaigns_action("nope")
    upload = campaigns_api.handle_campaigns_action(
        "attachments.upload", {"filename": "a.txt", "data_b64": base64.b64encode(b"hello").decode()})
    assert upload["size"] == 5


def test_agent_tool_refuses_launch_on_heartbeat(monkeypatch):
    import asyncio

    from navin.agent.tools.campaigns import CampaignsTool

    monkeypatch.setattr("navin.agent.tools.context.is_heartbeat_turn", lambda: True)
    tool = CampaignsTool()
    result = asyncio.run(tool.execute(action="schedule", id="cp_x"))
    assert "heartbeat" in str(result)
    monkeypatch.setattr("navin.agent.tools.context.is_heartbeat_turn", lambda: False)
    listed = asyncio.run(tool.execute(action="lists"))
    assert json.loads(listed) == []
    assert tool.call_read_only({"action": "replies"}) and not tool.call_read_only({"action": "schedule"})


def _wa_setup(monkeypatch):
    store.save_settings({"whatsapp": {"phone_number_id": "123456", "waba_id": "999", "token": "EAAtoken123456",
                                      "app_secret": "appsecret123", "webhook_enabled": True}})
    sent_payloads = []

    def handler(request):
        sent_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": f"wamid.out{len(sent_payloads)}"}]})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    contact = store.save_contact({"phone": "+33 6 11 22 33 44", "first_name": "Ana", "company": "Acme",
                                  "attrs": {"stage": "proposition"}})
    list_id = store.save_list({"name": "WA"})["id"]
    store.add_to_list(list_id, [contact["id"]])
    campaign = store.save_campaign({"name": "WA promo", "channel": "whatsapp", "wa_template": "promo",
                                    "wa_params": ["{{first_name}}"], "list_ids": [list_id]})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    return contact, campaign, sent_payloads


def _signed(body: bytes) -> dict:
    import hashlib
    import hmac

    return {"x-hub-signature-256": "sha256=" + hmac.new(b"appsecret123", body, hashlib.sha256).hexdigest()}


def test_whatsapp_webhook_reply_receipt_and_agent_answer(monkeypatch):
    import time as _time

    from navin.agent.tools.campaigns import CampaignsTool
    from navin.campaigns import wa_webhook

    contact, campaign, sent_payloads = _wa_setup(monkeypatch)
    wa = store.get_settings(with_secrets=True)["whatsapp"]
    assert wa["verify_token"]  # generated when the webhook was enabled
    # Meta's subscription check.
    status, _, body = wa_webhook.handle_request(
        "GET", f"/whatsapp/webhook?hub.mode=subscribe&hub.verify_token={wa['verify_token']}&hub.challenge=42", {}, b"")
    assert (status, body) == (200, b"42")
    assert wa_webhook.handle_request("GET", "/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=x", {}, b"")[0] == 403

    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "33611223344", "profile": {"name": "Ana D"}}],
        "messages": [{"from": "33611223344", "id": "wamid.in1", "timestamp": str(int(_time.time())),
                      "type": "text", "text": {"body": "Intéressée, quel est le prix ?"},
                      "context": {"id": "wamid.out1"}}],
        "statuses": [{"id": "wamid.out1", "status": "read", "recipient_id": "33611223344"}],
    }}]}]}
    raw = json.dumps(payload).encode()
    assert wa_webhook.handle_request("POST", "/whatsapp/webhook", {"x-hub-signature-256": "sha256=bad"}, raw)[0] == 401
    assert wa_webhook.handle_request("POST", "/whatsapp/webhook", _signed(raw), raw)[0] == 200
    reply = store.list_replies()["items"][0]
    assert reply["channel"] == "whatsapp" and reply["classification"] == "interested"
    assert reply["campaign_id"] == campaign["id"] and reply["contact_name"] == "Ana"
    stats = store.campaign_stats(campaign["id"])
    assert stats["opened"] == 1 and stats["replied"] == 1

    # The agent gets the whole context automatically, then answers on WhatsApp.
    import asyncio

    tool = CampaignsTool()
    context = json.loads(asyncio.run(tool.execute(action="reply_get", id=reply["id"])))
    assert context["contact"]["attrs"]["stage"] == "proposition"
    assert [e["who"] for e in context["thread"]] == ["us", "them"] and "24 h" in context["channel_rules"]
    answered = json.loads(asyncio.run(tool.execute(action="reply_send", id=reply["id"],
                                                   body="Bonjour Ana, l'offre pro est a 49 EUR/mois.")))
    assert answered["status"] == "answered"
    assert sent_payloads[-1] == {"messaging_product": "whatsapp", "to": "33611223344", "type": "text",
                                 "text": {"body": "Bonjour Ana, l'offre pro est a 49 EUR/mois."}}
    thread = store.contact_thread(contact["id"])
    assert [e["kind"] for e in thread] == ["campaign", "reply", "answer"]


def test_whatsapp_window_closed_and_live_server(monkeypatch):
    import asyncio
    import socket

    from navin.campaigns import wa_webhook

    contact, campaign, _ = _wa_setup(monkeypatch)
    old = store.add_reply({"campaign_id": campaign["id"], "contact_id": contact["id"], "from_addr": "+33611223344",
                           "body": "hello", "message_id": "wamid.old", "channel": "whatsapp",
                           "received_at": "2020-01-01T00:00:00+00:00"})
    with pytest.raises(CampaignError, match="24-hour"):
        inbound.send_reply(old["id"], "late answer")

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    store.save_settings({"whatsapp": {"webhook_port": port}})

    async def run():
        await wa_webhook.ensure_server()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        body = json.dumps({"entry": []}).encode()
        head = (f"POST /whatsapp/webhook HTTP/1.1\r\nHost: x\r\nContent-Length: {len(body)}\r\n"
                f"X-Hub-Signature-256: {_signed(body)['x-hub-signature-256']}\r\n\r\n").encode()
        writer.write(head + body)
        await writer.drain()
        answer = await reader.read()
        writer.close()
        store.save_settings({"whatsapp": {"webhook_enabled": False}})
        await wa_webhook.ensure_server()
        return answer

    answer = asyncio.run(run())
    assert answer.startswith(b"HTTP/1.1 200") and answer.endswith(b'{"ok":true}')
    assert not wa_webhook._SERVER


def test_webhook_port_never_collides_with_the_gateway(monkeypatch):
    monkeypatch.setattr(store, "_reserved_ports", lambda: {18790})
    assert store.get_settings()["whatsapp"]["webhook_port"] == 18795
    with pytest.raises(CampaignError, match="already used by Navin"):
        store.save_settings({"whatsapp": {"webhook_port": 18790}})
    assert store.save_settings({"whatsapp": {"webhook_port": 18800}})["whatsapp"]["webhook_port"] == 18800


def _age_sends(campaign_id, days):
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    with db.connect() as conn:
        conn.execute("UPDATE sends SET sent_at=? WHERE campaign_id=?", (old, campaign_id))
        conn.execute("UPDATE followups SET sent_at=? WHERE campaign_id=?", (old, campaign_id))


def test_sequence_follow_ups_threaded_and_stopped_by_replies(smtp):
    list_id, ids = _people(3)
    campaign = store.save_campaign({
        "name": "Outbound", "subject": "Quick question", "html": "<p>Hi {{first_name}}</p>", "list_ids": [list_id],
        "steps": [{"delay_days": 3, "condition": "no_reply", "html": "<p>Bumping this, {{first_name}}</p>"},
                  {"delay_days": 4, "condition": "no_open", "subject": "Last try", "html": "<p>Closing the loop</p>"}],
    })
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    first = {m["To"]: m["Message-ID"] for m in smtp.sent}
    assert len(first) == 3
    dispatcher.maybe_tick()
    assert len(smtp.sent) == 3  # day 0: nothing due yet

    # p0 replies, p1 opened; three days later step 1 goes only to p1 and p2, in the same thread.
    inbound.ingest_message(_reply(first["p0@client.com"], "p0@client.com", "Re: Quick question",
                                  "Sure, tell me more", "<rep0@client.com>"))
    send_p1 = store.list_sends(campaign["id"])["items"]
    p1 = next(s for s in send_p1 if s["address"] == "p1@client.com")
    store.mark_send(p1["id"], opened_at=store.now_iso())
    _age_sends(campaign["id"], 3)
    dispatcher.maybe_tick()
    step1 = smtp.sent[3:]
    assert sorted(m["To"] for m in step1) == ["p1@client.com", "p2@client.com"]
    p2_follow = next(m for m in step1 if m["To"] == "p2@client.com")
    assert p2_follow["Subject"] == "Re: Quick question" and p2_follow["In-Reply-To"] == first["p2@client.com"]
    assert "Bumping this, P2" in p2_follow.get_body(("html",)).get_content()
    skipped = store.get_campaign(campaign["id"])["stats"]["steps"][0]
    assert skipped == {"step": 1, "sent": 2, "failed": 0, "skipped": 1, "opened": 0}

    # A reply to the follow-up threads back to the campaign and stops step 2 for p2;
    # p1 opened, so the "not opened" step 2 skips them too.
    inbound.ingest_message(_reply(p2_follow["Message-ID"], "p2@client.com", "Re: Quick question",
                                  "Not interested", "<rep2@client.com>"))
    _age_sends(campaign["id"], 4)
    dispatcher.maybe_tick()
    assert len(smtp.sent) == 5
    steps = store.get_campaign(campaign["id"])["stats"]["steps"]
    assert steps[1]["sent"] == 0 and steps[1]["skipped"] == 2
    assert store.campaign_stats(campaign["id"])["replied"] == 2


def test_ab_subjects_split_and_reported(smtp):
    list_id, _ = _people(12)
    campaign = store.save_campaign({"name": "AB", "subject": "Version A", "subject_variants": ["Version B"],
                                    "html": "<p>x</p>", "list_ids": [list_id]})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    subjects = {m["Subject"] for m in smtp.sent}
    assert subjects == {"Version A", "Version B"}
    variants = store.get_campaign(campaign["id"])["stats"]["variants"]
    assert [v["variant"] for v in variants] == ["A", "B"] and sum(v["sent"] for v in variants) == 12
    assert [v["sent"] for v in variants] == [6, 6], "the split is balanced"
    assert variants[1]["subject"] == "Version B"


def test_ab_split_is_balanced_and_stable_on_small_lists():
    # Hashing each contact on its own sent one subject to a whole small list
    # now and then (4 contacts: 1 campaign in 8); the split is dealt instead.
    for n in (1, 2):  # save_campaign keeps at most two alternative subjects (A/B/C)
        for size in (2, 3, 4, 5, 7, 12):
            for _ in range(40):
                campaign = {"id": store.new_id("cp"), "subject_variants": [f"V{i}" for i in range(n)]}
                ids = [store.new_id("ct") for _ in range(size)]
                split = store.assign_variants(campaign, ids)
                assert set(split) == set(ids)
                counts = [list(split.values()).count(v) for v in "ABC"[: n + 1]]
                assert max(counts) - min(counts) <= 1, (n, size, counts)
                assert store.assign_variants(campaign, list(reversed(ids))) == split, "order does not matter"
    plain = store.assign_variants({"id": "cp_1", "subject_variants": []}, ["a", "b", "a"])
    assert plain == {"a": "", "b": ""}


def test_send_window_and_dead_domains(smtp, monkeypatch):
    from datetime import datetime, timezone

    from navin.campaigns import deliverability

    window = {"enabled": True, "days": [1, 2, 3, 4, 5], "start": "09:00", "end": "18:00", "timezone": "UTC"}
    assert deliverability.in_send_window(window, datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc))      # Monday
    assert not deliverability.in_send_window(window, datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc))  # Sunday
    assert not deliverability.in_send_window(window, datetime(2026, 10, 19, 19, 0, tzinfo=timezone.utc))
    with pytest.raises(CampaignError, match="end after"):
        store.save_settings({"send_window": {"start": "18:00", "end": "09:00"}})

    list_id, _ = _people(2)
    monkeypatch.setattr(deliverability, "in_send_window", lambda w, now=None: False)
    campaign = store.save_campaign({"name": "W", "subject": "S", "html": "<p>x</p>", "list_ids": [list_id]})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    assert not smtp.sent and store.get_campaign(campaign["id"])["status"] == "scheduled"

    monkeypatch.setattr(deliverability, "in_send_window", lambda w, now=None: True)
    monkeypatch.setattr(deliverability, "domain_accepts_mail", lambda d: False)
    dispatcher.maybe_tick()
    stats = store.campaign_stats(campaign["id"])
    assert stats["sent"] == 0 and stats["skipped"] == 2
    assert store.list_sends(campaign["id"])["items"][0]["error"] == "this domain does not receive email"


def test_dns_mx_query_parses_a_real_shaped_answer(monkeypatch):
    import struct

    from navin.campaigns import deliverability

    deliverability._CACHE.clear()

    class FakeSock:
        def __init__(self, *a):
            self.sent = b""

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def settimeout(self, t):
            pass

        def sendto(self, packet, addr):
            self.sent = packet

        def recvfrom(self, n):
            ident = struct.unpack(">H", self.sent[:2])[0]
            domain = self.sent[12:].split(b"\x00")[0]
            answers = 0 if b"nomail" in domain else 1
            rcode = 3 if b"nowhere" in domain else 0
            return struct.pack(">HHHHHH", ident, 0x8180 | rcode, 1, answers, 0, 0), ("1.1.1.1", 53)

    monkeypatch.setattr(deliverability.socket, "socket", FakeSock)
    assert _REAL_MX("acme.io") is True
    assert _REAL_MX("nowhere.invalid") is False
    assert _REAL_MX("nomail.example") is False  # no MX, no address


def test_prospect_search_with_leads_keys_then_import(monkeypatch):
    from navin.campaigns import prospecting

    monkeypatch.setattr(prospecting, "_secrets", lambda: {"apollo": "k1", "hunter": "k2"})
    hunted = []

    def hunt(profile, keys, limit):
        hunted.append(profile)
        return [{"company": "Acme", "domain": "acme.io", "website": "https://acme.io", "country": "FR",
                 "sector": "SaaS", "size": "50", "source": "sirene", "signal": "hiring sales"},
                {"company": "Nomail", "domain": "", "source": "osm"}]

    def people(domain, titles, keys):
        assert titles == ["CEO", "CTO"] and keys["apollo"] == "k1"
        return {"first_name": "Ana", "last_name": "Diaz", "role": "CEO", "email": "ana@acme.io",
                "email_status": "verified", "source": "apollo"}

    result = prospecting.search({"sector": "SaaS", "countries": ["fr"], "titles": ["CEO", "CTO"], "limit": 10},
                                hunt=hunt, people=people)
    assert hunted[0]["countries"] == ["FR"] and result["count"] == 2 and result["with_email"] == 1
    assert result["providers"]["can_find_people"] and not result["providers"]["can_enrich_company"]
    imported = prospecting.import_search(result["search_id"], only_with_email=True, new_list_name="SaaS FR")
    assert imported["created"] == 1
    ana = store.find_contact("ana@acme.io")
    assert ana["title"] == "CEO" and ana["attrs"]["domain"] == "acme.io" and ana["attrs"]["email_status"] == "verified"
    with pytest.raises(CampaignError, match="sector or keywords"):
        prospecting.search({})
    with pytest.raises(CampaignError, match="expired"):
        prospecting.import_search("nope")
    by_domain = prospecting.search({"domains": ["https://www.globex.com/about"]}, people=lambda d, t, k: {})
    assert by_domain["rows"][0]["domain"] == "globex.com"


def test_prospect_search_without_keys_reads_public_contact_pages_then_imports(monkeypatch):
    from navin.campaigns import prospecting
    from navin.leads import scrape_net

    monkeypatch.setattr(prospecting, "_secrets", lambda: {})
    sites = []

    def site(website, *, country="", fetch_fn=None):
        sites.append(website)
        if "dead" in website:
            raise OSError("down")
        domain = website.removeprefix("https://")
        return {"email": f"contact@{domain}", "phone": "+33140000000", "email_status": "unverified",
                "source": "web", "signal": "public company site"}

    monkeypatch.setattr(scrape_net, "enrich_site", site)
    companies = [{"company": "Acme", "domain": "acme.io", "website": "https://acme.io", "source": "osm",
                  "signal": "hiring sales"},
                 {"company": "Dead", "domain": "dead.io", "source": "osm"},
                 {"company": "Shop", "domain": "shop.io", "phone": "+33150000000", "source": "osm"},
                 {"company": "Nodomain", "domain": "", "source": "osm"}]
    # The UI sends find_people=false when no Apollo / Hunter key: the general contact still comes.
    result = prospecting.search({"sector": "SaaS", "find_people": False}, hunt=lambda p, k, n: companies)
    assert sorted(sites) == ["dead.io", "https://acme.io", "shop.io"]
    acme, dead, shop, nodomain = result["rows"]
    assert acme["email"] == "contact@acme.io" and acme["phone"] == "+33140000000"
    assert acme["contact_kind"] == "general" and acme["first_name"] == ""
    assert acme["source"] == "osm" and acme["signal"] == "hiring sales"  # the company facts stay
    assert dead["email"] == "" and dead["contact_kind"] == "" and nodomain["contact_kind"] == ""
    # A switchboard already known is kept; the public page adds the contact@ address.
    assert shop["phone"] == "+33150000000" and shop["email"] == "contact@shop.io"
    assert result["with_email"] == 2 and result["with_general"] == 2 and result["with_person"] == 0
    assert result["providers"]["can_find_contact"] and not result["providers"]["can_find_people"]
    assert "Apollo or Hunter" in result["providers"]["hint"]
    imported = prospecting.import_search(result["search_id"], new_list_name="Free")
    assert imported["created"] == 2
    general = store.find_contact("contact@acme.io")
    assert general["company"] == "Acme" and general["attrs"]["contact_kind"] == "general"

    sites.clear()
    skipped = prospecting.search({"sector": "SaaS", "find_contact": False}, hunt=lambda p, k, n: companies)
    assert sites == [] and skipped["with_email"] == 0


def test_prospect_search_keeps_the_named_person_and_falls_back_to_the_general_contact(monkeypatch):
    from navin.campaigns import prospecting
    from navin.leads import scrape_net

    monkeypatch.setattr(prospecting, "_secrets", lambda: {"apollo": "k1"})
    sites = []
    monkeypatch.setattr(scrape_net, "enrich_site", lambda website, **kw: sites.append(website) or {
        "email": "contact@globex.io", "phone": "+33140000000", "email_status": "unverified"})
    people = {"acme.io": {"first_name": "Ana", "last_name": "Diaz", "role": "CEO", "email": "ana@acme.io",
                          "email_status": "verified", "source": "apollo"},
              "globex.io": {"first_name": "Bo", "last_name": "Li", "role": "CTO", "source": "apollo"}}
    companies = [{"company": "Acme", "domain": "acme.io", "source": "sirene"},
                 {"company": "Globex", "domain": "globex.io", "source": "sirene"}]
    result = prospecting.search({"sector": "SaaS"}, hunt=lambda p, k, n: companies,
                                people=lambda domain, titles, keys: people[domain])
    acme, globex = result["rows"]
    assert sites == ["globex.io"]  # a direct email from Apollo needs no page fetch
    assert acme["email"] == "ana@acme.io" and acme["contact_kind"] == "person"
    assert globex["first_name"] == "Bo" and globex["email"] == "contact@globex.io"
    assert globex["contact_kind"] == "general" and globex["source"] == "apollo"
    assert result["with_person"] == 2 and result["with_general"] == 1


def test_enrich_finds_verifies_and_invalid_emails_are_never_sent(smtp, monkeypatch):
    from navin.campaigns import prospecting

    monkeypatch.setattr(prospecting, "_secrets", lambda: {"hunter": "hk", "pdl": "pk"})
    no_email = store.save_contact({"phone": "+33611111111", "first_name": "Zoe", "last_name": "Lee",
                                   "attrs": {"domain": "acme.io"}})
    dead = store.save_contact({"email": "old@acme.io", "first_name": "Old"})
    good = store.save_contact({"email": "max@acme.io", "first_name": "Max"})
    calls = {
        "finder": lambda domain, first, last, key: {"email": f"{first.lower()}@{domain}", "email_status": "verified"},
        "verify": lambda email, key: {"email_status": "invalid" if email.startswith("old") else "verified"},
        "company": lambda domain, key: {"sector": "SaaS", "size": "51-200"},
    }
    result = prospecting.enrich([no_email["id"], dead["id"], good["id"]], calls=calls)
    assert result == {"processed": 3, "emails_found": 1, "verified": 1, "invalid": 1, "companies": 3,
                      "skipped": 0, "errors": []}
    zoe = store.get_contact(no_email["id"])
    assert zoe["email"] == "zoe@acme.io" and zoe["attrs"]["sector"] == "SaaS"
    assert store.get_contact(dead["id"])["attrs"]["email_status"] == "invalid"

    list_id = store.save_list({"name": "Enriched"})["id"]
    store.add_to_list(list_id, [no_email["id"], dead["id"], good["id"]])
    campaign = store.save_campaign({"name": "E", "subject": "S", "html": "<p>x</p>", "list_ids": [list_id]})
    dispatcher.schedule(campaign["id"])
    dispatcher.maybe_tick()
    assert sorted(m["To"] for m in smtp.sent) == ["max@acme.io", "zoe@acme.io"]
    skipped = store.list_sends(campaign["id"], status="skipped")["items"]
    assert skipped[0]["error"] == "email verified as invalid"

    monkeypatch.setattr(prospecting, "_secrets", lambda: {})
    with pytest.raises(CampaignError, match="Hunter or People Data Labs"):
        prospecting.enrich([good["id"]])
