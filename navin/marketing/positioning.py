# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""ICP, personas and value proposition from the understood product."""

from __future__ import annotations

from typing import Any

from navin.marketing import ai
from navin.marketing.store import MarketingStore


def _personas_for(category: str) -> list[str]:
    if "invoice" in category:
        return ["CFO", "comptable", "dirigeant PME"]
    if category == "crm":
        return ["Head of Sales", "AE", "fondateur"]
    if "seo" in category:
        return ["Growth lead", "content manager", "fondateur"]
    return ["acheteur", "operateur", "dirigeant"]


def _icp_for(category: str, audience: str) -> str:
    clean = str(audience or "").strip()
    if clean and clean.lower() != "pme et equipes produit qui veulent scaler sans recruter":
        return clean
    if "invoice" in category:
        return "PME de 10-200 employes"
    if category == "crm":
        return "equipes commerciales B2B de 5-50 personnes"
    return ""


def _differentiation(name: str, research: dict[str, Any]) -> str:
    names = [
        str(item.get("name") or "").strip()
        for item in (research.get("competitors") or [])
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if names:
        return f"{name} se distingue de {', '.join(names[:3])} par un cycle Create-Measure-Improve dans le meme outil que le produit"
    return f"{name} relie le produit, le contenu et la mesure dans une seule boucle"


def build_positioning(store: MarketingStore) -> dict[str, Any]:
    product = store.load_product()
    brand = store.load_brand()
    research = store.load_research()
    from navin.marketing.understand import _usable_product_name

    harvest = store.load_harvest()
    name = (
        _usable_product_name(product.get("name"))
        or _usable_product_name(harvest.get("name"))
        or _usable_product_name(brand.get("product"))
        or _usable_product_name(brand.get("company"))
        or "the product"
    )
    category = str(product.get("category") or "").strip() or "product"
    if category in {"b2b saas", "saas"} and "saas" not in f"{name} {product.get('one_liner') or ''} {harvest.get('one_liner') or ''}".lower():
        category = "product"
    pain = str(product.get("pain") or "").strip()
    if pain.lower() in {
        "trop de travail manuel pour un resultat trop lent",
        "travail manuel trop lent",
        "manual work",
    }:
        pain = ""
    raw_value = str(product.get("value_prop") or harvest.get("one_liner") or "").strip()
    if "navinprojects" in raw_value.lower().replace(" ", "") and name.lower().replace(" ", "") != "navinprojects":
        raw_value = ""
    if any(
        token in raw_value.lower()
        for token in (
            "automatise le travail repetitif",
            "supprime le travail repetitif",
            "delivers the outcome on the live site",
        )
    ):
        raw_value = ""
    value = raw_value or str(harvest.get("one_liner") or product.get("one_liner") or "").strip()
    icp = _icp_for(category, str(brand.get("audience") or ""))
    personas = _personas_for(category)
    tail = value
    if tail.lower().startswith(name.lower()):
        tail = tail[len(name) :].lstrip(" -.,:;")
    if not tail:
        tail = "reste fidele au produit lie"
    tail = tail[0].lower() + tail[1:] if tail else tail
    need = f" qui souffrent de {pain}" if pain else ""
    if icp:
        statement = (
            f"Pour {icp}{need}, {name} est un {category} qui "
            f"{tail.rstrip('.')}. Contrairement aux outils generiques, "
            f"{name} reste branche au produit reel."
        )
    else:
        statement = (
            f"{name} est un {category} qui {tail.rstrip('.')}. "
            f"Contrairement aux outils generiques, {name} reste branche au produit reel."
        )
    draft = {
        "icp": icp,
        "personas": personas,
        "pain": pain,
        "value_prop": value,
        "differentiation": _differentiation(name, research),
        "statement": statement,
        "source": "template",
        "model": "",
    }
    settings = store.load_settings()
    if name != "the product" and ai.enabled(settings):
        written = ai.write_positioning({**product, "name": name}, brand, research, settings)
        if written:
            # The model writes the prose; the template stays as a floor for empty fields.
            draft = {
                "icp": written.get("icp") or icp,
                "personas": written.get("personas") or personas,
                "pain": written.get("pain") or pain,
                "value_prop": written.get("value_prop") or value,
                "differentiation": "; ".join(written.get("differentiation") or []) or draft["differentiation"],
                "statement": written["statement"],
                "source": "model",
                "model": written.get("model") or "",
            }
    row = store.save_positioning(draft)
    store.append_journal({"kind": "positioning", "text": f"positioning set for {name} ({draft['source']})"})
    return row
