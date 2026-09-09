# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Natural-language strategy compiler. Output is a structured book, not a vibe."""

from __future__ import annotations

import re
from typing import Any

from navin.trading.store import default_strategy

_SKILLS = {
    "value-investor": ("value", "valorisation", "pe ", "p/e", "discount"),
    "momentum-trader": ("momentum", "tendance", "breakout", "haussière", "swing"),
    "crypto-swing": ("btc", "eth", "sol", "crypto"),
    "nft-collector": ("nft", "nfts", "mft", "floor", "opensea"),
    "real-estate-investor": ("immo", "immobilier", "reit", "foncier", "logement"),
    "global-equities": ("bourse", "cac", "dax", "ftse", "nikkei", "worldwide", "pays"),
    "dividend-portfolio": ("dividend", "dividende", "yield", "revenu"),
    "earnings-trader": ("earnings", "résultat", "guidance", "eps"),
    "macro-investor": ("macro", "fed", "taux", "inflation"),
}


def infer_skill(text: str) -> str:
    blob = (text or "").lower()
    hits = [skill for skill, needles in _SKILLS.items() if any(needle in blob for needle in needles)]
    if len(hits) >= 2:
        return "trading-agent"
    return hits[0] if hits else "momentum-trader"


def _pct(text: str, patterns: tuple[str, ...], fallback: float) -> float:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        try:
            return float(match.group(1).replace(",", "."))
        except (TypeError, ValueError):
            continue
    return fallback


def _mode(text: str) -> str:
    blob = text.lower()
    if re.search(r"research only|recherche seulement|sans ordre", blob):
        return "research"
    if re.search(r"recommend|recommande|ne (trade|négocie) pas", blob):
        return "recommend"
    if re.search(r"demande[- ]moi validation|ask me (before|to (approve|validat))|approval mode", blob):
        if re.search(r"sup[eé]rieur|above|over|seuil", blob):
            return "autonomous"
        return "approval"
    return "autonomous"


def _universe(text: str) -> tuple[str, list[str]]:
    blob = text.lower()
    symbols = [item.upper() for item in re.findall(r"\b([A-Z]{2,5}(?:-USD)?)\b", text)]
    # Drop French/English glue words captured as tickers.
    deny = {
        "US", "THE", "AND", "FOR", "SUR", "LES", "DES", "UNE", "QUE", "PAS",
        "AVEC", "DANS", "PLUS", "SI", "NE", "JE", "TU", "IL", "ON", "CA",
        "BTC", "ETH", "SOL",
    }
    cleaned = [item for item in symbols if item not in deny]
    nft = any(word in blob for word in ("nft", "nfts", "mft", "opensea"))
    reit = any(word in blob for word in ("immo", "immobilier", "reit", "foncier"))
    crypto = any(word in blob for word in ("btc", "eth", "sol", "crypto"))
    nasdaq = "nasdaq" in blob or "ndx" in blob
    hits = sum(1 for flag in (nft, reit, crypto, nasdaq) if flag)
    if hits > 1:
        extras = list(cleaned)
        if "btc" in blob:
            extras.append("BTC-USD")
        if "eth" in blob:
            extras.append("ETH-USD")
        if "sol" in blob:
            extras.append("SOL-USD")
        return "MIXED", extras
    if nft:
        return "NFT", cleaned
    if reit:
        return "REALESTATE", cleaned
    if nasdaq:
        return "NASDAQ100", cleaned
    if crypto:
        extras = []
        if "btc" in blob:
            extras.append("BTC-USD")
        if "eth" in blob:
            extras.append("ETH-USD")
        if "sol" in blob:
            extras.append("SOL-USD")
        return "CRYPTO", extras or cleaned
    return "NASDAQ100", cleaned


def parse_strategy(
    text: str,
    base: dict[str, Any] | None = None,
    mandate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    src = (text or "").strip()
    if not src:
        raise ValueError("empty strategy brief")
    row = dict(base or default_strategy())
    universe, symbols = _universe(src)
    row["universe"] = universe
    if symbols:
        row["symbols"] = symbols
    row["brief"] = src
    row["skill"] = infer_skill(src)
    row["name"] = src.split(".")[0][:72] or row.get("name") or "Custom strategy"
    row["min_revenue_growth"] = _pct(
        src,
        (r"croissance[^0-9]{0,24}(\d+(?:[.,]\d+)?)\s*%", r"growth[^0-9]{0,16}(?:ca|>|above)?\s*(\d+(?:[.,]\d+)?)\s*%"),
        float(row.get("min_revenue_growth") or 15),
    )
    row["max_position"] = _pct(
        src,
        (r"(?:plus de|more than|max(?:imum)?(?: position)?)[^0-9]{0,24}(\d+(?:[.,]\d+)?)\s*%",),
        3.0,
    )
    row["stop_loss"] = _pct(
        src,
        (r"stop(?:[\s-]?loss)?[^0-9]{0,12}(\d+(?:[.,]\d+)?)\s*%",),
        5.0,
    )
    row["min_confidence"] = _pct(
        src,
        (r"confiance[^0-9]{0,16}(\d+(?:[.,]\d+)?)\s*%", r"confidence[^0-9]{0,16}(\d+(?:[.,]\d+)?)\s*%"),
        float(row.get("min_confidence") or 55),
    )
    row["approval_notional"] = _pct(
        src,
        (
            r"(?:sup[eé]rieur(?:e)?\s+a|above|over)\s*(\d+(?:[.,]\d+)?)\s*(?:€|eur|euros)?",
            r"(\d+(?:[.,]\d+)?)\s*(?:€|eur)\b",
        ),
        2000.0,
    )
    from navin.trading.mandate import parse_mandate_from_brief

    row["execution_mode"] = _mode(src)
    row["require_uptrend"] = bool(re.search(r"haussi[eè]re|uptrend|tendance", src, flags=re.IGNORECASE))
    current_mandate = mandate if isinstance(mandate, dict) else (
        row.get("mandate") if isinstance(row.get("mandate"), dict) else None
    )
    row["mandate"] = parse_mandate_from_brief(src, current_mandate)
    if re.search(r"24\s*/\s*7|24h|around the clock", src, flags=re.IGNORECASE):
        row["scan_interval_s"] = 300
    elif re.search(r"chaque matin|every morning", src, flags=re.IGNORECASE):
        row["scan_interval_s"] = 86400
    match = re.search(r"(\d+)\s*(?:sous-?agents|sub-?agents|debate)", src, flags=re.IGNORECASE)
    if match:
        row["debate_agents"] = max(3, min(7, int(match.group(1))))
    row["active"] = True
    return row


def apply_strategy_risk(strategy: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    out = dict(risk)
    if strategy.get("max_position") is not None:
        out["max_position_pct"] = float(strategy["max_position"])
    if strategy.get("stop_loss") is not None:
        out["stop_loss_pct"] = float(strategy["stop_loss"])
    if strategy.get("approval_notional") is not None:
        out["approval_notional"] = float(strategy["approval_notional"])
    return out
