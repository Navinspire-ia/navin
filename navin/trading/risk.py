# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic risk engine. The LLM may propose; this module may only block."""

from __future__ import annotations

from typing import Any

from navin.trading.store import default_risk
from navin.trading.universes import asset_kind, sector_of


def _f(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def limits_from(settings: dict[str, Any] | None) -> dict[str, Any]:
    merged = default_risk()
    raw = (settings or {}).get("risk") if settings else None
    if isinstance(raw, dict):
        merged.update({key: raw[key] for key in merged if key in raw})
        if "allowed_assets" in raw:
            merged["allowed_assets"] = list(raw.get("allowed_assets") or [])
        if "blocked_assets" in raw:
            merged["blocked_assets"] = list(raw.get("blocked_assets") or [])
    return merged


def _positions(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    rows = portfolio.get("positions") or []
    return [row for row in rows if isinstance(row, dict)]


def mark_equity(portfolio: dict[str, Any], prices: dict[str, float]) -> float:
    cash = _f(portfolio.get("cash"))
    total = cash
    for pos in _positions(portfolio):
        symbol = str(pos.get("symbol") or "").upper()
        qty = _f(pos.get("qty"))
        px = prices.get(symbol)
        if px is None:
            px = _f(pos.get("last") or pos.get("avg"))
        total += qty * px
    return total


def daily_pnl_pct(portfolio: dict[str, Any], equity: float) -> float:
    start = _f(portfolio.get("day_start_equity"))
    if start <= 0:
        return 0.0
    return (equity - start) / start * 100.0


def drawdown_pct(portfolio: dict[str, Any], equity: float) -> float:
    high = max(_f(portfolio.get("high_water")), equity)
    if high <= 0:
        return 0.0
    return (high - equity) / high * 100.0


def exposure_pct(portfolio: dict[str, Any], prices: dict[str, float], symbol: str) -> float:
    equity = mark_equity(portfolio, prices)
    if equity <= 0:
        return 0.0
    qty = 0.0
    for pos in _positions(portfolio):
        if str(pos.get("symbol") or "").upper() == symbol.upper():
            qty += _f(pos.get("qty"))
    px = prices.get(symbol.upper()) or 0.0
    return qty * px / equity * 100.0


def sector_pct(portfolio: dict[str, Any], prices: dict[str, float], sector: str) -> float:
    equity = mark_equity(portfolio, prices)
    if equity <= 0:
        return 0.0
    value = 0.0
    for pos in _positions(portfolio):
        symbol = str(pos.get("symbol") or "").upper()
        if sector_of(symbol) == sector:
            px = prices.get(symbol) or _f(pos.get("last") or pos.get("avg"))
            value += _f(pos.get("qty")) * px
    return value / equity * 100.0


def evaluate_order(
    *,
    side: str,
    symbol: str,
    qty: float,
    price: float,
    portfolio: dict[str, Any],
    prices: dict[str, float],
    limits: dict[str, Any],
    orders_today: int,
    confidence: float | None = None,
    min_confidence: float | None = None,
    execution_mode: str = "approval",
) -> dict[str, Any]:
    """Return a verdict. ``allow`` can only stay False once blocked."""
    symbol = symbol.upper()
    side = (side or "").strip().lower()
    qty = _f(qty)
    price = _f(price)
    blocks: list[str] = []
    warnings: list[str] = []

    if side not in {"buy", "sell"}:
        blocks.append("side must be buy or sell")
    if qty <= 0 or price <= 0:
        blocks.append("qty and price must be positive")

    # Exits reduce risk: the asset lists, drawdown, daily-loss, order-count and
    # cash floors only ever stop a buy. A sell is checked against what is held.
    is_buy = side == "buy"
    allowed = [str(item).upper() for item in (limits.get("allowed_assets") or []) if str(item).strip()]
    blocked = [str(item).upper() for item in (limits.get("blocked_assets") or []) if str(item).strip()]
    if is_buy and allowed and symbol not in allowed:
        blocks.append(f"{symbol} is outside allowed_assets")
    if is_buy and symbol in blocked:
        blocks.append(f"{symbol} is on the block list")

    if side == "sell":
        held = sum(_f(pos.get("qty")) for pos in _positions(portfolio) if str(pos.get("symbol") or "").upper() == symbol)
        if qty > held + 1e-9:
            blocks.append(f"only {held:g} {symbol} held, cannot sell {qty:g}")

    if is_buy and not bool(limits.get("leverage")):
        cash = _f(portfolio.get("cash"))
        if qty * price > cash + 1e-9:
            blocks.append("not enough cash (leverage is off)")

    marked_prices = {**prices, symbol: price}
    equity = mark_equity(portfolio, marked_prices)
    notional = qty * price
    pos_pct = notional / equity * 100.0 if equity > 0 else 100.0
    max_pos = _f(limits.get("max_position_pct"), 3.0)
    if is_buy and pos_pct > max_pos + 1e-6:
        blocks.append(f"position {pos_pct:.2f}% exceeds max_position {max_pos:.2f}%")

    sector = sector_of(symbol)
    if is_buy:
        next_sector = sector_pct(portfolio, marked_prices, sector) + pos_pct
        max_sector = _f(limits.get("max_sector_pct"), 20.0)
        if next_sector > max_sector + 1e-6:
            blocks.append(f"sector {sector} {next_sector:.1f}% exceeds max_sector {max_sector:.1f}%")

    dd = drawdown_pct(portfolio, equity)
    max_dd = _f(limits.get("max_drawdown_pct"), 10.0)
    if is_buy and dd >= max_dd:
        blocks.append(f"book drawdown {dd:.2f}% at or above max_drawdown {max_dd:.2f}%")

    day_pnl = daily_pnl_pct(portfolio, equity)
    max_day = _f(limits.get("max_daily_loss_pct"), 2.0)
    if is_buy and day_pnl <= -abs(max_day):
        blocks.append(f"daily loss {day_pnl:.2f}% hit max_daily_loss {max_day:.2f}%")

    max_orders = int(_f(limits.get("max_daily_orders"), 8))
    if is_buy and orders_today >= max_orders:
        blocks.append(f"daily order limit {max_orders} already used")

    min_cash = _f(limits.get("min_cash_pct"), 5.0)
    if is_buy and equity > 0:
        cash_after = _f(portfolio.get("cash")) - notional
        if cash_after / equity * 100.0 < min_cash:
            blocks.append(f"cash would fall below min_cash {min_cash:.1f}%")

    if min_confidence is not None and confidence is not None and confidence < min_confidence:
        blocks.append(f"confidence {confidence:.0f} below minimum {min_confidence:.0f}")

    approval_needed = False
    threshold = _f(limits.get("approval_notional"), 2000.0)
    if execution_mode in {"approval", "recommend", "research"}:
        approval_needed = True
        if execution_mode == "research":
            blocks.append("research-only mode cannot place orders")
        elif execution_mode == "recommend":
            blocks.append("recommend mode stops before execution")
    elif execution_mode == "autonomous" and notional >= threshold and is_buy:
        approval_needed = True
        warnings.append(f"buy {notional:.0f} needs approval (threshold {threshold:.0f})")
    # "manual": the user typed the order, so they are the approval. Hard limits still apply.

    allow = not blocks
    # Autonomous still respects approval_needed: the engine never silently
    # enlarges risk. It only creates a pending order the user can accept.
    return {
        "allow": allow,
        "approval_needed": bool(approval_needed and allow) or (allow and execution_mode == "approval"),
        "blocks": blocks,
        "warnings": warnings,
        "notional": notional,
        "position_pct": pos_pct,
        "equity": equity,
        "sector": sector,
        "stop_loss_pct": _f(limits.get("stop_loss_pct"), 5.0),
        "take_profit_pct": limits.get("take_profit_pct"),
    }


def round_qty(qty: float, price: float, symbol: str = "") -> float:
    """Whole shares for listings, 1e-6 units for crypto / NFT floors / cheap names."""
    kind = asset_kind(symbol) if symbol else "equity"
    if kind in {"crypto", "nft"} or price < 20:
        return float(int(qty * 1_000_000) / 1_000_000)
    return float(int(qty))


def size_buy(
    *,
    equity: float,
    price: float,
    limits: dict[str, Any],
    confidence: float,
    symbol: str = "",
) -> float:
    """Largest size that still fits max_position (whole shares, fractional crypto)."""
    if equity <= 0 or price <= 0:
        return 0.0
    max_pos = _f(limits.get("max_position_pct"), 3.0) / 100.0
    # Scale size down when confidence is barely above the floor.
    scale = min(1.0, max(0.35, (confidence - 70.0) / 30.0))
    notional = equity * max_pos * scale
    qty = round_qty(notional / price, price, symbol)
    # Whole-share names: one share is the smallest honest size. Take it when
    # it still fits under max_position instead of skipping every large cap.
    if qty <= 0 and price <= equity * max_pos and round_qty(1.0, price, symbol) == 1.0:
        qty = 1.0
    return max(0.0, qty)
