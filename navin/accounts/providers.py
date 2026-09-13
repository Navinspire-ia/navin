"""Official provider endpoints and bounded HTTP calls without token redirects."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from navin.accounts.store import AccountError

GOOGLE = "https://www.googleapis.com/auth/"
PROVIDERS = {
    "google": {"name": "Google", "auth": "https://accounts.google.com/o/oauth2/v2/auth",
               "token": "https://oauth2.googleapis.com/token", "identity": "https://openidconnect.googleapis.com/v1/userinfo",
               "base_scopes": ["openid", "email", "profile"],
               "scopes": {"read_mail": GOOGLE + "gmail.readonly", "draft_mail": GOOGLE + "gmail.compose",
                          "send_mail": GOOGLE + "gmail.send", "archive_mail": GOOGLE + "gmail.modify",
                          "delete_mail": GOOGLE + "gmail.modify", "read_calendar": GOOGLE + "calendar.readonly",
                          "write_calendar": GOOGLE + "calendar.events", "cancel_events": GOOGLE + "calendar.events",
                          "read_contacts": GOOGLE + "contacts.readonly"}},
    "microsoft": {"name": "Microsoft", "auth": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                  "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                  "identity": "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName",
                  "base_scopes": ["openid", "profile", "offline_access", "User.Read"],
                  "scopes": {"read_mail": "Mail.Read", "draft_mail": "Mail.ReadWrite", "send_mail": "Mail.Send",
                             "archive_mail": "Mail.ReadWrite", "delete_mail": "Mail.ReadWrite",
                             "read_calendar": "Calendars.Read", "write_calendar": "Calendars.ReadWrite",
                             "cancel_events": "Calendars.ReadWrite", "read_contacts": "Contacts.Read"}},
}


def registration(provider: str) -> dict[str, str]:
    if provider not in PROVIDERS:
        raise AccountError("Unknown provider.")
    # Publisher configuration, injected into the installed desktop build. Never a user form.
    from navin.accounts.publisher import configuration

    try:
        return configuration()[provider]
    except (OSError, ValueError):
        raise AccountError("Navin's publisher OAuth configuration is invalid. Contact Navinspire.", 503) from None


def scopes_for(provider: str, permissions: dict[str, bool]) -> list[str]:
    spec = PROVIDERS[provider]
    return sorted(set(spec["base_scopes"] + [scope for key, scope in spec["scopes"].items() if permissions.get(key)]))


def capabilities(provider: str, scopes: list[str]) -> dict[str, bool]:
    granted = set(scopes)
    if provider == "microsoft":
        granted.update(s.rsplit("/", 1)[-1] for s in scopes)
        if "Mail.ReadWrite" in granted:
            granted.add("Mail.Read")
        if "Calendars.ReadWrite" in granted:
            granted.add("Calendars.Read")
    else:
        if GOOGLE + "gmail.modify" in granted:
            granted.update(GOOGLE + "gmail." + s for s in ("readonly", "compose", "send"))
        if GOOGLE + "gmail.compose" in granted:
            granted.add(GOOGLE + "gmail.send")
        if GOOGLE + "calendar.events" in granted:
            granted.add(GOOGLE + "calendar.readonly")
    return {key: scope in granted for key, scope in PROVIDERS[provider]["scopes"].items()}


class ProviderError(AccountError):
    def __init__(self, status: int):
        super().__init__("The provider rejected the request (HTTP " + str(status) + ").", status)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(method: str, url: str, *, token: str = "", data: Any = None,
            form: bool = False, raw: bool = False, headers: dict[str, str] | None = None) -> Any:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in {
        "oauth2.googleapis.com", "openidconnect.googleapis.com", "gmail.googleapis.com",
        "www.googleapis.com", "people.googleapis.com", "graph.microsoft.com", "login.microsoftonline.com"
    }:
        raise AccountError("Provider URL is not allowed.")
    head = {"Accept": "application/json", **(headers or {})}
    if token:
        head["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        if isinstance(data, bytes):
            body = data
            head["Content-Type"] = "text/plain"
        elif form:
            body = urllib.parse.urlencode(data).encode()
            head["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            head["Content-Type"] = "application/json"
    try:
        with urllib.request.build_opener(_NoRedirect()).open(
            urllib.request.Request(url, data=body, headers=head, method=method), timeout=20
        ) as response:
            content = response.read(34 * 1024 * 1024 + 1)
        if len(content) > 34 * 1024 * 1024:
            raise AccountError("Provider response exceeds the size limit.", 413)
        return content if raw else json.loads(content) if content else {}
    except urllib.error.HTTPError as exc:
        raise ProviderError(exc.code) from None
    except (OSError, ValueError):
        raise AccountError("Provider temporarily unavailable. Try again later.", 503) from None
