"""Provider-neutral mail and calendar operations with durable approval and receipts."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from typing import Any

from navin.accounts.oauth import access_token
from navin.accounts.providers import capabilities, request
from navin.accounts.store import AccountError, AccountStore

GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
GRAPH = "https://graph.microsoft.com/v1.0/me"
CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"
PERMISSIONS = {"mail.search": "read_mail", "mail.read": "read_mail", "mail.attachment": "read_mail",
               "mail.draft": "draft_mail", "mail.send": "send_mail", "mail.reply": "send_mail",
               "mail.archive": "archive_mail", "mail.delete": "delete_mail", "calendar.list": "read_calendar",
               "calendar.create": "write_calendar", "calendar.update": "write_calendar",
               "calendar.cancel": "cancel_events", "contacts.list": "read_contacts"}
READ_ACTIONS = {"mail.search", "mail.read", "mail.attachment", "calendar.list", "contacts.list"}


def api(store: AccountStore, account: dict[str, Any], method: str, url: str, **kwargs):
    return request(method, url, token=access_token(store, account["id"]), **kwargs)


def raw_message(store: AccountStore, account_id: str, message_id: str) -> bytes:
    account = store.require(account_id, "read_mail")
    encoded_id = urllib.parse.quote(message_id, safe="")
    if account["provider"] == "google":
        data = api(store, account, "GET", GMAIL + "/messages/" + encoded_id + "?format=raw")
        raw = str(data.get("raw") or "")
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    return api(store, account, "GET", GRAPH + "/messages/" + encoded_id + "/$value", raw=True)


def send_mime(store: AccountStore, account_id: str, raw: bytes, *, approved: bool = False) -> dict[str, Any]:
    account = store.account(account_id) if approved else store.require(account_id, "send_mail")
    if not capabilities(account["provider"], account["scopes"])["send_mail"]:
        raise AccountError("Reconnect this account with mail sending permission.", 403)
    if len(raw) > (3 * 1024 * 1024 if account["provider"] == "microsoft" else 24 * 1024 * 1024):
        raise AccountError("Attachments exceed the provider API size limit.", 413)
    if account["provider"] == "google":
        result = api(store, account, "POST", GMAIL + "/messages/send", data={"raw": base64.urlsafe_b64encode(raw).decode()})
    else:
        result = api(store, account, "POST", GRAPH + "/sendMail", data=base64.b64encode(raw))
    return {"status": "accepted", "provider_id": result.get("id", "")}


def _message(account: dict[str, Any], body: dict[str, Any], reply: EmailMessage | None = None) -> EmailMessage:
    from navin.career.mail_settings import email_address

    message = EmailMessage(policy=policy.SMTP)
    message["From"] = account["email"]
    message["To"] = email_address(body.get("to"))
    if body.get("cc"):
        message["Cc"] = ", ".join(email_address(address) for address in body["cc"])
    message["Subject"] = str(body.get("subject") or "")[:500]
    if reply:
        message["In-Reply-To"] = str(reply.get("Message-ID") or "")
        message["References"] = str(reply.get("Message-ID") or "")
    message.set_content(str(body.get("text") or ""))
    return message


def _date(value: Any) -> str:
    from datetime import datetime

    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
    except ValueError:
        raise AccountError("Provide an ISO date with an explicit timezone.") from None
    return text


def _perform(store: AccountStore, account: dict[str, Any], action: str, body: dict[str, Any], *, approved: bool) -> Any:
    if not approved:
        account = store.require(account["id"], PERMISSIONS[action])
    google = account["provider"] == "google"
    message_id = urllib.parse.quote(str(body.get("message_id") or ""), safe="")
    query = urllib.parse.urlencode
    if action == "mail.search":
        term, cursor = str(body.get("query") or "")[:1000], str(body.get("cursor") or "")
        if google:
            return api(store, account, "GET", GMAIL + "/messages?" + query({"q": term, "maxResults": 25, **({"pageToken": cursor} if cursor else {})}))
        params = {"$top": 25, "$select": "id,subject,from,receivedDateTime,bodyPreview,internetMessageId"}
        if term:
            params["$search"] = '"' + term.replace('"', '') + '"'
        if cursor:
            params["$skiptoken"] = cursor
        return api(store, account, "GET", GRAPH + "/messages?" + query(params))
    if action in {"mail.read", "mail.attachment"}:
        raw = raw_message(store, account["id"], str(body.get("message_id") or ""))
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        attachments = [part for part in msg.walk() if part.get_filename()]
        if action == "mail.attachment":
            index = int(body.get("index") or 0)
            if not 0 <= index < len(attachments):
                raise AccountError("Attachment not found.", 404)
            part = attachments[index]
            return {"name": part.get_filename(), "mime": part.get_content_type(), "data_b64": base64.b64encode(part.get_payload(decode=True) or b"").decode()}
        text = "\n".join(str(part.get_content()) for part in msg.walk() if part.get_content_type() == "text/plain" and not part.get_filename())
        return {"message_id": body["message_id"], "subject": str(msg.get("Subject") or ""), "from": str(msg.get("From") or ""),
                "to": str(msg.get("To") or ""), "text": text[:40000],
                "attachments": [{"index": i, "name": p.get_filename(), "mime": p.get_content_type()} for i, p in enumerate(attachments)]}
    if action in {"mail.send", "mail.reply", "mail.draft"}:
        reply = BytesParser(policy=policy.default).parsebytes(raw_message(store, account["id"], body["message_id"])) if body.get("message_id") else None
        message = _message(account, body, reply)
        if action != "mail.draft":
            return send_mime(store, account["id"], message.as_bytes(), approved=approved)
        if google:
            return api(store, account, "POST", GMAIL + "/drafts", data={"message": {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}})
        return api(store, account, "POST", GRAPH + "/messages", data=base64.b64encode(message.as_bytes()))
    if action in {"mail.archive", "mail.delete"}:
        if not message_id:
            raise AccountError("A message ID is required.")
        if google:
            return api(store, account, "POST", GMAIL + "/messages/" + message_id + ("/trash" if action == "mail.delete" else "/modify"),
                       data={} if action == "mail.delete" else {"removeLabelIds": ["INBOX"]})
        return api(store, account, "POST", GRAPH + "/messages/" + message_id + "/move", data={"destinationId": "deleteditems" if action == "mail.delete" else "archive"})
    if action == "contacts.list":
        return api(store, account, "GET", "https://people.googleapis.com/v1/people/me/connections?personFields=names,emailAddresses,phoneNumbers&pageSize=100" if google else GRAPH + "/contacts?$top=100&$select=displayName,emailAddresses,businessPhones")
    if action == "calendar.list":
        start, end = _date(body.get("start")), _date(body.get("end"))
        params = {"timeMin": start, "timeMax": end, "singleEvents": "true", "maxResults": 100} if google else {"startDateTime": start, "endDateTime": end, "$top": 100}
        return api(store, account, "GET", (CALENDAR + "/events" if google else GRAPH + "/calendarView") + "?" + query(params))
    event_id = urllib.parse.quote(str(body.get("event_id") or ""), safe="")
    if action in {"calendar.update", "calendar.cancel"} and not event_id:
        raise AccountError("An event ID is required.")
    url = (CALENDAR if google else GRAPH) + "/events" + ("/" + event_id if event_id else "")
    if action == "calendar.cancel":
        return api(store, account, "DELETE", url)
    if action in {"calendar.create", "calendar.update"}:
        from datetime import datetime, timezone

        start, end = _date(body.get("start")), _date(body.get("end"))
        if datetime.fromisoformat(end.replace("Z", "+00:00")) <= datetime.fromisoformat(start.replace("Z", "+00:00")):
            raise AccountError("The end must be after the start.")
        if google:
            event = {"summary": str(body.get("title") or ""), "description": str(body.get("description") or ""),
                     "start": {"dateTime": start}, "end": {"dateTime": end}}
        else:
            def utc(value):
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
            event = {"subject": str(body.get("title") or ""), "body": {"contentType": "text", "content": str(body.get("description") or "")},
                     "start": {"dateTime": utc(start), "timeZone": "UTC"}, "end": {"dateTime": utc(end), "timeZone": "UTC"}}
        # Calendar writes create personal events. Invitations require a separately reviewed email.
        return api(store, account, "POST" if action == "calendar.create" else "PATCH", url, data=event)
    raise AccountError("Unknown action.")


def execute(store: AccountStore, account_id: str, action: str, body: dict[str, Any], *, approved: bool = False) -> Any:
    if action not in PERMISSIONS:
        raise AccountError("Unknown action.")
    account = store.account(account_id)
    permission = PERMISSIONS[action]
    if not capabilities(account["provider"], account["scopes"]).get(permission):
        raise AccountError("Reconnect the account to grant this provider permission.", 403)
    if action in READ_ACTIONS:
        store.require(account_id, permission)
        return _perform(store, account, action, body, approved=False)
    request_id = str(body.get("request_id") or "")
    if not request_id or len(request_id) > 200:
        raise AccountError("A stable request ID is required.")
    key = hashlib.sha256((account_id + request_id).encode()).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([action, body], sort_keys=True).encode()).hexdigest()
    with store.lock():
        data = store.load()
        previous = data["operations"].get(key)
        if previous and previous["fingerprint"] != fingerprint:
            raise AccountError("This request ID is already associated with a different action.", 409)
        if previous and previous["status"] in {"accepted", "sending", "unknown", "rejected", "failed"}:
            return {k: previous[k] for k in ("status", "result", "error") if k in previous}
        entry = {"id": key, "account_id": account_id, "action": action, "body": body,
                 "fingerprint": fingerprint, "at": time.time(), "status": "pending"}
        if not approved and not account["permissions"].get(permission):
            data["operations"][key] = entry
            store.save(data)
            return {"status": "approval_required", "approval_id": key}
        entry["status"] = "sending"
        data["operations"][key] = entry
        store.save(data)
    try:
        result = _perform(store, account, action, body, approved=approved)
        entry.update({"status": "accepted", "result": result})
    except Exception as exc:
        # Do not blindly repeat a write whose response was lost.
        certain = isinstance(exc, AccountError) and 400 <= exc.status < 500
        entry.update({"status": "failed" if certain else "unknown", "error": str(exc) if isinstance(exc, AccountError) else "The operation could not be confirmed."})
    with store.lock():
        data = store.load()
        data["operations"][key] = entry
        store.save(data)
    return {k: entry[k] for k in ("status", "result", "error") if k in entry}
