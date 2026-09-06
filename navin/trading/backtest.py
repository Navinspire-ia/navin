"""Walk a daily series with the live screen, the live specialists and the live risk rules.

What differs from the paper desk is stated, not hidden: there is no news
tape in the past, so the sentiment analyst is neutral, and fundamentals are
the latest filing held constant (a price-derived proxy when none is on
file). Costs (fee + slippage) are charged on every fill and the run is
compared with the benchmark over the same days.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from navin.trading.agents import run_specialists
from navin.trading.indicators import summarize_technicals
from navin.trading.metrics import summarize_curve, summarize_trades, vs_benchmark
from navin.trading.risk import evaluate_order, limits_from, size_buy
from navin.trading.screen import screen_candidate
from navin.trading.store import DEFAULT_CASH_EUR, TradingStore

DEFAULT_FEE_BPS = 5.0
DEFAULT_SLIPPAGE_BPS = 5.0
WARMUP_BARS = 30


def _fund_proxy(bars: list[dict[str, float]], index: int) -> dict[str, Any]:
    """Price-derived stand-in when no filing is on file. Flagged as a proxy."""
    window = bars[max(0, index - 20) : index + 1]
    if len(window) < 5:
        return {"available": False, "kind": "equity", "proxy": True}
    first = float(window[0]["c"])
    last = float(window[-1]["c"])
    growth = (last - first) / first * 100.0 if first else 0.0
    return {
        "available": True,
        "kind": "equity",
        "revenue_growth": max(growth, 0.0),
        "debt_equity": 0.6,
        "profit_margin": 0.15,
        "proxy": True,
        "note": "price-derived proxy: no filing on file for the backtest",
    }


def _cost(price: float, qty: float, fee_bps: float, slippage_bps: float, side: str) -> tuple[float, float]:
    """(fill price after slippage, fee) for a market fill."""
    slip = slippage_bps / 10_000.0
    fill = price * (1 + slip if side == "buy" else 1 - slip)
    fee = fill * qty * fee_bps / 10_000.0
    return fill, fee


def run_backtest(
    store: TradingStore,
    *,
    symbol: str,
    bars: list[dict[str, float]],
    strategy: dict[str, Any] | None = None,
    starting_cash: float | None = None,
    fundamentals: dict[str, Any] | None = None,
    bench_bars: list[dict[str, Any]] | None = None,
    benchmark: str = "",
    fee_bps: float | None = None,
    slippage_bps: float | None = None,
    range_code: str = "1y",
) -> dict[str, Any]:
    if len(bars) < 40:
        raise ValueError("need at least 40 daily bars")
    strat = strategy or store.active_strategy()
    settings = store.load_settings()
    limits = limits_from(settings)
    if strat.get("max_position") is not None:
        limits["max_position_pct"] = float(strat["max_position"])
    if strat.get("stop_loss") is not None:
        limits["stop_loss_pct"] = float(strat["stop_loss"])
    execution = settings.get("execution") or {}
    fee = float(fee_bps if fee_bps is not None else execution.get("fee_bps") or DEFAULT_FEE_BPS)
    slip = float(slippage_bps if slippage_bps is not None else execution.get("slippage_bps") or DEFAULT_SLIPPAGE_BPS)
    cash = float(starting_cash or settings.get("starting_cash") or DEFAULT_CASH_EUR)
    start_cash = cash
    qty = 0.0
    avg = 0.0
    stop = None
    take = None
    high_water = cash
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    fees_paid = 0.0
    min_conf = float(strat.get("min_confidence") or 55)
    take_pct = limits.get("take_profit_pct")
    enabled = {
        "fundamental": bool(strat.get("fundamental", True)),
        "technical": bool(strat.get("technical", True)),
        "sentiment": bool(strat.get("sentiment", True)),
        "macro": bool(strat.get("macro", True)),
    }
    bench_by_day = {int(float(bar.get("t") or 0) // 86400): float(bar.get("c") or 0) for bar in (bench_bars or []) if bar.get("c")}
    bench_days = sorted(bench_by_day)

    def _bench_change(day: int) -> float | None:
        if not bench_days:
            return None
        idx = None
        for pos, key in enumerate(bench_days):
            if key >= day:
                idx = pos
                break
        if idx is None or idx == 0:
            return None
        prev, cur = bench_by_day[bench_days[idx - 1]], bench_by_day[bench_days[idx]]
        return (cur - prev) / prev * 100.0 if prev else None

    def _close(index: int, price: float, reason: str) -> None:
        nonlocal cash, qty, avg, stop, take, fees_paid
        fill, cost = _cost(price, qty, fee, slip, "sell")
        pnl = (fill - avg) * qty - cost
        cash += qty * fill - cost
        fees_paid += cost
        trades.append({"pnl": pnl, "reason": reason, "t": bars[index]["t"], "qty": qty, "entry": avg, "exit": fill})
        qty = 0.0
        avg = 0.0
        stop = None
        take = None

    for index in range(WARMUP_BARS, len(bars)):
        window = bars[: index + 1]
        tech = summarize_technicals(window)
        last = float(tech.get("last") or bars[index]["c"])
        low = float(bars[index].get("l") or last)
        high = float(bars[index].get("h") or last)
        equity = cash + qty * last
        high_water = max(high_water, equity)
        curve.append({"t": bars[index]["t"], "equity": equity})
        # Stops are checked on the bar's range, like a resting order at the venue.
        if qty > 0 and stop is not None and low <= stop:
            _close(index, float(stop), "stop")
            continue
        if qty > 0 and take is not None and high >= take:
            _close(index, float(take), "take")
            continue
        fund = fundamentals if fundamentals and fundamentals.get("available") else _fund_proxy(bars, index)
        snap = {
            "symbol": symbol.upper(),
            "backtest": True,
            "quote": {"price": last, "symbol": symbol.upper()},
            "technical": {**tech, "bars": len(window)},
            "fundamental": fund,
            "news": [],
            "sentiment": {"score": 50.0, "bias": "neutral", "reasons": ["backtest: no news tape"], "method": "none"},
        }
        screen = screen_candidate(snap, strat)
        book = {"cash": cash, "positions": [{"symbol": symbol.upper(), "qty": qty, "avg": avg, "sector": "other"}] if qty else [], "high_water": high_water, "day_start_equity": equity}
        result = run_specialists(snap, book, bench_change_pct=_bench_change(int(float(bars[index]["t"]) // 86400)), enabled=enabled)
        score = float(result.get("confidence") or 0)
        action = str(result.get("action") or "WAIT")
        if qty == 0 and screen["passed"] and action == "BUY" and score >= min_conf:
            buy_qty = size_buy(equity=equity, price=last, limits=limits, confidence=score, symbol=symbol)
            verdict = evaluate_order(
                side="buy",
                symbol=symbol,
                qty=buy_qty,
                price=last,
                portfolio={"cash": cash, "positions": [], "high_water": high_water, "day_start_equity": equity},
                prices={symbol.upper(): last},
                limits=limits,
                orders_today=0,
                confidence=score,
                min_confidence=min_conf,
                execution_mode="autonomous",
            )
            if verdict["allow"] and buy_qty > 0:
                fill, cost = _cost(last, buy_qty, fee, slip, "buy")
                cash -= buy_qty * fill + cost
                fees_paid += cost
                qty = buy_qty
                avg = fill
                stop = fill * (1 - float(limits.get("stop_loss_pct") or 5) / 100.0)
                take = fill * (1 + float(take_pct) / 100.0) if take_pct else None
                signals.append({"t": bars[index]["t"], "side": "buy", "price": fill, "qty": buy_qty, "confidence": score})
        elif qty > 0 and (action == "SELL" or not screen["passed"]):
            reason = "judge sell" if action == "SELL" else "screen exit"
            signals.append({"t": bars[index]["t"], "side": "sell", "price": last, "qty": qty, "confidence": score})
            _close(index, last, reason)
    if qty > 0:
        _close(len(bars) - 1, float(bars[-1]["c"]), "eod")
        curve.append({"t": bars[-1]["t"], "equity": cash})
    stats = {**summarize_curve(curve), **summarize_trades(trades)}
    bench = vs_benchmark(curve, bench_bars or []) if bench_bars else {"alpha": None, "beta": None, "benchmark_return_pct": None}
    exposure_days = sum(1 for row in signals if row["side"] == "buy")
    row = {
        "id": f"bt-{uuid.uuid4().hex[:8]}",
        "symbol": symbol.upper(),
        "t": time.time(),
        "strategy_id": strat.get("id"),
        "strategy_name": strat.get("name"),
        "bars": len(bars),
        "range": range_code,
        "starting_cash": start_cash,
        "fee_bps": fee,
        "slippage_bps": slip,
        "fees_paid": fees_paid,
        "benchmark": benchmark or "",
        "engine": "specialists",
        "fundamentals": "filing" if fundamentals and fundamentals.get("available") else "price proxy",
        "entries": exposure_days,
        **stats,
        **{f"bench_{key}": value for key, value in bench.items()},
        "curve": [{"t": point["t"], "equity": round(float(point["equity"]), 2)} for point in curve[-260:]],
        "signals": signals[-60:],
        "trade_log": trades[-40:],
    }
    history = store.load_backtests()
    history.append(row)
    store.save_backtests(history)
    store.append_journal(
        {
            "kind": "backtest",
            "symbol": symbol.upper(),
            "text": (
                f"backtest {symbol.upper()} {range_code} return {stats.get('return_pct'):.1f}% "
                f"max DD {stats.get('max_drawdown'):.1f}% trades {stats.get('trades')}"
                + (f" vs {benchmark} {bench.get('benchmark_return_pct'):.1f}%" if bench.get("benchmark_return_pct") is not None else "")
            ),
        }
    )
    return row
