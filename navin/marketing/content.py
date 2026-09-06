"""One core message, rewritten for each channel from the bound product."""

from __future__ import annotations

import re
from typing import Any

from navin.marketing import ai
from navin.marketing.errors import MarketingError
from navin.marketing.pack import SOCIAL_CHANNELS
from navin.marketing.store import CHANNELS, MarketingStore
from navin.marketing.understand import _usable_product_name

_TEMPLATES = {
    "linkedin": "{name}: your team should not spend hours on {pain}. {value}.",
    "x": "{pain} should not take all afternoon. {name} takes it off your plate.",
    "instagram": "{name} - less {pain}, more time for the work that matters.",
    "facebook": "Still dealing with {pain}? {name} handles it for you.",
    "tiktok": "POV: {pain} used to eat your morning. {name} just finished it.",
    "youtube": "How {name} removes {pain} - product demo.",
    "blog": "How {name} reduces {pain} without adding headcount.",
    "email": "Still doing {pain} by hand?",
    "reddit": "We built {name} because {pain} was eating our week. Here is what changed.",
    "producthunt": "{name} - {value}",
    "telegram": "{name}: {value}. No more {pain}.",
}

_CTA = {
    "linkedin": "Book a walkthrough.",
    "x": "Reply START for the 60s clip.",
    "instagram": "Link in bio.",
    "facebook": "Start free.",
    "tiktok": "Follow for the full demo.",
    "youtube": "Watch the product demo.",
    "blog": "Try it on your own stack this week.",
    "email": "Open the product and run the first task.",
    "reddit": "Happy to share a setup if you want it.",
    "producthunt": "Upvote if this would save you a seat.",
    "telegram": "Try it and tell us what breaks.",
}

_GENERIC_PAIN = {
    "trop de travail manuel pour un resultat trop lent",
    "travail manuel trop lent",
    "manual work",
}
_GENERIC_VALUE = (
    "automatise le travail repetitif",
    "supprime le travail repetitif",
    "delivers the outcome on the live site",
)

_TAGS = {
    "linkedin": ["#B2B", "#SaaS"],
    "x": ["#buildinpublic"],
    "instagram": ["#product"],
    "facebook": ["#smallbusiness"],
    "tiktok": ["#product"],
    "youtube": ["#productdemo"],
    "blog": [],
    "email": [],
    "reddit": [],
    "producthunt": ["#ProductHunt"],
    "telegram": [],
}


def _first_text(items: Any) -> str:
    if isinstance(items, list):
        for item in items:
            text = str(item or "").strip()
            if text:
                return text
    return str(items or "").strip()


def _bound_text(value: Any, name: str) -> str:
    """Keep copy on the bound product. The projects folder name is never a brand."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    product = name.strip() if name and name != "the product" else ""
    if "navinprojects" in raw.lower().replace(" ", "") and product.lower().replace(" ", "") != "navinprojects":
        raw = re.sub(r"\bNavinProjects\b", product or "this product", raw, flags=re.I)
        raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _same(left: str, right: str) -> bool:
    return bool(left) and bool(right) and left.strip().lower() == right.strip().lower()


def _contained(needle: str, hay: str) -> bool:
    return bool(needle) and needle.strip().lower() in hay.strip().lower()


def _is_generic_pain(value: str) -> bool:
    return str(value or "").strip().lower() in _GENERIC_PAIN


def _is_generic_value(value: str) -> bool:
    low = str(value or "").strip().lower()
    return bool(low) and any(token in low for token in _GENERIC_VALUE)


def _join_unique(*parts: str) -> str:
    out: list[str] = []
    seen: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if not text:
            continue
        low = text.lower()
        if any(low == item or low in item or item in low for item in seen):
            continue
        out.append(text)
        seen.append(low)
    return " ".join(out)


def product_facts(store: MarketingStore) -> dict[str, str]:
    """Facts the copy must stay glued to. Empty fields stay empty."""
    product = store.load_product()
    brand = store.load_brand()
    harvest = store.load_harvest()
    positioning = store.load_positioning()
    name = (
        _usable_product_name(product.get("name"))
        or _usable_product_name(harvest.get("name"))
        or _usable_product_name(brand.get("product"))
        or _usable_product_name(brand.get("company"))
        or "the product"
    )
    heading = _bound_text(_first_text(harvest.get("headings")), name)
    if _same(heading, name):
        heading = ""
    one_liner = _bound_text(
        product.get("one_liner") or harvest.get("one_liner") or brand.get("description") or "",
        name,
    )
    if _same(one_liner, name):
        one_liner = ""
    value = _bound_text(positioning.get("value_prop") or product.get("value_prop") or "", name)
    if _same(value, name) or _is_generic_value(value):
        value = one_liner
    pain = _bound_text(positioning.get("pain") or product.get("pain") or "", name)
    if _is_generic_pain(pain):
        pain = ""
    category = str(product.get("category") or "").strip()
    hay = f"{name} {one_liner}".lower()
    if category in {"b2b saas", "saas"} and "saas" not in hay and "crm" not in hay:
        category = "product"
    return {
        "name": name,
        "one_liner": one_liner,
        "heading": heading,
        "cta": _bound_text(_first_text(harvest.get("ctas")), name),
        "site": str(product.get("site") or harvest.get("site") or brand.get("site") or "").strip(),
        "audience": _bound_text(
            ""
            if str(brand.get("audience") or positioning.get("icp") or "").strip().lower()
            == "pme et equipes produit qui veulent scaler sans recruter"
            else (brand.get("audience") or positioning.get("icp") or ""),
            name,
        ),
        "pain": pain,
        "value": value,
        "category": category,
        "tone": str(brand.get("tone") or "clear and confident").strip(),
    }


def core_message(product: dict[str, Any], positioning: dict[str, Any]) -> str:
    name = _usable_product_name(product.get("name")) or "the product"
    pain = _bound_text(positioning.get("pain") or product.get("pain") or "", name)
    if _is_generic_pain(pain):
        pain = ""
    value = _bound_text(positioning.get("value_prop") or product.get("value_prop") or "", name)
    if _is_generic_value(value):
        value = ""
    if pain and value:
        return f"{name} removes {pain}. {value}."
    if value:
        return f"{name}: {value}."
    if pain:
        return f"{name} removes {pain}."
    return name


def adapt_message(channel: str, product: dict[str, Any], positioning: dict[str, Any]) -> str:
    if channel not in CHANNELS:
        raise MarketingError(f"unknown channel {channel}")
    name = _usable_product_name(product.get("name")) or "the product"
    pain = _bound_text(positioning.get("pain") or product.get("pain") or "", name)
    if _is_generic_pain(pain):
        pain = ""
    value = _bound_text(positioning.get("value_prop") or product.get("value_prop") or "", name)
    if _is_generic_value(value):
        value = ""
    if not pain or not value:
        proof = value or pain
        if not proof:
            simple = {
                "linkedin": name,
                "x": name,
                "instagram": name,
                "facebook": f"Meet {name}.",
                "tiktok": f"POV: {name}",
                "youtube": f"How {name} works - product demo.",
                "blog": f"How {name} works.",
                "email": f"A note on {name}.",
                "reddit": f"We built {name}.",
                "producthunt": name,
                "telegram": f"{name} is live.",
            }
            return simple.get(channel, name)
        fallback = {
            "linkedin": f"{name}: {proof}",
            "x": f"{proof} - {name}",
            "instagram": f"{name} - {proof}",
            "facebook": f"Meet {name}. {proof}",
            "tiktok": f"POV: {name}. {proof}",
            "youtube": f"How {name} works - product demo.",
            "blog": f"How {name} works without extra headcount.",
            "email": f"A note on {name}.",
            "reddit": f"We built {name}. Here is what changed.",
            "producthunt": f"{name} - {proof}",
            "telegram": f"{name} is live. {proof}",
        }
        return fallback.get(channel, f"{name}: {proof}")
    template = _TEMPLATES[channel]
    return template.format(name=name, pain=pain, value=value)


def render_post(post: dict[str, Any]) -> str:
    """Hook, body, CTA and hashtags as one publishable text."""
    parts = [str(post.get("hook") or "").strip(), str(post.get("body") or "").strip(), str(post.get("cta") or "").strip()]
    if parts[0] and parts[1] and _contained(parts[0], parts[1]):
        parts[0] = ""
    if parts[2] and parts[1] and _contained(parts[2], parts[1]):
        parts[2] = ""
    text = "\n\n".join(part for part in parts if part)
    tags = [str(tag).strip() for tag in (post.get("hashtags") or []) if str(tag).strip()]
    if tags:
        text = f"{text}\n\n{' '.join(tags)}"
    return text.strip()


def _hashtag(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "", name)
    return f"#{slug[:28]}" if len(slug) >= 3 else ""


def _tags_for(channel: str, facts: dict[str, str]) -> list[str]:
    tags: list[str] = []
    own = _hashtag(facts["name"])
    if own and facts["name"] != "the product":
        tags.append(own)
    category = (facts.get("category") or "").lower()
    if category and category not in {"product", "b2b saas", "saas"}:
        cat = _hashtag(category)
        if cat and cat not in tags:
            tags.append(cat)
    saas_like = any(token in category for token in ("saas", "crm", "invoice"))
    if saas_like:
        for item in _TAGS.get(channel) or []:
            if item not in tags:
                tags.append(item)
    return tags[:4]


def _channel_body(channel: str, facts: dict[str, str], adapted: str) -> str:
    name = facts["name"]
    one = facts["one_liner"]
    heading = facts["heading"]
    pain = facts["pain"]
    value = facts["value"]
    site = facts["site"]
    audience = facts["audience"]
    if channel == "linkedin":
        proof = heading or one or value or adapted
        extra = one if one and not _contained(one, proof) else ""
        worth = value if value and not _contained(value, f"{proof} {extra}") else ""
        who = f"Built for {audience}." if audience else ""
        return _join_unique(proof, extra, worth, who, site)
    if channel == "facebook":
        ask = f"Still dealing with {pain}?" if pain else f"Meet {name}."
        rest = one or adapted
        if rest and _contained(ask, rest):
            ask = ""
        worth = value if value and not _contained(value, rest) else ""
        return _join_unique(ask, rest, worth, site)
    if channel == "instagram":
        line = heading or one or (f"{name} - {value}" if value else adapted)
        extra = one if one and not _contained(one, line) else ""
        return _join_unique(line, extra, site)
    if channel == "tiktok":
        hook = heading or (f"POV: {pain}" if pain else f"POV: {name}")
        rest = one or adapted
        return _join_unique(hook, rest, name)
    body = adapted
    if heading and not _contained(heading, body):
        body = f"{heading} {body}"
    if one and not _contained(one, body) and channel in {"blog", "email", "youtube"}:
        body = f"{body} {one}"
    return body.strip()


REFILL_ANGLES = (
    "a customer pain story told in the first person",
    "before and after: the day with and without the product",
    "the objection buyers raise most, answered honestly",
    "behind the scenes: how one feature was built and why",
    "a how-to in three steps that gives value even without the product",
    "myth versus reality in this category",
    "a founder note on why the product exists",
    "one concrete use case for one persona",
)


def generate_content(
    store: MarketingStore,
    *,
    channels: list[str] | None = None,
    campaign_id: str = "",
    hook: str = "",
    angle: str = "",
    fresh: bool = False,
) -> list[dict[str, Any]]:
    """One post per channel. ``angle`` steers a routed model without leaking into template copy;
    ``fresh`` always creates new rows instead of rewriting the batch that is still unsent."""
    product = store.load_product()
    positioning = store.load_positioning()
    facts = product_facts(store)
    stale = {}
    if _is_generic_pain(str(product.get("pain") or "")):
        stale["pain"] = facts["pain"]
    if str(product.get("category") or "") in {"b2b saas", "saas"} and facts["category"] == "product":
        stale["category"] = "product"
    if _is_generic_value(str(product.get("value_prop") or "")):
        stale["value_prop"] = facts["value"]
    if stale:
        store.save_product({**product, **stale})
    picked = [item.lower() for item in (channels or []) if str(item).lower() in CHANNELS]
    if not picked:
        campaign = store.get_campaign(campaign_id) if campaign_id else None
        picked = list((campaign or {}).get("channels") or SOCIAL_CHANNELS)
    core = hook.strip() or facts["heading"] or facts["one_liner"] or core_message(product, positioning)
    settings = store.load_settings()
    written: dict[str, dict[str, Any]] = {}
    if facts["name"] != "the product" and ai.enabled(settings):
        written = ai.write_channel_set(
            picked,
            {**product, "name": facts["name"]},
            store.load_brand(),
            positioning,
            angle=angle.strip() or hook.strip(),
            settings=settings,
        )
    created: list[dict[str, Any]] = []
    for channel in picked:
        adapted = adapt_message(channel, product, positioning)
        heading = hook.strip() or facts["heading"]
        body = _channel_body(channel, {**facts, "heading": heading}, adapted)
        cta = facts["cta"] or _CTA.get(channel, "Start free.")
        tags = _tags_for(channel, facts)
        full = body
        if cta and not _contained(cta, full):
            full = f"{full} {cta}".strip()
        if tags:
            full = f"{full} {' '.join(tags)}"
        source = "template"
        model = ""
        post = written.get(channel)
        if post:
            full = render_post(post)
            cta = post.get("cta") or cta
            tags = post.get("hashtags") or tags
            if post.get("hook"):
                heading = post["hook"]
            source = "model"
            model = str(post.get("model") or "")
        # Only an unsent draft is rewritten in place; approved, scheduled or published posts stay as sent.
        existing = (
            None
            if fresh
            else next(
                (
                    row
                    for row in store.load_content()
                    if row.get("channel") == channel
                    and str(row.get("campaign_id") or "") == campaign_id
                    and not row.get("parent_id")
                    and row.get("status") in {"draft", "ready", "failed"}
                ),
                None,
            )
        )
        row = store.upsert_content(
            {
                **(existing or {}),
                "channel": channel,
                "campaign_id": campaign_id,
                "hook": hook or heading or facts["one_liner"] or core,
                "title": (heading or facts["one_liner"] or body).split(" - ", 1)[0][:80],
                "body": full,
                "cta": cta,
                "hashtags": tags,
                "status": "ready",
                "angle": angle or hook or facts["pain"] or facts["heading"] or "product",
                "source": source,
                "model": model,
            }
        )
        created.append(row)
    if not campaign_id:
        keep = {str(row.get("id") or "") for row in created}
        leftover = []
        for row in store.load_content():
            rid = str(row.get("id") or "")
            parent = str(row.get("parent_id") or "")
            if rid in keep or parent in keep:
                leftover.append(row)
                continue
            hay = f"{row.get('body') or ''} {row.get('hook') or ''} {row.get('title') or ''}"
            if "navinprojects" in hay.lower().replace(" ", "") and facts["name"].lower().replace(" ", "") != "navinprojects":
                continue
            # Sent or queued posts are history; only stale free drafts are pruned.
            if row.get("campaign_id") or row.get("status") not in {"draft", "ready"}:
                leftover.append(row)
        store.save_content(leftover)
    store.append_journal({"kind": "content", "text": f"wrote {len(created)} channel variants"})
    return created
