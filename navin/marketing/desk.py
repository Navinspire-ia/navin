"""High-level Marketing desk snapshot and pipeline."""

from __future__ import annotations

from typing import Any

from navin.marketing import ai
from navin.marketing.ads import propose_ads
from navin.marketing.content import SOCIAL_CHANNELS, generate_content
from navin.marketing.creative import brief_creatives
from navin.marketing.errors import MarketingError
from navin.marketing.growth import scoreboard
from navin.marketing.heartbeat import brand_is_armed
from navin.marketing.launch import build_launch_kit
from navin.marketing.loop import peek_loop
from navin.marketing.plan import approve_campaign, build_campaign
from navin.marketing.positioning import build_positioning
from navin.marketing.publish import channel_state, publish_queue
from navin.marketing.research import build_research
from navin.marketing.seo import build_seo
from navin.marketing.social import build_social_calendar
from navin.marketing.store import SECRET_NAMES, MarketingStore
from navin.marketing.understand import understand_product


def kpis(store: MarketingStore) -> dict[str, Any]:
    analytics = store.load_analytics()
    content = store.load_content()
    by_content = analytics.get("by_content") if isinstance(analytics.get("by_content"), dict) else {}
    measured = [row for row in by_content.values() if isinstance(row, dict) and row.get("measured_at")]

    def _sum(key: str) -> float:
        total = 0.0
        for row in by_content.values():
            if not isinstance(row, dict):
                continue
            try:
                total += float(row.get(key) or 0)
            except (TypeError, ValueError):
                continue
        return total

    return {
        "campaigns": len(store.load_campaigns()),
        "content": len(content),
        "creatives": len(store.load_creatives()),
        "winners": sum(1 for row in content if row.get("status") == "winner"),
        "published": sum(1 for row in content if row.get("status") in {"published", "winner"}),
        "scheduled": sum(1 for row in content if row.get("status") == "scheduled"),
        "approved": sum(1 for row in content if row.get("status") == "approved"),
        "failed": sum(1 for row in content if row.get("status") == "failed"),
        "measured": len(measured),
        "views": int(_sum("views")),
        "clicks": int(_sum("clicks")),
        "signups": int(analytics.get("signups") or 0),
        "leads": int(analytics.get("leads") or 0),
        "traffic": int(analytics.get("traffic") or 0),
        "revenue": float(analytics.get("revenue") or 0),
    }


def snapshot(store: MarketingStore | None = None) -> dict[str, Any]:
    desk = store or MarketingStore()
    brand = desk.load_brand()
    product = desk.load_product()
    settings = desk.load_settings()
    return {
        "brand": brand,
        "product": product,
        "positioning": desk.load_positioning(),
        "research": desk.load_research(),
        "campaigns": desk.load_campaigns(),
        "content": desk.load_content()[:80],
        "creatives": desk.load_creatives()[:80],
        "experiments": desk.load_experiments()[:40],
        "analytics": desk.load_analytics(),
        "scoreboard": scoreboard(desk)[:20],
        "seo": desk.load_seo(),
        "ads": desk.load_ads(),
        "harvest": desk.load_harvest(),
        "social": desk.load_social(),
        "competitors": desk.load_competitors(),
        "launch": desk.load_launch(),
        "settings": settings,
        "connectors": channel_state(desk, settings),
        # Presence only: the values never leave marketing/secrets.json.
        "secrets_set": {name: bool(desk.get_secret(name)) for names in SECRET_NAMES.values() for name in names},
        "queue": publish_queue(desk),
        "ai": ai.routing(settings),
        "loop": peek_loop(desk),
        "journal": desk.load_journal(80),
        "kpis": kpis(desk),
        "armed": brand_is_armed(brand, product),
        "skills": [
            {"id": "marketing-strategist", "name": "Marketing Strategist"},
            {"id": "growth-marketing", "name": "Growth Marketing"},
            {"id": "digital-marketing", "name": "Digital Marketing"},
            {"id": "email-marketing", "name": "Email Marketing"},
            {"id": "marketing-analytics", "name": "Marketing Analytics"},
        ],
    }


def fill_from_site(store: MarketingStore) -> dict[str, Any]:
    """SEO, channel copy, social calendar and proposed ads from the bound site."""
    harvest = store.load_harvest()
    if not store.load_content() or harvest.get("headings") or harvest.get("ctas") or harvest.get("one_liner"):
        content = generate_content(store)
    else:
        content = store.load_content()
    seo = build_seo(store)
    social = build_social_calendar(store)
    ads = propose_ads(store)
    return {
        "content": content,
        "seo": seo,
        "social": social,
        "ads": ads,
        "desk": snapshot(store),
    }


def ship_social_pack(store: MarketingStore, *, generate: bool = True) -> dict[str, Any]:
    """Write LinkedIn / Facebook / Instagram / TikTok drafts plus stills and clips.

    Nothing is published. Bind a product or harvest a site first.
    """
    from navin.marketing.understand import _usable_product_name

    product = store.load_product()
    brand = store.load_brand()
    harvest = store.load_harvest()
    named = (
        _usable_product_name(product.get("name"))
        or _usable_product_name(harvest.get("name"))
        or _usable_product_name(brand.get("product"))
        or _usable_product_name(brand.get("company"))
    )
    if not named and not product.get("site") and not harvest.get("site") and not brand.get("site"):
        raise MarketingError("Bind a workspace, a live site, or a product first", status=400)
    build_positioning(store)
    content = generate_content(store, channels=list(SOCIAL_CHANNELS))
    from navin.marketing.produce import produce_assets

    produced = produce_assets(
        store,
        kinds=["image", "video"],
        pack="social",
        generate=generate,
        max_images=4,
        max_videos=4,
    )
    social = build_social_calendar(store)
    store.append_journal({"kind": "ship", "text": "social pack ready for LinkedIn, Facebook, Instagram, TikTok"})
    snap = snapshot(store)
    return {
        "content": content,
        "produce": {key: produced[key] for key in ("produced", "skipped") if key in produced},
        "social": social,
        "desk": snap,
    }


def run_pipeline(
    store: MarketingStore,
    *,
    workspace: str | None = None,
    goal: str = "",
    days: int = 30,
    signups: int = 1000,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Understand -> position -> research -> plan -> content -> creatives -> launch kit."""
    product = understand_product(store, workspace=workspace, extras=extras)
    positioning = build_positioning(store)
    research = build_research(store)
    campaign = build_campaign(store, goal=goal, days=days, signups=signups)
    content = generate_content(store, campaign_id=str(campaign.get("id") or ""))
    creatives = brief_creatives(store, campaign_id=str(campaign.get("id") or ""))
    seo = build_seo(store)
    social = build_social_calendar(store)
    ads = propose_ads(store)
    kit = build_launch_kit(store)
    return {
        "product": product,
        "positioning": positioning,
        "research": research,
        "campaign": campaign,
        "content": content,
        "creatives": creatives,
        "seo": seo,
        "social": social,
        "ads": ads,
        "launch": kit,
        "desk": snapshot(store),
    }


def approve_and_fill(store: MarketingStore, campaign_id: str) -> dict[str, Any]:
    campaign = approve_campaign(store, campaign_id)
    content = generate_content(store, campaign_id=campaign_id)
    creatives = brief_creatives(store, campaign_id=campaign_id)
    return {"campaign": campaign, "content": content, "creatives": creatives, "desk": snapshot(store)}
