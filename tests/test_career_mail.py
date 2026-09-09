# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Professional mail through captured SMTP/IMAP transports, never real accounts."""

from __future__ import annotations

import base64
import io
import json
import smtplib
import threading
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock

import pytest
from docx import Document

from navin.career.desk import prepare_application, snapshot
from navin.career.errors import CareerError
from navin.career.loop import maybe_tick
from navin.career.mail import (
    configure_mailbox,
    flush_mail_notifications,
    mail_draft,
    mailbox_status,
    run_mail_cycle,
    send_application,
)
from navin.career.mail import (
    test_mailbox as check_mailbox,
)
from navin.career.mail_settings import email_address, normalize_mailbox, published_recipient
from navin.career.mailbox import sync_mailbox
from navin.career.store import CareerStore
from navin.career.watch import digest_was_delivered, run_watch
from navin.webui.career_api import handle_career_action


class CapturedSMTP:
    def __init__(self, *, data_code: int = 250, recipient_code: int = 250, disconnect: bool = False):
        self.data_code, self.recipient_code, self.disconnect = data_code, recipient_code, disconnect
        self.messages: list[bytes] = []
        self.envelopes: list[tuple[str, str]] = []
        self.on_login = None

    def login(self, username, password):
        assert username == "candidate@example.test" and password == "fictional-smtp-password"
        if self.on_login:
            self.on_login()
        return 235, b"authenticated"

    def noop(self):
        return 250, b"OK"

    def mail(self, sender):
        self.sender = sender
        return 250, b"OK"

    def rcpt(self, recipient):
        self.envelopes.append((self.sender, recipient))
        return self.recipient_code, b"OK" if self.recipient_code == 250 else b"refused"

    def data(self, wire):
        self.messages.append(wire)
        if self.disconnect:
            raise smtplib.SMTPServerDisconnected("connection lost after DATA")
        return self.data_code, b"2.0.0 queued as captured-123"

    def quit(self):
        return 221, b"bye"

    def close(self):
        pass


class CapturedIMAP:
    def __init__(self, messages: dict[int, bytes], *, validity: str = "77"):
        self.messages, self.validity = messages, validity
        self.calls: list[tuple] = []

    def login(self, username, password):
        assert username == "candidate@example.test" and password == "fictional-imap-password"
        return "OK", [b"logged in"]

    def select(self, folder, readonly=False):
        assert folder == '"INBOX"' and readonly
        return "OK", [str(len(self.messages)).encode()]

    def response(self, key):
        assert key == "UIDVALIDITY"
        return key, [self.validity.encode()]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in self.messages)]
        assert command == "fetch"
        uid = int(args[0])
        raw = self.messages[uid]
        if "HEADER.FIELDS" in args[1]:
            raw = raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
        return "OK", [(f"1 (UID {uid} RFC822.SIZE {len(self.messages[uid])} BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self):
        return "BYE", [b"bye"]


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> CareerStore:
    store = CareerStore(tmp_path / "career")
    store.save_profile({
        "display_name": "Camille Exemple", "headline": "Data Engineer", "email": "candidate@example.test",
        "wizard_complete": True, "titles": ["Data Engineer"], "languages": ["fr", "en"],
        "stack": ["Python", "SQL", "Spark"], "ai_assist": False,
        "master_cv": "Camille Exemple, Data Engineer. Python, SQL et Spark.\n"
                     "2021-2025: Data Engineer chez Atelier Données.\n"
                     "Construction de pipelines Python pour les données de ventes.\n"
                     "Contrôles de qualité SQL documentés avec les équipes métier.\n"
                     "Traitement de données avec Spark et supervision des traitements quotidiens.\n"
                     "Transmission des procédures aux équipes et documentation des incidents.\n"
                     "2019: Master Informatique, Université Exemple.",
        "experiences": [{"title": "Data Engineer", "company": "Atelier Données", "period": "2021-2025",
                         "facts": "Pipelines Python pour les ventes.\nContrôles de qualité SQL.\nTraitements Spark quotidiens."}],
        "education": [{"diploma": "Master Informatique", "school": "Université Exemple", "year": "2019"}],
    })
    store.upsert_opportunities([{
        "id": "job-example", "title": "Data Engineer", "company": "Entreprise Démonstration",
        "description": "Mission Data Engineer. Python, SQL et Spark. Envoyez votre CV à recruiter@example.test.",
        "url": "https://example.test/jobs/1", "stage": "matched", "match_score": 94,
        "application_email": "recruiter@example.test", "stack": ["Python", "SQL", "Spark"],
    }])
    monkeypatch.setattr("navin.career.notify.deliver_alert", lambda *args, **kwargs: {"complete": True, "confirmed": True})
    monkeypatch.setattr("navin.career.mail._smtp_connection", Mock(side_effect=AssertionError("Unexpected real SMTP")))
    monkeypatch.setattr("navin.career.mailbox._imap_connection", Mock(side_effect=AssertionError("Unexpected real IMAP")))
    return store


def configured(store: CareerStore, **overrides) -> CareerStore:
    configure_mailbox(store, {
        "mailbox": {"enabled": True, "sender_name": "Camille Exemple", "sender_email": "candidate@example.test",
                    "smtp_host": "smtp.example.test", "smtp_username": "candidate@example.test",
                    "imap_host": "imap.example.test", "imap_username": "candidate@example.test", **overrides},
        "smtp_password": "fictional-smtp-password", "imap_password": "fictional-imap-password",
    })
    return store


def prepared(store: CareerStore) -> dict:
    return prepare_application(store, "job-example")["prepared"]


def sent(store: CareerStore, smtp: CapturedSMTP | None = None) -> dict:
    configured(store, read_replies=True)
    prepared(store)
    draft = mail_draft(store, "job-example")
    return send_application(store, "job-example", revision=draft["revision"], smtp_factory=lambda _: smtp or CapturedSMTP())


def reply(message_id: str, *, identity: str = "<reply-1@example.test>", subject: str = "Entretien Data Engineer", body: str = "Bonjour, nous vous proposons un entretien mardi.", sender: str = "recruiter@example.test") -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    message["From"], message["To"] = sender, "candidate@example.test"
    message["Subject"], message["Message-ID"] = subject, identity
    if message_id:
        message["In-Reply-To"] = message_id
        message["References"] = f"<earlier@example.test> {message_id}"
    message.set_content(body)
    return message.as_bytes()


def test_mail_opt_ins_are_separate_and_secrets_never_enter_snapshot(store):
    profile = store.save_profile({"apply_mode": "autopilot", "mail": {"gmail": True}, "mailbox": {"auto_send": "true", "read_replies": "true", "smtp_password": "must-be-stripped"}})
    assert profile["mailbox"]["auto_send"] is False and profile["mailbox"]["read_replies"] is False
    assert not run_mail_cycle(store)["sent"]
    assert sync_mailbox(store)["status"] == "disabled"
    configured(store)
    status = snapshot(store)
    rendered = json.dumps(status)
    assert "fictional-smtp-password" not in rendered and "fictional-imap-password" not in rendered
    assert "must-be-stripped" not in store.profile_path.read_text()
    assert status["mailbox_status"]["smtp_password_set"]
    assert store.secrets_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("address", ["a@example.test\r\nBcc: b@example.test", "a@example.test,b@example.test", "Someone <a@example.test>", "a", "a@localhost", "a@@example.test"])
def test_recipient_is_one_explicit_address(address):
    with pytest.raises(CareerError):
        email_address(address)


def test_only_published_application_invitation_can_supply_an_automatic_recipient():
    assert published_recipient({"description": "Envoyez votre CV à hiring@example.test."}) == ("hiring@example.test", "published_offer")
    assert published_recipient({"description": "Questions générales: privacy@example.test"}) == ("", "")
    assert published_recipient({"application_url": "mailto:jobs@example.test?subject=Data"})[0] == "jobs@example.test"


def test_connection_check_authenticates_but_never_sends_or_reads_bodies(store):
    configured(store)
    smtp, imap = CapturedSMTP(), CapturedIMAP({})
    checks = check_mailbox(store, smtp_factory=lambda _: smtp, imap_factory=lambda _: imap)
    assert checks["smtp"]["status"] == checks["imap"]["status"] == "connected"
    assert not smtp.messages and not smtp.envelopes and not imap.calls


def test_smtp_receipt_contains_actual_documents_and_persists_only_after_acceptance(store):
    smtp = CapturedSMTP()
    receipt = sent(store, smtp)
    assert receipt["status"] == "accepted" and receipt["smtp_code"] == 250
    assert receipt["smtp_reply"] == "2.0.0 queued as captured-123"
    message = BytesParser(policy=policy.default).parsebytes(smtp.messages[0])
    assert message["Message-ID"] == receipt["message_id"]
    assert smtp.envelopes == [("candidate@example.test", "recruiter@example.test")]
    attachments = list(message.iter_attachments())
    assert len(attachments) == 2
    app = store.load_applications()[0]
    assert message.get_body(preferencelist=("plain",)).get_content().replace("\r\n", "\n").strip() == app["cover"].strip()
    for attachment, metadata in zip(attachments, receipt["attachments"], strict=True):
        raw = attachment.get_payload(decode=True)
        assert raw == base64.b64decode(store.read_bytes(metadata["file_id"])["data"])
        assert "Camille Exemple" in "\n".join(para.text for para in Document(io.BytesIO(raw)).paragraphs)
    assert app["stage"] == "applied" and app["applied_at"] == receipt["accepted_at"]
    assert store.get_opportunity("job-example")["mail_receipt"]["message_id"] == receipt["message_id"]
    repeated = send_application(CareerStore(store.root), "job-example", smtp_factory=lambda _: smtp)
    assert repeated["deduplicated"] and len(smtp.messages) == 1
    prepared(store)
    assert snapshot(store)["applications"][0]["stage"] == "applied"


@pytest.mark.parametrize("failure,expected", [("recipient", "failed"), ("data", "failed"), ("disconnect", "unknown")])
def test_refusal_or_uncertain_data_never_records_a_sent_application(store, failure, expected):
    smtp = CapturedSMTP(recipient_code=550 if failure == "recipient" else 250, data_code=554 if failure == "data" else 250, disconnect=failure == "disconnect")
    receipt = sent(store, smtp)
    assert receipt["status"] == expected
    assert not receipt.get("accepted_at")
    assert store.get_opportunity("job-example")["stage"] == "ready"
    if expected == "unknown":
        assert send_application(store, "job-example", retry=True, smtp_factory=lambda _: smtp)["deduplicated"]
        assert len(smtp.messages) == 1


def test_a_changed_draft_or_unreviewed_document_cannot_be_sent(store):
    configured(store)
    prepared(store)
    draft = mail_draft(store, "job-example")
    app = store.load_applications()[0]
    store.upsert_application({**app, "cover": app["cover"] + "\nMerci pour votre retour."})
    with pytest.raises(CareerError, match="mail_draft_changed"):
        send_application(store, "job-example", revision=draft["revision"])
    store.upsert_application({"opportunity_id": "job-example", "generation": {"status": "needs_review"}})
    fresh = mail_draft(store, "job-example")
    with pytest.raises(CareerError, match="mail_documents_need_review"):
        send_application(store, "job-example", revision=fresh["revision"])


def test_concurrent_requests_never_send_twice(store):
    configured(store)
    prepared(store)
    draft = mail_draft(store, "job-example")
    smtp = CapturedSMTP()
    entered, release = threading.Event(), threading.Event()
    smtp.on_login = lambda: (entered.set(), release.wait(5))
    results = []
    thread = threading.Thread(target=lambda: results.append(send_application(store, "job-example", revision=draft["revision"], smtp_factory=lambda _: smtp)))
    thread.start()
    assert entered.wait(5)
    try:
        with pytest.raises(CareerError, match="mail_busy"):
            send_application(CareerStore(store.root), "job-example", revision=draft["revision"], smtp_factory=lambda _: smtp)
    finally:
        release.set()
        thread.join(5)
    assert results[0]["status"] == "accepted" and len(smtp.messages) == 1


def test_auto_cycle_tailors_per_job_and_obeys_opt_in_daily_cap_and_pause(store, monkeypatch):
    configured(store, auto_send=True, max_per_day=1, allowed_recipient_domains=["example.test"])
    store.save_loop({**store.load_loop(), "enabled": True})
    store.upsert_opportunities([{**store.get_opportunity("job-example"), "id": "job-second", "url": "https://example.test/jobs/2", "title": "Python Engineer", "match_score": 90}])
    smtp = CapturedSMTP()
    monkeypatch.setattr("navin.career.mail._smtp_connection", lambda _: smtp)
    result = run_mail_cycle(store)
    assert result["sent"] == 1 and len(smtp.messages) == 1
    assert run_mail_cycle(store)["sent"] == 0
    store.save_loop_intent({"enabled": False})
    assert run_mail_cycle(store)["skipped"][0]["reason"] == "mail_loop_paused"


def test_pause_between_authentication_and_data_prevents_automatic_submission(store):
    configured(store, auto_send=True)
    store.save_loop({**store.load_loop(), "enabled": True})
    prepared(store)
    smtp = CapturedSMTP()
    smtp.on_login = lambda: store.save_loop_intent({"enabled": False})
    receipt = send_application(store, "job-example", automatic=True, smtp_factory=lambda _: smtp)
    assert receipt["status"] == "failed" and receipt["error"] == "mail_loop_paused"
    assert not smtp.messages


def test_imap_threads_replies_and_deduplicates_even_after_uidvalidity_reset(store):
    receipt = sent(store)
    related = reply(receipt["message_id"])
    unrelated = reply("<unrelated@example.test>", identity="<other@example.test>", body="A private unrelated message")
    imap = CapturedIMAP({10: related, 11: unrelated})
    outcome = sync_mailbox(store, imap_factory=lambda _: imap)
    assert outcome["received"] == 1 and outcome["scanned"] == 2
    inbox = store.load_inbox()
    assert len(inbox) == 1 and inbox[0]["classification"] == "interview"
    assert inbox[0]["opportunity_id"] == "job-example" and inbox[0]["source"] == "imap"
    assert store.load_applications()[0]["stage"] == "interview"
    assert sync_mailbox(store, imap_factory=lambda _: imap)["received"] == 0
    reset = CapturedIMAP({1: related}, validity="88")
    assert sync_mailbox(store, imap_factory=lambda _: reset)["received"] == 0
    assert len(store.load_inbox()) == 1
    full_fetch = [call for call in imap.calls if call[0] == "fetch" and "HEADER.FIELDS" not in call[2]]
    assert len(full_fetch) == 1 and full_fetch[0][1] == "10" and "BODY.PEEK[]" in full_fetch[0][2]


def test_imap_requires_reference_or_exact_token_and_sender(store):
    receipt = sent(store)
    raw = reply("", subject=receipt["subject"], identity="<new-thread@example.test>")
    spoof = reply("", subject=receipt["subject"], identity="<unrelated-spam@example.test>", sender="elsewhere@example.test")
    result = sync_mailbox(store, imap_factory=lambda _: CapturedIMAP({1: raw, 2: spoof}))
    assert result["received"] == 1 and len(store.load_inbox()) == 1


def test_auto_replies_do_not_promote_application_to_interview(store):
    receipt = sent(store)
    msg = BytesParser(policy=policy.default).parsebytes(reply(receipt["message_id"]))
    msg["Auto-Submitted"] = "auto-replied"
    result = sync_mailbox(store, imap_factory=lambda _: CapturedIMAP({1: msg.as_bytes(policy=policy.SMTP)}))
    assert result["received"] == 1
    assert store.get_opportunity("job-example")["stage"] == "applied"
    assert store.load_inbox()[0]["automatic_reply"]


def test_pending_notifications_reuse_event_until_all_channels_confirm(store, monkeypatch):
    calls = []
    def notify(*args, **kwargs):
        calls.append(kwargs["event_id"])
        return {"email": True, "telegram": False, "confirmed": True, "complete": len(calls) >= 2}
    monkeypatch.setattr("navin.career.notify.deliver_alert", notify)
    sent(store)
    assert mailbox_status(store)["pending_notifications"] == 1
    flush_mail_notifications(store)
    assert mailbox_status(store)["pending_notifications"] == 0
    assert len(calls) == 2 and calls[0] == calls[1]
    assert not digest_was_delivered({"complete": False, "webui": True, "email": True})


def test_watch_keeps_batch_id_when_new_matches_arrive_during_async_confirmation(store, monkeypatch):
    calls = []
    def notify(*args, **kwargs):
        calls.append(kwargs["event_id"])
        return {"email": True, "complete": len(calls) >= 2}
    monkeypatch.setattr("navin.career.notify.deliver_alert", notify)
    first = run_watch(store)
    store.upsert_opportunities([{**store.get_opportunity("job-example"), "id": "job-new", "url": "https://example.test/jobs/2"}])
    second = run_watch(store)
    assert not first["delivered"] and second["delivered"]
    assert calls[0] == calls[1] and second["count"] == 1
    assert run_watch(store)["count"] == 1


def test_api_draft_send_sync_and_scheduled_loop_share_the_same_receipts(store, monkeypatch):
    configured(store, read_replies=True, auto_send=True)
    smtp = CapturedSMTP()
    imap = CapturedIMAP({})
    monkeypatch.setattr("navin.webui.career_api._store", lambda: store)
    monkeypatch.setattr("navin.career.mail._smtp_connection", lambda _: smtp)
    monkeypatch.setattr("navin.career.mailbox._imap_connection", lambda _: imap)
    prepared(store)
    draft = handle_career_action("mail_draft", {"id": "job-example"})["mail_draft"]
    result = handle_career_action("send_email", {"id": "job-example", "revision": draft["revision"]})
    assert result["mail_receipt"]["status"] == "accepted"
    imap.messages[1] = reply(result["mail_receipt"]["message_id"])
    outcome = handle_career_action("sync_mail")
    assert outcome["mail_sync"]["received"] == 1 and len(outcome["inbox"]) == 1
    store.save_loop({**store.load_loop(), "enabled": True, "next_due": 0})
    tick = maybe_tick(store, force=True, collect_fn=lambda *args, **kwargs: {"added": 0}, watch_fn=lambda _: {"count": 0})
    assert tick["mail"]["sent"] == 0 and len(smtp.messages) == 1


def test_default_mailbox_normalization_never_enables_old_autopilot():
    assert normalize_mailbox(None)["auto_send"] is False
    assert normalize_mailbox({"enabled": 1, "auto_send": "true"})["enabled"] is False
