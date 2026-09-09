# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The book: cash, positions, orders, in one book currency.

Prices reach this module already converted to the book currency (see
``market.book_prices``). Paper fills are instant and charge the configured
fee + slippage; when a broker is configured and holds the asset class, the
approved order is carried to the venue and the venue's fill is what gets
booked. Nothing here ever enlarges what the risk engine allowed.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from navin.trading.brokers import poll_order, submit_order
from navin.trading.errors import TradingError
from navin.trading.metrics import summarize_curve, summarize_trades, vs_benchmark
from navin.trading.risk import evaluate_order, limits_from, mark_equity, round_qty, size_buy
from navin.trading.store import TradingStore, normalize_execution
from navin.trading.universes import sector_of

CURVE_SAMPLE_S = 300.0
OPEN_STATUSES = {"submitted", "partial"}


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _day_stamp() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def rollover_day(portfolio: dict[str, Any], equity: float) -> dict[str, Any]:
    stamp = _day_stamp()
    if portfolio.get("day_stamp") != stamp:
        portfolio["day_stamp"] = stamp
        portfolio["day_start_equity"] = equity
    high = float(portfolio.get("high_water") or equity)
    portfolio["high_water"] = max(high, equity)
    return portfolio


def orders_today(orders: list[dict[str, Any]]) -> int:
    stamp = _day_stamp()
    count = 0
    for row in orders:
        if row.get("status") in {"filled", "pending", "submitted", "partial"} and str(row.get("day") or "") == stamp:
            count += 1
    return count


def _curve_point(portfolio: dict[str, Any], equity: float) -> None:
    """One point per CURVE_SAMPLE_S: a UI refresh must not inflate the curve."""
    curve = [row for row in (portfolio.get("equity_curve") or []) if isinstance(row, dict)]
    now = _now()
    if curve:
        last = curve[-1]
        try:
            last_t = float(last.get("t") or 0)
        except (TypeError, ValueError):
            last_t = 0.0
        if now - last_t < CURVE_SAMPLE_S and int(now // 86400) == int(last_t // 86400):
            curve[-1] = {"t": now, "equity": equity}
            portfolio["equity_curve"] = curve[-1000:]
            return
    curve.append({"t": now, "equity": equity})
    portfolio["equity_curve"] = curve[-1000:]


def mark_to_market(store: TradingStore, prices: dict[str, float]) -> dict[str, Any]:
    book = store.load_portfolio()
    equity = mark_equity(book, prices)
    rollover_day(book, equity)
    for pos in book.get("positions") or []:
        symbol = str(pos.get("symbol") or "").upper()
        if symbol in prices:
            pos["last"] = prices[symbol]
            pos["value"] = float(pos.get("qty") or 0) * prices[symbol]
            pos["unrealized"] = pos["value"] - float(pos.get("qty") or 0) * float(pos.get("avg") or 0)
    _curve_point(book, equity)
    return store.save_portfolio(book)


def _blocked_row(side: str, symbol: str, qty: float, price: float, reason: str, confidence: float, thesis: str, blocks: list[str]) -> dict[str, Any]:
    return {
        "id": _new_id("ord"),
        "side": side,
        "symbol": symbol.upper(),
        "qty": qty,
        "price": price,
        "status": "blocked",
        "blocks": blocks,
        "reason": reason,
        "confidence": confidence,
        "thesis": thesis,
        "day": _day_stamp(),
        "t": _now(),
    }


def place_order(
    store: TradingStore,
    *,
    side: str,
    symbol: str,
    qty: float,
    price: float,
    reason: str,
    confidence: float,
    execution_mode: str,
    prices: dict[str, float],
    thesis: str = "",
    quote_currency: str | None = None,
    fx_rate: float = 1.0,
    origin: str = "loop",
    http: Any = None,
) -> dict[str, Any]:
    """Run the risk engine, then fill (paper), carry (broker) or park (approval)."""
    side = str(side or "").strip().lower()
    symbol = symbol.upper()
    settings = store.load_settings()
    limits = limits_from(settings)
    execution = normalize_execution(settings.get("execution"))
    book = store.load_portfolio()
    orders = store.load_orders()
    verdict = evaluate_order(
        side=side,
        symbol=symbol,
        qty=qty,
        price=price,
        portfolio=book,
        prices=prices,
        limits=limits,
        orders_today=orders_today(orders),
        confidence=confidence,
        min_confidence=None,
        execution_mode=execution_mode,
    )
    if not verdict["allow"]:
        row = _blocked_row(side, symbol, qty, price, reason, confidence, thesis, verdict["blocks"])
        row["origin"] = origin
        orders.append(row)
        store.save_orders(orders[-400:])
        store.append_journal({"kind": "risk_block", "symbol": symbol, "text": "; ".join(verdict["blocks"])})
        return row

    status = "pending" if verdict["approval_needed"] else "filled"
    sl = float(verdict["stop_loss_pct"])
    tp_raw = verdict.get("take_profit_pct")
    stop = price * (1 - sl / 100.0) if side == "buy" else price * (1 + sl / 100.0)
    take = None
    if tp_raw:
        take = price * (1 + float(tp_raw) / 100.0) if side == "buy" else price * (1 - float(tp_raw) / 100.0)
    row = {
        "id": _new_id("ord"),
        "side": side,
        "symbol": symbol,
        "qty": qty,
        "price": price,
        "currency": str(book.get("currency") or "USD"),
        "quote_currency": str(quote_currency or book.get("currency") or "USD").upper(),
        "fx_rate": fx_rate,
        "status": status,
        "reason": reason,
        "confidence": confidence,
        "thesis": thesis,
        "stop": stop,
        "take": take,
        "day": _day_stamp(),
        "t": _now(),
        "origin": origin,
        "warnings": verdict.get("warnings") or [],
    }
    if status == "filled":
        _execute(store, book, row, prices, execution, http=http)
        orders.append(row)
        store.save_orders(orders[-400:])
        return row
    store.append_journal({"kind": "approval", "symbol": symbol, "text": f"{side.upper()} {qty:g} {symbol} waiting for approval"})
    orders.append(row)
    store.save_orders(orders[-400:])
    return row


def _execute(
    store: TradingStore,
    book: dict[str, Any],
    row: dict[str, Any],
    prices: dict[str, float],
    execution: dict[str, Any],
    *,
    http: Any = None,
) -> None:
    """Carry an allowed order to paper or to the venue and book what came back."""
    symbol = str(row["symbol"]).upper()
    side = str(row["side"])
    qty = float(row["qty"])
    kwargs = {"http": http} if http is not None else {}
    venue = submit_order(store, symbol=symbol, side=side, qty=qty, **kwargs)
    row["broker"] = venue.get("broker") or "paper"
    row["venue_symbol"] = venue.get("venue_symbol") or symbol
    if venue.get("broker_order_id"):
        row["broker_order_id"] = venue["broker_order_id"]
    state = str(venue.get("status") or "failed")
    if state == "failed":
        row["status"] = "failed"
        row["error"] = str(venue.get("error") or "venue rejected the order")
        store.append_journal({"kind": "risk_block", "symbol": symbol, "text": f"{row['broker']} rejected {side} {qty:g} {symbol}: {row['error']}"})
        return
    if state in OPEN_STATUSES:
        row["status"] = "submitted"
        row["venue_status"] = venue.get("venue_status")
        store.append_journal({"kind": "approval", "symbol": symbol, "text": f"{side.upper()} {qty:g} {symbol} sent to {row['broker']} ({venue.get('venue_status') or 'submitted'})"})
        return
    if row["broker"] == "paper":
        slip = float(execution.get("slippage_bps") or 0) / 10_000.0
        fill = float(row["price"]) * (1 + slip if side == "buy" else 1 - slip)
        fee = fill * qty * float(execution.get("fee_bps") or 0) / 10_000.0
    else:
        venue_price = venue.get("fill_price")
        fill = float(venue_price) * float(row.get("fx_rate") or 1.0) if venue_price else float(row["price"])
        fee = 0.0
        filled_qty = float(venue.get("filled_qty") or 0)
        if filled_qty > 0 and abs(filled_qty - qty) > 1e-9:
            row["qty"] = filled_qty
            qty = filled_qty
    row["fill_price"] = fill
    row["fee"] = fee
    _apply_fill(book, row, fill_price=fill, fee=fee)
    equity = mark_equity(book, {**prices, symbol: fill})
    rollover_day(book, equity)
    _curve_point(book, equity)
    store.save_portfolio(book)
    row["status"] = "filled"
    row["filled_at"] = _now()
    label = "paper" if row["broker"] == "paper" else row["broker"]
    store.append_journal({"kind": "fill", "symbol": symbol, "text": f"{side.upper()} {qty:g} {symbol} @ {fill:.4f} {label}"})


def _apply_fill(book: dict[str, Any], order: dict[str, Any], *, fill_price: float | None = None, fee: float = 0.0) -> None:
    symbol = str(order.get("symbol") or "").upper()
    qty = float(order.get("qty") or 0)
    price = float(fill_price if fill_price is not None else order.get("price") or 0)
    side = str(order.get("side") or "")
    positions = [row for row in (book.get("positions") or []) if isinstance(row, dict)]
    cash = float(book.get("cash") or 0) - fee
    if side == "buy":
        cash -= qty * price
        found = False
        for pos in positions:
            if str(pos.get("symbol") or "").upper() == symbol:
                old_qty = float(pos.get("qty") or 0)
                avg = float(pos.get("avg") or 0)
                new_qty = old_qty + qty
                pos["avg"] = (avg * old_qty + price * qty) / new_qty if new_qty else price
                pos["qty"] = new_qty
                pos["stop"] = order.get("stop")
                pos["take"] = order.get("take")
                pos["last"] = price
                pos["sector"] = sector_of(symbol)
                pos["currency"] = order.get("quote_currency") or pos.get("currency")
                found = True
                break
        if not found:
            positions.append(
                {
                    "id": _new_id("pos"),
                    "symbol": symbol,
                    "qty": qty,
                    "avg": price,
                    "last": price,
                    "stop": order.get("stop"),
                    "take": order.get("take"),
                    "opened_at": _now(),
                    "sector": sector_of(symbol),
                    "thesis": order.get("thesis") or "",
                    "currency": order.get("quote_currency") or book.get("currency"),
                    "broker": order.get("broker") or "paper",
                }
            )
        book["cash"] = cash
        book["positions"] = positions
        return
    remaining = qty
    realized = float(book.get("realized_pnl") or 0)
    fill_pnl = -fee
    kept: list[dict[str, Any]] = []
    for pos in positions:
        if str(pos.get("symbol") or "").upper() != symbol or remaining <= 0:
            kept.append(pos)
            continue
        have = float(pos.get("qty") or 0)
        take = min(have, remaining)
        pnl = (price - float(pos.get("avg") or 0)) * take
        fill_pnl += pnl
        cash += take * price
        remaining -= take
        leftover = have - take
        if leftover > 1e-9:
            pos["qty"] = leftover
            kept.append(pos)
    if remaining > 1e-8:
        raise TradingError(f"not enough {symbol} to sell", status=400)
    realized += fill_pnl
    book["cash"] = cash
    book["positions"] = kept
    book["realized_pnl"] = realized
    order["pnl"] = fill_pnl


def decide_order(store: TradingStore, order_id: str, accept: bool, prices: dict[str, float], *, http: Any = None) -> dict[str, Any]:
    orders = store.load_orders()
    found = None
    for row in orders:
        if row.get("id") == order_id:
            found = row
            break
    if found is None:
        raise TradingError("order not found", status=404)
    if found.get("status") != "pending":
        raise TradingError("order is not waiting for approval", status=409)
    if not accept:
        found["status"] = "rejected"
        store.save_orders(orders)
        store.append_journal({"kind": "reject", "symbol": found.get("symbol"), "text": f"rejected {found.get('id')}"})
        return found
    book = store.load_portfolio()
    settings = store.load_settings()
    symbol = str(found.get("symbol") or "").upper()
    live_price = prices.get(symbol)
    if live_price:
        found["price"] = float(live_price)  # approve at today's tape, not the proposal's
    verdict = evaluate_order(
        side=str(found.get("side") or ""),
        symbol=symbol,
        qty=float(found.get("qty") or 0),
        price=float(found.get("price") or 0),
        portfolio=book,
        prices=prices,
        limits=limits_from(settings),
        orders_today=orders_today(orders),
        execution_mode="manual",
    )
    if not verdict["allow"]:
        found["status"] = "blocked"
        found["blocks"] = verdict["blocks"]
        store.save_orders(orders)
        store.append_journal({"kind": "risk_block", "symbol": symbol, "text": "; ".join(verdict["blocks"])})
        return found
    found["approved_at"] = _now()
    _execute(store, book, found, prices, normalize_execution(settings.get("execution")), http=http)
    store.save_orders(orders)
    return found


def reconcile_orders(store: TradingStore, prices: dict[str, float], *, http: Any = None) -> list[dict[str, Any]]:
    """Ask the venue about orders we sent and book the ones that filled."""
    orders = store.load_orders()
    changed: list[dict[str, Any]] = []
    book = None
    kwargs = {"http": http} if http is not None else {}
    for row in orders:
        if row.get("status") not in OPEN_STATUSES:
            continue
        state = poll_order(store, row, **kwargs)
        status = str(state.get("status") or "unknown")
        if status == "filled":
            book = book or store.load_portfolio()
            venue_price = state.get("fill_price")
            fill = float(venue_price) * float(row.get("fx_rate") or 1.0) if venue_price else float(row.get("price") or 0)
            filled_qty = float(state.get("filled_qty") or 0)
            if filled_qty > 0:
                row["qty"] = filled_qty
            row["fill_price"] = fill
            row["fee"] = 0.0
            _apply_fill(book, row, fill_price=fill)
            row["status"] = "filled"
            row["filled_at"] = _now()
            store.append_journal({"kind": "fill", "symbol": row.get("symbol"), "text": f"{row.get('broker')} filled {row.get('side')} {row['qty']:g} {row.get('symbol')} @ {fill:.4f}"})
            changed.append(row)
        elif status == "failed":
            row["status"] = "failed"
            row["error"] = str(state.get("error") or state.get("venue_status") or "venue cancelled")
            store.append_journal({"kind": "risk_block", "symbol": row.get("symbol"), "text": f"{row.get('broker')} cancelled {row.get('id')}: {row['error']}"})
            changed.append(row)
        elif status == "partial":
            row["status"] = "partial"
            row["venue_status"] = state.get("venue_status")
    if book is not None:
        equity = mark_equity(book, prices)
        rollover_day(book, equity)
        _curve_point(book, equity)
        store.save_portfolio(book)
    if changed:
        store.save_orders(orders)
    return changed


def monitor_stops(store: TradingStore, prices: dict[str, float], *, http: Any = None) -> list[dict[str, Any]]:
    book = store.load_portfolio()
    fills: list[dict[str, Any]] = []
    for pos in list(book.get("positions") or []):
        symbol = str(pos.get("symbol") or "").upper()
        px = prices.get(symbol)
        if px is None:
            continue
        stop = pos.get("stop")
        take = pos.get("take")
        hit = None
        if stop is not None and px <= float(stop):
            hit = "stop"
        elif take is not None and px >= float(take):
            hit = "take"
        if not hit:
            continue
        order = place_order(
            store,
            side="sell",
            symbol=symbol,
            qty=float(pos.get("qty") or 0),
            price=px,
            reason=f"{hit} hit at {px:.4f}",
            confidence=100.0,
            execution_mode="autonomous",
            prices=prices,
            thesis=str(pos.get("thesis") or ""),
            quote_currency=pos.get("currency"),
            origin="guard",
            http=http,
        )
        fills.append(order)
    return fills


def close_position(
    store: TradingStore,
    symbol: str,
    prices: dict[str, float],
    *,
    qty: float | None = None,
    reason: str = "closed from the desk",
    http: Any = None,
) -> dict[str, Any]:
    """Sell all (or *qty*) of a held name at the live price. User is the approval."""
    symbol = str(symbol or "").strip().upper()
    book = store.load_portfolio()
    held = [pos for pos in (book.get("positions") or []) if str(pos.get("symbol") or "").upper() == symbol]
    if not held:
        raise TradingError(f"no open position in {symbol}", status=404)
    total = sum(float(pos.get("qty") or 0) for pos in held)
    amount = float(qty) if qty else total
    if amount <= 0 or amount > total + 1e-9:
        raise TradingError(f"can sell up to {total:g} {symbol}", status=400)
    px = prices.get(symbol)
    if not px:
        raise TradingError(f"no live price for {symbol}", status=502)
    return place_order(
        store,
        side="sell",
        symbol=symbol,
        qty=amount,
        price=float(px),
        reason=reason,
        confidence=100.0,
        execution_mode="manual",
        prices=prices,
        thesis=str(held[0].get("thesis") or ""),
        quote_currency=held[0].get("currency"),
        origin="manual",
        http=http,
    )


def manual_order(
    store: TradingStore,
    *,
    side: str,
    symbol: str,
    prices: dict[str, float],
    qty: float | None = None,
    notional: float | None = None,
    thesis: str = "",
    quote_currency: str | None = None,
    fx_rate: float = 1.0,
    http: Any = None,
) -> dict[str, Any]:
    """A buy or sell typed on the desk. Sized by qty or by notional in book currency."""
    side = str(side or "").strip().lower()
    symbol = str(symbol or "").strip().upper()
    if side not in {"buy", "sell"}:
        raise TradingError("side must be buy or sell", status=400)
    if not symbol:
        raise TradingError("symbol required", status=400)
    px = prices.get(symbol)
    if not px:
        raise TradingError(f"no live price for {symbol}", status=502)
    amount = float(qty or 0)
    if amount <= 0 and notional:
        amount = round_qty(float(notional) / float(px), float(px), symbol)
        if amount <= 0:
            ccy = str(store.load_portfolio().get("currency") or "USD")
            raise TradingError(f"{float(notional):g} {ccy} buys less than one {symbol} at {float(px):.2f} {ccy}", status=400)
    if amount <= 0:
        raise TradingError("qty or notional must be positive", status=400)
    if side == "sell":
        return close_position(store, symbol, prices, qty=amount, reason="sold from the desk", http=http)
    return place_order(
        store,
        side="buy",
        symbol=symbol,
        qty=amount,
        price=float(px),
        reason="bought from the desk",
        confidence=100.0,
        execution_mode="manual",
        prices=prices,
        thesis=thesis,
        quote_currency=quote_currency,
        fx_rate=fx_rate,
        origin="manual",
        http=http,
    )


def update_stop(store: TradingStore, symbol: str, *, stop: float | None = None, take: float | None = None, clear_take: bool = False) -> dict[str, Any]:
    """Move the stop / take of a held name. A stop above the last price is refused."""
    symbol = str(symbol or "").strip().upper()
    book = store.load_portfolio()
    target = None
    for pos in book.get("positions") or []:
        if str(pos.get("symbol") or "").upper() == symbol:
            target = pos
            break
    if target is None:
        raise TradingError(f"no open position in {symbol}", status=404)
    last = float(target.get("last") or target.get("avg") or 0)
    if stop is not None:
        stop = float(stop)
        if stop <= 0 or (last and stop >= last):
            raise TradingError(f"stop must sit below the last price {last:.4f}", status=400)
        target["stop"] = stop
    if clear_take:
        target["take"] = None
    elif take is not None:
        take = float(take)
        if take <= 0 or (last and take <= last):
            raise TradingError(f"take profit must sit above the last price {last:.4f}", status=400)
        target["take"] = take
    store.save_portfolio(book)
    store.append_journal({"kind": "risk", "symbol": symbol, "text": f"stop {target.get('stop')} take {target.get('take')} set from the desk"})
    return target


def proposed_buy(
    store: TradingStore,
    *,
    symbol: str,
    price: float,
    confidence: float,
    prices: dict[str, float],
) -> float:
    book = store.load_portfolio()
    equity = mark_equity(book, {**prices, symbol.upper(): price})
    return size_buy(
        equity=equity,
        price=price,
        limits=limits_from(store.load_settings()),
        confidence=confidence,
        symbol=symbol,
    )


def book_metrics(store: TradingStore, bench_bars: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    book = store.load_portfolio()
    closed = [row for row in store.load_orders() if row.get("status") == "filled" and row.get("side") == "sell"]
    trades = [{"pnl": row.get("pnl") or 0.0} for row in closed]
    curve = book.get("equity_curve") or []
    out = {
        **summarize_curve(curve),
        **summarize_trades(trades),
        "cash": book.get("cash"),
        "realized_pnl": book.get("realized_pnl"),
        "positions": len(book.get("positions") or []),
        "fees": sum(float(row.get("fee") or 0) for row in store.load_orders() if row.get("status") == "filled"),
    }
    if bench_bars:
        out["benchmark"] = vs_benchmark(curve, bench_bars)
    return out
