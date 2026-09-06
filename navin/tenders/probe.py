"""Reachability probe for official tender portals. No scrape recipes."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from navin.tenders.sources import catalog

# Windows Chrome: some government F5 WAFs (Portugal BASE) answer a fake 404
# to Linux Chrome fingerprints. Catalog probes must report the public portal.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_TIMEOUT = 14
_OK = {200, 201, 202, 204, 301, 302, 303, 307, 308}


def _ctx(*, verify: bool = True) -> ssl.SSLContext:
    if not verify:
        return ssl._create_unverified_context()
    return ssl.create_default_context()


def _open(url: str, *, method: str = "GET", verify: bool = True) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "en,fr;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ctx(verify=verify)) as resp:  # noqa: S310
            final = str(resp.geturl() or url)
            code = int(resp.status)
            return code, final, ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), url, (exc.reason or "")[:120]
    except Exception as exc:  # noqa: BLE001 - probe must never crash the desk
        return 0, url, str(exc)[:160]


def probe_url(url: str) -> dict[str, Any]:
    code, final, err = _open(url, method="HEAD")
    if code in {0, 400, 403, 405, 501}:
        code, final, err = _open(url, method="GET")
    if code == 0 and "CERTIFICATE_VERIFY_FAILED" in err:
        code, final, retry_err = _open(url, method="GET", verify=False)
        if code in _OK:
            return {
                "url": url,
                "status": code,
                "final": final,
                "ok": True,
                "blocked": False,
                "error": "TLS certificate failed verification; host still answered",
            }
        err = retry_err or err
    ok = code in _OK
    blocked = code in {400, 403, 405} or "captcha" in err.lower() or "waf" in err.lower()
    return {
        "url": url,
        "status": code,
        "final": final,
        "ok": ok,
        "blocked": blocked,
        "error": err,
    }


def probe_api(source: dict[str, Any]) -> dict[str, Any]:
    sid = source.get("id")
    api = str(source.get("api") or "")
    if sid == "ted":
        body = json.dumps(
            {
                "query": "form-type=competition AND publication-date>=20260101",
                "fields": ["publication-number"],
                "limit": 1,
                "page": 1,
                "scope": "ACTIVE",
                "paginationMode": "PAGE_NUMBER",
            }
        ).encode()
        req = urllib.request.Request(
            api,
            data=body,
            method="POST",
            headers={
                "User-Agent": _UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ctx()) as resp:  # noqa: S310
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            notices = raw.get("notices") if isinstance(raw, dict) else []
            count = len(notices) if isinstance(notices, list) else 0
            return {"ok": count > 0, "status": 200, "count": count, "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": 0, "count": 0, "error": str(exc)[:160]}
    if sid == "world-bank":
        url = f"{api}?format=json&rows=1"
        code, _final, err = _open(url, method="GET")
        return {"ok": code in _OK, "status": code, "count": 1 if code in _OK else 0, "error": err}
    if sid == "find-a-tender":
        url = f"{api}?limit=1&stages=tender"
        code, _final, err = _open(url, method="GET")
        return {"ok": code in _OK, "status": code, "count": 1 if code in _OK else 0, "error": err}
    if sid == "contracts-finder":
        body = json.dumps({"searchCriteria": {"statuses": ["Open"]}, "size": 1}).encode()
        req = urllib.request.Request(
            api,
            data=body,
            method="POST",
            headers={
                "User-Agent": _UA,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ctx()) as resp:  # noqa: S310
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
            releases = raw.get("releases") if isinstance(raw, dict) else []
            count = len(releases) if isinstance(releases, list) else 0
            return {"ok": count > 0, "status": 200, "count": count, "error": ""}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": 0, "count": 0, "error": str(exc)[:160]}
    if sid == "boamp":
        url = f"{api}?limit=1&where=datelimitereponse%20%3E%3D%20now()"
        code, _final, err = _open(url, method="GET")
        return {"ok": code in _OK, "status": code, "count": 1 if code in _OK else 0, "error": err}
    if sid == "canadabuys":
        code, _final, err = _open(api, method="GET")
        return {"ok": code in _OK, "status": code, "count": 1 if code in _OK else 0, "error": err}
    if sid == "sam-gov":
        return {
            "ok": False,
            "status": 0,
            "count": 0,
            "error": "official API needs a free SAM.gov key; portal stays public",
        }
    if api:
        code, _final, err = _open(api, method="GET")
        return {
            "ok": code in _OK,
            "status": code,
            "count": 0,
            "error": err or "portal API not wired in collect yet",
        }
    return {"ok": False, "status": 0, "count": 0, "error": "no api"}


def probe_source(source: dict[str, Any]) -> dict[str, Any]:
    portal = probe_url(str(source["url"]))
    row = {
        "id": source["id"],
        "name": source["name"],
        "priority": source.get("priority"),
        "ingest": source.get("ingest"),
        "country": source.get("country"),
        "portal_ok": portal["ok"],
        "portal_blocked": portal.get("blocked", False),
        "portal_status": portal["status"],
        "portal_final": portal["final"],
        "portal_error": portal["error"],
        "api_ok": None,
        "api_status": None,
        "api_error": "",
    }
    if source.get("api"):
        api = probe_api(source)
        row["api_ok"] = api["ok"]
        row["api_status"] = api["status"]
        row["api_error"] = api["error"]
    row["reachable"] = bool(row["portal_ok"] or row["api_ok"] or row["portal_blocked"])
    return row


def probe_catalog(*, workers: int = 12) -> list[dict[str, Any]]:
    rows = catalog()
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(probe_source, source): source["id"] for source in rows}
        for fut in as_completed(futs):
            out.append(fut.result())
    out.sort(key=lambda r: (str(r.get("priority") or ""), str(r.get("id") or "")))
    return out
