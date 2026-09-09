# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Optional model layer for the Trading desk.

Rules decide, risk blocks; the model only reads. It scores headlines the
keyword filter cannot parse and writes the thesis a human reads on the desk.
No route configured, no call: every function returns None or "" and the
caller keeps its deterministic output. Never a price, never a fill.
"""

from __future__ import annotations

import json
from typing import Any

from navin.desk_ai import ai_enabled, ask, ask_json, routing_snapshot

ENV_FLAG = "NAVIN_TRADING_AI"
TRADING_TASK_ROLES: dict[str, str] = {
    "sentiment": "fast",
    "thesis": "docs",
}

_SENTIMENT_SYSTEM = (
    "You are a sell-side news desk. Read the headlines about one listed asset and "
    "rate the short-term sentiment from 0 (very bearish) to 100 (very bullish); 50 is "
    "neutral. Ignore clickbait, weigh earnings, guidance, regulation, lawsuits, "
    "product and macro news. Reply with JSON: "
    '{"score": <number>, "bias": "positive|neutral|negative", "reasons": ["<=4 short strings"]}.'
)

_THESIS_SYSTEM = (
    "You are the portfolio manager of a paper-trading desk. Write a 3 to 5 sentence "
    "investment note from the specialist reports you are given. State the verdict "
    "first, then the two strongest arguments for and against, then what would change "
    "the view. Use only the figures present in the material; never invent prices, "
    "targets or dates. Plain text, no markdown, no bullet points."
)


def enabled(settings: dict[str, Any] | None = None) -> bool:
    return ai_enabled(ENV_FLAG, settings)


def routing(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return routing_snapshot(TRADING_TASK_ROLES, env_flag=ENV_FLAG, profile=settings)


def score_news(symbol: str, items: list[dict[str, Any]], settings: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Model sentiment for *items* or None when no model answers usefully."""
    titles = [str(item.get("title") or "").strip() for item in items if item.get("title")]
    if not titles:
        return None
    user = f"Asset: {symbol.upper()}\nHeadlines:\n" + "\n".join(f"- {title}" for title in titles[:12])
    data, model = ask_json(
        TRADING_TASK_ROLES["sentiment"],
        _SENTIMENT_SYSTEM,
        user,
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=300,
        temperature=0.1,
    )
    if not isinstance(data, dict):
        return None
    try:
        score = float(data.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0.0, min(100.0, score))
    bias = str(data.get("bias") or "").lower()
    if bias not in {"positive", "neutral", "negative"}:
        bias = "positive" if score >= 62 else "negative" if score <= 38 else "neutral"
    reasons = [str(item) for item in (data.get("reasons") or []) if str(item).strip()][:4]
    return {"score": round(score, 1), "bias": bias, "reasons": reasons or ["model read the headlines"], "model": model}


def write_thesis(result: dict[str, Any], snapshot: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    """Readable note for the research card; "" keeps the judge's sentence."""
    reports = []
    for row in result.get("reports") or []:
        reports.append(
            {
                "name": row.get("name"),
                "score": row.get("score"),
                "stance": row.get("stance"),
                "reasons": (row.get("reasons") or [])[:4],
            }
        )
    material = {
        "symbol": result.get("symbol"),
        "action": result.get("action"),
        "confidence": result.get("confidence"),
        "price": result.get("price"),
        "currency": (snapshot.get("quote") or {}).get("currency"),
        "judge": (result.get("debate") or {}).get("judge"),
        "reports": reports,
        "fundamental": {
            key: (snapshot.get("fundamental") or {}).get(key)
            for key in ("revenue_growth", "debt_equity", "profit_margin", "pe", "recommendation", "target")
        },
        "technical": {
            key: (snapshot.get("technical") or {}).get(key)
            for key in ("rsi", "sma20", "sma50", "support", "resistance", "uptrend", "atr")
        },
        "headlines": [str(item.get("title") or "") for item in (snapshot.get("news") or [])[:6]],
    }
    text, _model = ask(
        TRADING_TASK_ROLES["thesis"],
        _THESIS_SYSTEM,
        json.dumps(material, ensure_ascii=False, default=str),
        env_flag=ENV_FLAG,
        profile=settings,
        max_tokens=420,
        temperature=0.2,
    )
    body = " ".join(text.split())
    return body if 40 <= len(body) <= 1400 else ""
