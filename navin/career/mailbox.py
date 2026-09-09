"""Read only replies to recorded Career messages, with IMAP UID checkpoints."""

from __future__ import annotations

import datetime as dt
import hashlib
import imaplib
import re
import ssl
import time
from contextlib import suppress
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable

from navin.career.errors import CareerError
from navin.career.mail import (
    TRANSPORT_TIMEOUT_S,
    _error_code,
    _hash,
    flush_mail_notifications,
    load_mail_state,
    mail_lock,
    queue_mail_notification,
    save_mail_state,
)
from navin.career.mail_settings import normalize_mailbox, validate_mailbox
from navin.career.sources import html_to_text
from navin.career.store import CareerStore

IMAPFactory = Callable[[dict[str, Any]], Any]
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_MESSAGES_PER_SYNC = 100
MESSAGE_ID = re.compile(r"<[^<>\s]{1,250}>")


def _imap_connection(config: dict[str, Any]) -> Any:
    context = ssl.create_default_context()
    if config["imap_security"] == "ssl":
        return imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"], ssl_context=context, timeout=TRANSPORT_TIMEOUT_S)
    client = imaplib.IMAP4(config["imap_host"], config["imap_port"], timeout=TRANSPORT_TIMEOUT_S)
    try:
        client.starttls(ssl_context=context)
        return client
    except Exception:
        with suppress(Exception):
            client.logout()
        raise


def _select(client: Any, config: dict[str, Any]) -> None:
    folder = '"' + str(config["imap_folder"]).replace("\\", "\\\\").replace('"', '\\"') + '"'
    status, _ = client.select(folder, readonly=True)
    if status != "OK":
        raise CareerError("mail_imap_folder_unavailable")


def _open(store: CareerStore, config: dict[str, Any], factory: IMAPFactory | None) -> Any:
    validate_mailbox(config, protocol="imap")
    password = store.get_secret("CAREER_IMAP_PASSWORD")
    if not password:
        raise CareerError("mail_imap_password_required")
    client = (factory or _imap_connection)(config)
    try:
        status, _ = client.login(config["imap_username"], password)
        if status != "OK":
            raise CareerError("mail_authentication_failed")
        _select(client, config)
        return client
    except Exception:
        with suppress(Exception):
            client.logout()
        raise


def check_imap(store: CareerStore, config: dict[str, Any], *, imap_factory: IMAPFactory | None = None) -> dict[str, Any]:
    client = None
    try:
        client = _open(store, config, imap_factory)
        return {"status": "connected", "checked_at": time.time()}
    except Exception as exc:
        error = "mail_authentication_failed" if isinstance(exc, imaplib.IMAP4.error) else _error_code(exc)
        return {"status": "failed", "error": error, "checked_at": time.time()}
    finally:
        if client is not None:
            with suppress(Exception):
                client.logout()


def _literal(data: Any) -> bytes:
    return b"\n".join(item[1] for item in data or [] if isinstance(item, tuple) and isinstance(item[1], bytes))


def _message_text(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        text = str(part.get_content())
    except (LookupError, UnicodeError):
        text = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = html_to_text(text)
    return text.strip()[:50000]


def classify_reply(text: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ("unfortunately", "not selected", "not been selected", "regret", "refus", "not retained", "ne pas donner suite")):
        return "rejected"
    if any(token in lower for token in ("interview", "entretien", "call next", "availability for a call", "disponibilités pour un échange")):
        return "interview"
    if any(token in lower for token in ("offer letter", "job offer", "employment offer", "offre d'emploi", "proposition d'embauche", "offre de contrat")):
        return "offer"
    if any(token in lower for token in ("tjm", "daily rate", "salary", "please provide", "pouvez-vous", "merci de nous transmettre")):
        return "need_response"
    if any(token in lower for token in ("pleased", "interested", "next step", "intéress", "prochaine étape")):
        return "positive"
    return "waiting"


def _thread(message: EmailMessage, outbox: dict[str, Any]) -> dict[str, Any] | None:
    references = set(MESSAGE_ID.findall(" ".join(str(message.get(key) or "") for key in ("In-Reply-To", "References"))))
    rows = [row for row in outbox.values() if row.get("status") in {"accepted", "unknown", "sending"}]
    matches = [row for row in rows if row.get("message_id") in references]
    if len(matches) == 1:
        return matches[0]
    if references:
        return None
    sender = parseaddr(str(message.get("From") or ""))[1].casefold()
    subject = str(message.get("Subject") or "")
    # Some recruiters start a new thread. The exact application token AND known
    # recipient are required; a shared company address alone is ambiguous.
    matches = [row for row in rows if sender == str(row.get("recipient") or "").casefold()
               and f"[{row.get('application_id')}]" in subject]
    return matches[0] if len(matches) == 1 else None


def _record_reply(store: CareerStore, state: dict[str, Any], account: str, uid: int, raw: bytes, record: dict[str, Any]) -> bool:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    parsed_id = MESSAGE_ID.findall(str(message.get("Message-ID") or ""))
    message_id = parsed_id[0] if len(parsed_id) == 1 else "sha256:" + hashlib.sha256(raw).hexdigest()
    # Deduplication survives UIDVALIDITY changes and moved/reimported messages.
    identity = _hash([record["application_id"], message_id])
    items = store.load_inbox()
    existing = next((item for item in items if item.get("mail_identity") == identity), None)
    if existing:
        return False
    body = _message_text(message)
    subject = str(message.get("Subject") or "")[:500]
    automatic = str(message.get("Auto-Submitted") or "no").lower() != "no"
    bounced = message.get_content_type() == "multipart/report" or bool(message.get("X-Failed-Recipients"))
    classification = "waiting" if automatic or bounced else classify_reply(subject + "\n" + body)
    received_at = time.time()
    with suppress(ValueError, TypeError, OverflowError):
        received_at = min(time.time(), parsedate_to_datetime(str(message.get("Date") or "")).timestamp())
    item = {
        "id": "in-mail-" + identity[:20], "mail_identity": identity, "message_id": message_id,
        "opportunity_id": record["opportunity_id"], "application_id": record["application_id"],
        "sender": str(message.get("From") or "")[:500], "subject": subject, "body": body,
        "classification": classification, "received_at": received_at, "synced_at": time.time(),
        "source": "imap", "imap_uid": uid, "mail_account": account, "in_reply_to": record["message_id"],
        "automatic_reply": automatic, "delivery_report": bounced,
    }
    store.save_inbox([item, *items])
    oid = record["opportunity_id"]
    try:
        job = store.get_opportunity(oid)
        patch: dict[str, Any] = {"last_reply_at": received_at}
        if bounced:
            record["delivery_status"] = "delivery_report"
            patch["mail_receipt"] = {**(job.get("mail_receipt") or {}), "delivery_status": "delivery_report"}
            patch["next_action"] = "Review the mail server's delivery report."
        elif not automatic:
            stage = {"interview": "interview", "offer": "offer", "rejected": "rejected"}.get(classification, "replied")
            if job.get("stage") not in {"won", "offer", "interview"} or stage in {"offer", "interview", "rejected"}:
                patch["stage"] = stage
        store.update_opportunity(oid, patch)
        if any(row.get("opportunity_id") == oid for row in store.load_applications()):
            store.upsert_application({"opportunity_id": oid, **patch})
    except CareerError:
        pass
    queue_mail_notification(
        state, f"application_reply:{message_id}", title="Réponse à une candidature" if str((record.get("generation") or {}).get("language") or record.get("language")) == "fr" else "Application reply",
        detail=f"{item['sender']}\n{subject}\n{body[:1200]}", event_type="application_reply",
    )
    return True


def sync_mailbox(
    store: CareerStore, *, force: bool = True, imap_factory: IMAPFactory | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    config = normalize_mailbox(store.load_profile().get("mailbox"))
    clock = time.time() if now is None else now
    if not config["enabled"] or not config["read_replies"]:
        return {"status": "disabled", "received": 0, "reason": "mail_reply_sync_disabled"}
    result: dict[str, Any] = {"status": "complete", "received": 0, "scanned": 0, "skipped_large": 0, "checked_at": clock}
    with mail_lock(store):
        state = load_mail_state(store)
        last = state.get("last_sync") or {}
        if not force and float(last.get("next_due") or 0) > clock:
            return {"status": "waiting", "received": 0, "next_due": last["next_due"]}
        account = _hash({key: config[key] for key in ("imap_host", "imap_port", "imap_username", "imap_folder")})
        checkpoint = state["mailboxes"].setdefault(account, {})
        client = None
        try:
            client = _open(store, config, imap_factory)
            _, validity_data = client.response("UIDVALIDITY")
            validity = next((value.decode("ascii", "replace") for value in validity_data or [] if isinstance(value, bytes)), "")
            if not validity.isdigit():
                raise CareerError("mail_imap_uidvalidity_missing")
            if checkpoint.get("uidvalidity") != validity:
                checkpoint.update({"uidvalidity": validity, "last_uid": 0})
            first_uid = int(checkpoint.get("last_uid") or 0) + 1
            sent_times = [float(row.get("attempted_at") or 0) for row in state["outbox"].values()]
            since = dt.datetime.fromtimestamp(max(0, min(sent_times or [clock]) - 86400), dt.timezone.utc)
            months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
            since_arg = f"{since.day:02d}-{months[since.month - 1]}-{since.year}"
            status, data = client.uid("search", None, "UID", f"{first_uid}:*", "SINCE", since_arg)
            if status != "OK":
                raise CareerError("mail_imap_search_failed")
            uids = sorted({int(value) for part in data or [] if isinstance(part, bytes) for value in part.split() if value.isdigit() and int(value) >= first_uid})
            for uid in uids[:MAX_MESSAGES_PER_SYNC]:
                latest = normalize_mailbox(store.load_profile().get("mailbox"))
                if not latest["enabled"] or not latest["read_replies"] or _hash(latest) != _hash(config):
                    raise CareerError("mail_configuration_changed")
                status, header_data = client.uid("fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES FROM SUBJECT)] RFC822.SIZE)")
                if status != "OK":
                    raise CareerError("mail_imap_fetch_failed")
                headers = BytesParser(policy=policy.default).parsebytes(_literal(header_data))
                record = _thread(headers, state["outbox"])
                if record:
                    size_match = re.search(rb"RFC822\.SIZE\s+(\d+)", b" ".join(part[0] for part in header_data or [] if isinstance(part, tuple)))
                    if size_match and int(size_match[1]) > MAX_MESSAGE_BYTES:
                        result["skipped_large"] += 1
                    else:
                        status, message_data = client.uid("fetch", str(uid), f"(BODY.PEEK[]<0.{MAX_MESSAGE_BYTES + 1}>)")
                        if status != "OK":
                            raise CareerError("mail_imap_fetch_failed")
                        raw = _literal(message_data)
                        if len(raw) > MAX_MESSAGE_BYTES:
                            result["skipped_large"] += 1
                        elif raw and _record_reply(store, state, account, uid, raw, record):
                            result["received"] += 1
                result["scanned"] += 1
                checkpoint["last_uid"] = uid
                # Cursor is persisted only after the related reply and its event.
                save_mail_state(store, state)
            result["has_more"] = len(uids) > MAX_MESSAGES_PER_SYNC
        except Exception as exc:
            result.update({"status": "failed", "error": "mail_imap_command_failed" if isinstance(exc, imaplib.IMAP4.error) else _error_code(exc)})
        finally:
            if client is not None:
                with suppress(Exception):
                    client.logout()
            result["next_due"] = clock + (60 if result.get("has_more") else config["poll_interval_minutes"] * 60)
            state["last_sync"] = result
            save_mail_state(store, state)
    with suppress(CareerError):
        flush_mail_notifications(store)
    return result


def poll_replies(store: CareerStore) -> dict[str, Any]:
    """Trusted gateway/loop polling; does not authorize outbound applications."""
    try:
        result = sync_mailbox(store, force=False)
        with suppress(CareerError):
            flush_mail_notifications(store)
        return result
    except CareerError as exc:
        return {"status": "busy" if exc.message == "mail_busy" else "failed", "error": _error_code(exc), "received": 0}
