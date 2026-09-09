# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Specialist scores + debate. Rules first; a model never overrides risk."""

from __future__ import annotations

from typing import Any

from navin.trading.universes import sector_of


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def fundamental_analyst(snapshot: dict[str, Any]) -> dict[str, Any]:
    fund = snapshot.get("fundamental") or {}
    if fund.get("kind") == "crypto" or not fund.get("available"):
        return {
            "name": "fundamental",
            "score": 55.0,
            "stance": "HOLD",
            "reasons": [fund.get("note") or "no corporate filings for this asset"],
        }
    score = 50.0
    reasons: list[str] = []
    growth = _num(fund.get("revenue_growth"))
    if growth is not None:
        if growth >= 25:
            score += 22
            reasons.append(f"revenue growth {growth:.1f}% is strong")
        elif growth >= 15:
            score += 14
            reasons.append(f"revenue growth {growth:.1f}% clears 15%")
        elif growth >= 5:
            score += 4
            reasons.append(f"revenue growth {growth:.1f}% is modest")
        else:
            score -= 12
            reasons.append(f"revenue growth {growth:.1f}% is weak")
    debt = _num(fund.get("debt_equity"))
    if debt is not None:
        ratio = debt / 100.0 if debt > 8 else debt
        if ratio <= 0.6:
            score += 10
            reasons.append(f"debt/equity {ratio:.2f} is conservative")
        elif ratio <= 1.5:
            score += 2
            reasons.append(f"debt/equity {ratio:.2f} is acceptable")
        else:
            score -= 14
            reasons.append(f"debt/equity {ratio:.2f} is heavy")
    margin = _num(fund.get("profit_margin"))
    if margin is not None:
        pct = margin * 100.0 if abs(margin) <= 2 else margin
        if pct >= 20:
            score += 8
            reasons.append(f"profit margin {pct:.1f}%")
        elif pct < 0:
            score -= 10
            reasons.append("negative profit margin")
    rec = str(fund.get("recommendation") or "").lower()
    if rec in {"buy", "strong_buy"}:
        score += 6
        reasons.append(f"street recommendation {rec}")
    elif rec in {"sell", "strong_sell"}:
        score -= 8
        reasons.append(f"street recommendation {rec}")
    score = _clamp(score)
    stance = "BUY" if score >= 70 else "SELL" if score <= 38 else "HOLD"
    return {"name": "fundamental", "score": score, "stance": stance, "reasons": reasons or ["thin filings"]}


def technical_analyst(snapshot: dict[str, Any]) -> dict[str, Any]:
    tech = snapshot.get("technical") or {}
    score = 50.0
    reasons: list[str] = []
    if tech.get("uptrend"):
        score += 16
        reasons.append("uptrend: price above SMA50")
    else:
        score -= 10
        reasons.append("trend not confirmed vs SMA50")
    rsi = _num(tech.get("rsi"))
    if rsi is not None:
        if 45 <= rsi <= 68:
            score += 10
            reasons.append(f"RSI {rsi:.0f} is constructive")
        elif rsi > 78:
            score -= 8
            reasons.append(f"RSI {rsi:.0f} is stretched")
        elif rsi < 32:
            score -= 4
            reasons.append(f"RSI {rsi:.0f} is washed out")
    hist = _num(tech.get("macd_hist"))
    if hist is not None:
        if hist > 0:
            score += 8
            reasons.append("MACD histogram positive")
        else:
            score -= 6
            reasons.append("MACD histogram negative")
    last = _num(tech.get("last"))
    resistance = _num(tech.get("resistance"))
    support = _num(tech.get("support"))
    if last and resistance and last >= resistance * 0.995:
        score -= 4
        reasons.append("price is pressing resistance")
    if last and support and last <= support * 1.01:
        score -= 6
        reasons.append("price is sitting on support")
    score = _clamp(score)
    stance = "BUY" if score >= 68 else "SELL" if score <= 40 else "HOLD"
    return {"name": "technical", "score": score, "stance": stance, "reasons": reasons}


def sentiment_analyst(snapshot: dict[str, Any]) -> dict[str, Any]:
    sent = snapshot.get("sentiment") or {}
    score = _num(sent.get("score")) or 50.0
    bias = str(sent.get("bias") or "neutral")
    reasons = list(sent.get("reasons") or [])
    stance = "BUY" if score >= 64 else "SELL" if score <= 36 else "HOLD"
    return {"name": "sentiment", "score": score, "stance": stance, "bias": bias, "reasons": reasons}


def researcher(snapshot: dict[str, Any]) -> dict[str, Any]:
    fund = snapshot.get("fundamental") or {}
    tech = snapshot.get("technical") or {}
    news = snapshot.get("news") or []
    gaps: list[str] = []
    if not fund.get("available") and fund.get("kind") != "crypto":
        gaps.append("filings feed missing")
    if tech.get("sma50") is None:
        gaps.append("not enough history for SMA50")
    if not news and not snapshot.get("backtest"):
        gaps.append("no headlines in the cache")
    score = 70.0 if not gaps else 48.0
    return {
        "name": "researcher",
        "score": score,
        "stance": "HOLD" if gaps else "BUY",
        "reasons": gaps or ["history, filings and headlines are present"],
        "gaps": gaps,
    }


def macro_analyst(snapshot: dict[str, Any], bench_change_pct: float | None) -> dict[str, Any]:
    tech = snapshot.get("technical") or {}
    atr = _num(tech.get("atr"))
    last = _num(tech.get("last"))
    score = 55.0
    reasons: list[str] = []
    if bench_change_pct is not None:
        if bench_change_pct >= 0.4:
            score += 8
            reasons.append(f"benchmark {bench_change_pct:.2f}% on the day")
        elif bench_change_pct <= -0.8:
            score -= 12
            reasons.append(f"risk-off tape {bench_change_pct:.2f}%")
    if atr and last:
        vol = atr / last * 100.0
        if vol > 5:
            score -= 10
            reasons.append(f"realized vol {vol:.1f}% is elevated")
        else:
            score += 6
            reasons.append(f"realized vol {vol:.1f}% is contained")
    score = _clamp(score)
    stance = "BUY" if score >= 64 else "SELL" if score <= 40 else "HOLD"
    return {"name": "macro", "score": score, "stance": stance, "reasons": reasons or ["no macro overlay"]}


def strategy_trader(reports: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {row["name"]: row for row in reports}
    weights = {
        "fundamental": 0.28,
        "technical": 0.26,
        "sentiment": 0.16,
        "macro": 0.15,
        "researcher": 0.15,
    }
    score = 0.0
    weight_sum = 0.0
    for name, weight in weights.items():
        row = by_name.get(name)
        if not row:
            continue
        score += float(row["score"]) * weight
        weight_sum += weight
    fused = score / weight_sum if weight_sum else 50.0
    if fused >= 62:
        action = "BUY"
    elif fused <= 38:
        action = "SELL"
    elif fused >= 58:
        action = "HOLD"
    else:
        action = "WAIT"
    return {
        "name": "trader",
        "score": fused,
        "stance": action,
        "action": action,
        "reasons": [f"fused specialist score {fused:.0f}"],
    }


def risk_manager_notes(snapshot: dict[str, Any], fused: dict[str, Any]) -> dict[str, Any]:
    tech = snapshot.get("technical") or {}
    reasons: list[str] = []
    score = 70.0
    last = _num(tech.get("last"))
    atr = _num(tech.get("atr"))
    if last and atr and atr / last > 0.06:
        score -= 18
        reasons.append("wide ATR vs price - size must stay small")
    rsi = _num(tech.get("rsi"))
    if rsi and rsi > 80:
        score -= 12
        reasons.append("overbought - chase risk")
    if fused.get("action") == "BUY" and score < 55:
        reasons.append("risk notes disagree with a chase buy")
    return {
        "name": "risk",
        "score": _clamp(score),
        "stance": "WAIT" if score < 55 else fused.get("action") or "HOLD",
        "reasons": reasons or ["no extra risk flag beyond the hard engine"],
    }


def portfolio_manager(snapshot: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    symbol = str(snapshot.get("symbol") or "").upper()
    sector = sector_of(symbol)
    held = [row for row in (book.get("positions") or []) if isinstance(row, dict)]
    same = sum(1 for row in held if str(row.get("sector") or "") == sector)
    score = 72.0 if same <= 2 else 48.0
    reasons = (
        [f"{same} open names already in {sector}"]
        if held
        else ["book has room to diversify"]
    )
    return {"name": "portfolio", "score": score, "stance": "HOLD" if same > 3 else "BUY", "reasons": reasons}


def debate(reports: list[dict[str, Any]], fused: dict[str, Any]) -> dict[str, Any]:
    bull_bits = [row for row in reports if float(row.get("score") or 0) >= 62]
    bear_bits = [row for row in reports if float(row.get("score") or 0) <= 45]
    bull = {
        "name": "bull",
        "score": max((float(row["score"]) for row in bull_bits), default=fused["score"]),
        "reasons": [item for row in bull_bits for item in (row.get("reasons") or [])][:4]
        or ["limited bullish evidence"],
    }
    bear = {
        "name": "bear",
        "score": min((float(row["score"]) for row in bear_bits), default=100 - fused["score"]),
        "reasons": [item for row in bear_bits for item in (row.get("reasons") or [])][:4]
        or ["limited bearish evidence"],
    }
    votes = []
    for row in reports:
        if row.get("name") in {"fundamental", "technical", "sentiment", "macro", "risk"}:
            votes.append(1 if float(row.get("score") or 0) >= 60 else 0)
    consensus = sum(votes)
    needed = 2
    action = fused.get("action") or "WAIT"
    if action == "BUY" and (bull["score"] - (100 - bear["score"]) < 8 or consensus < needed):
        judge_action = "WAIT"
        why = (
            f"Judge rejects BUY: consensus {consensus}/5 and the bull/bear gap is thin. "
            "Wait for a cleaner tape or a cheaper entry."
        )
    elif action == "BUY":
        judge_action = "BUY"
        why = f"Judge accepts BUY: {consensus}/5 specialists above 60 and the bull case is clearer."
    elif action == "SELL":
        judge_action = "SELL" if consensus <= 1 else "HOLD"
        why = "Judge confirms reduce" if judge_action == "SELL" else "Judge keeps HOLD - sell evidence is mixed"
    else:
        judge_action = action
        why = f"Judge keeps {action}: fused score {fused['score']:.0f}."
    return {
        "bull": bull,
        "bear": bear,
        "judge": {"action": judge_action, "why": why, "consensus": f"{consensus}/5"},
        "consensus": consensus,
    }


def run_specialists(
    snapshot: dict[str, Any],
    book: dict[str, Any],
    *,
    bench_change_pct: float | None = None,
    enabled: dict[str, bool] | None = None,
) -> dict[str, Any]:
    flags = enabled or {}
    reports: list[dict[str, Any]] = []
    if flags.get("fundamental", True):
        reports.append(fundamental_analyst(snapshot))
    if flags.get("technical", True):
        reports.append(technical_analyst(snapshot))
    if flags.get("sentiment", True):
        reports.append(sentiment_analyst(snapshot))
    if flags.get("macro", True):
        reports.append(macro_analyst(snapshot, bench_change_pct))
    reports.append(researcher(snapshot))
    fused = strategy_trader(reports)
    risk_notes = risk_manager_notes(snapshot, fused)
    reports.append(risk_notes)
    reports.append(portfolio_manager(snapshot, book))
    argued = debate(reports, fused)
    action = argued["judge"]["action"]
    scores = {row["name"]: row["score"] for row in reports}
    confidence = fused["score"]
    if action == "WAIT":
        confidence = min(confidence, 74.0)
    last = (snapshot.get("technical") or {}).get("last") or (snapshot.get("quote") or {}).get("price")
    thesis = argued["judge"]["why"]
    if action == "WAIT" and last:
        support = (snapshot.get("technical") or {}).get("support")
        if support:
            thesis += f" Prefer a better entry near {float(support):.2f} or a confirmed breakout."
    return {
        "symbol": str(snapshot.get("symbol") or "").upper(),
        "action": action,
        "confidence": round(confidence, 1),
        "scores": {
            "fundamental": scores.get("fundamental"),
            "technical": scores.get("technical"),
            "sentiment": scores.get("sentiment"),
            "macro": scores.get("macro"),
            "risk": scores.get("risk"),
            "researcher": scores.get("researcher"),
            "portfolio": scores.get("portfolio"),
            "trader": fused["score"],
        },
        "reports": reports,
        "debate": argued,
        "thesis": thesis,
        "name": (snapshot.get("quote") or {}).get("name") or snapshot.get("symbol"),
        "price": last,
    }
