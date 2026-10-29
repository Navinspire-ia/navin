# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How Navin talks to each connector's API.

One :class:`Spec` per catalog app: the API base URL, where the credential
goes (a header, the query string, HTTP Basic or Digest), the headers every
call needs (API versions), and the cheap call that proves a credential works
and names the account. Ported from OpenConnector's provider executors and
credential validators (modified Apache-2.0, Copyright 2026 OOMOL).

Templates use the connection values: ``{apiKey}``, the extra fields of the
method (``{subdomain}``, ``{apiSecret}``...), ``{accessToken}`` for OAuth and
what :func:`prepare` derives. ``{a|b}`` takes the first non-empty value.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from navin.connectors.catalog import ConnectorError

AuthRule = tuple[str, str, str]  # (header | query | basic | digest, name, template)


def header(name: str, template: str) -> AuthRule:
    return ("header", name, template)


def query(name: str, template: str = "{apiKey}") -> AuthRule:
    return ("query", name, template)


def basic(user: str, password: str) -> AuthRule:
    return ("basic", user, password)


def digest(user: str, password: str) -> AuthRule:
    return ("digest", user, password)


BEARER_KEY: tuple[AuthRule, ...] = (header("Authorization", "Bearer {apiKey}"),)
BEARER_TOKEN: tuple[AuthRule, ...] = (header("Authorization", "Bearer {accessToken}"),)
GRAPH = "https://graph.microsoft.com/v1.0"
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
DATADOG_SITES = {
    "us1": "https://api.datadoghq.com", "us3": "https://api.us3.datadoghq.com",
    "us5": "https://api.us5.datadoghq.com", "eu": "https://api.datadoghq.eu",
    "ap1": "https://api.ap1.datadoghq.com", "ap2": "https://api.ap2.datadoghq.com",
    "uk1": "https://api.uk1.datadoghq.com", "gov": "https://api.ddog-gov.com",
    "gov2": "https://api.us2.ddog-gov.com",
}


@dataclass(frozen=True)
class Verify:
    """The call that proves a credential: path (relative or absolute) and the account fields."""

    path: str = ""
    method: str = "GET"
    json: Any = None
    raw: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    id: str = ""
    name: str = ""
    #: Extra statuses that still prove the credential (a known-missing id answering 404).
    accept: tuple[int, ...] = ()


@dataclass(frozen=True)
class Spec:
    base: str
    key_auth: tuple[AuthRule, ...] = BEARER_KEY
    oauth_auth: tuple[AuthRule, ...] = BEARER_TOKEN
    headers: dict[str, str] = field(default_factory=dict)
    verify: Verify | None = None
    verify_by: dict[str, Verify] = field(default_factory=dict)
    hosts: tuple[str, ...] = ()
    #: Error hidden in a 2xx body (Slack ``ok: false``, TikTok ``code != 0``).
    check: Callable[[Any], str] | None = None
    google: bool = False
    #: ``docker_hub``: the key is exchanged for a short-lived bearer first.
    exchange: str = ""
    prepare: Callable[[dict[str, str]], dict[str, str]] | None = None
    docs: str = ""
    #: How a request body is sent when the agent does not say: json or form.
    body_format: str = "json"
    #: Lists in a form body: ``repeat`` (``to=a&to=b``) or ``index`` (``expand[0]=a``).
    form_lists: str = "repeat"
    #: One line for the agent about this API's conventions (shown by describe).
    notes: str = ""

    def verify_for(self, kind: str, values: dict[str, str] | None = None) -> Verify | None:
        if kind == "custom" and self.verify_by.get("custom_app") and not (values or {}).get("userAccessToken"):
            return self.verify_by["custom_app"]
        return self.verify_by.get(kind, self.verify)


_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_|]+)\}")


def placeholders(template: str) -> list[str]:
    return _PLACEHOLDER.findall(template or "")


def render(template: str, values: dict[str, Any]) -> str:
    def sub(match: re.Match[str]) -> str:
        for key in match.group(1).split("|"):
            value = values.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    return _PLACEHOLDER.sub(sub, template or "")


def complete(template: str, values: dict[str, Any]) -> bool:
    """True when every placeholder of ``template`` has a value."""
    return all(
        any(values.get(key) not in (None, "") for key in group.split("|")) for group in placeholders(template)
    )


# -- body-level error checks ---------------------------------------------------


def _slack_ok(body: Any) -> str:
    if isinstance(body, dict) and body.get("ok") is False:
        return f"Slack refused the call: {body.get('error') or 'unknown error'}"
    return ""


def _tiktok_ok(body: Any) -> str:
    if isinstance(body, dict) and "code" in body and body.get("code") not in (0, "0"):
        return f"TikTok refused the call: {body.get('message') or body.get('code')}"
    return ""


def _cloudflare_ok(body: Any) -> str:
    if isinstance(body, dict) and body.get("success") is False:
        errors = body.get("errors") or []
        first = errors[0] if errors and isinstance(errors[0], dict) else {}
        return f"Cloudflare refused the call: {first.get('message') or 'unknown error'}"
    return ""


def _apollo_ok(body: Any) -> str:
    if isinstance(body, dict) and body.get("is_logged_in") is False:
        return "Apollo did not accept this API key."
    return ""


def _datadog_ok(body: Any) -> str:
    if isinstance(body, dict) and body.get("valid") is False:
        return "Datadog did not accept this API key."
    return ""


def _zendesk_ok(body: Any) -> str:
    user = body.get("user") if isinstance(body, dict) else None
    if isinstance(user, dict) and user.get("id") is None:
        return "Zendesk did not authenticate this email and API token."
    return ""


def _text_error_ok(body: Any) -> str:
    if isinstance(body, str):
        lowered = body.strip().lower()
        if any(word in lowered for word in ("not valid", "invalid", "unauthorized", "key_not", "error")):
            return f"The provider refused the call: {body.strip()[:200]}"
    if isinstance(body, dict) and str(body.get("status") or "").lower() in {"error", "failed"}:
        return f"The provider refused the call: {body.get('message') or body.get('status')}"
    return ""


# -- value preparation -------------------------------------------------------------

_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _private_host(host: str) -> bool:
    if host == "localhost" or host.endswith(".local") or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def https_url(value: Any, label: str) -> str:
    """A clean ``https://host/path`` (plain HTTP only for a local or private host)."""
    text = str(value or "").strip().rstrip("/")
    if text and "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        port = parts.port  # urlsplit checks the port only when it is read
    except ValueError:
        raise ConnectorError(f"{label} must be a URL such as https://example.com.") from None
    if not host or parts.username or parts.password or parts.query or parts.fragment:
        raise ConnectorError(f"{label} must be a URL such as https://example.com.")
    if parts.scheme != "https" and not (parts.scheme == "http" and _private_host(host)):
        raise ConnectorError(f"{label} must use HTTPS.")
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port is not None else "")
    return f"{parts.scheme}://{netloc}{parts.path.rstrip('/')}"


def _subdomain(value: Any, label: str, suffix: str) -> str:
    text = re.sub(r"^https?://", "", str(value or "").strip().lower()).split("/")[0]
    if text.endswith(suffix):
        text = text[: -len(suffix)]
    if not _SUBDOMAIN.match(text):
        raise ConnectorError(f"{label} must be a subdomain such as acme.")
    return text


def _prep_url(key: str, label: str, *, strip: str = "") -> Callable[[dict[str, str]], dict[str, str]]:
    def prep(values: dict[str, str]) -> dict[str, str]:
        url = https_url(values.get(key), label)
        if strip:
            url = re.sub(strip, "", url)
        return {**values, key: url}

    return prep


def _prep_choice(key: str, default: str, allowed: set[str]) -> Callable[[dict[str, str]], dict[str, str]]:
    def prep(values: dict[str, str]) -> dict[str, str]:
        url = str(values.get(key) or default).strip().rstrip("/")
        if url not in allowed:
            raise ConnectorError(f"{key} must be one of {', '.join(sorted(allowed))}.")
        return {**values, key: url}

    return prep


def _prep_wordpress(values: dict[str, str]) -> dict[str, str]:
    url = https_url(values.get("siteUrl"), "Site URL")
    return {**values, "siteUrl": re.sub(r"/wp-json(?:/wp/v2)?$", "", url)}


def _prep_zendesk(values: dict[str, str]) -> dict[str, str]:
    return {**values, "subdomain": _subdomain(values.get("subdomain"), "Subdomain", ".zendesk.com")}


def _prep_sage(values: dict[str, str]) -> dict[str, str]:
    return {**values, "domain": _subdomain(values.get("domain"), "Domain", ".sage.hr")}


def _prep_mailchimp(values: dict[str, str]) -> dict[str, str]:
    key = str(values.get("apiKey") or "")
    dc = key.rsplit("-", 1)[-1].lower() if "-" in key else ""
    if not re.fullmatch(r"[a-z]{2,4}[0-9]{1,3}", dc):
        raise ConnectorError("A Mailchimp API key ends with its data center, such as -us21.")
    return {**values, "dc": dc}


def _prep_shopify(values: dict[str, str]) -> dict[str, str]:
    raw = re.sub(r"^https?://", "", str(values.get("shopDomain") or values.get("shopSubdomain") or "").strip().lower())
    raw = raw.split("?")[0].rstrip("/")
    admin = re.match(r"^admin\.shopify\.com/store/([a-z0-9-]+)", raw)
    host = raw.split("/")[0]
    sub = admin.group(1) if admin else (host[: -len(".myshopify.com")] if host.endswith(".myshopify.com") else host)
    if not _SUBDOMAIN.match(sub):
        raise ConnectorError("The shop domain must look like acme.myshopify.com.")
    return {**values, "shop": f"{sub}.myshopify.com", "shopSubdomain": sub}


def _prep_datadog(values: dict[str, str]) -> dict[str, str]:
    """``us1`` / ``datadoghq.com`` / ``api.datadoghq.eu`` -> the site domain and its API base.

    The API key form asks for a site id (us1, eu...), the OAuth form for a
    domain (its token URL is ``https://api.{site}/...``): both end as a domain.
    """
    raw = str(values.get("site") or "us1").strip().lower()
    raw = re.sub(r"^https?://", "", raw).split("/")[0].removeprefix("api.").removeprefix("app.")
    domains = {key: url.split("://api.", 1)[1] for key, url in DATADOG_SITES.items()}
    domain = domains.get(raw) or (raw if raw in domains.values() else "")
    if not domain:
        raise ConnectorError("The Datadog site must be one of us1, us3, us5, eu, ap1, ap2, uk1, gov or gov2.")
    return {**values, "site": domain, "apiBase": f"https://api.{domain}"}


def _prep_docker(values: dict[str, str]) -> dict[str, str]:
    ident, sep, secret = str(values.get("apiKey") or "").partition(":")
    if not sep or not ident.strip() or not secret.strip():
        raise ConnectorError("The Docker Hub credential must be username:personal_access_token.")
    return {**values, "identifier": ident.strip(), "secret": secret.strip()}


def _prep_slug(key: str, label: str) -> Callable[[dict[str, str]], dict[str, str]]:
    def prep(values: dict[str, str]) -> dict[str, str]:
        slug = str(values.get(key) or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", slug):
            raise ConnectorError(f"{label} must be a slug such as my-org.")
        return {**values, key: slug}

    return prep


def _prep_salesforce(values: dict[str, str]) -> dict[str, str]:
    login = https_url(values.get("loginUrl") or "https://login.salesforce.com", "Login URL")
    host = urlsplit(login).hostname or ""
    if not (host in {"login.salesforce.com", "test.salesforce.com"} or host.endswith(".my.salesforce.com")):
        raise ConnectorError("The Salesforce login URL must be login.salesforce.com, test.salesforce.com or <domain>.my.salesforce.com.")
    return {**values, "loginUrl": f"https://{host}"}


def _prep_microsoft(values: dict[str, str]) -> dict[str, str]:
    tenant = str(values.get("tenant") or "common").strip()
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,100}", tenant):
        raise ConnectorError("The tenant must be common, organizations, consumers or a tenant id.")
    return {**values, "tenant": tenant}


# -- the specs -------------------------------------------------------------------

_ME = Verify("/me", id="id", name="displayName|userPrincipalName|mail")
_GOOGLE_SCOPES_HINT = "Enable the matching Google API in Google Cloud Console for the OAuth client project."

SPECS: dict[str, Spec] = {
    # Communication and messaging
    "slack": Spec("https://slack.com/api", verify=Verify("/auth.test", "POST", id="user_id|team_id", name="user|team"),
                  hosts=("files.slack.com",), check=_slack_ok, docs="https://api.slack.com/methods",
                  notes="Methods are POST /<method> (chat.postMessage...); a few such as files.getUploadURLExternal "
                        "only take body_format=form."),
    "slackbot": Spec("https://slack.com/api", verify=Verify("/auth.test", "POST", id="user_id|bot_id", name="user|team"),
                     hosts=("files.slack.com",), check=_slack_ok, docs="https://api.slack.com/methods",
                     notes="Methods are POST /<method> (chat.postMessage...); a few such as files.getUploadURLExternal "
                           "only take body_format=form."),
    "microsoft_teams": Spec(GRAPH, verify=_ME, prepare=_prep_microsoft, docs="https://learn.microsoft.com/graph/api/resources/teams-api-overview"),
    "discord": Spec("https://discord.com/api/v10", verify=Verify("/users/@me", id="id", name="global_name|username"),
                    docs="https://discord.com/developers/docs/reference"),
    "gmail": Spec("https://gmail.googleapis.com/gmail/v1", verify=Verify("/users/me/profile", id="emailAddress", name="emailAddress"),
                  google=True, docs="https://developers.google.com/gmail/api/reference/rest",
                  notes="Send with POST /users/me/messages/send and body {\"raw\": <base64url RFC 2822 message>}."),
    "outlook": Spec(GRAPH, verify=_ME, prepare=_prep_microsoft, docs="https://learn.microsoft.com/graph/api/resources/mail-api-overview"),
    # Video conferencing
    "googlemeet": Spec("https://meet.googleapis.com/v2", verify=Verify("/conferenceRecords?pageSize=1"), google=True,
                       docs="https://developers.google.com/meet/api/reference/rest"),
    "zoom": Spec("https://api.zoom.us/v2", verify=Verify("/users/me", id="id", name="display_name|email"),
                 docs="https://developers.zoom.us/docs/api/"),
    "agora": Spec("https://api.agora.io", key_auth=(basic("{customerId}", "{apiKey}"),), verify=Verify("/dev/v1/projects"),
                  docs="https://docs.agora.io/en/video-calling/reference/restful-authentication"),
    # Calendars and tasks
    "googlecalendar": Spec("https://www.googleapis.com/calendar/v3", verify=Verify("/users/me/calendarList?maxResults=1"),
                           google=True, docs="https://developers.google.com/calendar/api/v3/reference"),
    "outlook_calendar": Spec(GRAPH, verify=_ME, prepare=_prep_microsoft, docs="https://learn.microsoft.com/graph/api/resources/calendar"),
    "calendly": Spec("https://api.calendly.com", verify=Verify("/users/me", id="resource.uri", name="resource.name|resource.email"),
                     docs="https://developer.calendly.com/api-docs"),
    "googletasks": Spec("https://tasks.googleapis.com/tasks/v1", verify=Verify("/users/@me/lists?maxResults=1"), google=True,
                        docs="https://developers.google.com/tasks/reference/rest"),
    "motion": Spec("https://api.usemotion.com/v1", key_auth=(header("X-API-Key", "{apiKey}"),),
                   verify=Verify("/users/me", id="id", name="name|email"), docs="https://docs.usemotion.com/"),
    # Documents and files
    "notion": Spec("https://api.notion.com/v1", headers={"Notion-Version": "2026-03-11"},
                   verify=Verify("/users/me", id="id", name="bot.workspace_name|name"), docs="https://developers.notion.com/reference"),
    "googledrive": Spec("https://www.googleapis.com/drive/v3", verify=Verify("/about?fields=user", id="user.emailAddress",
                        name="user.displayName|user.emailAddress"), google=True,
                        docs="https://developers.google.com/drive/api/reference/rest/v3",
                        notes="Uploads: POST https://www.googleapis.com/upload/drive/v3/files?uploadType=media with body_file; "
                              "downloads: GET /files/<id>?alt=media with save_to."),
    "googlesheets": Spec("https://sheets.googleapis.com/v4", hosts=("www.googleapis.com",), google=True,
                         docs="https://developers.google.com/sheets/api/reference/rest"),
    "dropbox": Spec("https://api.dropboxapi.com/2", verify=Verify("/users/get_current_account", "POST", raw="null",
                    id="account_id", name="name.display_name|email"), hosts=("content.dropboxapi.com",),
                    docs="https://www.dropbox.com/developers/documentation/http/documentation",
                    notes="Content calls go to https://content.dropboxapi.com/2/files/upload|download with the JSON "
                          "arguments in the Dropbox-API-Arg header (body_file to upload, save_to to download)."),
    "one_drive": Spec(GRAPH, verify=_ME, prepare=_prep_microsoft, docs="https://learn.microsoft.com/graph/api/resources/onedrive"),
    "excel": Spec(GRAPH, verify=_ME, prepare=_prep_microsoft, docs="https://learn.microsoft.com/graph/api/resources/excel"),
    "airtable": Spec("https://api.airtable.com", verify=Verify("/v0/meta/whoami", id="id", name="email|id"),
                     docs="https://airtable.com/developers/web/api/introduction"),
    # Forms
    "googleforms": Spec("https://forms.googleapis.com/v1", google=True, docs="https://developers.google.com/forms/api/reference/rest"),
    "typeform": Spec("https://api.typeform.com", verify=Verify("/me", id="user_id|alias|email", name="alias|email"),
                     docs="https://www.typeform.com/developers/"),
    # Content management
    "wordpress": Spec("{siteUrl}/wp-json/wp/v2", key_auth=(basic("{username}", "{apiKey}"),),
                      verify=Verify("/users/me?context=edit", id="id", name="name|slug"), prepare=_prep_wordpress,
                      docs="https://developer.wordpress.org/rest-api/reference/"),
    "readme": Spec("https://dash.readme.com/api/v1", key_auth=(basic("{apiKey}", ""),), verify=Verify("", id="subdomain", name="name|subdomain"),
                   docs="https://docs.readme.com/main/reference"),
    # CRM and support
    "salesforce": Spec("{instance_url}/services/data/v66.0", verify=Verify("{instance_url}/services/oauth2/userinfo", id="user_id",
                       name="name|preferred_username"), prepare=_prep_salesforce,
                       docs="https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/"),
    "hubspot": Spec("https://api.hubapi.com", verify=Verify("/account-info/v3/details", id="portalId", name="uiDomain|portalId"),
                    docs="https://developers.hubspot.com/docs/api/overview"),
    "zendesk": Spec("https://{subdomain}.zendesk.com/api/v2", key_auth=(basic("{email}/token", "{apiKey}"),),
                    verify=Verify("/users/me.json", id="user.id", name="user.name|user.email"), check=_zendesk_ok,
                    prepare=_prep_zendesk, docs="https://developer.zendesk.com/api-reference/"),
    # Prospecting
    "hunter": Spec("https://api.hunter.io/v2", key_auth=(header("X-API-KEY", "{apiKey}"),),
                   verify=Verify("/account", id="data.email", name="data.email"), docs="https://hunter.io/api-documentation/v2"),
    "apollo": Spec("https://api.apollo.io", key_auth=(header("X-Api-Key", "{apiKey}"),), headers={"Cache-Control": "no-cache"},
                   verify=Verify("/v1/auth/health"), check=_apollo_ok, docs="https://docs.apollo.io/reference"),
    "anymail_finder": Spec("https://api.anymailfinder.com", key_auth=(header("Authorization", "{apiKey}"),),
                           verify=Verify("/v5.1/account", name="email"), docs="https://anymailfinder.com/email-finder-api/docs"),
    "crunchbase": Spec("https://api.crunchbase.com/v4", key_auth=(header("X-cb-user-key", "{apiKey}"),),
                       verify=Verify("/data/autocompletes?query=apple&limit=1"), docs="https://data.crunchbase.com/docs"),
    # ERP and HR
    "erpnext": Spec("{baseUrl}", key_auth=(header("Authorization", "token {apiKey}:{apiSecret}"),),
                    verify=Verify("/api/method/frappe.auth.get_logged_user", id="message", name="message"),
                    prepare=_prep_url("baseUrl", "Base URL"), docs="https://docs.frappe.io/framework/user/en/api/rest"),
    "sage_hr": Spec("https://{domain}.sage.hr/api", key_auth=(header("X-Auth-Token", "{apiKey}"),),
                    verify=Verify("/employees?page=1"), prepare=_prep_sage, docs="https://sagehr.docs.apiary.io/"),
    # E-commerce and payments
    "shopify": Spec("https://{shop}/admin/api/2026-10", key_auth=(header("X-Shopify-Access-Token", "{apiKey}"),),
                    oauth_auth=(header("X-Shopify-Access-Token", "{accessToken}"),),
                    verify=Verify("/shop.json", id="shop.id", name="shop.name|shop.domain"), prepare=_prep_shopify,
                    docs="https://shopify.dev/docs/api/admin-rest"),
    "woocommerce": Spec("{storeUrl}/wp-json/wc/v3", key_auth=(basic("{consumerKey}", "{consumerSecret}"),),
                        verify=Verify("/products?per_page=1"), prepare=_prep_url("storeUrl", "Store URL", strip=r"/wp-json.*$"),
                        docs="https://woocommerce.github.io/woocommerce-rest-api-docs/"),
    "stripe": Spec("https://api.stripe.com", headers={"Stripe-Version": "2024-06-20"},
                   verify=Verify("/v1/account", id="id", name="settings.dashboard.display_name|business_profile.name|email|id"),
                   hosts=("files.stripe.com",), docs="https://docs.stripe.com/api", body_format="form", form_lists="index",
                   notes="Write calls take form-encoded bodies (sent for you): pass nested JSON such as "
                         "{\"metadata\": {\"k\": \"v\"}, \"expand\": [\"customer\"]}."),
    # Social networks
    "tiktok_business": Spec("https://business-api.tiktok.com/open_api/v1.3", key_auth=(header("Access-Token", "{apiKey}"),),
                            oauth_auth=(header("Access-Token", "{accessToken}"),),
                            verify=Verify("/user/info/", id="data.core_user_id", name="data.display_name|data.email"),
                            check=_tiktok_ok, docs="https://business-api.tiktok.com/portal/docs"),
    "youtube": Spec("https://www.googleapis.com/youtube/v3", key_auth=(query("key"),),
                    verify=Verify("/channels?part=id,snippet&mine=true", id="items.0.id", name="items.0.snippet.title"),
                    verify_by={"api_key": Verify("/i18nLanguages?part=snippet&hl=en")}, hosts=("youtube.googleapis.com",),
                    google=True, docs="https://developers.google.com/youtube/v3/docs"),
    "twitter": Spec("https://api.x.com/2", key_auth=(header("Authorization", "Bearer {userAccessToken|appBearerToken}"),),
                    verify=Verify("/users/me", id="data.id", name="data.username"),
                    verify_by={"custom_app": Verify("/users/by/username/XDevelopers", id="data.id", name="data.username")},
                    hosts=("upload.twitter.com",), docs="https://docs.x.com/x-api/introduction"),
    # Marketing, advertising and SEO
    "google_analytics": Spec("https://analyticsdata.googleapis.com/v1beta", hosts=("analyticsadmin.googleapis.com",),
                             verify=Verify("https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=1",
                                           name="accountSummaries.0.displayName"), google=True,
                             docs="https://developers.google.com/analytics/devguides/reporting/data/v1"),
    "google_search_console": Spec("https://www.googleapis.com/webmasters/v3", hosts=("searchconsole.googleapis.com",),
                                  verify=Verify("/sites", name="siteEntry.0.siteUrl"), google=True,
                                  docs="https://developers.google.com/webmaster-tools/v1/api_reference_index"),
    "googleads": Spec("https://googleads.googleapis.com/v25", headers={"developer-token": "{developerToken}"},
                      verify=Verify("/customers:listAccessibleCustomers", name="resourceNames.0"), google=True,
                      docs="https://developers.google.com/google-ads/api/rest/overview"),
    "ahrefs": Spec("https://api.ahrefs.com/v3", verify=Verify("/subscription-info/limits-and-usage"),
                   docs="https://docs.ahrefs.com/docs/api/reference/introduction"),
    # Emailing
    "mailchimp": Spec("https://{dc}.api.mailchimp.com/3.0", key_auth=(basic("navin", "{apiKey}"),),
                      verify=Verify("/", id="account_id", name="account_name|email"), prepare=_prep_mailchimp,
                      docs="https://mailchimp.com/developer/marketing/api/"),
    "brevo": Spec("https://api.brevo.com/v3", key_auth=(header("api-key", "{apiKey}"),),
                  verify=Verify("/account", id="email", name="companyName|email"), docs="https://developers.brevo.com/reference"),
    "mailgun": Spec("{apiBaseUrl}", key_auth=(basic("api", "{apiKey}"),), verify=Verify("/v4/domains?limit=1"),
                    prepare=_prep_choice("apiBaseUrl", "https://api.mailgun.net", {"https://api.mailgun.net", "https://api.eu.mailgun.net"}),
                    docs="https://documentation.mailgun.com/docs/mailgun/api-reference/", body_format="form",
                    notes="Send with POST /v3/<domain>/messages (form fields from, to, subject, text or html; "
                          "attachments with body_format=multipart and files)."),
    "sendgrid": Spec("{baseUrl}", verify=Verify("/v3/scopes"),
                     prepare=_prep_choice("baseUrl", "https://api.sendgrid.com", {"https://api.sendgrid.com", "https://api.eu.sendgrid.com"}),
                     docs="https://www.twilio.com/docs/sendgrid/api-reference"),
    "emaillistverify": Spec("https://apps.emaillistverify.com", key_auth=(query("secret"),), verify=Verify("/api/credits"),
                            check=_text_error_ok, docs="https://www.emaillistverify.com/docs"),
    # Web search and extraction
    "tavily": Spec("https://api.tavily.com", verify=Verify("/usage"), docs="https://docs.tavily.com/documentation/api-reference"),
    "serpapi": Spec("https://serpapi.com", key_auth=(query("api_key"),), verify=Verify("/account.json", id="account_id", name="account_email"),
                    docs="https://serpapi.com/search-api"),
    "apify": Spec("https://api.apify.com", verify=Verify("/v2/users/me", id="data.id", name="data.username"),
                  docs="https://docs.apify.com/api/v2"),
    # Geolocation
    "google_maps": Spec("https://places.googleapis.com", key_auth=(header("X-Goog-Api-Key", "{apiKey}"), query("key")),
                        verify=Verify("/v1/places:searchText", "POST", json={"textQuery": "Eiffel Tower", "pageSize": 1},
                                      headers={"X-Goog-FieldMask": "places.id"}),
                        hosts=("routes.googleapis.com", "maps.googleapis.com", "addressvalidation.googleapis.com"),
                        docs="https://developers.google.com/maps/documentation"),
    "google_address_validation": Spec("https://addressvalidation.googleapis.com", key_auth=(query("key"),),
                                      verify=Verify("/v1:validateAddress", "POST", json={"address": {"regionCode": "US",
                                                    "addressLines": ["1600 Amphitheatre Parkway, Mountain View, CA"]}}),
                                      docs="https://developers.google.com/maps/documentation/address-validation"),
    "yelp": Spec("https://api.yelp.com/v3", verify=Verify("/businesses/search?location=San%20Francisco&limit=1"),
                 docs="https://docs.developer.yelp.com/reference"),
    "iplocate": Spec("https://iplocate.io/api", key_auth=(header("X-API-Key", "{apiKey}"),), verify=Verify("/lookup/8.8.8.8"),
                     docs="https://www.iplocate.io/docs"),
    # Databases and analytics
    "supabase": Spec("https://api.supabase.com/v1", verify=Verify("/organizations", id="0.id", name="0.name"),
                     hosts=("*.supabase.co",), docs="https://supabase.com/docs/reference/api"),
    "pinecone": Spec("https://api.pinecone.io", key_auth=(header("Api-Key", "{apiKey}"),), headers={"X-Pinecone-Api-Version": "2026-04"},
                     verify=Verify("/indexes", name="indexes.0.name"), hosts=("*.pinecone.io",), docs="https://docs.pinecone.io/reference"),
    "mongo_db_atlas_administration": Spec("https://cloud.mongodb.com/api/atlas/v2", key_auth=(digest("{apiKey}", "{privateKey}"),),
                                          headers={"Accept": "application/vnd.atlas.2024-08-05+json"},
                                          verify=Verify("/groups?itemsPerPage=1", name="results.0.name"),
                                          docs="https://www.mongodb.com/docs/atlas/reference/api-resources-spec/v2/"),
    "databricks": Spec("{host}", verify=Verify("/api/2.0/preview/scim/v2/Me", id="id", name="userName|displayName"),
                       prepare=_prep_url("host", "Workspace host"), docs="https://docs.databricks.com/api/workspace/introduction"),
    # Cloud and development
    "cloudflare_dns": Spec("https://api.cloudflare.com/client/v4", verify=Verify("/zones?per_page=1", name="result.0.account.name"),
                           check=_cloudflare_ok, docs="https://developers.cloudflare.com/api/resources/dns/"),
    "vercel": Spec("https://api.vercel.com", verify=Verify("/v2/user", id="user.id", name="user.username|user.email"),
                   docs="https://vercel.com/docs/rest-api"),
    "laravel_cloud": Spec("https://cloud.laravel.com/api", headers={"Accept": "application/json"},
                          verify=Verify("/meta/organization", id="data.id", name="data.attributes.name|data.name"),
                          docs="https://cloud.laravel.com/docs/api"),
    "docker_hub": Spec("https://hub.docker.com", key_auth=BEARER_TOKEN, exchange="docker_hub", prepare=_prep_docker,
                       docs="https://docs.docker.com/reference/api/hub/latest/"),
    "circleci": Spec("https://circleci.com/api/v2", key_auth=(header("Circle-Token", "{apiKey}"),),
                     verify=Verify("/me", id="id", name="login|name"), docs="https://circleci.com/docs/api/v2/"),
    "ngrok": Spec("https://api.ngrok.com", headers={"Ngrok-Version": "2"}, verify=Verify("/tunnels?limit=1"),
                  docs="https://ngrok.com/docs/api/"),
    # Security and monitoring
    "permit_io": Spec("https://api.permit.io", verify=Verify("/v2/api-key/scope", id="organization_id", name="project_id|organization_id"),
                      docs="https://api.permit.io/v2/redoc"),
    "sentry": Spec("https://sentry.io/api/0", verify=Verify("/organizations/", id="0.slug", name="0.name"), hosts=("*.sentry.io",),
                   docs="https://docs.sentry.io/api/"),
    "datadog": Spec("{apiBase}", key_auth=(header("DD-API-KEY", "{apiKey}"), header("DD-APPLICATION-KEY", "{applicationKey}")),
                    verify=Verify("/api/v1/validate"), check=_datadog_ok, prepare=_prep_datadog,
                    docs="https://docs.datadoghq.com/api/latest/"),
    # /api/org also answers anonymous visitors on public instances, so an invalid
    # token would pass there; /api/user refuses anonymous calls (401) and answers
    # a valid service account token with its profile or 403 (not a user).
    "grafana": Spec("{baseUrl}", verify=Verify("/api/user", id="id", name="login|name|email", accept=(403,)),
                    prepare=_prep_url("baseUrl", "Grafana base URL"),
                    docs="https://grafana.com/docs/grafana/latest/developers/http_api/"),
    "grafana_cloud": Spec("https://grafana.com/api", verify=Verify("/orgs/{orgSlug}/instances", name="items.0.name"),
                          prepare=_prep_slug("orgSlug", "Organization slug"), docs="https://grafana.com/docs/grafana-cloud/developer-resources/api-reference/cloud-api/"),
    # Design
    "figma": Spec("https://api.figma.com", key_auth=(header("X-Figma-Token", "{apiKey}"),),
                  verify=Verify("/v1/me", id="id", name="handle|email"), docs="https://www.figma.com/developers/api"),
    "canva": Spec("https://api.canva.com/rest", verify=Verify("/v1/users/me", id="team_user.user_id", name="team_user.user_id"),
                  docs="https://www.canva.dev/docs/connect/"),
    # Image, video, voice
    "heygen": Spec("https://api.heygen.com", key_auth=(header("X-Api-Key", "{apiKey}"),), verify=Verify("/v2/user/remaining_quota"),
                   hosts=("upload.heygen.com",), docs="https://docs.heygen.com/reference"),
    "higgsfield_ai": Spec("https://platform.higgsfield.ai", key_auth=(header("Authorization", "Key {apiKey}:{apiSecret}"),),
                          verify=Verify("/requests/validation/status", accept=(404,)),
                          docs="https://docs.higgsfield.ai/"),
    "elevenlabs": Spec("https://api.elevenlabs.io", key_auth=(header("xi-api-key", "{apiKey}"),),
                       verify=Verify("/v1/user", id="user_id", name="first_name|user_id"), docs="https://elevenlabs.io/docs/api-reference"),
    "deepgram": Spec("https://api.deepgram.com", key_auth=(header("Authorization", "Token {apiKey}"),),
                     verify=Verify("/v1/projects", id="projects.0.project_id", name="projects.0.name"),
                     docs="https://developers.deepgram.com/reference"),
    # Blockchain and crypto
    "alchemy": Spec("https://eth-mainnet.g.alchemy.com", verify=Verify("/v2", "POST", json={"jsonrpc": "2.0", "id": 1,
                    "method": "eth_blockNumber", "params": []}), hosts=("*.g.alchemy.com",), docs="https://docs.alchemy.com/reference",
                    notes="JSON-RPC: POST /v2 with {\"jsonrpc\": \"2.0\", \"id\": 1, \"method\": ..., \"params\": [...]}; "
                          "other chains use their own host such as https://polygon-mainnet.g.alchemy.com/v2."),
    "crypto_apis": Spec("https://rest.cryptoapis.io", key_auth=(header("X-API-Key", "{apiKey}"),), headers={"x-api-version": "2024-12-12"},
                        verify=Verify("/market-data/metadata/assets?limit=1"), docs="https://developers.cryptoapis.io/"),
}


def spec_for(service: str) -> Spec:
    try:
        return SPECS[service]
    except KeyError:
        raise ConnectorError(f"No request rule for connector {service}.", 500) from None


def prepare(service: str, values: dict[str, str]) -> dict[str, str]:
    """Normalize and validate the connection values (URLs, subdomains, sites)."""
    spec = spec_for(service)
    clean = {key: value for key, value in values.items() if value not in (None, "")}
    return spec.prepare(clean) if spec.prepare else clean


def host_allowed(host: str, allowed: set[str]) -> bool:
    host = host.lower()
    for item in allowed:
        if item.startswith("*."):
            if host.endswith(item[1:]) and host != item[2:]:
                return True
        elif host == item:
            return True
    return False
