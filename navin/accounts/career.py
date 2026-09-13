"""Connect the existing Career outbox and reply state machine to official mail APIs."""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from email import policy
from email.parser import BytesParser
from typing import Any

from navin.accounts.oauth import access_token
from navin.accounts.service import GMAIL, GRAPH, api, raw_message, send_mime
from navin.accounts.store import AccountError, AccountStore


class ApiMailTransport:
    """Envelope compatibility for the Career outbox; all I/O uses HTTPS APIs."""
    def __init__(self, config: dict[str, Any], *, approved: bool = False):
        self.store, self.config, self.approved = AccountStore(), config, approved

    def login(self, *args):
        if not self.approved:
            self.store.require(self.config["account_id"], "send_mail")
        access_token(self.store, self.config["account_id"])

    def mail(self, address):
        return 250, b"API envelope ready"

    def rcpt(self, address):
        return 250, b"API envelope ready"

    def data(self, raw):
        send_mime(self.store, self.config["account_id"], raw, approved=self.approved)
        return 250, b"Accepted by provider API"

    def quit(self):
        pass


def sync_replies(store, config: dict[str, Any], *, force: bool = True, now: float | None = None,
                 deadline: float | None = None) -> dict[str, Any]:
    from navin.career.mail import load_mail_state, mail_lock, save_mail_state
    from navin.career.mailbox import _record_reply, _thread

    accounts = AccountStore()
    clock = time.time() if now is None else now
    result = {"status": "complete", "received": 0, "scanned": 0, "checked_at": clock}
    with mail_lock(store):
        state = load_mail_state(store)
        last = state.get("last_sync") or {}
        if not force and float(last.get("next_due") or 0) > clock:
            return {"status": "waiting", "received": 0, "next_due": last["next_due"]}
        checkpoint = state["mailboxes"].setdefault("oauth:" + config["account_id"], {})
        seen = set(checkpoint.get("seen", []))
        try:
            account = accounts.require(config["account_id"], "read_mail")
            google = account["provider"] == "google"
            # Freeze the window while paginating; only advance after its final page.
            if not checkpoint.get("window"):
                checkpoint["window"] = clock
            since = float(checkpoint.get("completed_at") or min([r.get("attempted_at", clock) for r in state["outbox"].values()] or [clock])) - 86400
            cursor = checkpoint.get("cursor", "")
            if google:
                params = {"q": f"after:{int(since)} before:{int(checkpoint['window']) + 1} -in:sent -in:drafts", "maxResults": 25}
                if cursor:
                    params["pageToken"] = cursor
                url = GMAIL + "/messages?" + urllib.parse.urlencode(params)
            elif cursor:
                if not cursor.startswith(GRAPH + "/mailFolders/inbox/messages?"):
                    raise AccountError("Invalid mailbox cursor.")
                url = cursor
            else:
                from datetime import datetime, timezone

                def iso(value):
                    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
                url = GRAPH + "/mailFolders/inbox/messages?" + urllib.parse.urlencode({"$top": 25, "$select": "id",
                    "$filter": f"receivedDateTime ge {iso(since)} and receivedDateTime le {iso(checkpoint['window'])}"})
            page = api(accounts, account, "GET", url)
            rows = page.get("messages" if google else "value", [])
            for row in rows:
                if deadline is not None and time.monotonic() >= deadline:
                    result["status"] = "partial"
                    break
                message_id = row["id"]
                if message_id in seen:
                    continue
                latest = store.load_profile().get("mailbox", {})
                if not latest.get("read_replies") or not latest.get("enabled") or latest.get("account_id") != config["account_id"]:
                    raise AccountError("Mailbox synchronization is paused.")
                raw = raw_message(accounts, account["id"], message_id)
                message = BytesParser(policy=policy.default).parsebytes(raw)
                record = _thread(message, state["outbox"])
                uid = int(hashlib.sha256(message_id.encode()).hexdigest()[:12], 16)
                if record and _record_reply(store, state, "oauth:" + account["id"], uid, raw, record):
                    result["received"] += 1
                seen.add(message_id)
                result["scanned"] += 1
                checkpoint["seen"] = list(seen)[-5000:]
                save_mail_state(store, state)
            if result["status"] == "complete":
                checkpoint["cursor"] = page.get("nextPageToken" if google else "@odata.nextLink", "")
                if not checkpoint["cursor"]:
                    checkpoint["completed_at"] = checkpoint.pop("window")
                result["has_more"] = bool(checkpoint["cursor"])
        except Exception as exc:
            result.update({"status": "failed", "error": str(exc) if isinstance(exc, AccountError) else "Mailbox synchronization failed."})
        result["next_due"] = clock + (60 if result.get("has_more") or result["status"] == "partial" else config["poll_interval_minutes"] * 60)
        state["last_sync"] = result
        save_mail_state(store, state)
    return result
