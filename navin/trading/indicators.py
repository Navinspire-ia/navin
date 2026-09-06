"""Deterministic technicals. No model, no guess."""

from __future__ import annotations

from typing import Sequence


def _closes(bars: Sequence[dict]) -> list[float]:
    out: list[float] = []
    for bar in bars:
        try:
            out.append(float(bar.get("c")))
        except (TypeError, ValueError):
            continue
    return out


def sma(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema(values: Sequence[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1)
    acc = sum(values[:period]) / period
    for value in values[period:]:
        acc = value * k + acc * (1.0 - k)
    return acc


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for prev, curr in zip(values[-(period + 1) : -1], values[-period:]):
        delta = curr - prev
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(values: Sequence[float]) -> dict[str, float | None]:
    fast = ema(values, 12)
    slow = ema(values, 26)
    if fast is None or slow is None:
        return {"macd": None, "signal": None, "hist": None}
    line = fast - slow
    # Signal needs a MACD series; approximate with last line vs SMA of recent diffs.
    recent = []
    for end in range(26, len(values) + 1):
        f = ema(values[:end], 12)
        s = ema(values[:end], 26)
        if f is not None and s is not None:
            recent.append(f - s)
    signal = ema(recent, 9) if recent else None
    hist = None if signal is None else line - signal
    return {"macd": line, "signal": signal, "hist": hist}


def atr(bars: Sequence[dict], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    trs: list[float] = []
    prev_close = None
    for bar in bars[-(period + 1) :]:
        high = float(bar.get("h") or 0)
        low = float(bar.get("l") or 0)
        close = float(bar.get("c") or 0)
        if prev_close is None:
            trs.append(max(high - low, 0.0))
        else:
            trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    window = trs[-period:]
    return sum(window) / period if window else None


def support_resistance(bars: Sequence[dict], lookback: int = 20) -> dict[str, float | None]:
    window = list(bars[-lookback:]) if bars else []
    if not window:
        return {"support": None, "resistance": None}
    lows = [float(bar.get("l") or 0) for bar in window]
    highs = [float(bar.get("h") or 0) for bar in window]
    return {"support": min(lows) if lows else None, "resistance": max(highs) if highs else None}


def summarize_technicals(bars: Sequence[dict]) -> dict[str, float | None | str | bool]:
    closes = _closes(bars)
    last = closes[-1] if closes else None
    sma20 = sma(closes, 20)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    macd_row = macd(closes)
    levels = support_resistance(bars)
    volumes = []
    for bar in bars[-20:]:
        try:
            volumes.append(float(bar.get("v") or 0))
        except (TypeError, ValueError):
            continue
    vol_last = volumes[-1] if volumes else None
    vol_avg = (sum(volumes) / len(volumes)) if volumes else None
    uptrend = bool(
        last is not None
        and sma50 is not None
        and last > sma50
        and (sma200 is None or sma50 > sma200)
    )
    return {
        "last": last,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "rsi": rsi(closes),
        "macd": macd_row.get("macd"),
        "macd_signal": macd_row.get("signal"),
        "macd_hist": macd_row.get("hist"),
        "atr": atr(bars),
        "support": levels.get("support"),
        "resistance": levels.get("resistance"),
        "volume": vol_last,
        "volume_avg": vol_avg,
        "uptrend": uptrend,
    }
