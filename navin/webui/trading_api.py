# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP payloads for the Trading Agent OS desk."""

from __future__ import annotations

from typing import Any

from navin.trading.brokers import SECRET_NAMES, broker_status, test_connection
from navin.trading.errors import TradingError
from navin.trading.heartbeat import HEARTBEAT_TRADING_ACTIONS
from navin.trading.loop import (
    desk_snapshot,
    guard_tick,
    live_prices,
    maybe_tick,
    peek_loop,
    run_symbol_backtest,
    run_symbol_research,
    start_loop,
    stop_loop,
    symbol_chart,
    update_loop_schedule,
)
from navin.trading.mandate import catalog, merge_mandate, normalize_mandate, resolve_mandate
from navin.trading.notify import channel_readiness
from navin.trading.portfolio import close_position, decide_order, manual_order, update_stop
from navin.trading.schedule import LoopScheduleError
from navin.trading.store import (
    BROKERS,
    TradingStore,
    default_alerts,
    default_risk,
    normalize_execution,
)
from navin.trading.strategy import parse_strategy

RISK_KEYS = (
    "max_position_pct",
    "max_sector_pct",
    "max_daily_loss_pct",
    "max_drawdown_pct",
    "stop_loss_pct",
    "take_profit_pct",
    "leverage",
    "max_daily_orders",
    "min_cash_pct",
    "approval_notional",
    "allowed_assets",
    "blocked_assets",
)


def _store() -> TradingStore:
    return TradingStore()


def snapshot_payload() -> dict[str, Any]:
    return desk_snapshot(_store())


def tick_payload(*, force: bool = False) -> dict[str, Any]:
    return maybe_tick(_store(), force=force)


def _schedule_from_body(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("schedule")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TradingError("schedule must be an object", status=400)
    return raw


def start_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    try:
        return start_loop(
            _store(),
            schedule=_schedule_from_body(body),
            run_now=bool(body.get("run_now")),
            tz=str(body.get("tz") or "") or None,
        )
    except LoopScheduleError as exc:
        raise TradingError(str(exc), status=400) from exc


def schedule_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = payload or {}
    raw = _schedule_from_body(body)
    if raw is None:
        raise TradingError("schedule is required", status=400)
    try:
        return {"loop": update_loop_schedule(_store(), schedule=raw, tz=str(body.get("tz") or "") or None)}
    except LoopScheduleError as exc:
        raise TradingError(str(exc), status=400) from exc


def stop_payload() -> dict[str, Any]:
    return {"loop": stop_loop(_store())}


def watchlist_payload(symbols: list[str] | None = None) -> dict[str, Any]:
    store = _store()
    if symbols is not None:
        store.save_watchlist(symbols)
    return {"watchlist": store.load_watchlist()}


def compile_strategy_payload(brief: str) -> dict[str, Any]:
    store = _store()
    try:
        current = store.load_settings()
        row = parse_strategy(
            brief,
            store.active_strategy(),
            mandate=current.get("mandate") if isinstance(current.get("mandate"), dict) else None,
        )
    except ValueError as exc:
        raise TradingError(str(exc), status=400) from exc
    saved = store.upsert_strategy(row, make_active=True)
    settings = store.load_settings()
    risk = settings.get("risk") or default_risk()
    if saved.get("max_position") is not None:
        risk["max_position_pct"] = float(saved["max_position"])
    if saved.get("stop_loss") is not None:
        risk["stop_loss_pct"] = float(saved["stop_loss"])
    if saved.get("approval_notional") is not None:
        risk["approval_notional"] = float(saved["approval_notional"])
    settings["risk"] = risk
    if isinstance(saved.get("mandate"), dict):
        settings["mandate"] = normalize_mandate(saved["mandate"])
    settings["currency"] = resolve_mandate(settings.get("mandate")).get("currency") or "USD"
    settings["execution_mode"] = str(saved.get("execution_mode") or "autonomous")
    store.save_settings(settings)
    store.append_journal({"kind": "strategy", "text": saved.get("name") or "strategy updated"})
    return {"strategy": saved, "settings": settings}


def mandate_payload(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    settings = store.load_settings()
    incoming = body.get("mandate") if isinstance(body.get("mandate"), dict) else body
    settings["mandate"] = merge_mandate(settings.get("mandate") if isinstance(settings.get("mandate"), dict) else None, incoming)
    resolved = resolve_mandate(settings["mandate"])
    settings["currency"] = resolved.get("currency") or "USD"
    store.save_settings(settings)
    store.append_journal({"kind": "mandate", "text": "mandate updated"})
    return {
        "mandate": resolve_mandate(settings["mandate"]),
        "settings": settings,
        "catalog": catalog(),
        "channels": channel_readiness(),
    }


def settings_payload(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    settings = store.load_settings()
    if "starting_cash" in body:
        settings["starting_cash"] = float(body.get("starting_cash") or settings.get("starting_cash") or 0)
    if "benchmark" in body:
        settings["benchmark"] = str(body.get("benchmark") or "QQQ").upper()
    if isinstance(body.get("risk"), dict):
        risk = settings.get("risk") or default_risk()
        incoming = {key: value for key, value in body["risk"].items() if key in RISK_KEYS}
        if "leverage" in incoming:
            incoming["leverage"] = bool(incoming["leverage"])
        for key in ("allowed_assets", "blocked_assets"):
            if key in incoming:
                raw = incoming[key]
                items = raw if isinstance(raw, list) else str(raw or "").replace(";", ",").split(",")
                incoming[key] = [str(item).strip().upper() for item in items if str(item).strip()]
        if "take_profit_pct" in incoming and incoming["take_profit_pct"] in ("", None, 0, "0"):
            incoming["take_profit_pct"] = None
        for key, value in list(incoming.items()):
            if key in {"leverage", "allowed_assets", "blocked_assets", "take_profit_pct"}:
                continue
            try:
                incoming[key] = float(value)
            except (TypeError, ValueError) as exc:
                raise TradingError(f"risk.{key} must be a number", status=400) from exc
            if incoming[key] < 0:
                raise TradingError(f"risk.{key} must be positive", status=400)
        if incoming.get("take_profit_pct") is not None:
            incoming["take_profit_pct"] = float(incoming["take_profit_pct"])
        if "max_daily_orders" in incoming:
            incoming["max_daily_orders"] = int(incoming["max_daily_orders"])
        risk.update(incoming)
        settings["risk"] = risk
    if isinstance(body.get("mandate"), dict):
        settings["mandate"] = merge_mandate(
            settings.get("mandate") if isinstance(settings.get("mandate"), dict) else None,
            body["mandate"],
        )
    if "execution_mode" in body:
        mode = str(body.get("execution_mode") or "autonomous").strip().lower()
        if mode not in {"autonomous", "approval", "research", "recommend"}:
            mode = "autonomous"
        settings["execution_mode"] = mode
        active = store.active_strategy()
        active["execution_mode"] = mode
        store.upsert_strategy(active, make_active=True)
    if isinstance(body.get("execution"), dict):
        settings["execution"] = normalize_execution({**(settings.get("execution") or {}), **body["execution"]})
    if isinstance(body.get("alerts"), dict):
        settings["alerts"] = _alerts_from(settings.get("alerts") or default_alerts(), body["alerts"])
    if "ai_assist" in body:
        settings["ai_assist"] = bool(body.get("ai_assist"))
    settings["currency"] = resolve_mandate(settings.get("mandate")).get("currency") or "USD"
    store.save_settings(settings)
    return {"settings": settings, "mandate": resolve_mandate(settings.get("mandate")), "broker": broker_status(store)}


def _alerts_from(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {**default_alerts(), **current}
    for key in ("stop_approach_pct", "drawdown_warn_pct", "daily_loss_warn_pct"):
        if key in incoming:
            try:
                merged[key] = max(0.0, float(incoming[key]))
            except (TypeError, ValueError) as exc:
                raise TradingError(f"alerts.{key} must be a number", status=400) from exc
    if "price_levels" in incoming:
        levels = []
        for row in incoming.get("price_levels") or []:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            try:
                price = float(row.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if symbol and price > 0:
                levels.append({"symbol": symbol, "price": price, "when": "below" if str(row.get("when") or "above") == "below" else "above"})
        merged["price_levels"] = levels[:40]
    return merged


def order_payload(body: dict[str, Any]) -> dict[str, Any]:
    """A buy or sell typed on the desk: live quote, risk engine, then paper or venue."""
    store = _store()
    symbol = str(body.get("symbol") or "").strip().upper()
    if not symbol:
        raise TradingError("symbol required", status=400)
    quotes, prices, fx = live_prices(store, [symbol])
    quote = quotes.get(symbol) or {}
    book_ccy = str(store.load_portfolio().get("currency") or "USD")

    def _num(key: str) -> float | None:
        raw = body.get(key)
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise TradingError(f"{key} must be a number", status=400) from exc

    order = manual_order(
        store,
        side=str(body.get("side") or "buy"),
        symbol=symbol,
        prices=prices,
        qty=_num("qty"),
        notional=_num("notional"),
        thesis=str(body.get("thesis") or ""),
        quote_currency=quote.get("currency"),
        fx_rate=fx.get(str(quote.get("currency") or book_ccy).upper(), 1.0),
    )
    return {"order": order, "portfolio": store.load_portfolio(), "orders": store.load_orders()[-60:], "quote": quote}


def close_payload(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    symbol = str(body.get("symbol") or "").strip().upper()
    if not symbol:
        raise TradingError("symbol required", status=400)
    _quotes, prices, _fx = live_prices(store, [symbol])
    qty = body.get("qty")
    try:
        amount = float(qty) if qty not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise TradingError("qty must be a number", status=400) from exc
    order = close_position(store, symbol, prices, qty=amount)
    return {"order": order, "portfolio": store.load_portfolio(), "orders": store.load_orders()[-60:]}


def position_stop_payload(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    symbol = str(body.get("symbol") or "").strip().upper()
    if not symbol:
        raise TradingError("symbol required", status=400)

    def _num(key: str) -> float | None:
        raw = body.get(key)
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise TradingError(f"{key} must be a number", status=400) from exc

    position = update_stop(store, symbol, stop=_num("stop"), take=_num("take"), clear_take=bool(body.get("clear_take")))
    return {"position": position, "portfolio": store.load_portfolio()}


def chart_payload(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    symbol = str(body.get("symbol") or "").strip().upper()
    if not symbol:
        raise TradingError("symbol required", status=400)
    range_code = str(body.get("range") or "6mo")
    if range_code not in {"1mo", "3mo", "6mo", "1y", "2y"}:
        range_code = "6mo"
    return {"chart": symbol_chart(store, symbol, range_code=range_code)}


def broker_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Pick the venue and its sandbox flag; keys go through ``secret``."""
    store = _store()
    settings = store.load_settings()
    execution = dict(settings.get("execution") or {})
    if "broker" in body:
        broker = str(body.get("broker") or "paper").strip().lower()
        if broker not in BROKERS:
            raise TradingError(f"broker must be one of {', '.join(BROKERS)}", status=400)
        execution["broker"] = broker
    for key in ("alpaca_paper", "binance_testnet"):
        if key in body:
            execution[key] = bool(body.get(key))
    for key in ("fee_bps", "slippage_bps", "guard_interval_s"):
        if key in body:
            execution[key] = body.get(key)
    settings["execution"] = normalize_execution(execution)
    store.save_settings(settings)
    store.append_journal({"kind": "settings", "text": f"execution venue {settings['execution']['broker']}"})
    out = {"settings": settings, "broker": broker_status(store)}
    if body.get("test"):
        out["connection"] = test_connection(store, settings["execution"]["broker"])
    return out


def secret_payload(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    name = str(body.get("name") or "").strip()
    known = {key for pair in SECRET_NAMES.values() for key in pair}
    if name not in known:
        raise TradingError(f"secret must be one of {', '.join(sorted(known))}", status=400)
    store.save_secret(name, str(body.get("value") or ""))
    store.append_journal({"kind": "settings", "text": f"secret {name} {'set' if body.get('value') else 'cleared'}"})
    return {"broker": broker_status(store)}


def connection_payload(body: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    return {"connection": test_connection(store, str(body.get("broker") or "") or None), "broker": broker_status(store)}


def activate_strategy_payload(strategy_id: str) -> dict[str, Any]:
    store = _store()
    sid = str(strategy_id or "").strip()
    if not sid:
        raise TradingError("strategy id required", status=400)
    rows = store.load_strategies()
    if not any(str(row.get("id") or "") == sid for row in rows):
        raise TradingError("unknown strategy", status=404)
    saved = store.upsert_strategy({"id": sid}, make_active=True)
    store.append_journal({"kind": "strategy", "text": f"activated {saved.get('name') or sid}"})
    return {"strategy": saved, "strategies": store.load_strategies()}


def research_payload(symbol: str) -> dict[str, Any]:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise TradingError("symbol required", status=400)
    return {"research": run_symbol_research(_store(), symbol)}


def backtest_payload(body: dict[str, Any]) -> dict[str, Any]:
    symbol = str(body.get("symbol") or "").strip().upper()
    if not symbol:
        raise TradingError("symbol required", status=400)
    params: dict[str, Any] = {}
    for key in ("range", "benchmark"):
        if body.get(key):
            params[key] = str(body[key])
    for key in ("fee_bps", "slippage_bps"):
        if body.get(key) not in (None, ""):
            try:
                params[key] = float(body[key])
            except (TypeError, ValueError) as exc:
                raise TradingError(f"{key} must be a number", status=400) from exc
    try:
        return {"backtest": run_symbol_backtest(_store(), symbol, params=params)}
    except ValueError as exc:
        raise TradingError(str(exc), status=400) from exc


def decide_payload(order_id: str, accept: bool) -> dict[str, Any]:
    store = _store()
    _quotes, prices, _fx = live_prices(
        store,
        [str(row.get("symbol")) for row in store.load_orders() if row.get("id") == order_id],
    )
    return {"order": decide_order(store, order_id, accept, prices), "portfolio": store.load_portfolio()}


def handle_trading_action(action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = body or {}
    act = (action or "snapshot").strip().lower()
    from navin.agent.tools.context import is_heartbeat_turn

    heartbeat = is_heartbeat_turn()
    if heartbeat and act not in HEARTBEAT_TRADING_ACTIONS:
        raise TradingError(
            "Refused on heartbeat. Trading silent checks may only run "
            "status/snapshot/journal/watch. "
            "Start, stop, schedule and tick stay on the desk or a user chat.",
            status=400,
        )
    if act in {"snapshot", "desk", "status", ""}:
        return desk_snapshot(_store(), live=not heartbeat)
    if act == "journal":
        store = _store()
        snap = desk_snapshot(store, live=not heartbeat)
        return {"journal": snap.get("journal") or [], "loop": snap.get("loop") or {}}
    if act == "watch":
        from navin.trading.watch import run_watch

        store = _store()
        result = run_watch(store)
        return {"watch": result, "loop": peek_loop(store)}
    if act == "tick":
        return tick_payload(force=bool(payload.get("force", True)))
    if act == "start":
        return start_payload(payload)
    if act == "schedule":
        return schedule_payload(payload)
    if act == "stop":
        return stop_payload()
    if act == "watchlist":
        raw = payload.get("symbols")
        symbols = [str(item) for item in raw] if isinstance(raw, list) else None
        return watchlist_payload(symbols)
    if action == "strategy":
        return compile_strategy_payload(str(payload.get("brief") or ""))
    if action == "activate":
        return activate_strategy_payload(str(payload.get("id") or ""))
    if action == "settings":
        return settings_payload(payload)
    if action == "mandate":
        return mandate_payload(payload)
    if action == "research":
        return research_payload(str(payload.get("symbol") or ""))
    if action == "backtest":
        return backtest_payload(payload)
    if action == "approve":
        return decide_payload(str(payload.get("id") or ""), True)
    if action == "reject":
        return decide_payload(str(payload.get("id") or ""), False)
    if act in {"order", "buy", "sell"}:
        if act in {"buy", "sell"}:
            payload = {**payload, "side": act}
        return order_payload(payload)
    if act == "close":
        return close_payload(payload)
    if act in {"set_stop", "stop_order"}:
        return position_stop_payload(payload)
    if act == "chart":
        return chart_payload(payload)
    if act == "broker":
        return broker_payload(payload)
    if act == "secret":
        return secret_payload(payload)
    if act == "connection":
        return connection_payload(payload)
    if act == "guard":
        return guard_tick(_store(), force=bool(payload.get("force", True)))
    raise TradingError(f"unknown trading action {act}", status=400)
