"""Measure, learn, improve: A/B and the growth loop."""

from __future__ import annotations

from typing import Any

from navin.marketing import ai
from navin.marketing.content import adapt_message, render_post
from navin.marketing.errors import MarketingError
from navin.marketing.store import MarketingStore


def _metric(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _keep_number(payload: dict[str, Any], name: str, current: Any, caster: type) -> Any:
    if name not in payload:
        return current
    raw = payload.get(name)
    if raw is None or raw == "":
        return current
    try:
        return caster(raw)
    except (TypeError, ValueError) as exc:
        raise MarketingError(f"{name} must be a number", status=400) from exc


def ingest_metrics(store: MarketingStore, payload: dict[str, Any]) -> dict[str, Any]:
    current = store.load_analytics()
    by_content = dict(current.get("by_content") or {})
    incoming = payload.get("by_content") if isinstance(payload.get("by_content"), dict) else {}
    for content_id, raw in incoming.items():
        if not isinstance(raw, dict):
            continue
        prev = by_content.get(content_id) if isinstance(by_content.get(content_id), dict) else {}
        by_content[str(content_id)] = {**prev, **raw}
    totals = {
        "traffic": _keep_number(payload, "traffic", current.get("traffic") or 0, int),
        "leads": _keep_number(payload, "leads", current.get("leads") or 0, int),
        "signups": _keep_number(payload, "signups", current.get("signups") or 0, int),
        "customers": _keep_number(payload, "customers", current.get("customers") or 0, int),
        "revenue": _keep_number(payload, "revenue", current.get("revenue") or 0, float),
        "cac": _keep_number(payload, "cac", current.get("cac") or 0, float),
        "by_content": by_content,
    }
    saved = store.save_analytics(totals)
    store.append_journal({"kind": "analytics", "text": f"metrics in: {saved.get('signups')} signups"})
    return saved


def scoreboard(store: MarketingStore) -> list[dict[str, Any]]:
    analytics = store.load_analytics()
    content = {row["id"]: row for row in store.load_content() if row.get("id")}
    rows: list[dict[str, Any]] = []
    for content_id, raw in (analytics.get("by_content") or {}).items():
        if not isinstance(raw, dict):
            continue
        views = _metric(raw, "views")
        clicks = _metric(raw, "clicks")
        signups = _metric(raw, "signups")
        conversions = _metric(raw, "conversions") or signups
        ctr = (clicks / views) if views else 0.0
        item = content.get(content_id) or {}
        rows.append(
            {
                "id": content_id,
                "channel": item.get("channel"),
                "title": item.get("title") or item.get("body"),
                "views": views,
                "clicks": clicks,
                "signups": signups,
                "conversions": conversions,
                "ctr": round(ctr, 4),
            }
        )
    rows.sort(key=lambda item: (item["conversions"], item["ctr"], item["views"]), reverse=True)
    return rows


def find_winners(store: MarketingStore) -> list[dict[str, Any]]:
    rows = scoreboard(store)
    if len(rows) < 2:
        return []
    settings = store.load_settings()
    multiple = float(settings.get("winner_multiple") or 2.0)

    def _rest_mean(item: dict[str, Any], key: str) -> float:
        others = [row[key] for row in rows if row["id"] != item["id"]]
        return (sum(others) / len(others)) if others else 0.0

    winners = [
        item
        for item in rows
        if (
            _rest_mean(item, "conversions") > 0
            and item["conversions"] >= _rest_mean(item, "conversions") * multiple
        )
        or (
            _rest_mean(item, "ctr") > 0
            and item["ctr"] >= _rest_mean(item, "ctr") * multiple
            and item["views"] >= 20
        )
    ]
    for item in winners:
        try:
            store.upsert_content({"id": item["id"], "status": "winner"})
        except Exception:
            continue
    return winners


def improve_from_winner(store: MarketingStore, winner: dict[str, Any]) -> dict[str, Any]:
    product = store.load_product()
    positioning = store.load_positioning()
    settings = store.load_settings()
    channel = str(winner.get("channel") or "linkedin")
    hook = f"Double down: {winner.get('title') or positioning.get('pain')}"
    body = f"{hook}. {adapt_message(channel, product, positioning)}"
    source = "template"
    model = ""
    if ai.enabled(settings):
        try:
            control = store.get_content(str(winner.get("id") or ""))
        except MarketingError:
            control = dict(winner)
        rows = ai.write_variants(control, product, store.load_brand(), positioning, n=1, settings=settings)
        if rows:
            hook = rows[0].get("hook") or hook
            body = render_post(rows[0])
            source = "model"
            model = str(rows[0].get("model") or "")
    variant = store.upsert_content(
        {
            "channel": channel,
            "parent_id": winner.get("id"),
            "hook": hook,
            "title": f"Variant of {winner.get('id')}",
            "body": body,
            "status": "ready",
            "angle": str(winner.get("title") or "winning angle"),
            "source": source,
            "model": model,
        }
    )
    store.upsert_experiment(
        {
            "kind": "ab",
            "control": winner.get("id"),
            "variant": variant["id"],
            "channel": channel,
            "status": "running",
        }
    )
    store.append_journal(
        {"kind": "improve", "text": f"new variant {variant['id']} from winner {winner.get('id')}"}
    )
    return variant


def run_growth_cycle(store: MarketingStore) -> dict[str, Any]:
    winners = find_winners(store)
    variants = [improve_from_winner(store, winner) for winner in winners[:3]]
    return {"winners": winners, "variants": variants, "scoreboard": scoreboard(store)[:12]}
