"""Career application mail with durable intent, SMTP receipts and bounded automation."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import smtplib
import ssl
import time
from contextlib import contextmanager, suppress
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from filelock import FileLock, Timeout

from navin.career.errors import CareerError
from navin.career.mail_settings import (
    email_address,
    normalize_mailbox,
    published_recipient,
    validate_mailbox,
)
from navin.career.store import CareerStore, _atomic_write, _read_json

TRANSPORT_TIMEOUT_S = 15
SMTPFactory = Callable[[dict[str, Any]], Any]
MAX_ATTACHMENTS_BYTES = 15 * 1024 * 1024
_SENT_STAGES = {"applied", "replied", "interview", "offer", "won", "rejected"}


@contextmanager
def mail_lock(store: CareerStore, *, wait_s: float = 0) -> Iterator[None]:
    lock = FileLock(str(store.root / "mail.lock"))
    try:
        lock.acquire(timeout=wait_s)
    except Timeout as exc:
        raise CareerError("mail_busy", status=409) from exc
    try:
        yield
    finally:
        lock.release()


def load_mail_state(store: CareerStore) -> dict[str, Any]:
    raw = _read_json(store.root / "mail-state.json", {})
    state = dict(raw) if isinstance(raw, dict) else {}
    for key in ("outbox", "mailboxes", "notifications", "checks"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    return state


def save_mail_state(store: CareerStore, state: dict[str, Any]) -> None:
    path = store.root / "mail-state.json"
    _atomic_write(path, {**state, "schema": 1, "updated_at": time.time()})
    path.chmod(0o600)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _account_identity(store: CareerStore, config: dict[str, Any]) -> str:
    return _hash([config, store.get_secret("CAREER_SMTP_PASSWORD"), store.get_secret("CAREER_IMAP_PASSWORD")])


def public_receipt(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "opportunity_id", "application_id", "status", "message_id", "sender", "recipient",
        "subject", "revision", "attachments", "attempted_at", "accepted_at", "smtp_code",
        "smtp_reply", "error", "delivery_status", "attempts", "retry_at", "automatic",
    )
    return {key: record[key] for key in keys if key in record}


def mailbox_status(store: CareerStore) -> dict[str, Any]:
    config = normalize_mailbox(store.load_profile().get("mailbox"))
    state = load_mail_state(store)
    receipts = list(state["outbox"].values())
    return {
        "enabled": config["enabled"],
        "smtp_password_set": store.has_secret("CAREER_SMTP_PASSWORD"),
        "imap_password_set": store.has_secret("CAREER_IMAP_PASSWORD"),
        "checks": state["checks"] if state.get("checks_identity") == _account_identity(store, config) else {},
        "sync": state.get("last_sync") or {},
        "accepted": sum(row.get("status") == "accepted" for row in receipts),
        "failed": sum(row.get("status") == "failed" for row in receipts),
        "uncertain": sum(row.get("status") in {"sending", "unknown"} for row in receipts),
        "pending_notifications": sum(not row.get("complete") for row in state["notifications"].values()),
        "receipts": [public_receipt(row) for row in receipts][-100:],
    }


def configure_mailbox(store: CareerStore, body: dict[str, Any]) -> dict[str, Any]:
    incoming = body.get("mailbox")
    if not isinstance(incoming, dict):
        raise CareerError("mail_config_required")
    current = normalize_mailbox(store.load_profile().get("mailbox"))
    for key in ("smtp_security", "imap_security"):
        if key in incoming and incoming[key] not in {"ssl", "starttls"}:
            raise CareerError("mail_tls_required")
    config = normalize_mailbox({**current, **incoming})
    if config["enabled"]:
        validate_mailbox(config)
        if config["read_replies"]:
            validate_mailbox(config, protocol="imap")
    for domain in config["allowed_recipient_domains"]:
        email_address(f"recipient@{domain}")
    # Secrets live in the existing 0600 secret store, never in snapshot/profile.
    for field, secret in (("smtp_password", "CAREER_SMTP_PASSWORD"), ("imap_password", "CAREER_IMAP_PASSWORD")):
        if body.get(f"clear_{field}") is True:
            store.save_secret(secret, "")
        elif isinstance(body.get(field), str) and body[field]:
            store.save_secret(secret, body[field])
    patch: dict[str, Any] = {"mailbox": config}
    if isinstance(body.get("channels"), dict):
        channels = dict(store.load_profile().get("channels") or {})
        for channel in ("email", "telegram", "whatsapp", "teams"):
            if channel in body["channels"]:
                channels[channel] = body["channels"][channel] is True
            key = f"{channel}_to"
            if key in body["channels"]:
                channels[key] = str(body["channels"][key] or "").strip()[:500]
        patch["channels"] = channels
    store.save_profile(patch)
    store.append_journal({"kind": "mail_config", "text": "Professional mail settings saved"})
    return mailbox_status(store)


def _smtp_connection(config: dict[str, Any]) -> Any:
    context = ssl.create_default_context()
    if config["smtp_security"] == "ssl":
        client = smtplib.SMTP_SSL(config["smtp_host"], config["smtp_port"], timeout=TRANSPORT_TIMEOUT_S, context=context)
    else:
        client = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=TRANSPORT_TIMEOUT_S)
    try:
        client.ehlo_or_helo_if_needed()
        if config["smtp_security"] == "starttls":
            client.starttls(context=context)
            client.ehlo()
        return client
    except Exception:
        with suppress(Exception):
            client.close()
        raise


def _close_smtp(client: Any) -> None:
    if client is not None:
        with suppress(Exception):
            client.quit()
        with suppress(Exception):
            client.close()


def _error_code(exc: Exception) -> str:
    # Provider error strings may echo credentials or message contents.
    if isinstance(exc, CareerError):
        return exc.message if exc.message.startswith("mail_") else "mail_document_error"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return "mail_authentication_failed"
    if isinstance(exc, ssl.SSLError):
        return "mail_tls_failed"
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "mail_tls_or_auth_unsupported"
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return "mail_connection_failed"
    return "mail_transport_failed"


def test_mailbox(store: CareerStore, *, smtp_factory: SMTPFactory | None = None, imap_factory: Any = None) -> dict[str, Any]:
    """Authenticate and check the folder. This action never sends a message."""
    from navin.career.mailbox import check_imap

    config = normalize_mailbox(store.load_profile().get("mailbox"))
    results: dict[str, Any] = {}
    with mail_lock(store):
        client = None
        try:
            validate_mailbox(config)
            password = store.get_secret("CAREER_SMTP_PASSWORD")
            if not password:
                raise CareerError("mail_smtp_password_required")
            client = (smtp_factory or _smtp_connection)(config)
            client.login(config["smtp_username"], password)
            code, _ = client.noop()
            if code != 250:
                raise CareerError("mail_smtp_check_failed")
            results["smtp"] = {"status": "connected", "checked_at": time.time()}
        except Exception as exc:
            results["smtp"] = {"status": "failed", "error": _error_code(exc), "checked_at": time.time()}
        finally:
            _close_smtp(client)
        if config["imap_host"]:
            results["imap"] = check_imap(store, config, imap_factory=imap_factory)
        state = load_mail_state(store)
        state["checks"] = results
        state["checks_identity"] = _account_identity(store, config)
        save_mail_state(store, state)
    return results


def _application(store: CareerStore, oid: str) -> dict[str, Any]:
    app = next((row for row in store.load_applications() if row.get("opportunity_id") == oid), {})
    if not app.get("pack_ready") or not app.get("cv_text") or not app.get("cover"):
        raise CareerError("mail_prepare_documents_first", status=409)
    return app


def _draft(store: CareerStore, oid: str, recipient: str = "") -> tuple[dict[str, Any], list[tuple[dict[str, Any], bytes]]]:
    from navin.career.export import attach_exports, document_fingerprint

    job = store.get_opportunity(oid)
    app = _application(store, oid)
    exports = app.get("exports") or {}
    digest = document_fingerprint(app)
    if any((exports.get(kind) or {}).get("source_digest") != digest for kind in ("cv_docx", "cover_docx")):
        # Re-export stored, accepted text only. A mail preview never calls an LLM.
        app = attach_exports(store, job, store.load_profile(), app)
        store.upsert_application(app)
        store.update_opportunity(oid, {key: app.get(key) for key in ("cv", "cv_text", "cover", "exports", "generation")})
    config = normalize_mailbox(store.load_profile().get("mailbox"))
    address, source = (email_address(recipient), "provided") if recipient else published_recipient(job)
    files: list[tuple[dict[str, Any], bytes]] = []
    for kind in ("cv_docx", "cover_docx"):
        export = (app.get("exports") or {}).get(kind) or {}
        if not export.get("file_id"):
            raise CareerError("mail_prepare_documents_first", status=409)
        raw = base64.b64decode(store.read_bytes(export["file_id"])["data"], validate=True)
        if not raw.startswith(b"PK"):
            raise CareerError("mail_attachment_invalid")
        files.append(({
            "kind": kind, "file_id": export["file_id"], "name": str(export.get("name") or f"{kind}.docx"),
            "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        }, raw))
    if sum(len(raw) for _, raw in files) > MAX_ATTACHMENTS_BYTES:
        raise CareerError("mail_attachments_too_large")
    language = str(app.get("language") or (app.get("cv") or {}).get("language") or "en")
    subject = f"{'Candidature' if language == 'fr' else 'Application'} - {job.get('title') or ''} [{app['id']}]"
    subject = " ".join(subject.split())[:220]
    draft = {
        "opportunity_id": oid, "application_id": app["id"], "sender": config["sender_email"],
        "sender_name": config["sender_name"], "recipient": address, "recipient_source": source,
        "subject": subject, "body": str(app["cover"]), "attachments": [meta for meta, _ in files],
        "requires_review": (app.get("generation") or {}).get("status") != "complete",
        "language": language,
        "generation": app.get("generation") or {},
    }
    draft["revision"] = _hash({**draft, "cv": app.get("cv"), "cv_text": app["cv_text"]})
    return draft, files


def mail_draft(store: CareerStore, oid: str, *, recipient: str = "") -> dict[str, Any]:
    draft, _ = _draft(store, oid, recipient)
    return draft


def _day_key(store: CareerStore, stamp: float) -> str:
    timezone = str((store.load_loop().get("schedule") or {}).get("tz") or "UTC")
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        zone = dt.timezone.utc
    return dt.datetime.fromtimestamp(stamp, zone).date().isoformat()


def _auto_permission(store: CareerStore, job: dict[str, Any], state: dict[str, Any], recipient: str) -> None:
    config = normalize_mailbox(store.load_profile().get("mailbox"))
    if not config["enabled"] or not config["auto_send"]:
        raise CareerError("mail_auto_send_disabled")
    loop = store.load_loop()
    intent = store.load_loop_intent()
    if not loop.get("enabled") or intent.get("enabled") is False:
        raise CareerError("mail_loop_paused")
    if job.get("archived") or job.get("stage") in _SENT_STAGES:
        raise CareerError("mail_application_not_eligible")
    try:
        score = float(job.get("match_score") or 0)
    except (TypeError, ValueError):
        score = 0
    if score < config["min_match_score"]:
        raise CareerError("mail_match_below_threshold")
    domains = config["allowed_recipient_domains"]
    if domains and recipient.rsplit("@", 1)[-1].lower() not in domains:
        raise CareerError("mail_recipient_domain_not_allowed")
    today = _day_key(store, time.time())
    used = sum(
        row.get("status") in {"accepted", "sending", "unknown"}
        and _day_key(store, float(row.get("accepted_at") or row.get("attempted_at") or 0)) == today
        for row in state["outbox"].values()
    )
    if used >= config["max_per_day"]:
        raise CareerError("mail_daily_limit_reached")


def _sync_receipt(store: CareerStore, record: dict[str, Any]) -> None:
    oid = str(record["opportunity_id"])
    try:
        job = store.get_opportunity(oid)
    except CareerError:
        return
    patch: dict[str, Any] = {"mail_receipt": public_receipt(record)}
    if record["status"] == "accepted":
        patch.update({
            "stage": job.get("stage") if job.get("stage") in _SENT_STAGES else "applied",
            "applied_at": record["accepted_at"], "application_email": record["recipient"],
            "application_email_source": record.get("recipient_source") or "provided",
            "next_action": "Await the employer's response. The mail server accepted the application.",
        })
    store.update_opportunity(oid, patch)
    app = next((row for row in store.load_applications() if row.get("opportunity_id") == oid), None)
    if app:
        store.upsert_application({"opportunity_id": oid, **patch})


def queue_mail_notification(state: dict[str, Any], event_id: str, *, title: str, detail: str, event_type: str) -> None:
    state["notifications"].setdefault(event_id, {
        "event_id": event_id, "event_type": event_type, "title": title,
        "detail": detail, "complete": False, "created_at": time.time(),
    })


def flush_mail_notifications(store: CareerStore) -> dict[str, Any]:
    from navin.career.notify import deliver_alert
    from navin.career.watch import digest_was_delivered

    outcomes: dict[str, Any] = {}
    with mail_lock(store):
        state = load_mail_state(store)
        for event_id, event in state["notifications"].items():
            if event.get("complete"):
                continue
            try:
                receipt = deliver_alert(
                    store, title=event["title"], detail=event["detail"], event_id=event_id,
                    event_type=event["event_type"], level="info",
                )
                event.update({"complete": digest_was_delivered(receipt), "delivery": receipt, "checked_at": time.time()})
                outcomes[event_id] = receipt
            except Exception:
                event["error"] = "mail_notification_failed"
        if outcomes or any(not event.get("complete") for event in state["notifications"].values()):
            save_mail_state(store, state)
    return outcomes


def send_application(
    store: CareerStore, oid: str, *, recipient: str = "", revision: str = "",
    reviewed: bool = False, automatic: bool = False, retry: bool = False,
    smtp_factory: SMTPFactory | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Send the exact reviewed documents once. A 250 DATA reply is the receipt."""
    with mail_lock(store):
        state = load_mail_state(store)
        prior = state["outbox"].get(oid) or {}
        if prior.get("status") in {"accepted", "sending", "unknown"}:
            # A crash during DATA is uncertain. Never silently retry that intent.
            if prior["status"] == "sending":
                prior.update({"status": "unknown", "error": "mail_acceptance_unknown"})
                save_mail_state(store, state)
            _sync_receipt(store, prior)
            return {**public_receipt(prior), "deduplicated": True}
        if prior.get("status") == "failed" and not retry and not automatic:
            return {**public_receipt(prior), "retry_required": True}
        config = normalize_mailbox(store.load_profile().get("mailbox"))
        account_identity = _account_identity(store, config)
        if not config["enabled"]:
            raise CareerError("mail_account_disabled")
        validate_mailbox(config)
        password = store.get_secret("CAREER_SMTP_PASSWORD")
        if not password:
            raise CareerError("mail_smtp_password_required")
        draft, files = _draft(store, oid, recipient)
        address = email_address(draft["recipient"])
        if automatic:
            if deadline is not None and time.monotonic() >= deadline:
                raise CareerError("mail_cycle_deadline")
            published, _ = published_recipient(store.get_opportunity(oid))
            if not published or address != published:
                raise CareerError("mail_published_recipient_required")
            _auto_permission(store, store.get_opportunity(oid), state, address)
            if draft["requires_review"]:
                raise CareerError("mail_documents_need_review", status=409)
            if int(prior.get("attempts") or 0) >= 3 or float(prior.get("retry_at") or 0) > time.time():
                raise CareerError("mail_retry_paused")
        else:
            if not revision or revision != draft["revision"]:
                raise CareerError("mail_draft_changed", status=409)
            if draft["requires_review"] and reviewed is not True:
                raise CareerError("mail_documents_need_review", status=409)
        record = {
            **draft, "status": "sending", "attempted_at": time.time(),
            "message_id": str(prior.get("message_id") or make_msgid(domain=config["sender_email"].rsplit("@", 1)[1])),
            "attempts": int(prior.get("attempts") or 0) + 1, "automatic": automatic,
        }
        message = EmailMessage(policy=policy.SMTP)
        message["From"] = formataddr((config["sender_name"], config["sender_email"]))
        message["To"], message["Reply-To"] = address, config["sender_email"]
        message["Subject"], message["Message-ID"] = draft["subject"], record["message_id"]
        message["Date"] = formatdate(record["attempted_at"], localtime=False)
        message["X-Navin-Application-ID"] = draft["application_id"]
        message.set_content(draft["body"])
        for meta, raw in files:
            main, sub = meta["mime"].split("/", 1)
            message.add_attachment(raw, maintype=main, subtype=sub, filename=meta["name"])
        wire = message.as_bytes()
        record["message_sha256"] = hashlib.sha256(wire).hexdigest()
        archive = store.save_bytes(f"{draft['application_id']}.eml", wire, file_id="mail-" + _hash(record["message_id"])[:20])
        record["message_file_id"] = archive["file_id"]
        state["outbox"][oid] = record
        save_mail_state(store, state)
        client, data_started = None, False
        try:
            client = (smtp_factory or _smtp_connection)(config)
            client.login(config["smtp_username"], password)
            code, _ = client.mail(config["sender_email"])
            if code != 250:
                raise CareerError("mail_sender_refused")
            code, _ = client.rcpt(address)
            if code not in {250, 251}:
                record["smtp_code"] = code
                raise CareerError("mail_recipient_refused")
            # Recheck persisted permissions at the last point before DATA.
            latest = normalize_mailbox(store.load_profile().get("mailbox"))
            if not latest["enabled"] or _account_identity(store, latest) != account_identity:
                raise CareerError("mail_configuration_changed")
            if automatic:
                if deadline is not None and time.monotonic() >= deadline:
                    raise CareerError("mail_cycle_deadline")
                # This in-flight intent consumes its slot only after this check.
                without_current = {**state, "outbox": {key: row for key, row in state["outbox"].items() if key != oid}}
                _auto_permission(store, store.get_opportunity(oid), without_current, address)
            data_started = True
            code, reply = client.data(wire)
            record["smtp_code"] = code
            if code != 250:
                data_started = False
                raise CareerError("mail_message_refused")
            record.update({"status": "accepted", "accepted_at": time.time(), "delivery_status": "server_accepted"})
            # Receipt text is limited to a server acknowledgement, never credentials.
            reply_text = reply.decode("utf-8", "replace") if isinstance(reply, bytes) else str(reply)
            record["smtp_reply"] = " ".join(reply_text.split()).replace(password, "[redacted]")[:300]
            record.pop("error", None)
        except Exception as exc:
            certain_refusal = isinstance(exc, smtplib.SMTPResponseException)
            record["status"] = "unknown" if data_started and not certain_refusal else "failed"
            record["error"] = "mail_acceptance_unknown" if record["status"] == "unknown" else _error_code(exc)
            if isinstance(exc, smtplib.SMTPResponseException):
                record["smtp_code"] = exc.smtp_code
            record["retry_at"] = time.time() + min(86400, 300 * 2 ** min(record["attempts"], 6))
        finally:
            # Persist acceptance before QUIT: a disconnected QUIT cannot undo a 250.
            save_mail_state(store, state)
            _close_smtp(client)
        if record["status"] == "accepted":
            queue_mail_notification(
                state, f"application_sent:{record['application_id']}:{record['message_id']}",
                title="Candidature envoyée" if draft["language"] == "fr" else "Application sent",
                detail=f"{draft['subject']}\n{address}\nMessage-ID: {record['message_id']}", event_type="application_sent",
            )
            save_mail_state(store, state)
        _sync_receipt(store, record)
        store.append_journal({"kind": "mail_send", "text": f"{oid}: {record['status']}", "message_id": record["message_id"]})
        result = public_receipt(record)
    with suppress(CareerError):
        flush_mail_notifications(store)
    return result


def run_mail_cycle(store: CareerStore, *, deadline: float | None = None) -> dict[str, Any]:
    """Daily hunt continuation. Default profiles cannot connect or send."""
    from navin.career.desk import prepare_application

    config = normalize_mailbox(store.load_profile().get("mailbox"))
    result: dict[str, Any] = {"sent": 0, "skipped": [], "receipts": []}
    if not config["enabled"] or not config["auto_send"]:
        return {**result, "reason": "mail_auto_send_disabled"}
    rows = sorted(store.load_opportunities(), key=lambda row: float(row.get("match_score") or 0), reverse=True)
    for job in rows:
        if deadline is not None and time.monotonic() >= deadline:
            result["reason"] = "mail_cycle_deadline"
            break
        oid = str(job.get("id") or "")
        state = load_mail_state(store)
        if (state["outbox"].get(oid) or {}).get("status") in {"accepted", "sending", "unknown"}:
            continue
        try:
            recipient, _ = published_recipient(job)
            if not recipient:
                continue
            _auto_permission(store, job, state, recipient)
            prepare_application(store, oid)
            receipt = send_application(store, oid, recipient=recipient, automatic=True, deadline=deadline)
            result["receipts"].append(receipt)
            result["sent"] += receipt.get("status") == "accepted"
        except CareerError as exc:
            result["skipped"].append({"id": oid, "reason": _error_code(exc)})
            if exc.message in {"mail_auto_send_disabled", "mail_loop_paused", "mail_daily_limit_reached", "mail_busy", "mail_cycle_deadline"}:
                break
        except Exception:
            result["skipped"].append({"id": oid, "reason": "mail_document_error"})
        if result["sent"] >= config["max_per_day"]:
            break
    return result
