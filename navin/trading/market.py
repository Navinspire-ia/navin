"""Live market data from public Yahoo, Binance and CoinGecko endpoints.

Never invent prices. Yahoo's batch quote (v7) and fundamentals (v10) need an
anonymous session cookie plus a crumb; the session is acquired once, cached
and refreshed on 401. Every quote carries its quotation currency so the book
can be marked in one currency.
"""

from __future__ import annotations

import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

import defusedxml.ElementTree

from navin.trading.errors import TradingError
from navin.trading.indicators import summarize_technicals
from navin.trading.store import TradingStore
from navin.trading.universes import asset_kind, nft_id_for

HttpGet = Callable[..., bytes]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_YAHOO_QUOTE = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}"
_YAHOO_SUMMARY = (
    "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    "?modules=financialData,defaultKeyStatistics,earningsTrend"
)
_YAHOO_NEWS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
_YAHOO_COOKIE_URL = "https://fc.yahoo.com"
_YAHOO_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
_BINANCE_KLINE = "https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=200"
_BINANCE_TICK = "https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"

_CRYPTO_BINANCE = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT",
    "XRP-USD": "XRPUSDT",
    "ADA-USD": "ADAUSDT",
    "AVAX-USD": "AVAXUSDT",
    "DOGE-USD": "DOGEUSDT",
    "DOT-USD": "DOTUSDT",
    "LINK-USD": "LINKUSDT",
    "MATIC-USD": "MATICUSDT",
    "ATOM-USD": "ATOMUSDT",
    "LTC-USD": "LTCUSDT",
    "UNI-USD": "UNIUSDT",
    "NEAR-USD": "NEARUSDT",
    "APT-USD": "APTUSDT",
    "SUI-USD": "SUIUSDT",
    "ARB-USD": "ARBUSDT",
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
    "SOLUSDT": "SOLUSDT",
}
_COINGECKO_NFT = "https://api.coingecko.com/api/v3/nfts/{nft_id}"

# Yahoo quotes London stocks in pence; the book wants pounds.
_MINOR_UNITS = {"GBP": ("GBP", 0.01), "ILA": ("ILS", 0.01), "ZAC": ("ZAR", 0.01)}
_QUOTE_CHUNK = 40
SESSION_TTL_S = 1800.0


def default_http_get(url: str, timeout: float = 12.0, headers: dict[str, str] | None = None) -> bytes:
    merged = {"User-Agent": _UA, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, headers=merged)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise TradingError(f"market data HTTP {exc.code} for {url}", status=502) from exc
    except urllib.error.URLError as exc:
        raise TradingError(f"market data unreachable: {exc.reason}", status=502) from exc
    except (TimeoutError, OSError) as exc:
        raise TradingError(f"market data unreachable: {exc}", status=502) from exc


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        value = value.get("raw", value.get("fmt"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json(http_get: HttpGet, url: str, headers: dict[str, str] | None = None) -> Any:
    raw = _get(http_get, url, headers)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TradingError("market data was not JSON", status=502) from exc


def _get(http_get: HttpGet, url: str, headers: dict[str, str] | None) -> bytes:
    """Injected getters (tests, fixtures) only take a URL; the default takes headers."""
    if headers and http_get is default_http_get:
        return http_get(url, headers=headers)
    return http_get(url)


# ---------------------------------------------------------------- Yahoo session


def acquire_yahoo_session(timeout: float = 12.0) -> dict[str, str] | None:
    """Anonymous cookie + crumb. None when Yahoo refuses (offline, consent wall)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", _UA), ("Accept", "*/*"), ("Accept-Language", "en-US,en;q=0.9")]
    try:
        try:
            opener.open(_YAHOO_COOKIE_URL, timeout=timeout).close()
        except urllib.error.HTTPError:
            pass  # fc.yahoo.com answers 404 but still sets the A3 cookie
        if not any(cookie.name == "A3" for cookie in jar):
            opener.open("https://finance.yahoo.com/", timeout=timeout).close()
        with opener.open(_YAHOO_CRUMB_URL, timeout=timeout) as resp:
            crumb = resp.read().decode("utf-8", errors="replace").strip()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if not crumb or "<" in crumb or len(crumb) > 64:
        return None
    cookie = "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar if cookie.domain.endswith("yahoo.com"))
    if not cookie:
        return None
    return {"cookie": cookie, "crumb": crumb, "t": time.time()}


def yahoo_session(
    store: TradingStore | None,
    http_get: HttpGet,
    *,
    refresh: bool = False,
) -> dict[str, str] | None:
    """Cached session for the default getter. Injected getters need no crumb."""
    if http_get is not default_http_get:
        return {"cookie": "", "crumb": ""}
    if store is not None and not refresh:
        cached = store.cache_get("yahoo-session", SESSION_TTL_S)
        if isinstance(cached, dict) and cached.get("crumb"):
            return cached
    session = acquire_yahoo_session()
    if session and store is not None:
        store.cache_set("yahoo-session", session)
    return session


def _yahoo_auth_url(url: str, session: dict[str, str] | None) -> tuple[str, dict[str, str] | None]:
    if not session or not session.get("crumb"):
        return url, None
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}crumb={urllib.parse.quote(session['crumb'], safe='')}", {"Cookie": session["cookie"]}


def _yahoo_json(store: TradingStore | None, http_get: HttpGet, url: str) -> Any:
    """Authenticated Yahoo call; one retry with a fresh session on 401."""
    session = yahoo_session(store, http_get)
    if session is None:
        raise TradingError("yahoo session unavailable", status=502)
    auth_url, headers = _yahoo_auth_url(url, session)
    try:
        return _json(http_get, auth_url, headers)
    except TradingError as exc:
        if "HTTP 401" not in exc.message and "HTTP 403" not in exc.message:
            raise
    session = yahoo_session(store, http_get, refresh=True)
    if session is None:
        raise TradingError("yahoo session refused", status=502)
    auth_url, headers = _yahoo_auth_url(url, session)
    return _json(http_get, auth_url, headers)


# ---------------------------------------------------------------- OHLCV


def _normalize_currency(raw: Any, price: float | None = None) -> tuple[str, float]:
    """(ISO currency, multiplier) so GBp 12345 becomes GBP 123.45."""
    code = str(raw or "USD").strip()
    upper = code.upper()
    if code == "GBp" or upper == "GBP" and price is not None and price > 5000:
        return "GBP", 0.01
    if upper in _MINOR_UNITS and code != upper:
        return _MINOR_UNITS[upper]
    return upper or "USD", 1.0


def _yahoo_chart(
    symbol: str,
    http_get: HttpGet,
    range_code: str = "6mo",
    *,
    meta_out: dict[str, Any] | None = None,
) -> list[dict[str, float]]:
    url = (
        _YAHOO_CHART.format(symbol=urllib.parse.quote(symbol, safe=""))
        + f"?interval=1d&range={urllib.parse.quote(range_code)}"
    )
    payload = _json(http_get, url)
    results = (((payload or {}).get("chart") or {}).get("result") or [])
    if not results:
        raise TradingError(f"no chart for {symbol}", status=404)
    row = results[0]
    meta = row.get("meta") or {}
    ts = row.get("timestamp") or []
    quote = ((row.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    currency, factor = _normalize_currency(meta.get("currency"), _safe_float(meta.get("regularMarketPrice")))
    bars: list[dict[str, float]] = []
    for index, stamp in enumerate(ts):
        close = _safe_float(closes[index] if index < len(closes) else None)
        if close is None:
            continue
        bars.append(
            {
                "t": float(stamp),
                "o": (_safe_float(opens[index] if index < len(opens) else None) or close) * factor,
                "h": (_safe_float(highs[index] if index < len(highs) else None) or close) * factor,
                "l": (_safe_float(lows[index] if index < len(lows) else None) or close) * factor,
                "c": close * factor,
                "v": _safe_float(vols[index] if index < len(vols) else None) or 0.0,
            }
        )
    if not bars:
        raise TradingError(f"empty chart for {symbol}", status=404)
    if meta_out is not None:
        meta_out.update(
            {
                "currency": currency,
                "name": meta.get("shortName") or meta.get("longName") or symbol,
                "exchange": meta.get("fullExchangeName") or meta.get("exchangeName") or "",
                "kind": str(meta.get("instrumentType") or "").lower(),
            }
        )
    return bars


def _binance_klines(pair: str, http_get: HttpGet) -> list[dict[str, float]]:
    url = _BINANCE_KLINE.format(symbol=pair)
    payload = _json(http_get, url)
    if not isinstance(payload, list):
        raise TradingError(f"no binance klines for {pair}", status=404)
    bars: list[dict[str, float]] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            continue
        bars.append(
            {
                "t": float(row[0]) / 1000.0,
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            }
        )
    if not bars:
        raise TradingError(f"empty binance klines for {pair}", status=404)
    return bars


def symbol_meta(symbol: str, store: TradingStore | None) -> dict[str, Any]:
    """Currency / name learned from the last chart or quote for *symbol*."""
    if store is None:
        return {}
    cached = store.cache_get(f"meta-{symbol.upper()}", 7 * 86400)
    return cached if isinstance(cached, dict) else {}


def _remember_meta(symbol: str, store: TradingStore | None, meta: dict[str, Any]) -> None:
    if store is None or not meta.get("currency"):
        return
    store.cache_set(f"meta-{symbol.upper()}", meta)


def _nft_history_bars(symbol: str, store: TradingStore | None, quote: dict[str, Any]) -> list[dict[str, float]]:
    """Floors are appended once a day so a trend can be read after a few weeks."""
    price = float(quote.get("price") or 0)
    stamp = float(quote.get("t") or time.time())
    series: list[dict[str, float]] = []
    if store is not None:
        cached = store.cache_get(f"nfthist-{symbol.upper()}", 400 * 86400)
        if isinstance(cached, list):
            series = [row for row in cached if isinstance(row, dict) and row.get("c")]
    day = int(stamp // 86400)
    if series and int(float(series[-1].get("t") or 0) // 86400) == day:
        series[-1] = {**series[-1], "c": price, "h": max(float(series[-1].get("h") or price), price), "l": min(float(series[-1].get("l") or price), price)}
    else:
        series.append({"t": stamp, "o": price, "h": price, "l": price, "c": price, "v": 0.0})
    series = series[-400:]
    if store is not None:
        store.cache_set(f"nfthist-{symbol.upper()}", series)
    return series


def fetch_ohlcv(
    symbol: str,
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
    range_code: str = "6mo",
) -> list[dict[str, float]]:
    getter = http_get or default_http_get
    cache_key = f"ohlcv-{symbol.upper()}-{range_code}"
    if store is not None:
        cached = store.cache_get(cache_key, 900)
        if isinstance(cached, list) and cached:
            return cached
    if asset_kind(symbol) == "nft":
        quote = fetch_nft_quote(symbol, store=store, http_get=getter)
        price = float(quote.get("price") or 0)
        if price <= 0:
            raise TradingError(f"no NFT floor for {symbol}", status=404)
        bars = _nft_history_bars(symbol, store, quote)
        if store is not None:
            store.cache_set(cache_key, bars)
        return bars
    pair = _CRYPTO_BINANCE.get(symbol.upper())
    meta: dict[str, Any] = {}
    try:
        if pair:
            bars = _binance_klines(pair, getter)
            meta = {"currency": "USD", "name": symbol.upper(), "kind": "crypto"}
        else:
            bars = _yahoo_chart(symbol.upper(), getter, range_code, meta_out=meta)
    except TradingError:
        if pair:
            bars = _yahoo_chart(symbol.upper(), getter, range_code, meta_out=meta)
        else:
            raise
    _remember_meta(symbol, store, meta)
    if store is not None:
        store.cache_set(cache_key, bars)
    return bars


def _quote_from_bars(symbol: str, bars: list[dict[str, float]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    last = bars[-1]
    prev = bars[-2] if len(bars) > 1 else last
    change = float(last["c"]) - float(prev["c"])
    pct = (change / float(prev["c"]) * 100.0) if prev["c"] else 0.0
    info = meta or {}
    return {
        "symbol": symbol.upper(),
        "name": info.get("name") or symbol.upper(),
        "price": float(last["c"]),
        "change": change,
        "change_pct": pct,
        "volume": float(last.get("v") or 0),
        "currency": str(info.get("currency") or "USD").upper(),
        "source": "ohlcv",
        "t": float(last.get("t") or time.time()),
    }


# ---------------------------------------------------------------- quotes


def _yahoo_batch_quotes(
    symbols: list[str],
    *,
    store: TradingStore | None,
    http_get: HttpGet,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for start in range(0, len(symbols), _QUOTE_CHUNK):
        chunk = ",".join(symbols[start : start + _QUOTE_CHUNK])
        url = _YAHOO_QUOTE.format(symbols=urllib.parse.quote(chunk, safe=","))
        payload = _yahoo_json(store, http_get, url)
        results = (((payload or {}).get("quoteResponse") or {}).get("result") or [])
        for row in results:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "").upper()
            price = _safe_float(row.get("regularMarketPrice"))
            if not symbol or price is None:
                continue
            currency, factor = _normalize_currency(row.get("currency"), price)
            quote = {
                "symbol": symbol,
                "name": row.get("shortName") or row.get("longName") or symbol,
                "price": price * factor,
                "change": (_safe_float(row.get("regularMarketChange")) or 0.0) * factor,
                "change_pct": _safe_float(row.get("regularMarketChangePercent")) or 0.0,
                "volume": _safe_float(row.get("regularMarketVolume")) or 0.0,
                "market_cap": _safe_float(row.get("marketCap")),
                "pe": _safe_float(row.get("trailingPE")),
                "currency": currency,
                "exchange": row.get("fullExchangeName") or row.get("exchange") or "",
                "market_state": row.get("marketState") or "",
                "kind": str(row.get("quoteType") or "").lower(),
                "source": "yahoo",
                "t": float(row.get("regularMarketTime") or time.time()),
            }
            out[symbol] = quote
            _remember_meta(symbol, store, {"currency": currency, "name": quote["name"], "exchange": quote["exchange"], "kind": quote["kind"]})
    return out


def fetch_quotes(
    symbols: list[str],
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, dict[str, Any]]:
    getter = http_get or default_http_get
    wanted = list(dict.fromkeys(str(item).strip().upper() for item in symbols if str(item).strip()))
    out: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for symbol in wanted:
        if store is not None:
            cached = store.cache_get(f"quote-{symbol}", 45)
            if isinstance(cached, dict) and cached.get("price"):
                out[symbol] = cached
                continue
        pending.append(symbol)
    yahoo_pending = [symbol for symbol in pending if asset_kind(symbol) != "nft" and symbol not in _CRYPTO_BINANCE]
    if yahoo_pending:
        try:
            batch = _yahoo_batch_quotes(yahoo_pending, store=store, http_get=getter)
        except TradingError:
            batch = {}
        for symbol, quote in batch.items():
            out[symbol] = quote
            if store is not None:
                store.cache_set(f"quote-{symbol}", quote)
    for symbol in pending:
        if symbol in out:
            continue
        pair = _CRYPTO_BINANCE.get(symbol)
        if pair:
            try:
                tick = _json(getter, _BINANCE_TICK.format(symbol=pair))
                price = _safe_float(tick.get("lastPrice"))
                if price is None:
                    continue
                quote = {
                    "symbol": symbol,
                    "name": symbol,
                    "price": price,
                    "change": _safe_float(tick.get("priceChange")) or 0.0,
                    "change_pct": _safe_float(tick.get("priceChangePercent")) or 0.0,
                    "volume": _safe_float(tick.get("volume")) or 0.0,
                    "currency": "USD",
                    "kind": "crypto",
                    "source": "binance",
                    "t": time.time(),
                }
                out[symbol] = quote
                if store is not None:
                    store.cache_set(f"quote-{symbol}", quote)
                continue
            except TradingError:
                pass
        if asset_kind(symbol) == "nft":
            continue
        try:
            bars = fetch_ohlcv(symbol, store=store, http_get=getter)
            quote = _quote_from_bars(symbol, bars, symbol_meta(symbol, store))
            out[symbol] = quote
            if store is not None:
                store.cache_set(f"quote-{symbol}", quote)
        except TradingError:
            continue
    nft_pending = [symbol for symbol in pending if symbol not in out and asset_kind(symbol) == "nft"]
    for symbol in nft_pending:
        try:
            quote = fetch_nft_quote(symbol, store=store, http_get=getter)
            if quote.get("price"):
                out[symbol] = quote
        except TradingError:
            continue
    return out


def fetch_nft_quote(
    symbol: str,
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, Any]:
    """Live collection floor from CoinGecko, in USD. Never invent a floor."""
    getter = http_get or default_http_get
    cache_key = f"quote-{symbol.upper()}"
    if store is not None:
        cached = store.cache_get(cache_key, 300)
        if isinstance(cached, dict) and cached.get("price"):
            return cached
    nft_id = nft_id_for(symbol)
    if not nft_id:
        raise TradingError(f"unknown NFT collection {symbol}", status=404)
    payload = _json(getter, _COINGECKO_NFT.format(nft_id=urllib.parse.quote(nft_id, safe="")))
    if not isinstance(payload, dict):
        raise TradingError(f"NFT floor unavailable for {symbol}", status=404)
    raw_floor = payload.get("floor_price")
    floor = _safe_float(raw_floor.get("usd")) if isinstance(raw_floor, dict) else None
    currency = "USD"
    if floor is None and isinstance(raw_floor, dict):
        # No USD leg: keep the native floor but say so (ETH, SOL...), never call it USD.
        floor = _safe_float(raw_floor.get("native_currency"))
        currency = str(payload.get("native_currency_symbol") or payload.get("native_currency") or "ETH").upper()[:5]
    if floor is None or floor <= 0:
        raise TradingError(f"NFT floor unavailable for {symbol}", status=404)
    change_pct = _safe_float(payload.get("floor_price_in_usd_24h_percentage_change")) or 0.0
    quote = {
        "symbol": symbol.upper(),
        "name": payload.get("name") or symbol.upper(),
        "price": floor,
        "change": floor - floor / (1 + change_pct / 100.0) if change_pct > -100 else 0.0,
        "change_pct": change_pct,
        "volume": _safe_float((payload.get("volume_24h") or {}).get("usd")) or 0.0 if isinstance(payload.get("volume_24h"), dict) else 0.0,
        "currency": currency,
        "source": "coingecko-nft",
        "kind": "nft",
        "t": time.time(),
    }
    if store is not None:
        store.cache_set(cache_key, quote)
        _nft_history_bars(symbol, store, quote)
    return quote


# ---------------------------------------------------------------- FX


def fetch_fx_rates(
    currencies: list[str],
    book_currency: str,
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, float]:
    """``{"USD": 0.92, "EUR": 1.0}``: multiply an amount in *ccy* to get *book_currency*."""
    book = str(book_currency or "USD").upper()
    rates: dict[str, float] = {book: 1.0}
    needed = sorted({str(ccy or "").upper() for ccy in currencies if ccy and str(ccy).upper() != book})
    if not needed:
        return rates
    pairs = [f"{ccy}{book}=X" for ccy in needed]
    quotes = fetch_quotes(pairs, store=store, http_get=http_get)
    for ccy, pair in zip(needed, pairs, strict=True):
        price = _safe_float((quotes.get(pair) or {}).get("price"))
        if price and price > 0:
            rates[ccy] = price
    return rates


def to_book_currency(amount: float, currency: str | None, rates: dict[str, float], book_currency: str) -> float:
    """Convert *amount* quoted in *currency*; unknown rates fall back to 1:1."""
    ccy = str(currency or book_currency or "USD").upper()
    if ccy == str(book_currency or "USD").upper():
        return amount
    rate = rates.get(ccy)
    return amount * rate if rate else amount


def book_prices(
    quotes: dict[str, dict[str, Any]],
    book_currency: str,
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
    rates: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """``(prices in book currency, fx rates used)`` for a quote batch.

    FX pairs themselves (``EURUSD=X``) are left untouched: they are rates,
    not assets the book holds.
    """
    book = str(book_currency or "USD").upper()
    currencies = [str(row.get("currency") or book) for symbol, row in quotes.items() if not symbol.endswith("=X")]
    fx = dict(rates) if rates else fetch_fx_rates(currencies, book, store=store, http_get=http_get)
    prices: dict[str, float] = {}
    for symbol, row in quotes.items():
        price = _safe_float(row.get("price"))
        if price is None:
            continue
        if symbol.endswith("=X"):
            prices[symbol] = price
            continue
        prices[symbol] = to_book_currency(price, row.get("currency"), fx, book)
    return prices, fx


def fetch_fx_eurusd(
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
) -> float | None:
    quotes = fetch_quotes(["EURUSD=X"], store=store, http_get=http_get)
    price = _safe_float((quotes.get("EURUSD=X") or {}).get("price"))
    return price if price and price > 0 else None


def to_portfolio_ccy(amount_usd: float, currency: str, eurusd: float | None) -> tuple[float, str]:
    ccy = (currency or "EUR").upper()
    if ccy != "EUR" or eurusd is None or eurusd <= 0:
        return amount_usd, "USD"
    return amount_usd / eurusd, "EUR"


# ---------------------------------------------------------------- fundamentals


def fetch_fundamentals(
    symbol: str,
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
) -> dict[str, Any]:
    getter = http_get or default_http_get
    cache_key = f"fund-{symbol.upper()}"
    if store is not None:
        cached = store.cache_get(cache_key, 6 * 3600)
        if isinstance(cached, dict) and (cached.get("available") or cached.get("kind") in {"crypto", "nft"}):
            return cached
        if isinstance(cached, dict) and store.cache_get(cache_key, 900) is not None:
            return cached  # a failed feed is retried every 15 minutes, not every call
    kind = asset_kind(symbol)
    if kind in {"crypto", "nft"}:
        row = {
            "symbol": symbol.upper(),
            "kind": kind,
            "revenue_growth": None,
            "debt_equity": 0.0,
            "profit_margin": None,
            "pe": None,
            "free_cashflow": None,
            "available": False,
            "note": f"{kind} has no corporate fundamentals",
        }
        if store is not None:
            store.cache_set(cache_key, row)
        return row
    url = _YAHOO_SUMMARY.format(symbol=urllib.parse.quote(symbol.upper(), safe=""))
    try:
        payload = _yahoo_json(store, getter, url)
        result = (((payload or {}).get("quoteSummary") or {}).get("result") or [{}])[0]
        fin = result.get("financialData") or {}
        stats = result.get("defaultKeyStatistics") or {}
        trends = ((result.get("earningsTrend") or {}).get("trend") or [])
        growth = _safe_float(fin.get("revenueGrowth"))
        if growth is not None and abs(growth) <= 5:
            growth = growth * 100.0
        if growth is None:
            for item in trends:
                if item.get("period") == "0y":
                    growth = _safe_float((item.get("revenueEstimate") or {}).get("growth"))
                    if growth is not None and abs(growth) <= 5:
                        growth = growth * 100.0
        row = {
            "symbol": symbol.upper(),
            "kind": "equity",
            "revenue_growth": growth,
            "debt_equity": _safe_float(fin.get("debtToEquity")),
            "profit_margin": _safe_float(fin.get("profitMargins")),
            "pe": _safe_float(stats.get("trailingPE") or stats.get("forwardPE")),
            "free_cashflow": _safe_float(fin.get("freeCashflow")),
            "recommendation": (fin.get("recommendationKey") or ""),
            "target": _safe_float(fin.get("targetMeanPrice")),
            "available": True,
            "source": "yahoo",
        }
    except TradingError as exc:
        row = {
            "symbol": symbol.upper(),
            "kind": "equity",
            "available": False,
            "note": f"fundamentals feed unavailable ({exc.message[:80]})",
        }
    if store is not None:
        store.cache_set(cache_key, row)
    return row


# ---------------------------------------------------------------- news


_POS_WORDS = (
    "beat", "beats", "upgrade", "raises", "surge", "record", "growth", "profit",
    "outperform", "bullish", "approval", "partnership", "hausse", "record", "contrat",
)
_NEG_WORDS = (
    "miss", "misses", "downgrade", "cuts", "plunge", "lawsuit", "fraud", "probe",
    "warning", "layoff", "bankrupt", "bearish", "recall", "fine", "chute", "baisse",
    "enquete", "amende", "licenciements",
)


def fetch_news(
    symbol: str,
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
) -> list[dict[str, Any]]:
    getter = http_get or default_http_get
    cache_key = f"news-{symbol.upper()}"
    if store is not None:
        cached = store.cache_get(cache_key, 900)
        if isinstance(cached, list):
            return cached
    url = _YAHOO_NEWS.format(symbol=urllib.parse.quote(symbol.upper(), safe=""))
    try:
        raw = getter(url)
    except TradingError:
        return []
    try:
        root = defusedxml.ElementTree.fromstring(raw)
    except Exception:  # noqa: BLE001 - defusedxml raises several parse errors
        return []
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item")[:8]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title:
            continue
        items.append({"title": title, "url": link, "published": (item.findtext("pubDate") or "").strip()})
    if store is not None:
        store.cache_set(cache_key, items)
    return items


def score_headlines(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"score": 50.0, "bias": "neutral", "hits": 0, "reasons": ["no fresh headlines"], "method": "keywords"}
    pos = 0
    neg = 0
    reasons: list[str] = []
    for item in items:
        title = str(item.get("title") or "").lower()
        if any(re.search(rf"\b{re.escape(word)}\b", title) for word in _POS_WORDS):
            pos += 1
            reasons.append(f"+ {item.get('title')}")
        if any(re.search(rf"\b{re.escape(word)}\b", title) for word in _NEG_WORDS):
            neg += 1
            reasons.append(f"- {item.get('title')}")
    raw = 50.0 + (pos - neg) * 8.0
    score = max(5.0, min(95.0, raw))
    if score >= 62:
        bias = "positive"
    elif score <= 38:
        bias = "negative"
    else:
        bias = "neutral"
    return {
        "score": score,
        "bias": bias,
        "hits": pos + neg,
        "reasons": reasons[:4] or [f"{len(items)} headlines, no strong keywords"],
        "method": "keywords",
    }


def sentiment_for(
    symbol: str,
    items: list[dict[str, Any]],
    *,
    store: TradingStore | None = None,
    ai: bool = True,
) -> dict[str, Any]:
    """Keyword score, upgraded by the routed model when one is configured."""
    base = score_headlines(items)
    if not ai or not items:
        return base
    cache_key = f"sent-{symbol.upper()}"
    if store is not None:
        cached = store.cache_get(cache_key, 900)
        if isinstance(cached, dict) and cached.get("score") is not None:
            return cached
    try:
        from navin.trading.ai import score_news

        scored = score_news(symbol, items)
    except Exception:  # noqa: BLE001 - a model is never a dependency
        scored = None
    if not scored:
        return base
    merged = {**base, **scored, "method": "model"}
    if store is not None:
        store.cache_set(cache_key, merged)
    return merged


def snapshot_symbol(
    symbol: str,
    *,
    store: TradingStore | None = None,
    http_get: HttpGet | None = None,
    quote: dict[str, Any] | None = None,
    ai: bool = True,
) -> dict[str, Any]:
    bars = fetch_ohlcv(symbol, store=store, http_get=http_get)
    tech = summarize_technicals(bars)
    fund = fetch_fundamentals(symbol, store=store, http_get=http_get)
    news = fetch_news(symbol, store=store, http_get=http_get)
    sent = sentiment_for(symbol, news, store=store, ai=ai)
    q = quote or _quote_from_bars(symbol, bars, symbol_meta(symbol, store))
    return {
        "symbol": symbol.upper(),
        "quote": q,
        "technical": {**tech, "bars": len(bars)},
        "fundamental": fund,
        "news": news,
        "sentiment": sent,
        "bars": bars[-60:],
    }
