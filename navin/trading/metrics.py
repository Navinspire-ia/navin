"""Portfolio / backtest statistics. All from the equity curve, never guessed."""

from __future__ import annotations

import math
from typing import Any, Sequence


def _returns(curve: Sequence[float]) -> list[float]:
    out: list[float] = []
    prev = None
    for value in curve:
        if prev and prev > 0:
            out.append((value - prev) / prev)
        prev = value
    return out


def max_drawdown(curve: Sequence[float]) -> float:
    peak = None
    worst = 0.0
    for value in curve:
        if peak is None or value > peak:
            peak = value
        if peak and peak > 0:
            dd = (peak - value) / peak
            if dd > worst:
                worst = dd
    return worst * 100.0


def sharpe(returns: Sequence[float], periods_per_year: float = 252.0) -> float | None:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    var = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return (mean / std) * math.sqrt(periods_per_year)


def sortino(returns: Sequence[float], periods_per_year: float = 252.0) -> float | None:
    downside = [item for item in returns if item < 0]
    if len(returns) < 2 or not downside:
        return sharpe(returns, periods_per_year)
    mean = sum(returns) / len(returns)
    var = sum(item * item for item in downside) / len(downside)
    std = math.sqrt(var)
    if std == 0:
        return None
    return (mean / std) * math.sqrt(periods_per_year)


def summarize_trades(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(row.get("pnl") or 0) for row in trades]
    wins = [item for item in pnls if item > 0]
    losses = [item for item in pnls if item < 0]
    win_rate = (len(wins) / len(pnls) * 100.0) if pnls else None
    profit_factor = None
    if losses:
        profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else None
    elif wins:
        profit_factor = 99.0
    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "net_pnl": sum(pnls),
    }


def daily_closes(curve: Sequence[dict[str, Any]]) -> list[float]:
    """Last equity of each UTC day, so Sharpe is annualized on daily returns.

    The live book appends a point every refresh; annualizing those intraday
    ticks with 252 periods would print a meaningless ratio.
    """
    by_day: dict[int, float] = {}
    for row in curve:
        equity = row.get("equity")
        if equity is None:
            continue
        try:
            stamp = float(row.get("t") or 0)
        except (TypeError, ValueError):
            stamp = 0.0
        by_day[int(stamp // 86400)] = float(equity)
    return [by_day[key] for key in sorted(by_day)]


def summarize_curve(curve: Sequence[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row.get("equity") or 0) for row in curve if row.get("equity") is not None]
    if not values:
        return {
            "sharpe": None,
            "sortino": None,
            "max_drawdown": 0.0,
            "return_pct": 0.0,
            "points": 0,
        }
    daily = daily_closes(curve)
    rets = _returns(daily if len(daily) >= 2 else values)
    start = values[0]
    end = values[-1]
    ret_pct = ((end - start) / start * 100.0) if start else 0.0
    return {
        "sharpe": sharpe(rets),
        "sortino": sortino(rets),
        "max_drawdown": max_drawdown(values),
        "return_pct": ret_pct,
        "points": len(values),
        "days": len(daily),
        "start": start,
        "end": end,
    }


def vs_benchmark(
    curve: Sequence[dict[str, Any]],
    bench_bars: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Alpha (excess return, pct) and beta from daily returns aligned by UTC day."""
    port: dict[int, float] = {}
    for row in curve:
        if row.get("equity") is None:
            continue
        port[int(float(row.get("t") or 0) // 86400)] = float(row["equity"])
    bench: dict[int, float] = {}
    for bar in bench_bars:
        close = bar.get("c") if "c" in bar else bar.get("close")
        if close is None:
            continue
        bench[int(float(bar.get("t") or 0) // 86400)] = float(close)
    days = sorted(set(port) & set(bench))
    if len(days) < 3:
        return {"alpha": None, "beta": None, "benchmark_return_pct": None, "days": len(days)}
    p_vals = [port[day] for day in days]
    b_vals = [bench[day] for day in days]
    p_ret = _returns(p_vals)
    b_ret = _returns(b_vals)
    n = len(p_ret)
    if n < 2:
        return {"alpha": None, "beta": None, "benchmark_return_pct": None, "days": len(days)}
    mean_p = sum(p_ret) / n
    mean_b = sum(b_ret) / n
    cov = sum((p - mean_p) * (b - mean_b) for p, b in zip(p_ret, b_ret, strict=True)) / (n - 1)
    var_b = sum((b - mean_b) ** 2 for b in b_ret) / (n - 1)
    beta = cov / var_b if var_b > 0 else None
    port_total = (p_vals[-1] - p_vals[0]) / p_vals[0] * 100.0 if p_vals[0] else 0.0
    bench_total = (b_vals[-1] - b_vals[0]) / b_vals[0] * 100.0 if b_vals[0] else 0.0
    return {
        "alpha": port_total - bench_total,
        "beta": beta,
        "benchmark_return_pct": bench_total,
        "portfolio_return_pct": port_total,
        "days": len(days),
    }
