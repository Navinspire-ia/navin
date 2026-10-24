# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SEO desk API: audits, history, Core Web Vitals, SERP tracking, provider keys.

Runs the same engine as the ``seo`` agent tool (``navin.seo.pipeline``). The
desk keeps its own book under the runtime data dir: one JSON report per audit,
an index of sites, and the SERP snapshot history.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from navin.seo.crawler import SeoCrawler
from navin.seo.models import AuditResult
from navin.seo.performance import PerformanceClient
from navin.seo.pipeline import run_pipeline
from navin.seo.report import render_html, render_markdown, report_payload
from navin.seo.serp import DataForSeoAdapter, SemrushAdapter, SerpStore

_KEYS = ("psi_api_key", "crux_api_key", "dataforseo_login", "dataforseo_password", "semrush_api_key")
_ENV = {
    "psi_api_key": "PAGESPEED_API_KEY",
    "crux_api_key": "CRUX_API_KEY",
    "dataforseo_login": "DATAFORSEO_LOGIN",
    "dataforseo_password": "DATAFORSEO_PASSWORD",
    "semrush_api_key": "SEMRUSH_API_KEY",
}
_MAX_AUDITS_PER_SITE = 30
_lock = threading.Lock()


class SeoDeskError(Exception):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _root() -> Path:
    from navin.config.paths import get_runtime_subdir

    return get_runtime_subdir("seo-desk")


def _config():
    from navin.config.loader import load_config

    return load_config().tools.seo


def _site_key(url: str) -> str:
    parsed = urlparse(url)
    return re.sub(r"[^a-z0-9.-]+", "-", (parsed.netloc or "").lower()).strip("-") or "site"


def _normalize_url(raw: Any) -> str:
    url = str(raw or "").strip()
    if not url:
        raise SeoDeskError("Enter the URL of the site to audit.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or " " in parsed.netloc:
        raise SeoDeskError("That URL is not valid.")
    return url


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _index() -> dict[str, Any]:
    return _read_json(_root() / "sites.json", {})


def _compact_report(payload: dict[str, Any]) -> dict[str, Any]:
    """The audit without page bodies or raw provider dumps (UI-sized)."""
    out = dict(payload)
    out["pages"] = [
        {key: page.get(key) for key in (
            "url", "final_url", "status", "title", "meta_description", "h1", "canonical", "error",
        ) if page.get(key) not in (None, "", [])}
        for page in payload.get("pages", [])
    ]
    perf = dict((payload.get("metadata") or {}).get("performance") or {})
    for name, value in list(perf.items()):
        if isinstance(value, dict):
            perf[name] = {key: item for key, item in value.items() if key != "raw"}
    out["metadata"] = {**(payload.get("metadata") or {}), "performance": perf}
    return out


def _key_status() -> dict[str, Any]:
    cfg = _config()
    status: dict[str, Any] = {}
    for key in _KEYS:
        value = getattr(cfg, key, None) or ""
        env = os.getenv(_ENV[key]) or ""
        shown = value or env
        status[key] = {
            "configured": bool(shown),
            "source": "settings" if value else "environment" if env else "",
            "hint": ("" if not shown else ("****" + shown[-4:] if "login" not in key else shown)),
        }
    status["psi_ready"] = status["psi_api_key"]["configured"]
    status["crux_ready"] = status["crux_api_key"]["configured"] or status["psi_ready"]
    status["serp_ready"] = (
        status["dataforseo_login"]["configured"] and status["dataforseo_password"]["configured"]
    ) or status["semrush_api_key"]["configured"]
    return status


def _serp_store() -> SerpStore:
    return SerpStore(_root() / "serp-history.jsonl")


def snapshot() -> dict[str, Any]:
    cfg = _config()
    index = _index()
    sites = sorted(index.values(), key=lambda item: item.get("last_audit_at", ""), reverse=True)
    keywords = sorted({item.keyword for item in _serp_store().history()})
    return {
        "sites": sites,
        "keys": _key_status(),
        "serp_keywords": keywords,
        "limits": {"max_pages": cfg.max_pages, "max_depth": cfg.max_depth},
    }


def run_audit(body: dict[str, Any]) -> dict[str, Any]:
    url = _normalize_url(body.get("url"))
    cfg = _config()
    max_pages = int(body.get("max_pages") or cfg.max_pages)
    max_depth = int(body.get("max_depth") if body.get("max_depth") is not None else cfg.max_depth)
    max_pages = max(1, min(500, max_pages))
    max_depth = max(0, min(8, max_depth))
    started = time.time()
    audit = run_pipeline(
        url,
        crawler=SeoCrawler(timeout=cfg.timeout_seconds, allow_private_network=cfg.allow_private_network),
        performance=PerformanceClient(psi_api_key=cfg.psi_api_key, crux_api_key=cfg.crux_api_key),
        max_pages=max_pages,
        max_depth=max_depth,
    )
    if not audit.pages or all(page.status == 0 for page in audit.pages):
        reason = audit.pages[0].error if audit.pages else "no answer"
        raise SeoDeskError(f"The site could not be reached ({reason}). Check the URL and try again.", status=502)
    payload = report_payload(audit)
    site = _site_key(url)
    audit_id = time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-{int(started * 1000) % 1000:03d}"
    folder = _root() / "audits" / site
    _write_json(folder / f"{audit_id}.json", payload)
    health = payload["health"]
    perf = (payload.get("metadata") or {}).get("performance") or {}
    psi = perf.get("psi") or {}
    entry = {
        "id": audit_id,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "url": url,
        "score": health["score"],
        "grade": health["grade"],
        "findings": health["finding_count"],
        "counts": health.get("counts", {}),
        "pages": len(audit.pages),
        "performance_score": psi.get("performance_score") if psi.get("status") == "ok" else None,
        "duration_s": round(time.time() - started, 1),
    }
    with _lock:
        index = _index()
        record = index.get(site) or {"site": site, "url": url, "audits": []}
        record["url"] = url
        record["audits"] = ([entry] + list(record.get("audits") or []))[:_MAX_AUDITS_PER_SITE]
        record.update(last_audit_id=audit_id, last_audit_at=entry["at"], last_score=entry["score"],
                      last_grade=entry["grade"])
        index[site] = record
        _write_json(_root() / "sites.json", index)
        keep = {item["id"] for item in record["audits"]}
        for old in folder.glob("*.json"):
            if old.stem not in keep:
                old.unlink(missing_ok=True)
    return {"site": site, "audit_id": audit_id, "entry": entry, "report": _compact_report(payload)}


def _load_audit(site: str, audit_id: str | None) -> tuple[str, dict[str, Any]]:
    record = _index().get(site)
    if record is None:
        raise SeoDeskError("No audit for this site yet.", status=404)
    chosen = audit_id or record.get("last_audit_id")
    path = _root() / "audits" / site / f"{chosen}.json"
    if not re.fullmatch(r"[0-9A-Za-z-]{1,40}", str(chosen)) or not path.is_file():
        raise SeoDeskError("This audit is no longer available.", status=404)
    return str(chosen), _read_json(path, {})


def get_audit(body: dict[str, Any]) -> dict[str, Any]:
    site = str(body.get("site") or "")
    audit_id, payload = _load_audit(site, body.get("audit_id"))
    return {"site": site, "audit_id": audit_id, "report": _compact_report(payload)}


def history(body: dict[str, Any]) -> dict[str, Any]:
    record = _index().get(str(body.get("site") or ""))
    if record is None:
        raise SeoDeskError("No audit for this site yet.", status=404)
    return {"site": record["site"], "audits": list(reversed(record.get("audits") or []))}


def export_report(body: dict[str, Any]) -> dict[str, Any]:
    site = str(body.get("site") or "")
    audit_id, payload = _load_audit(site, body.get("audit_id"))
    fmt = str(body.get("format") or "html")
    audit = AuditResult.model_validate({k: v for k, v in payload.items() if k != "health"})
    if fmt == "md":
        content, mime = render_markdown(audit), "text/markdown"
    elif fmt == "json":
        content, mime = json.dumps(payload, ensure_ascii=False, indent=2), "application/json"
    else:
        fmt, content, mime = "html", render_html(audit), "text/html"
    return {"filename": f"seo-{site}-{audit_id}.{fmt}", "mime": mime, "content": content}


def delete_site(body: dict[str, Any]) -> dict[str, Any]:
    site = str(body.get("site") or "")
    with _lock:
        index = _index()
        if index.pop(site, None) is None:
            raise SeoDeskError("Unknown site.", status=404)
        _write_json(_root() / "sites.json", index)
    folder = _root() / "audits" / site
    if folder.is_dir():
        for item in folder.glob("*.json"):
            item.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
    return {"deleted": site}


def save_keys(body: dict[str, Any]) -> dict[str, Any]:
    """Store provider keys in Settings (tools.seo). Empty keeps, clear[] removes."""
    from navin.config.loader import load_config, save_config

    config = load_config()
    seo = config.tools.seo
    clear = {str(item) for item in body.get("clear") or []}
    for key in _KEYS:
        value = body.get(key)
        if key in clear:
            setattr(seo, key, None)
        elif isinstance(value, str) and value.strip():
            if len(value) > 512:
                raise SeoDeskError(f"{key} is too long.")
            setattr(seo, key, value.strip())
    save_config(config)
    return {"keys": _key_status()}


def serp_snapshot(body: dict[str, Any]) -> dict[str, Any]:
    keyword = str(body.get("keyword") or "").strip()
    if not keyword:
        raise SeoDeskError("Enter a keyword.")
    cfg = _config()
    provider = str(body.get("provider") or "")
    if not provider:
        provider = "dataforseo" if (cfg.dataforseo_login or os.getenv("DATAFORSEO_LOGIN")) else "semrush"
    if provider == "dataforseo":
        login = cfg.dataforseo_login or os.getenv("DATAFORSEO_LOGIN")
        password = cfg.dataforseo_password or os.getenv("DATAFORSEO_PASSWORD")
        adapter = DataForSeoAdapter(login, password) if login and password else None
    elif provider == "semrush":
        key = cfg.semrush_api_key or os.getenv("SEMRUSH_API_KEY")
        adapter = SemrushAdapter(key) if key else None
    else:
        raise SeoDeskError("Provider must be dataforseo or semrush.")
    if adapter is None:
        return {
            "status": "data_gap", "service": provider,
            "reason": "Add DataForSEO or Semrush credentials in the SEO keys to track rankings.",
        }
    try:
        snap = adapter.snapshot(
            keyword,
            location=str(body.get("location") or "United States"),
            language=str(body.get("language") or "en"),
        )
    except Exception as exc:  # noqa: BLE001 - provider errors are shown as is
        raise SeoDeskError(f"The {provider} request failed: {exc}", status=502) from exc
    _serp_store().append(snap)
    return {"status": "ok", "snapshot": snap.model_dump(mode="json")}


def serp_history(body: dict[str, Any]) -> dict[str, Any]:
    keyword = str(body.get("keyword") or "").strip() or None
    snapshots = [item.model_dump(mode="json") for item in _serp_store().history(keyword=keyword)]
    return {"snapshots": snapshots[-200:]}


def handle_seo_action(action: str, body: dict[str, Any]) -> dict[str, Any]:
    handlers = {
        "snapshot": lambda _b: snapshot(),
        "pipeline": run_audit,
        "audit": run_audit,
        "audit.get": get_audit,
        "history": history,
        "report": export_report,
        "site.delete": delete_site,
        "keys.get": lambda _b: {"keys": _key_status()},
        "keys.save": save_keys,
        "serp_snapshot": serp_snapshot,
        "serp_history": serp_history,
    }
    handler = handlers.get(action)
    if handler is None:
        raise SeoDeskError(f"Unknown SEO action {action!r}.", status=404)
    return handler(body or {})
