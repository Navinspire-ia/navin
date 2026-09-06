"""Pull real numbers behind published content.

Engagement comes from the channel that carries the post (X public metrics,
Facebook post fields, LinkedIn social actions). Traffic and signups come from
the site analytics the user connected (Plausible or Matomo) through the UTM
tags every published link carries (``utm_content`` = content id). Manual
numbers typed in the UI still work; measured values overwrite them per field.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

from loguru import logger

from navin.marketing.errors import MarketingError
from navin.marketing.publish import (
    GRAPH_VERSION,
    LINKEDIN_VERSION,
    HttpFn,
    _json,
    _x_headers,
    http_request,
)
from navin.marketing.store import MarketingStore


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# -- engagement per channel ------------------------------------------------------------


def x_metrics(store: MarketingStore, tweet_id: str, http: HttpFn) -> dict[str, float]:
    url = f"https://api.x.com/2/tweets/{tweet_id}?tweet.fields=public_metrics"
    status, _headers, body = http("GET", url, _x_headers(store, "GET", url), None)
    if status != 200:
        raise MarketingError(f"x metrics HTTP {status}", status=502)
    metrics = ((_json(body).get("data") or {}).get("public_metrics")) or {}
    return {
        "views": _num(metrics.get("impression_count")),
        "likes": _num(metrics.get("like_count")),
        "comments": _num(metrics.get("reply_count")),
        "shares": _num(metrics.get("retweet_count")) + _num(metrics.get("quote_count")),
        "saves": _num(metrics.get("bookmark_count")),
    }


def facebook_metrics(store: MarketingStore, post_id: str, http: HttpFn) -> dict[str, float]:
    token = store.get_secret("facebook_page_token")
    fields = "shares,likes.summary(true),comments.summary(true),insights.metric(post_impressions,post_clicks)"
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{post_id}?fields={urllib.parse.quote(fields)}&access_token={urllib.parse.quote(token)}"
    status, _headers, body = http("GET", url, {}, None)
    if status != 200:
        raise MarketingError(f"facebook metrics HTTP {status}", status=502)
    data = _json(body)
    out = {
        "likes": _num(((data.get("likes") or {}).get("summary") or {}).get("total_count")),
        "comments": _num(((data.get("comments") or {}).get("summary") or {}).get("total_count")),
        "shares": _num((data.get("shares") or {}).get("count")),
    }
    for item in ((data.get("insights") or {}).get("data") or []):
        values = item.get("values") or []
        value = _num((values[0] or {}).get("value")) if values else 0.0
        if item.get("name") == "post_impressions":
            out["views"] = value
        elif item.get("name") == "post_clicks":
            out["clicks"] = value
    return out


def linkedin_metrics(store: MarketingStore, urn: str, http: HttpFn) -> dict[str, float]:
    token = store.get_secret("linkedin_token")
    url = f"https://api.linkedin.com/rest/socialActions/{urllib.parse.quote(urn, safe='')}"
    headers = {"Authorization": f"Bearer {token}", "LinkedIn-Version": LINKEDIN_VERSION, "X-Restli-Protocol-Version": "2.0.0"}
    status, _headers, body = http("GET", url, headers, None)
    if status != 200:
        raise MarketingError(f"linkedin metrics HTTP {status}", status=502)
    data = _json(body)
    return {
        "likes": _num((data.get("likesSummary") or {}).get("totalLikes")),
        "comments": _num((data.get("commentsSummary") or {}).get("totalFirstLevelComments") or (data.get("commentsSummary") or {}).get("aggregatedTotalComments")),
    }


_ENGAGEMENT = {"x": x_metrics, "facebook": facebook_metrics, "linkedin": linkedin_metrics}


def collect_engagement(store: MarketingStore, *, http: HttpFn | None = None) -> dict[str, dict[str, float]]:
    """Per content id, the numbers the channel reports for its published post."""
    fetch = http or http_request
    out: dict[str, dict[str, float]] = {}
    for row in store.load_content():
        if row.get("status") not in {"published", "winner"} or not row.get("remote_id"):
            continue
        channel = str(row.get("channel") or "")
        reader = _ENGAGEMENT.get(channel)
        if reader is None or (row.get("receipt") or {}).get("mode") == "manual":
            continue
        try:
            out[str(row["id"])] = reader(store, str(row["remote_id"]), fetch)
        except MarketingError as exc:
            logger.info("marketing metrics skipped for {} ({}): {}", row.get("id"), channel, exc.message)
        except Exception as exc:  # noqa: BLE001 - one bad post never stops the measure pass
            logger.info("marketing metrics failed for {} ({}): {}", row.get("id"), channel, exc)
    return out


# -- site analytics (UTM) ----------------------------------------------------------------


def plausible_breakdown(cfg: dict[str, Any], key: str, http: HttpFn, *, period: str = "30d") -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    base = (cfg.get("base_url") or "https://plausible.io").rstrip("/")
    site = str(cfg.get("site_id") or "")
    headers = {"Authorization": f"Bearer {key}"}
    per_content: dict[str, dict[str, float]] = {}
    query = urllib.parse.urlencode({"site_id": site, "period": period, "property": "visit:utm_content", "metrics": "visitors,visits", "limit": 200})
    status, _headers, body = http("GET", f"{base}/api/v1/stats/breakdown?{query}", headers, None)
    if status != 200:
        raise MarketingError(f"plausible HTTP {status}", status=502)
    for item in _json(body).get("results") or []:
        content_id = str(item.get("utm_content") or "").strip()
        if content_id:
            per_content[content_id] = {"visitors": _num(item.get("visitors")), "visits": _num(item.get("visits"))}
    goal = str(cfg.get("goal") or "").strip()
    if goal:
        filters = f"event:goal=={goal}"
        query = urllib.parse.urlencode({"site_id": site, "period": period, "property": "visit:utm_content", "metrics": "visitors,events", "filters": filters, "limit": 200})
        status, _headers, body = http("GET", f"{base}/api/v1/stats/breakdown?{query}", headers, None)
        if status == 200:
            for item in _json(body).get("results") or []:
                content_id = str(item.get("utm_content") or "").strip()
                if content_id:
                    per_content.setdefault(content_id, {})["signups"] = _num(item.get("events") or item.get("visitors"))
    query = urllib.parse.urlencode({"site_id": site, "period": period, "metrics": "visitors,visits"})
    status, _headers, body = http("GET", f"{base}/api/v1/stats/aggregate?{query}", headers, None)
    totals: dict[str, float] = {}
    if status == 200:
        results = _json(body).get("results") or {}
        totals["traffic"] = _num((results.get("visitors") or {}).get("value"))
        totals["visits"] = _num((results.get("visits") or {}).get("value"))
    return per_content, totals


def matomo_breakdown(cfg: dict[str, Any], token: str, http: HttpFn) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise MarketingError("matomo base_url missing", status=400)
    site = str(cfg.get("site_id") or "")
    common = {"module": "API", "idSite": site, "period": "month", "date": "today", "format": "json"}
    body_params = urllib.parse.urlencode({"token_auth": token}).encode()
    per_content: dict[str, dict[str, float]] = {}
    totals: dict[str, float] = {}
    query = urllib.parse.urlencode({**common, "method": "VisitsSummary.get"})
    status, _headers, body = http("POST", f"{base}/index.php?{query}", {"Content-Type": "application/x-www-form-urlencoded"}, body_params)
    if status != 200:
        raise MarketingError(f"matomo HTTP {status}", status=502)
    summary = _json(body)
    totals["traffic"] = _num(summary.get("nb_uniq_visitors") or summary.get("nb_visits"))
    totals["visits"] = _num(summary.get("nb_visits"))
    # Per post needs the MarketingCampaignsReporting plugin (bundled on Matomo Cloud, free on-prem).
    query = urllib.parse.urlencode({**common, "method": "MarketingCampaignsReporting.getContent", "filter_limit": 200})
    status, _headers, body = http("POST", f"{base}/index.php?{query}", {"Content-Type": "application/x-www-form-urlencoded"}, body_params)
    if status == 200:
        try:
            rows = json.loads(body.decode("utf-8", errors="replace") or "[]")
        except json.JSONDecodeError:
            rows = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            content_id = str(item.get("label") or "").strip()
            if content_id:
                per_content[content_id] = {
                    "visitors": _num(item.get("nb_uniq_visitors") or item.get("nb_visits")),
                    "visits": _num(item.get("nb_visits")),
                    "signups": _num(item.get("nb_conversions")),
                }
    return per_content, totals


def collect_traffic(store: MarketingStore, *, http: HttpFn | None = None) -> tuple[dict[str, dict[str, float]], dict[str, float], str]:
    settings = store.load_settings()
    cfg = settings.get("analytics") if isinstance(settings.get("analytics"), dict) else {}
    provider = str(cfg.get("provider") or "")
    if not provider:
        return {}, {}, ""
    fetch = http or http_request
    if provider == "plausible":
        key = store.get_secret("plausible_key")
        if not key:
            raise MarketingError("plausible_key missing", status=400)
        per_content, totals = plausible_breakdown(cfg, key, fetch)
    elif provider == "matomo":
        token = store.get_secret("matomo_token")
        if not token:
            raise MarketingError("matomo_token missing", status=400)
        per_content, totals = matomo_breakdown(cfg, token, fetch)
    else:
        return {}, {}, ""
    return per_content, totals, provider


def test_analytics(store: MarketingStore, *, http: HttpFn | None = None) -> dict[str, Any]:
    try:
        _per_content, totals, provider = collect_traffic(store, http=http)
    except MarketingError as exc:
        store.save_settings({"analytics": {"last_error": exc.message}})
        return {"ok": False, "error": exc.message}
    if not provider:
        return {"ok": False, "error": "no analytics provider selected"}
    store.save_settings({"analytics": {"last_error": "", "measured_at": time.time()}})
    return {"ok": True, "provider": provider, "traffic": totals.get("traffic", 0.0)}


# -- the measure pass ----------------------------------------------------------------------


def collect_metrics(store: MarketingStore, *, http: HttpFn | None = None) -> dict[str, Any]:
    """Merge channel engagement and UTM traffic into analytics.by_content."""
    engagement = collect_engagement(store, http=http)
    traffic: dict[str, dict[str, float]] = {}
    totals: dict[str, float] = {}
    provider = ""
    error = ""
    try:
        traffic, totals, provider = collect_traffic(store, http=http)
    except MarketingError as exc:
        error = exc.message
        logger.info("marketing traffic skipped: {}", exc.message)
    except Exception as exc:  # noqa: BLE001 - analytics outage is not a loop failure
        error = f"{type(exc).__name__}: {exc}"[:200]
        logger.info("marketing traffic failed: {}", exc)
    if not engagement and not traffic and not totals:
        if provider or error:
            store.save_settings({"analytics": {"last_error": error, "measured_at": time.time() if not error else 0.0}})
        return {"measured": 0, "engagement": 0, "traffic": 0, "provider": provider, "error": error}
    analytics = store.load_analytics()
    by_content = dict(analytics.get("by_content") or {})
    stamp = time.time()
    for content_id, numbers in engagement.items():
        prev = by_content.get(content_id) if isinstance(by_content.get(content_id), dict) else {}
        by_content[content_id] = {**prev, **numbers, "measured_at": stamp, "source": "channel"}
    for content_id, numbers in traffic.items():
        prev = by_content.get(content_id) if isinstance(by_content.get(content_id), dict) else {}
        merged = {**prev, "measured_at": stamp}
        merged["clicks"] = numbers.get("visits", numbers.get("visitors", 0.0))
        merged["visitors"] = numbers.get("visitors", 0.0)
        if "signups" in numbers:
            merged["signups"] = numbers["signups"]
            merged["conversions"] = numbers["signups"]
        merged["traffic_source"] = provider
        by_content[content_id] = merged
    payload: dict[str, Any] = {"by_content": by_content}
    if totals.get("traffic") is not None and provider:
        payload["traffic"] = int(totals.get("traffic") or 0)
    signups = sum(_num(row.get("signups")) for row in by_content.values() if isinstance(row, dict))
    if traffic and signups:
        payload["signups"] = int(signups)
    store.save_analytics(payload)
    if provider:
        store.save_settings({"analytics": {"last_error": error, "measured_at": stamp}})
    store.append_journal(
        {"kind": "analytics", "text": f"measured {len(engagement)} posts, {len(traffic)} tracked links" + (f" via {provider}" if provider else "")}
    )
    return {"measured": len(by_content), "engagement": len(engagement), "traffic": len(traffic), "provider": provider, "error": error}
