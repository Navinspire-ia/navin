"""Optional model layer for the Marketing desk.

Every function returns ``None`` (or an empty list) when no model is routed,
the flag is off, or the answer is unusable; callers keep their deterministic
template. Copy that a model writes always goes through ``clean_copy`` so a
hallucinated number or a competitor name never sneaks into a post unnoticed.
"""

from __future__ import annotations

import re
from typing import Any

from navin.agent.skill_routing import build_action_skill_context
from navin.desk_ai import ai_enabled, ask, ask_json, routing_snapshot

ENV_FLAG = "NAVIN_MARKETING_AI"

MARKETING_TASK_ROLES: dict[str, str] = {
    "understand": "fast",
    "positioning": "deep",
    "copy": "docs",
    "variants": "docs",
    "research": "fast",
    "launch": "docs",
}

_BRAND_RULES = (
    "Rules: write in the brand language (<<language>>); keep the tone '<<tone>>'; never invent "
    "numbers, customers, awards or features that are not in the facts; no em dash or en dash "
    "characters, use a plain hyphen; do not mention competitors by name unless the facts list "
    "them; forbidden words: <<forbidden>>."
)

_PRODUCT_SYSTEM = (
    "You are a product marketer reading a product's own description and landing page. Name its "
    "software category the way a buyer would search for it (2 to 4 words, lowercase, e.g. "
    "'ai coding agent', 'invoice automation', 'crm for agencies'), the buyer and the one pain "
    "it removes. Stick to what the facts say; when the facts are thin, keep the answer short "
    "rather than inventing. " + _BRAND_RULES + "\n"
    'Return JSON: {"category": str, "audience": str, "pain": str, "value_prop": str, '
    '"search_terms": [str]}. value_prop is one sentence naming the product; search_terms are '
    "3 to 6 short queries a buyer would type to find this kind of product."
)

_POSITIONING_SYSTEM = (
    "You are a B2B positioning strategist. From the product facts, write a positioning "
    "that a founder would sign. " + _BRAND_RULES + "\n"
    'Return JSON: {"icp": str, "personas": [str], "pain": str, "value_prop": str, '
    '"differentiation": [str], "statement": str}. The statement is two sentences max '
    "and names the product."
)

_COPY_SYSTEM = (
    "You are a senior social media copywriter. Write <<n>> distinct post(s) for the channel "
    "'<<channel>>' about the product below. Respect channel norms: linkedin 600-1200 chars with "
    "line breaks; x under 260 chars; facebook conversational; instagram short with hashtags; "
    "tiktok a spoken hook; blog a 120-200 word intro; email subject-like hook plus 3 short "
    "paragraphs; reddit plain and honest, no hashtags; producthunt a tagline plus maker note. "
    + _BRAND_RULES
    + '\nReturn JSON: [{"hook": str, "body": str, "cta": str, "hashtags": [str]}]. '
    "Hashtags without the # sign, 0 to 4 of them, none for linkedin, x, email, reddit."
)

_VARIANT_SYSTEM = (
    "You are a growth marketer. A post won (best conversions or click-through). Write <<n>> new "
    "variants that keep the winning angle but change the hook and the proof order. "
    + _BRAND_RULES
    + '\nReturn JSON: [{"hook": str, "body": str, "cta": str, "hashtags": [str], "angle": str}].'
)

_RESEARCH_SYSTEM = (
    "You are a market analyst. From web search results, extract real competitors of the product "
    "(companies or products solving the same problem for the same buyers), and the market trends "
    "the titles reveal. Ignore listicle sites, marketplaces, generic advice pages and the product "
    "itself. Never invent a company that is not visible in the results.\n"
    'Return JSON: {"competitors": [{"name": str, "url": str, "pricing": str, "angle": str, '
    '"note": str}], "trends": [str], "keywords": [str]}. Max 8 competitors, 6 trends, 12 keywords.'
)

_LAUNCH_SYSTEM = (
    "You write launch assets for a software product. Produce the asset named below, complete and "
    "ready to paste, in Markdown when it is a page or a mail. " + _BRAND_RULES + " Plain text only."
)

_DASHES = {"\u2014": "-", "\u2013": "-", "\u2012": "-", "\u2015": "-"}


def enabled(settings: dict[str, Any] | None = None) -> bool:
    return ai_enabled(ENV_FLAG, settings)


def routing(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return routing_snapshot(MARKETING_TASK_ROLES, env_flag=ENV_FLAG, profile=settings)


def clean_copy(text: Any) -> str:
    """Model text made safe for the desk: no long dashes, no fences, trimmed."""
    body = str(text or "")
    for bad, good in _DASHES.items():
        body = body.replace(bad, good)
    body = re.sub(r"[ \t]+\n", "\n", body)
    return body.strip()


def _facts(product: dict[str, Any], brand: dict[str, Any], positioning: dict[str, Any] | None = None) -> str:
    lines = [
        f"Product: {product.get('name') or brand.get('product') or ''}",
        f"Company: {brand.get('company') or ''}",
        f"Category: {product.get('category') or ''}",
        f"One-liner: {product.get('one_liner') or ''}",
        f"Site: {product.get('site') or brand.get('site') or ''}",
        f"Description: {brand.get('description') or ''}",
        f"Audience: {brand.get('audience') or ''}",
        f"Pain: {(positioning or {}).get('pain') or product.get('pain') or ''}",
        f"Value: {(positioning or {}).get('value_prop') or product.get('value_prop') or ''}",
    ]
    if positioning and positioning.get("statement"):
        lines.append(f"Positioning: {positioning.get('statement')}")
    if positioning and positioning.get("differentiation"):
        lines.append("Differentiation: " + "; ".join(str(item) for item in positioning.get("differentiation") or []))
    stack = product.get("stack")
    if stack:
        lines.append(f"Stack: {', '.join(str(item) for item in stack) if isinstance(stack, list) else stack}")
    docs = product.get("docs")
    if isinstance(docs, list) and docs:
        lines.append("Docs excerpts: " + " | ".join(str(item)[:200] for item in docs[:3]))
    return "\n".join(line for line in lines if not line.endswith(": "))


def _fill(template: str, brand: dict[str, Any], **extra: Any) -> str:
    """Prompt placeholders use <<name>> so the JSON braces in the schema stay literal."""
    languages = brand.get("languages") or ["fr"]
    forbidden = brand.get("forbidden") or []
    values: dict[str, Any] = {
        "language": str(languages[0] if isinstance(languages, list) and languages else languages or "fr"),
        "tone": str(brand.get("tone") or "clear and confident"),
        "forbidden": ", ".join(str(item) for item in forbidden) if forbidden else "none",
        **extra,
    }
    out = template
    for key, value in values.items():
        out = out.replace(f"<<{key}>>", str(value))
    return out


def _strings(raw: Any, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = [clean_copy(item) for item in raw if str(item or "").strip()]
    return out[:limit]


def write_product_brief(
    product: dict[str, Any],
    brand: dict[str, Any],
    harvest: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Category, buyer, pain and value read out of the facts and the live site."""
    name = str(product.get("name") or brand.get("product") or "").strip()
    if not name:
        return None
    user = _facts(product, brand)
    site = harvest if isinstance(harvest, dict) else {}
    for label, key in (("Site title", "name"), ("Site tagline", "one_liner")):
        value = str(site.get(key) or "").strip()
        if value:
            user += f"\n{label}: {value}"
    headings = [str(item).strip() for item in (site.get("headings") or []) if str(item).strip()]
    if headings:
        user += "\nSite headings: " + " | ".join(headings[:10])
    ctas = [str(item).strip() for item in (site.get("ctas") or []) if str(item).strip()]
    if ctas:
        user += "\nSite calls to action: " + ", ".join(ctas[:6])
    pages = []
    for item in site.get("pages") or []:
        if isinstance(item, dict) and (item.get("title") or item.get("description")):
            pages.append(f"{str(item.get('title') or item.get('path') or '').strip()}: {str(item.get('description') or '').strip()[:160]}")
    if pages:
        user += "\nSite pages: " + " | ".join(pages[:6])
    skills = build_action_skill_context("marketing", "understand", workspace=product.get("workspace"))
    data, model = ask_json(
        MARKETING_TASK_ROLES["understand"],
        skills.augment_system(_fill(_PRODUCT_SYSTEM, brand)),
        user,
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=500,
        temperature=0.2,
    )
    if not isinstance(data, dict):
        return None
    category = clean_copy(data.get("category")).lower().strip(" .")
    if not category or len(category.split()) > 5:
        return None
    return {
        "category": category,
        "audience": clean_copy(data.get("audience")),
        "pain": clean_copy(data.get("pain")),
        "value_prop": clean_copy(data.get("value_prop")),
        "search_terms": _strings(data.get("search_terms"), 6),
        "model": model,
        "skill_context": skills.metadata,
    }


def write_positioning(
    product: dict[str, Any],
    brand: dict[str, Any],
    research: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    name = str(product.get("name") or brand.get("product") or "").strip()
    if not name:
        return None
    user = _facts(product, brand)
    if research and research.get("competitors"):
        names = [str(row.get("name") or "") for row in research.get("competitors") or [] if isinstance(row, dict)]
        user += "\nKnown competitors: " + ", ".join(item for item in names if item)
    if research and research.get("trends"):
        user += "\nMarket trends: " + "; ".join(str(item) for item in research.get("trends") or [])
    skills = build_action_skill_context("marketing", "positioning", workspace=product.get("workspace"))
    data, model = ask_json(
        MARKETING_TASK_ROLES["positioning"],
        skills.augment_system(_fill(_POSITIONING_SYSTEM, brand)),
        user,
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=700,
        temperature=0.4,
    )
    if not isinstance(data, dict):
        return None
    statement = clean_copy(data.get("statement"))
    if not statement or name.lower() not in statement.lower():
        return None
    return {
        "icp": clean_copy(data.get("icp")),
        "personas": _strings(data.get("personas"), 6),
        "pain": clean_copy(data.get("pain")),
        "value_prop": clean_copy(data.get("value_prop")),
        "differentiation": _strings(data.get("differentiation"), 5),
        "statement": statement,
        "model": model,
        "skill_context": skills.metadata,
    }


def _posts(raw: Any, limit: int) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("posts") or raw.get("variants") or [raw]
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        body = clean_copy(item.get("body"))
        if not body:
            continue
        tags = [re.sub(r"^#+", "", clean_copy(tag)).replace(" ", "") for tag in (item.get("hashtags") or []) if str(tag or "").strip()]
        rows.append(
            {
                "hook": clean_copy(item.get("hook")),
                "body": body,
                "cta": clean_copy(item.get("cta")),
                "hashtags": [f"#{tag}" for tag in tags if tag][:4],
                "angle": clean_copy(item.get("angle")),
            }
        )
    return rows[:limit]


def write_posts(
    channel: str,
    product: dict[str, Any],
    brand: dict[str, Any],
    positioning: dict[str, Any],
    *,
    n: int = 1,
    angle: str = "",
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    user = _facts(product, brand, positioning)
    if angle:
        user += f"\nAngle to develop: {angle}"
    action = channel.strip().lower() if channel.strip().lower() in {"email", "blog"} else "copy"
    skills = build_action_skill_context("marketing", action, workspace=product.get("workspace"))
    data, model = ask_json(
        MARKETING_TASK_ROLES["copy"],
        skills.augment_system(_fill(_COPY_SYSTEM, brand, n=max(1, n), channel=channel)),
        user,
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=400 + 500 * max(1, n),
        temperature=0.7,
    )
    rows = _posts(data, max(1, n))
    for row in rows:
        row["model"] = model
        row["channel"] = channel
        row["skill_context"] = skills.metadata
    return rows


_SET_SYSTEM = (
    "You are a senior social media copywriter. Write exactly one post per channel listed below "
    "about the product. Respect channel norms: linkedin 600-1200 chars with line breaks; x under "
    "260 chars; facebook conversational; instagram short with hashtags; tiktok a spoken hook; "
    "youtube a video title plus description; blog a 120-200 word intro; email a subject-like hook "
    "plus 3 short paragraphs; reddit plain and honest, no hashtags; producthunt a tagline plus "
    "maker note; telegram a 300-600 char announcement with one link. " + _BRAND_RULES + '\nReturn JSON: [{"channel": str, "hook": str, "body": str, '
    '"cta": str, "hashtags": [str]}]. Hashtags without the # sign, 0 to 4, none for linkedin, x, '
    "email, reddit."
)


def write_channel_set(
    channels: list[str],
    product: dict[str, Any],
    brand: dict[str, Any],
    positioning: dict[str, Any],
    *,
    angle: str = "",
    settings: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """One call, one post per channel. Missing channels keep their template."""
    wanted = [str(item).lower() for item in channels if str(item).strip()]
    if not wanted:
        return {}
    user = _facts(product, brand, positioning) + "\nChannels: " + ", ".join(wanted)
    if angle:
        user += f"\nAngle to develop: {angle}"
    special_channels = set(wanted) & {"email", "blog"}
    action = wanted[0] if len(set(wanted)) == 1 and special_channels else "channel-set" if special_channels else "copy"
    skills = build_action_skill_context("marketing", action, workspace=product.get("workspace"))
    data, model = ask_json(
        MARKETING_TASK_ROLES["copy"],
        skills.augment_system(_fill(_SET_SYSTEM, brand)),
        user,
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=500 + 450 * len(wanted),
        temperature=0.7,
    )
    if isinstance(data, dict):
        data = data.get("posts") or data.get("channels") or list(data.values())
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        channel = str(item.get("channel") or "").lower().strip()
        if channel not in wanted or channel in out:
            continue
        rows = _posts([item], 1)
        if rows:
            rows[0]["model"] = model
            rows[0]["channel"] = channel
            rows[0]["skill_context"] = skills.metadata
            out[channel] = rows[0]
    return out


def write_variants(
    winner: dict[str, Any],
    product: dict[str, Any],
    brand: dict[str, Any],
    positioning: dict[str, Any],
    *,
    n: int = 2,
    settings: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    channel = str(winner.get("channel") or "linkedin")
    user = (
        _facts(product, brand, positioning)
        + f"\nChannel: {channel}\nWinning post:\n{winner.get('hook') or winner.get('title') or ''}\n{winner.get('body') or ''}"
    )
    skills = build_action_skill_context("marketing", "variants", workspace=product.get("workspace"))
    data, model = ask_json(
        MARKETING_TASK_ROLES["variants"],
        skills.augment_system(_fill(_VARIANT_SYSTEM, brand, n=max(1, n))),
        user,
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=400 + 500 * max(1, n),
        temperature=0.8,
    )
    rows = _posts(data, max(1, n))
    for row in rows:
        row["model"] = model
        row["channel"] = channel
        row["skill_context"] = skills.metadata
    return rows


def extract_research(
    product: dict[str, Any],
    brand: dict[str, Any],
    hits: list[dict[str, Any]],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Competitors and trends read out of search results by a routed model."""
    if not hits:
        return None
    lines = []
    for hit in hits[:30]:
        lines.append(f"- [{hit.get('query') or ''}] {hit.get('title') or ''} | {hit.get('url') or ''} | {str(hit.get('snippet') or '')[:220]}")
    user = _facts(product, brand) + "\n\nSearch results:\n" + "\n".join(lines)
    skills = build_action_skill_context("marketing", "research", workspace=product.get("workspace"))
    data, model = ask_json(
        MARKETING_TASK_ROLES["research"],
        skills.augment_system(_RESEARCH_SYSTEM),
        user,
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=900,
        temperature=0.2,
    )
    if not isinstance(data, dict):
        return None
    visible = " ".join(f"{hit.get('title') or ''} {hit.get('url') or ''} {hit.get('snippet') or ''}" for hit in hits).lower()
    own = str(product.get("name") or brand.get("product") or "").strip().lower()
    competitors: list[dict[str, Any]] = []
    for row in data.get("competitors") or []:
        if not isinstance(row, dict):
            continue
        name = clean_copy(row.get("name"))
        if not name or name.lower() == own or name.lower() not in visible:
            continue
        competitors.append(
            {
                "name": name,
                "url": clean_copy(row.get("url")),
                "pricing": clean_copy(row.get("pricing")),
                "angle": clean_copy(row.get("angle")) or "competitor",
                "note": clean_copy(row.get("note")),
                "source": clean_copy(row.get("url")),
            }
        )
    return {
        "competitors": competitors[:8],
        "trends": _strings(data.get("trends"), 6),
        "keywords": [item.lower() for item in _strings(data.get("keywords"), 12)],
        "model": model,
        "skill_context": skills.metadata,
    }


def write_launch_asset(
    item: str,
    label: str,
    product: dict[str, Any],
    brand: dict[str, Any],
    positioning: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> str:
    skills = build_action_skill_context("marketing", "launch", workspace=product.get("workspace"))
    text, _model = ask(
        MARKETING_TASK_ROLES["launch"],
        skills.augment_system(_fill(_LAUNCH_SYSTEM, brand)),
        f"Asset: {label} ({item})\n\n{_facts(product, brand, positioning)}",
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=900,
        temperature=0.5,
    )
    return clean_copy(text)


_KIT_SYSTEM = (
    "You write a complete launch kit for a software product. For each asset id listed, produce "
    "the asset, complete and ready to paste (Markdown for pages, mails and articles; plain text "
    "for posts; a shot list for briefs). " + _BRAND_RULES + '\nReturn JSON: {"<asset id>": "<text>"} '
    "with exactly the requested ids as keys."
)


def write_launch_kit(
    items: list[tuple[str, str]],
    product: dict[str, Any],
    brand: dict[str, Any],
    positioning: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> dict[str, str]:
    """One call for the whole text kit; ids the model skipped keep the template."""
    if not items:
        return {}
    listing = "\n".join(f"- {key}: {label}" for key, label in items)
    skills = build_action_skill_context("marketing", "launch", workspace=product.get("workspace"))
    data, model = ask_json(
        MARKETING_TASK_ROLES["launch"],
        skills.augment_system(_fill(_KIT_SYSTEM, brand)),
        f"{_facts(product, brand, positioning)}\n\nAssets:\n{listing}",
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=600 + 450 * len(items),
        temperature=0.5,
        timeout_s=180.0,
    )
    if not isinstance(data, dict):
        return {}
    wanted = {key for key, _label in items}
    out: dict[str, str] = {}
    for key, value in data.items():
        text = clean_copy(value if isinstance(value, str) else "")
        if key in wanted and len(text) >= 20:
            out[str(key)] = text
    if out:
        out["__model__"] = model
    return out
