# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Execution adapters: paper (default), Alpaca (stocks + crypto), Binance (crypto).

The risk engine has already said yes when an order reaches this module. An
adapter only carries the order to the venue and reports what the venue did;
it never resizes, never retries into a bigger position and never invents a
fill. Keys live in the desk secrets file and are only read here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from navin.trading.errors import TradingError
from navin.trading.store import BROKERS, TradingStore, normalize_execution
from navin.trading.universes import asset_kind

HttpJson = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]

ALPACA_LIVE = "https://api.alpaca.markets"
ALPACA_PAPER = "https://paper-api.alpaca.markets"
BINANCE_LIVE = "https://api.binance.com"
BINANCE_TESTNET = "https://testnet.binance.vision"

SECRET_NAMES = {
    "alpaca": ("alpaca_key", "alpaca_secret"),
    "binance": ("binance_key", "binance_secret"),
}

_ALPACA_CRYPTO = {"BTC-USD": "BTC/USD", "ETH-USD": "ETH/USD", "SOL-USD": "SOL/USD", "LTC-USD": "LTC/USD", "DOGE-USD": "DOGE/USD", "AVAX-USD": "AVAX/USD", "LINK-USD": "LINK/USD", "UNI-USD": "UNI/USD", "DOT-USD": "DOT/USD"}
_BINANCE_PAIRS = {
    "BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT", "BNB-USD": "BNBUSDT", "XRP-USD": "XRPUSDT",
    "ADA-USD": "ADAUSDT", "AVAX-USD": "AVAXUSDT", "DOGE-USD": "DOGEUSDT", "DOT-USD": "DOTUSDT", "LINK-USD": "LINKUSDT",
    "MATIC-USD": "MATICUSDT", "ATOM-USD": "ATOMUSDT", "LTC-USD": "LTCUSDT", "UNI-USD": "UNIUSDT", "NEAR-USD": "NEARUSDT",
    "APT-USD": "APTUSDT", "SUI-USD": "SUIUSDT", "ARB-USD": "ARBUSDT",
}


def default_http_json(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": "NavinTrading/2.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TradingError(f"broker unreachable: {exc}", status=502) from exc


def _decode(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": raw[:200].decode("utf-8", errors="replace")}


def _f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- status


def broker_status(store: TradingStore) -> dict[str, Any]:
    """What the desk executes with, without ever echoing a key."""
    execution = normalize_execution(store.load_settings().get("execution"))
    broker = execution["broker"]
    secrets = store.load_secrets()
    rows = []
    for name in BROKERS:
        wanted = SECRET_NAMES.get(name, ())
        rows.append(
            {
                "id": name,
                "active": name == broker,
                "configured": all(secrets.get(key) for key in wanted),
                "secrets": [{"name": key, "set": bool(secrets.get(key))} for key in wanted],
                "sandbox": bool(execution["alpaca_paper"]) if name == "alpaca" else bool(execution["binance_testnet"]) if name == "binance" else True,
                "assets": {"paper": "any", "alpaca": "US stocks, ETFs, crypto", "binance": "crypto spot"}[name],
            }
        )
    live = broker != "paper" and all(secrets.get(key) for key in SECRET_NAMES.get(broker, ()))
    return {
        "broker": broker,
        "live": live,
        "sandbox": execution["alpaca_paper"] if broker == "alpaca" else execution["binance_testnet"] if broker == "binance" else True,
        "fee_bps": execution["fee_bps"],
        "slippage_bps": execution["slippage_bps"],
        "guard_interval_s": execution["guard_interval_s"],
        "brokers": rows,
    }


def can_route(store: TradingStore, symbol: str) -> tuple[str, str]:
    """(broker, venue symbol) for *symbol*, or ("paper", symbol) when it cannot leave paper."""
    execution = normalize_execution(store.load_settings().get("execution"))
    broker = execution["broker"]
    secrets = store.load_secrets()
    if broker == "paper" or not all(secrets.get(key) for key in SECRET_NAMES.get(broker, ())):
        return "paper", symbol.upper()
    upper = symbol.upper()
    kind = asset_kind(upper)
    if broker == "alpaca":
        if kind == "crypto":
            venue = _ALPACA_CRYPTO.get(upper)
            return ("alpaca", venue) if venue else ("paper", upper)
        if kind == "nft" or "." in upper or "=" in upper:
            return "paper", upper  # Alpaca trades US listings only
        return "alpaca", upper
    if broker == "binance":
        venue = _BINANCE_PAIRS.get(upper) or (upper if upper.endswith("USDT") else None)
        return ("binance", venue) if venue else ("paper", upper)
    return "paper", upper


# ---------------------------------------------------------------- Alpaca


def _alpaca_base(store: TradingStore) -> str:
    execution = normalize_execution(store.load_settings().get("execution"))
    return ALPACA_PAPER if execution["alpaca_paper"] else ALPACA_LIVE


def _alpaca_headers(store: TradingStore) -> dict[str, str]:
    key, secret = SECRET_NAMES["alpaca"]
    return {
        "APCA-API-KEY-ID": store.get_secret(key),
        "APCA-API-SECRET-KEY": store.get_secret(secret),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def alpaca_account(store: TradingStore, http: HttpJson = default_http_json) -> dict[str, Any]:
    status, raw = http("GET", f"{_alpaca_base(store)}/v2/account", _alpaca_headers(store), None)
    data = _decode(raw)
    if status != 200:
        raise TradingError(f"alpaca account HTTP {status}: {data.get('message') or data}", status=502)
    return {
        "broker": "alpaca",
        "account": data.get("account_number"),
        "status": data.get("status"),
        "currency": data.get("currency"),
        "cash": _f(data.get("cash")),
        "equity": _f(data.get("equity")),
        "buying_power": _f(data.get("buying_power")),
        "paper": _alpaca_base(store) == ALPACA_PAPER,
    }


def _alpaca_order_row(data: dict[str, Any]) -> dict[str, Any]:
    state = str(data.get("status") or "").lower()
    filled_qty = _f(data.get("filled_qty")) or 0.0
    if state == "filled":
        status = "filled"
    elif state in {"canceled", "cancelled", "expired", "rejected", "stopped", "suspended"}:
        status = "failed"
    elif filled_qty > 0:
        status = "partial"
    else:
        status = "submitted"
    return {
        "status": status,
        "broker_order_id": str(data.get("id") or ""),
        "venue_status": state,
        "filled_qty": filled_qty,
        "fill_price": _f(data.get("filled_avg_price")),
    }


def alpaca_submit(store: TradingStore, *, venue_symbol: str, side: str, qty: float, http: HttpJson = default_http_json) -> dict[str, Any]:
    body = {
        "symbol": venue_symbol,
        "qty": f"{qty:.6f}".rstrip("0").rstrip(".") if "/" in venue_symbol else str(int(qty)) if float(qty).is_integer() else f"{qty:.4f}",
        "side": side,
        "type": "market",
        "time_in_force": "gtc" if "/" in venue_symbol else "day",
    }
    status, raw = http("POST", f"{_alpaca_base(store)}/v2/orders", _alpaca_headers(store), json.dumps(body).encode("utf-8"))
    data = _decode(raw)
    if status not in {200, 201}:
        return {"status": "failed", "error": f"alpaca HTTP {status}: {data.get('message') or data}"}
    return _alpaca_order_row(data)


def alpaca_order(store: TradingStore, broker_order_id: str, http: HttpJson = default_http_json) -> dict[str, Any]:
    status, raw = http("GET", f"{_alpaca_base(store)}/v2/orders/{urllib.parse.quote(broker_order_id, safe='')}", _alpaca_headers(store), None)
    data = _decode(raw)
    if status != 200:
        return {"status": "unknown", "error": f"alpaca HTTP {status}: {data.get('message') or data}"}
    return _alpaca_order_row(data)


# ---------------------------------------------------------------- Binance


def _binance_base(store: TradingStore) -> str:
    execution = normalize_execution(store.load_settings().get("execution"))
    return BINANCE_TESTNET if execution["binance_testnet"] else BINANCE_LIVE


def _binance_signed(store: TradingStore, params: dict[str, Any]) -> tuple[str, dict[str, str]]:
    key, secret = SECRET_NAMES["binance"]
    params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
    query = urllib.parse.urlencode(params)
    signature = hmac.new(store.get_secret(secret).encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}", {"X-MBX-APIKEY": store.get_secret(key)}


def binance_account(store: TradingStore, http: HttpJson = default_http_json) -> dict[str, Any]:
    query, headers = _binance_signed(store, {})
    status, raw = http("GET", f"{_binance_base(store)}/api/v3/account?{query}", headers, None)
    data = _decode(raw)
    if status != 200:
        raise TradingError(f"binance account HTTP {status}: {data.get('msg') or data}", status=502)
    balances = [
        {"asset": row.get("asset"), "free": _f(row.get("free")), "locked": _f(row.get("locked"))}
        for row in (data.get("balances") or [])
        if (_f(row.get("free")) or 0) > 0 or (_f(row.get("locked")) or 0) > 0
    ]
    return {
        "broker": "binance",
        "can_trade": bool(data.get("canTrade")),
        "balances": balances[:20],
        "testnet": _binance_base(store) == BINANCE_TESTNET,
    }


def _binance_order_row(data: dict[str, Any]) -> dict[str, Any]:
    state = str(data.get("status") or "").upper()
    fills = data.get("fills") or []
    filled_qty = _f(data.get("executedQty")) or 0.0
    fill_price = None
    if fills:
        total_qty = sum(_f(row.get("qty")) or 0 for row in fills)
        total_cost = sum((_f(row.get("qty")) or 0) * (_f(row.get("price")) or 0) for row in fills)
        fill_price = total_cost / total_qty if total_qty else None
    elif filled_qty > 0 and _f(data.get("cummulativeQuoteQty")):
        fill_price = float(data["cummulativeQuoteQty"]) / filled_qty
    if state == "FILLED":
        status = "filled"
    elif state in {"CANCELED", "REJECTED", "EXPIRED", "EXPIRED_IN_MATCH"}:
        status = "failed"
    elif state == "PARTIALLY_FILLED":
        status = "partial"
    else:
        status = "submitted"
    return {
        "status": status,
        "broker_order_id": str(data.get("orderId") or ""),
        "venue_status": state,
        "filled_qty": filled_qty,
        "fill_price": fill_price,
    }


def binance_submit(store: TradingStore, *, venue_symbol: str, side: str, qty: float, http: HttpJson = default_http_json) -> dict[str, Any]:
    query, headers = _binance_signed(
        store,
        {"symbol": venue_symbol, "side": side.upper(), "type": "MARKET", "quantity": f"{qty:.6f}".rstrip("0").rstrip("."), "newOrderRespType": "FULL"},
    )
    status, raw = http("POST", f"{_binance_base(store)}/api/v3/order?{query}", headers, b"")
    data = _decode(raw)
    if status != 200:
        return {"status": "failed", "error": f"binance HTTP {status}: {data.get('msg') or data}"}
    return _binance_order_row(data)


def binance_order(store: TradingStore, venue_symbol: str, broker_order_id: str, http: HttpJson = default_http_json) -> dict[str, Any]:
    query, headers = _binance_signed(store, {"symbol": venue_symbol, "orderId": broker_order_id})
    status, raw = http("GET", f"{_binance_base(store)}/api/v3/order?{query}", headers, None)
    data = _decode(raw)
    if status != 200:
        return {"status": "unknown", "error": f"binance HTTP {status}: {data.get('msg') or data}"}
    return _binance_order_row(data)


# ---------------------------------------------------------------- facade


def submit_order(
    store: TradingStore,
    *,
    symbol: str,
    side: str,
    qty: float,
    http: HttpJson = default_http_json,
) -> dict[str, Any]:
    """Carry an approved order to the configured venue. Paper answers instantly."""
    broker, venue = can_route(store, symbol)
    if broker == "paper":
        return {"broker": "paper", "venue_symbol": venue, "status": "filled", "filled_qty": qty, "fill_price": None}
    try:
        if broker == "alpaca":
            row = alpaca_submit(store, venue_symbol=venue, side=side, qty=qty, http=http)
        else:
            row = binance_submit(store, venue_symbol=venue, side=side, qty=qty, http=http)
    except TradingError as exc:
        row = {"status": "failed", "error": exc.message}
    return {"broker": broker, "venue_symbol": venue, **row}


def poll_order(store: TradingStore, order: dict[str, Any], http: HttpJson = default_http_json) -> dict[str, Any]:
    """Fresh venue status for an order we submitted earlier."""
    broker = str(order.get("broker") or "paper")
    oid = str(order.get("broker_order_id") or "")
    if broker == "paper" or not oid:
        return {"status": str(order.get("status") or "filled")}
    try:
        if broker == "alpaca":
            return alpaca_order(store, oid, http=http)
        if broker == "binance":
            return binance_order(store, str(order.get("venue_symbol") or ""), oid, http=http)
    except TradingError as exc:
        return {"status": "unknown", "error": exc.message}
    return {"status": "unknown"}


def test_connection(store: TradingStore, broker: str | None = None, http: HttpJson = default_http_json) -> dict[str, Any]:
    """Round-trip to the venue account endpoint with the stored keys."""
    name = str(broker or normalize_execution(store.load_settings().get("execution"))["broker"]).lower()
    if name == "paper":
        book = store.load_portfolio()
        return {"broker": "paper", "ok": True, "cash": book.get("cash"), "currency": book.get("currency")}
    if name not in BROKERS:
        raise TradingError(f"unknown broker {name}", status=400)
    if not all(store.get_secret(key) for key in SECRET_NAMES[name]):
        return {"broker": name, "ok": False, "error": "keys missing"}
    try:
        account = alpaca_account(store, http=http) if name == "alpaca" else binance_account(store, http=http)
    except TradingError as exc:
        return {"broker": name, "ok": False, "error": exc.message}
    return {"broker": name, "ok": True, **account}
