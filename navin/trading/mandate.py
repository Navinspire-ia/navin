# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""User-or-agent mandate: domains, countries, risk, targets, alert channels."""

from __future__ import annotations

import re
from typing import Any

DOMAINS = ("equities", "crypto", "nft", "realestate")

DOMAIN_LABELS = {
    "equities": "Bourse",
    "crypto": "Crypto",
    "nft": "NFT",
    "realestate": "Immobilier",
}

# Liquid country books. Agent mode walks these; the user can narrow the list.
COUNTRIES: dict[str, dict[str, Any]] = {
    "US": {"name": "United States", "region": "americas"},
    "CA": {"name": "Canada", "region": "americas"},
    "BR": {"name": "Brazil", "region": "americas"},
    "MX": {"name": "Mexico", "region": "americas"},
    "AR": {"name": "Argentina", "region": "americas"},
    "CL": {"name": "Chile", "region": "americas"},
    "CO": {"name": "Colombia", "region": "americas"},
    "PE": {"name": "Peru", "region": "americas"},
    "GB": {"name": "United Kingdom", "region": "europe"},
    "FR": {"name": "France", "region": "europe"},
    "DE": {"name": "Germany", "region": "europe"},
    "NL": {"name": "Netherlands", "region": "europe"},
    "BE": {"name": "Belgium", "region": "europe"},
    "ES": {"name": "Spain", "region": "europe"},
    "IT": {"name": "Italy", "region": "europe"},
    "CH": {"name": "Switzerland", "region": "europe"},
    "SE": {"name": "Sweden", "region": "europe"},
    "NO": {"name": "Norway", "region": "europe"},
    "DK": {"name": "Denmark", "region": "europe"},
    "FI": {"name": "Finland", "region": "europe"},
    "AT": {"name": "Austria", "region": "europe"},
    "PT": {"name": "Portugal", "region": "europe"},
    "IE": {"name": "Ireland", "region": "europe"},
    "PL": {"name": "Poland", "region": "europe"},
    "CZ": {"name": "Czechia", "region": "europe"},
    "GR": {"name": "Greece", "region": "europe"},
    "TR": {"name": "Turkiye", "region": "europe"},
    "RU": {"name": "Russia", "region": "europe"},
    "JP": {"name": "Japan", "region": "asia"},
    "CN": {"name": "China", "region": "asia"},
    "HK": {"name": "Hong Kong", "region": "asia"},
    "TW": {"name": "Taiwan", "region": "asia"},
    "KR": {"name": "South Korea", "region": "asia"},
    "IN": {"name": "India", "region": "asia"},
    "SG": {"name": "Singapore", "region": "asia"},
    "AU": {"name": "Australia", "region": "asia"},
    "NZ": {"name": "New Zealand", "region": "asia"},
    "ID": {"name": "Indonesia", "region": "asia"},
    "TH": {"name": "Thailand", "region": "asia"},
    "MY": {"name": "Malaysia", "region": "asia"},
    "PH": {"name": "Philippines", "region": "asia"},
    "VN": {"name": "Vietnam", "region": "asia"},
    "PK": {"name": "Pakistan", "region": "asia"},
    "AE": {"name": "United Arab Emirates", "region": "mena"},
    "SA": {"name": "Saudi Arabia", "region": "mena"},
    "QA": {"name": "Qatar", "region": "mena"},
    "KW": {"name": "Kuwait", "region": "mena"},
    "IL": {"name": "Israel", "region": "mena"},
    "EG": {"name": "Egypt", "region": "mena"},
    "ZA": {"name": "South Africa", "region": "africa"},
    "NG": {"name": "Nigeria", "region": "africa"},
    "KE": {"name": "Kenya", "region": "africa"},
}

_COUNTRY_ALIASES = {
    "usa": "US",
    "etats-unis": "US",
    "états-unis": "US",
    "america": "US",
    "uk": "GB",
    "united kingdom": "GB",
    "angleterre": "GB",
    "britain": "GB",
    "france": "FR",
    "allemagne": "DE",
    "germany": "DE",
    "japon": "JP",
    "japan": "JP",
    "chine": "CN",
    "china": "CN",
    "hong kong": "HK",
    "inde": "IN",
    "india": "IN",
    "emirats": "AE",
    "émirats": "AE",
    "dubai": "AE",
    "abou dhabi": "AE",
    "arabie": "SA",
    "canada": "CA",
    "bresil": "BR",
    "brésil": "BR",
    "australie": "AU",
    "suisse": "CH",
    "espagne": "ES",
    "italie": "IT",
    "pays-bas": "NL",
    "singapour": "SG",
    "coree": "KR",
    "corée": "KR",
    "taiwan": "TW",
    "taïwan": "TW",
    "mexique": "MX",
    "afrique du sud": "ZA",
}

AGENT_COUNTRIES = tuple(COUNTRIES)

# Official euro area in this desk. Other books display in USD.
EURO_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "CY",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PT",
        "SK",
        "SI",
        "ES",
    }
)


def book_currency(countries: list[str] | tuple[str, ...] | None) -> str:
    """EUR only when every selected country is in the euro area. Otherwise USD."""
    codes = [str(code or "").strip().upper() for code in (countries or []) if str(code or "").strip()]
    if codes and all(code in EURO_COUNTRIES for code in codes):
        return "EUR"
    return "USD"


AGENT_DOMAINS = list(DOMAINS)

_DOMAIN_NEEDLES = {
    "equities": ("bourse", "action", "equity", "equities", "stock", "nasdaq", "cac", "dax", "ftse"),
    "crypto": ("crypto", "bitcoin", "btc", "eth", "solana", "token"),
    "nft": ("nft", "nfts", "mft", "opensea", "floor"),
    "realestate": ("immo", "immobilier", "real estate", "reit", "foncier", "logement", "property"),
}

_AGENT_NEEDLES = (
    "laisse l'agent",
    "laisse l agent",
    "let the agent",
    "agent decide",
    "agent decides",
    "agent gère",
    "agent gere",
    "tu decids",
    "tu decides",
    "a toi de",
    "manage for me",
    "au choix de l'agent",
    "au choix de l agent",
)


def default_mandate() -> dict[str, Any]:
    return {
        "domains": ["equities"],
        "domains_mode": "user",
        "countries": ["US"],
        "countries_mode": "user",
        "tapes": [{"domain": "equities", "country": "US"}],
        "risk_mode": "user",
        "target_mode": "user",
        "target_return_pct": 12.0,
        "channels": {
            "telegram": False,
            "whatsapp": False,
            "email": False,
            "telegram_to": "",
            "whatsapp_to": "",
            "email_to": "",
        },
    }


def catalog() -> dict[str, Any]:
    return {
        "domains": [{"id": key, "name": DOMAIN_LABELS[key]} for key in DOMAINS],
        "countries": [
            {"id": code, "name": meta["name"], "region": meta["region"]}
            for code, meta in COUNTRIES.items()
        ],
    }


def _clean_domains(raw: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        key = str(item or "").strip().lower()
        if key in {"mft", "nfts"}:
            key = "nft"
        if key in {"immobilier", "real_estate", "re"}:
            key = "realestate"
        if key in {"equity", "stock", "stocks", "bourse"}:
            key = "equities"
        if key not in DOMAINS or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _clean_countries(raw: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        code = str(item or "").strip().upper()
        if len(code) != 2:
            code = _COUNTRY_ALIASES.get(str(item or "").strip().lower(), "")
        if code not in COUNTRIES or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _cartesian_tapes(domains: list[str], countries: list[str]) -> list[dict[str, str]]:
    tapes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for domain in domains or ["equities"]:
        for country in countries or ["US"]:
            key = (domain, country)
            if key in seen:
                continue
            seen.add(key)
            tapes.append({"domain": domain, "country": country})
    return tapes


def _clean_tapes(raw: Any) -> list[dict[str, str]]:
    tapes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        domains = _clean_domains([item.get("domain")])
        countries = _clean_countries([item.get("country")])
        if not domains or not countries:
            continue
        key = (domains[0], countries[0])
        if key in seen:
            continue
        seen.add(key)
        tapes.append({"domain": domains[0], "country": countries[0]})
    return tapes


def _mode(value: Any, fallback: str = "user") -> str:
    text = str(value or fallback).strip().lower()
    return "agent" if text in {"agent", "auto", "managed"} else "user"


def normalize_mandate(raw: Any) -> dict[str, Any]:
    base = default_mandate()
    src = raw if isinstance(raw, dict) else {}
    domains = _clean_domains(src.get("domains"))
    countries = _clean_countries(src.get("countries"))
    channels_in = src.get("channels") if isinstance(src.get("channels"), dict) else {}
    channels = dict(base["channels"])
    for key in ("telegram", "whatsapp", "email"):
        if key in channels_in:
            channels[key] = bool(channels_in.get(key))
        dest = channels_in.get(f"{key}_to")
        if dest is not None:
            channels[f"{key}_to"] = str(dest).strip()
    try:
        target = float(src.get("target_return_pct", base["target_return_pct"]))
    except (TypeError, ValueError):
        target = 12.0
    if "tapes" in src:
        tapes = _clean_tapes(src.get("tapes"))
    else:
        tapes = []
    if tapes:
        domains = _clean_domains([item["domain"] for item in tapes])
        countries = _clean_countries([item["country"] for item in tapes])
    else:
        domains = domains or list(base["domains"])
        countries = countries or list(base["countries"])
        tapes = _cartesian_tapes(domains, countries)
    return {
        "domains": domains or list(base["domains"]),
        "domains_mode": _mode(src.get("domains_mode"), "user"),
        "countries": countries or list(base["countries"]),
        "countries_mode": _mode(src.get("countries_mode"), "user"),
        "tapes": tapes,
        "risk_mode": _mode(src.get("risk_mode"), "user"),
        "target_mode": _mode(src.get("target_mode"), "user"),
        "target_return_pct": max(1.0, min(80.0, target)),
        "channels": channels,
    }


def resolve_mandate(mandate: dict[str, Any] | None) -> dict[str, Any]:
    """Apply agent-managed defaults without wiping the user's saved picks."""
    row = normalize_mandate(mandate)
    if row["domains_mode"] == "agent":
        row["active_domains"] = list(AGENT_DOMAINS)
    else:
        row["active_domains"] = list(row["domains"])
    if row["countries_mode"] == "agent":
        row["active_countries"] = list(AGENT_COUNTRIES)
    else:
        row["active_countries"] = list(row["countries"])
    if row["domains_mode"] == "user" and row["countries_mode"] == "user":
        row["active_tapes"] = list(row["tapes"])
    else:
        row["active_tapes"] = []
    row["currency"] = book_currency(row["active_countries"])
    return row


def agent_risk_for(domains: list[str]) -> dict[str, float]:
    """Conservative code limits when the user lets the agent pick risk."""
    set_ = set(domains or ["equities"])
    max_pos = 3.0
    stop = 5.0
    daily = 2.0
    drawdown = 10.0
    approval = 2000.0
    if "crypto" in set_:
        max_pos = min(max_pos, 2.0)
        stop = max(stop, 8.0)
        daily = min(daily, 1.5)
        drawdown = min(drawdown, 12.0)
    if "nft" in set_:
        max_pos = min(max_pos, 1.5)
        stop = max(stop, 12.0)
        daily = min(daily, 1.5)
        drawdown = min(drawdown, 15.0)
        approval = min(approval, 800.0)
    if "realestate" in set_ and set_ == {"realestate"}:
        max_pos = 5.0
        stop = 8.0
        daily = 1.5
        drawdown = 12.0
        approval = 4000.0
    return {
        "max_position_pct": max_pos,
        "stop_loss_pct": stop,
        "max_daily_loss_pct": daily,
        "max_drawdown_pct": drawdown,
        "approval_notional": approval,
        "take_profit_pct": 12.0,
    }


def merge_mandate(current: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, Any]:
    """Patch a saved mandate. Incoming tapes win; otherwise lists rebuild the pairs."""
    src = incoming if isinstance(incoming, dict) else {}
    merged = {**(current or {}), **src}
    if "tapes" not in src:
        merged.pop("tapes", None)
    return normalize_mandate(merged)


def parse_mandate_from_brief(text: str, current: dict[str, Any] | None = None) -> dict[str, Any]:
    row = normalize_mandate(current)
    blob = (text or "").lower()
    if not blob:
        return row
    found_domains = [key for key, needles in _DOMAIN_NEEDLES.items() if any(n in blob for n in needles)]
    if found_domains:
        row["domains"] = found_domains
        row["domains_mode"] = "user"
    found_countries: list[str] = []
    for alias, code in _COUNTRY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", blob):
            found_countries.append(code)
    for code, meta in COUNTRIES.items():
        if re.search(rf"\b{re.escape(meta['name'].lower())}\b", blob):
            found_countries.append(code)
    if found_countries:
        row["countries"] = _clean_countries(found_countries)
        row["countries_mode"] = "user"
    if found_domains or found_countries:
        row.pop("tapes", None)
    agentish = any(needle in blob for needle in _AGENT_NEEDLES)
    if agentish:
        specified = False
        if re.search(r"domaine|domain", blob) and not found_domains:
            row["domains_mode"] = "agent"
            specified = True
        if re.search(r"pays|countr", blob) and not found_countries:
            row["countries_mode"] = "agent"
            specified = True
        if re.search(r"risque|risk|seuil", blob):
            row["risk_mode"] = "agent"
            specified = True
        if re.search(r"gain|profit|rendement|return|cible", blob) and not re.search(
            r"\d+(?:[.,]\d+)?\s*%", blob
        ):
            row["target_mode"] = "agent"
            specified = True
        if not specified and not found_domains and not found_countries:
            row["domains_mode"] = "agent"
            row["countries_mode"] = "agent"
            row["risk_mode"] = "agent"
            row["target_mode"] = "agent"
    if re.search(r"tous les pays|all countries|monde entier|worldwide", blob):
        row["countries_mode"] = "agent"
    if re.search(r"tous les domaines|all domains|partout", blob):
        row["domains_mode"] = "agent"
    match = re.search(
        r"(?:gain|profit|rendement|cible|target)[^0-9]{0,20}(\d+(?:[.,]\d+)?)\s*%",
        blob,
    )
    if match:
        row["target_return_pct"] = float(match.group(1).replace(",", "."))
        row["target_mode"] = "user"
    return normalize_mandate(row)
