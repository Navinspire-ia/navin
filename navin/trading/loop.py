"""Trading loop: real work or an explicit skip. Never a busy-wait of fake tasks.

Heartbeat still only runs silent watch. This loop is the autonomous cycle:
scan, debate, risk, journal. Start / stop / schedule always persist, even
while a cycle holds the desk lock. The cycle applies that intent when it
finishes so Pause cannot be overwritten.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from navin.loop_runtime import as_float, call_with_deadline, retry_due_after
from navin.trading.agents import run_specialists
from navin.trading.backtest import run_backtest
from navin.trading.brokers import broker_status
from navin.trading.errors import TradingError
from navin.trading.lock import trading_desk_lock
from navin.trading.mandate import agent_risk_for, catalog, resolve_mandate
from navin.trading.market import (
    HttpGet,
    book_prices,
    fetch_ohlcv,
    fetch_quotes,
    snapshot_symbol,
)
from navin.trading.notify import alert_cycle, channel_readiness, deliver_alert
from navin.trading.portfolio import (
    book_metrics,
    mark_to_market,
    monitor_stops,
    place_order,
    proposed_buy,
    reconcile_orders,
)
from navin.trading.schedule import (
    LoopScheduleError,
    describe_schedule,
    format_due,
    next_due_after,
    normalize_schedule,
    with_default_schedule,
)
from navin.trading.screen import screen_candidate
from navin.trading.store import TradingStore
from navin.trading.strategy import apply_strategy_risk
from navin.trading.universes import expand_universe, symbols_for_mandate

HttpGetFn = HttpGet | None

# In-process cycles. A new process has an empty set, so recover_stale_cycle
# can clear a leftover scan/debate phase after a crash.
_live_cycles: set[str] = set()
LIVE_PHASES = frozenset(
    {
        "scan",
        "screen",
        "analyze",
        "debate",
        "risk",
        "execute",
        "journal",
        "busy",
        "hunt",
    }
)

# Wall clock for one scan+research. A hung quote fetch must not freeze the desk.
MAX_CYCLE_S = 8 * 60.0
WATCH_S = 45.0


def _now() -> float:
    return time.time()


def _fingerprint(quotes: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for symbol in sorted(quotes):
        row = quotes[symbol]
        price = float(row.get("price") or 0)
        # Bucket to ~0.4% so noise does not retrigger a full research cycle.
        bucket = round(price * 250) / 250 if price else 0
        parts.append(f"{symbol}:{bucket:.4f}")
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _scan_symbols(store: TradingStore, strategy: dict[str, Any]) -> list[str]:
    extra = list(strategy.get("symbols") or [])
    extra.extend(store.load_watchlist())
    settings = store.load_settings()
    mandate = resolve_mandate(settings.get("mandate") or strategy.get("mandate"))
    if mandate.get("active_domains") or mandate.get("active_countries"):
        universe = symbols_for_mandate(
            list(mandate.get("active_domains") or []),
            list(mandate.get("active_countries") or []),
            extra,
            tapes=list(mandate.get("active_tapes") or []) or None,
        )
    else:
        universe = expand_universe(str(strategy.get("universe") or "NASDAQ100"), extra)
    # Cap the quote batch; deep research still uses analyze_budget.
    return universe[:80]


def _needs_research(store: TradingStore, symbol: str, interval_s: float) -> bool:
    book = store.load_research()
    row = book.get(symbol.upper()) if isinstance(book, dict) else None
    if not isinstance(row, dict):
        return True
    stamped = float(row.get("t") or 0)
    return (_now() - stamped) >= interval_s


def _ai_thesis(result: dict[str, Any], snapshot: dict[str, Any], settings: dict[str, Any]) -> str:
    """Routed-model note for the research card; empty when no model is routed."""
    try:
        from navin.trading.ai import write_thesis

        return write_thesis(result, snapshot, settings)
    except Exception as exc:  # noqa: BLE001 - a model is never a dependency
        logger.debug("trading thesis skipped: {}", exc)
        return ""


def _parse_consensus(raw: Any) -> int:
    text = str(raw or "4/5")
    head = text.split("/", 1)[0]
    try:
        return max(1, int(head))
    except ValueError:
        return 4


def _sched(state: dict[str, Any]) -> dict[str, Any] | None:
    raw = state.get("schedule")
    return raw if isinstance(raw, dict) else None


def _busy_payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "did_work": False,
        "phase": "busy",
        "reason": "a cycle is already running",
        "loop": state,
    }


def _apply_intent(
    state: dict[str, Any],
    intent: dict[str, Any] | None,
    *,
    now: float,
    persist_due: bool = True,
) -> dict[str, Any]:
    row = dict(state)
    if not isinstance(intent, dict):
        return row
    if "schedule" in intent and isinstance(intent["schedule"], dict):
        try:
            row["schedule"] = normalize_schedule(intent["schedule"])
        except LoopScheduleError:
            pass
        else:
            if persist_due:
                row["next_due"] = next_due_after(row, now=now, fallback_s=900)
    if "enabled" in intent:
        row["enabled"] = bool(intent["enabled"])
        if not row["enabled"]:
            row["phase"] = "paused"
        elif row.get("phase") == "paused":
            row["phase"] = "armed"
    return row


def _cycle_key(desk: TradingStore) -> str:
    return str(Path(desk.root).resolve())


def mark_loop_cycling(desk: TradingStore, live: bool) -> None:
    key = _cycle_key(desk)
    if live:
        _live_cycles.add(key)
    else:
        _live_cycles.discard(key)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def cycle_expired(state: dict[str, Any] | None, *, now: float | None = None) -> bool:
    """True when a live phase is older than MAX_CYCLE_S (crash, PID reuse, hang)."""
    row = state if isinstance(state, dict) else {}
    if str(row.get("phase") or "") not in LIVE_PHASES:
        return False
    started = as_float(row.get("cycle_started_at"))
    if started <= 0:
        return False
    clock = now if now is not None else _now()
    return (clock - started) > MAX_CYCLE_S


def cycle_is_live(desk: TradingStore, state: dict[str, Any] | None = None) -> bool:
    """True only while a cycle is actually running (this process or another)."""
    row = state if isinstance(state, dict) else {}
    if str(row.get("phase") or "") not in LIVE_PHASES:
        return False
    if cycle_expired(row):
        return False
    if _cycle_key(desk) in _live_cycles:
        return True
    try:
        pid = int(row.get("cycle_pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid and pid != os.getpid():
        return _pid_alive(pid)
    return False


def recover_stale_cycle(desk: TradingStore, *, now: float | None = None) -> dict[str, Any]:
    """Clear a leftover cycle and persist a leftover Stop/Horaires when the lock is free."""
    clock = now if now is not None else _now()
    disk = with_default_schedule(desk.load_loop())
    intent = desk.load_loop_intent()
    stale = str(disk.get("phase") or "") in LIVE_PHASES and not cycle_is_live(desk, disk)
    if cycle_is_live(desk, disk) or (not stale and not intent):
        return _apply_intent(disk, intent, now=clock)
    with trading_desk_lock(desk, wait_s=0) as got:
        if not got:
            return _apply_intent(disk, intent, now=clock)
        current = with_default_schedule(desk.load_loop())
        if cycle_is_live(desk, current):
            return _apply_intent(current, desk.load_loop_intent(), now=clock)
        repaired = False
        if str(current.get("phase") or "") in LIVE_PHASES:
            current.pop("cycle_pid", None)
            current.pop("cycle_started_at", None)
            current["skipped_reason"] = "stale_cycle"
            current["last_result"] = "cycle recovered - previous scan did not finish"
            repaired = True
        current = _apply_intent(current, desk.load_loop_intent(), now=clock)
        if repaired:
            current["phase"] = "paused" if not current.get("enabled") else "idle"
        desk.save_loop(current)
        desk.clear_loop_intent()
        if repaired:
            desk.append_journal({"kind": "loop", "text": "stale cycle recovered"})
        return current


def peek_loop(store: TradingStore, *, now: float | None = None) -> dict[str, Any]:
    """Loop state the desk must show, including Pause/Horaires written mid-cycle."""
    clock = now if now is not None else _now()
    recover_stale_cycle(store, now=clock)
    state = with_default_schedule(store.load_loop())
    return _apply_intent(state, store.load_loop_intent(), now=clock)


def _merge_intent(desk: TradingStore, **fields: Any) -> dict[str, Any]:
    intent = dict(desk.load_loop_intent())
    for key, value in fields.items():
        if value is not None:
            intent[key] = value
    desk.save_loop_intent(intent)
    return intent


def _commit_control(
    desk: TradingStore,
    *,
    now: float,
    journal: str,
    last_result: str | None = None,
    phase: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Write operator intent, then apply it now if the cycle lock is free."""
    intent = _merge_intent(desk, **fields)
    with trading_desk_lock(desk, wait_s=0) as got:
        state = _apply_intent(with_default_schedule(desk.load_loop()), intent, now=now)
        if last_result:
            state["last_result"] = last_result
        if phase:
            if state.get("enabled") or phase == "paused":
                state["phase"] = phase
        if got:
            desk.save_loop(state)
            desk.clear_loop_intent()
    desk.append_journal({"kind": "loop", "text": journal})
    return peek_loop(desk, now=now)


def _finish_cycle_state(
    desk: TradingStore,
    state: dict[str, Any],
    *,
    clock: float,
) -> dict[str, Any]:
    """Cycle fields first, then operator intent so Stop always wins."""
    merged = _apply_intent(state, desk.load_loop_intent(), now=clock)
    if not merged.get("enabled"):
        merged["phase"] = "paused"
    merged.pop("cycle_pid", None)
    merged.pop("cycle_started_at", None)
    desk.save_loop(merged)
    desk.clear_loop_intent()
    return merged


def _watch_ok(watch: dict[str, Any] | None) -> bool:
    if not isinstance(watch, dict):
        return False
    try:
        count = int(watch.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    return count == 0 or bool(watch.get("delivered"))


def _run_locked_watch(desk: TradingStore) -> dict[str, Any]:
    """Same silent book pass as heartbeat, while the cycle already holds the lock."""
    from navin.trading.watch import run_watch

    return run_watch(desk, already_locked=True)


def _mark_pending_if_cycle_alerted(
    desk: TradingStore,
    orders: list[Any],
    sent: dict[str, Any] | None,
) -> None:
    """Keep one notify path: cycle digest first, heartbeat watch only for leftovers."""
    from navin.trading.watch import PENDING, digest_was_delivered, mark_sent

    if not digest_was_delivered(sent):
        return
    events: list[dict[str, Any]] = []
    for row in orders:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status not in PENDING:
            continue
        oid = str(row.get("id") or "").strip()
        if oid:
            events.append({"id": oid, "key": "pending"})
    if events:
        mark_sent(desk, events)


def maybe_tick(
    store: TradingStore | None = None,
    *,
    now: float | None = None,
    force: bool = False,
    http_get: HttpGetFn = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    desk = store or TradingStore()
    clock = now if now is not None else _now()
    state = peek_loop(desk, now=clock)
    if not state.get("enabled") and not force:
        return {
            "did_work": False,
            "phase": "paused",
            "reason": "loop is paused",
            "loop": state,
        }
    if not force and float(state.get("next_due") or 0) > clock + 0.01:
        return {
            "did_work": False,
            "phase": "sleep",
            "reason": "not due yet",
            "loop": state,
        }

    with trading_desk_lock(desk, wait_s=0) as got:
        if not got:
            return _busy_payload(peek_loop(desk, now=clock))
        return _run_cycle(
            desk,
            clock=clock,
            force=force,
            http_get=http_get,
            on_progress=on_progress,
        )


def _run_cycle(
    desk: TradingStore,
    *,
    clock: float,
    force: bool,
    http_get: HttpGetFn = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    state = peek_loop(desk, now=clock)
    was_enabled = bool(state.get("enabled"))
    if not was_enabled and not force:
        state["phase"] = "paused"
        desk.save_loop(state)
        desk.clear_loop_intent()
        return {
            "did_work": False,
            "phase": "paused",
            "reason": "loop is paused",
            "loop": state,
        }

    mark_loop_cycling(desk, True)
    try:
        try:
            return call_with_deadline(
                lambda: _cycle_body(
                    desk,
                    state,
                    was_enabled=was_enabled,
                    clock=clock,
                    force=force,
                    http_get=http_get,
                    on_progress=on_progress,
                ),
                timeout_s=MAX_CYCLE_S,
                label="cycle",
                thread_prefix="navin-trading",
            )
        except Exception as exc:
            logger.exception("Trading loop cycle failed")
            try:
                streak = int(state.get("error_streak") or 0) + 1
            except (TypeError, ValueError):
                streak = 1
            due = retry_due_after({**state, "error_streak": streak}, clock=clock, fallback_s=900)
            summary = f"cycle failed - {exc}"
            state.update(
                {
                    "enabled": was_enabled,
                    "phase": "idle",
                    "last_tick": clock,
                    "next_due": due,
                    "last_result": summary,
                    "skipped_reason": "error",
                    "error_streak": streak,
                }
            )
            state = _finish_cycle_state(desk, state, clock=clock)
            desk.append_journal({"kind": "loop", "text": summary})
            return {
                "did_work": False,
                "phase": state.get("phase") or "idle",
                "reason": summary,
                "loop": state,
            }
    finally:
        mark_loop_cycling(desk, False)


def _cycle_body(
    desk: TradingStore,
    state: dict[str, Any],
    *,
    was_enabled: bool,
    clock: float,
    force: bool,
    http_get: HttpGetFn = None,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    strategy = desk.active_strategy()
    settings = desk.load_settings()
    mandate = resolve_mandate(settings.get("mandate") or strategy.get("mandate"))
    settings["mandate"] = {**mandate, "active_domains": mandate["active_domains"], "active_countries": mandate["active_countries"]}
    risk = apply_strategy_risk(strategy, settings.get("risk") or {})
    if mandate.get("risk_mode") == "agent":
        risk.update(agent_risk_for(mandate.get("active_domains") or ["equities"]))
    if mandate.get("target_mode") == "agent" or mandate.get("target_return_pct"):
        risk["take_profit_pct"] = float(mandate.get("target_return_pct") or risk.get("take_profit_pct") or 12)
    settings["risk"] = risk
    desk.save_settings(settings)

    symbols = _scan_symbols(desk, strategy)
    if on_progress:
        on_progress(f"scan {len(symbols)} symbols")
    state["phase"] = "scan"
    state["cycle_pid"] = os.getpid()
    state["cycle_started_at"] = time.time()
    desk.save_loop(state)

    quotes = fetch_quotes(symbols + [str(settings.get("benchmark") or "QQQ")], store=desk, http_get=http_get)
    bench = quotes.get(str(settings.get("benchmark") or "QQQ").upper())
    bench_chg = float((bench or {}).get("change_pct") or 0) if bench else None
    book_ccy = str(settings.get("currency") or "USD")
    prices, fx = book_prices(quotes, book_ccy, store=desk, http_get=http_get)
    reconciled = reconcile_orders(desk, prices)
    mark_to_market(desk, prices)
    stops = monitor_stops(desk, prices)
    if reconciled:
        stops = [*reconciled, *stops]

    fp = _fingerprint(quotes)
    scan_s = float(strategy.get("scan_interval_s") or 900)
    due = next_due_after(state, now=clock, fallback_s=scan_s)
    move = max((abs(float(row.get("change_pct") or 0)) for row in quotes.values()), default=0.0)
    same_tape = fp == state.get("last_fingerprint") and move < float(strategy.get("price_move_pct") or 0.8)
    if same_tape and not force and not stops:
        watch = _run_locked_watch(desk)
        state.update(
            {
                "enabled": was_enabled,
                "phase": "idle",
                "last_tick": clock,
                "last_watch": clock if _watch_ok(watch) else state.get("last_watch") or 0,
                "next_due": due,
                "last_fingerprint": fp,
                "last_result": "tape unchanged - no new research",
                "skipped_reason": "fingerprint",
                "error_streak": 0,
            }
        )
        state = _finish_cycle_state(desk, state, clock=clock)
        due = next_due_after(state, now=clock, fallback_s=scan_s)
        if state.get("skipped_reason") != "error":
            state["next_due"] = due
            desk.save_loop(state)
        desk.append_journal(
            {
                "kind": "skip",
                "text": f"cycle skipped: prices within {strategy.get('price_move_pct')}% and no stop hit",
            }
        )
        return {
            "did_work": False,
            "phase": state.get("phase") or "idle",
            "reason": "fingerprint",
            "loop": state,
            "stops": [],
        }

    state["phase"] = "screen"
    desk.save_loop(state)
    watch = desk.load_watchlist()
    research_s = float(strategy.get("research_interval_s") or 86400)
    budget = int(strategy.get("analyze_budget") or 8)
    candidates: list[str] = []
    # Prefer names the user is watching, then movers from the scan.
    movers = sorted(
        quotes.values(),
        key=lambda row: abs(float(row.get("change_pct") or 0)),
        reverse=True,
    )
    ordered = list(dict.fromkeys([*watch, *[str(row.get("symbol")) for row in movers]]))
    for symbol in ordered:
        if not symbol or symbol not in quotes:
            continue
        if not _needs_research(desk, symbol, research_s) and not force:
            continue
        candidates.append(symbol)
        if len(candidates) >= budget:
            break

    analyzed: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    book = desk.load_portfolio()
    held_symbols = {str(pos.get("symbol") or "").upper() for pos in (book.get("positions") or [])}
    # A held name is always re-read: the judge may say SELL even when the
    # research interval has not elapsed and the name is not a mover.
    for symbol in held_symbols:
        if symbol in quotes and symbol not in candidates:
            candidates.append(symbol)
    min_conf = float(strategy.get("min_confidence") or 55)
    need_votes = _parse_consensus(strategy.get("require_consensus") or "2/5")
    mode = str(settings.get("execution_mode") or strategy.get("execution_mode") or "autonomous")
    ai_on = settings.get("ai_assist") is not False

    state["phase"] = "analyze"
    desk.save_loop(state)
    for symbol in candidates:
        if on_progress:
            on_progress(f"analyze {symbol}")
        try:
            snap = snapshot_symbol(symbol, store=desk, http_get=http_get, quote=quotes.get(symbol), ai=ai_on)
        except TradingError as exc:
            desk.append_journal({"kind": "data", "symbol": symbol, "text": exc.message})
            continue
        screen = screen_candidate(snap, strategy)
        if not screen["passed"] and symbol not in held_symbols:
            report = {
                "symbol": symbol,
                "action": "WAIT",
                "confidence": 40.0,
                "thesis": "Screen rejected: " + "; ".join(screen["rejected"]),
                "scores": {},
                "screen": screen,
                "t": clock,
            }
            desk.put_research(symbol, report)
            analyzed.append(report)
            continue
        state["phase"] = "debate"
        result = run_specialists(
            snap,
            book,
            bench_change_pct=bench_chg,
            enabled={
                "fundamental": bool(strategy.get("fundamental", True)),
                "technical": bool(strategy.get("technical", True)),
                "sentiment": bool(strategy.get("sentiment", True)),
                "macro": bool(strategy.get("macro", True)),
            },
        )
        result["screen"] = screen
        result["t"] = clock
        result["held"] = symbol in held_symbols
        votes = int(((result.get("debate") or {}).get("consensus") or 0))
        if result["action"] == "BUY" and votes < need_votes:
            result["action"] = "WAIT"
            result["thesis"] = (
                f"Consensus {votes} below required {need_votes}. "
                + str(result.get("thesis") or "")
            )
        if result["action"] == "BUY" and float(result.get("confidence") or 0) < min_conf:
            result["action"] = "WAIT"
            result["thesis"] = (
                f"Confidence {result.get('confidence')} below {min_conf}. "
                + str(result.get("thesis") or "")
            )
        if result["action"] == "BUY" and symbol in held_symbols:
            # Never pyramid from the loop: one line per name, sized once.
            result["action"] = "HOLD"
            result["thesis"] = "Already held; the loop does not add to a line. " + str(result.get("thesis") or "")
        if ai_on:
            result["note"] = _ai_thesis(result, snap, settings)
        desk.put_research(symbol, result)
        analyzed.append(result)
        decisions.append(result)

    state["phase"] = "risk"
    desk.save_loop(state)
    placed: list[dict[str, Any]] = []
    for result in decisions:
        action = result.get("action")
        if action not in {"BUY", "SELL"} or mode == "research":
            continue
        symbol = result["symbol"]
        quote = quotes.get(symbol) or {}
        price = float(prices.get(symbol) or 0)
        if price <= 0:
            continue
        if action == "SELL":
            held_qty = sum(
                float(pos.get("qty") or 0)
                for pos in (desk.load_portfolio().get("positions") or [])
                if str(pos.get("symbol") or "").upper() == symbol
            )
            if held_qty <= 0:
                continue
            state["phase"] = "execute"
            order = place_order(
                desk,
                side="sell",
                symbol=symbol,
                qty=held_qty,
                price=price,
                reason=str(result.get("thesis") or "judge: reduce"),
                confidence=float(result.get("confidence") or 0),
                execution_mode=mode,
                prices=prices,
                thesis=str(result.get("thesis") or ""),
                quote_currency=quote.get("currency"),
                fx_rate=fx.get(str(quote.get("currency") or book_ccy).upper(), 1.0),
            )
            placed.append(order)
            continue
        qty = proposed_buy(
            desk,
            symbol=symbol,
            price=price,
            confidence=float(result.get("confidence") or 0),
            prices=prices,
        )
        if qty <= 0:
            desk.append_journal(
                {"kind": "skip", "symbol": symbol, "text": "size rounded to zero under risk limits"}
            )
            continue
        state["phase"] = "execute"
        order = place_order(
            desk,
            side="buy",
            symbol=symbol,
            qty=qty,
            price=price,
            reason=str(result.get("thesis") or "orchestrator"),
            confidence=float(result.get("confidence") or 0),
            execution_mode=mode,
            prices=prices,
            thesis=str(result.get("thesis") or ""),
            quote_currency=quote.get("currency"),
            fx_rate=fx.get(str(quote.get("currency") or book_ccy).upper(), 1.0),
        )
        placed.append(order)

    state["phase"] = "journal"
    summary = (
        f"cycle {int(state.get('cycle') or 0) + 1}: scanned {len(quotes)} "
        f"researched {len(analyzed)} orders {len(placed)} stops {len(stops)}"
    )
    desk.append_journal({"kind": "cycle", "text": summary})
    try:
        cycle_alerts = alert_cycle(
            desk,
            {"reason": summary, "orders": placed, "stops": stops},
        )
    except Exception:
        cycle_alerts = None
    _mark_pending_if_cycle_alerted(
        desk,
        placed,
        cycle_alerts if isinstance(cycle_alerts, dict) else None,
    )
    watch = _run_locked_watch(desk)
    state.update(
        {
            "enabled": was_enabled,
            "phase": "idle",
            "cycle": int(state.get("cycle") or 0) + 1,
            "last_tick": clock,
            "last_watch": clock if _watch_ok(watch) else state.get("last_watch") or 0,
            "next_due": due,
            "last_fingerprint": fp,
            "last_result": summary,
            "skipped_reason": "",
            "error_streak": 0,
            "analyzed": [row.get("symbol") for row in analyzed],
        }
    )
    state = _finish_cycle_state(desk, state, clock=clock)
    due = next_due_after(state, now=clock, fallback_s=scan_s)
    if state.get("skipped_reason") != "error":
        state["next_due"] = due
        desk.save_loop(state)
    return {
        "did_work": True,
        "phase": state.get("phase") or "idle",
        "reason": summary,
        "loop": state,
        "analyzed": analyzed,
        "orders": placed,
        "stops": stops,
        "quotes": list(quotes.values()),
        "metrics": book_metrics(desk),
        "alerts": cycle_alerts,
        "watch": watch,
    }


def start_loop(
    store: TradingStore | None = None,
    *,
    http_get: HttpGetFn = None,
    schedule: dict[str, Any] | None = None,
    run_now: bool = False,
    now: float | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    desk = store or TradingStore()
    clock = now if now is not None else _now()
    fields: dict[str, Any] = {"enabled": True}
    if schedule is not None:
        fields["schedule"] = normalize_schedule(schedule, tz=tz)
    elif tz:
        current = peek_loop(desk, now=clock)
        if isinstance(current.get("schedule"), dict):
            fields["schedule"] = normalize_schedule(current["schedule"], tz=tz)
    preview = _apply_intent(
        with_default_schedule(desk.load_loop()),
        {**desk.load_loop_intent(), **fields},
        now=clock,
    )
    label = describe_schedule(_sched(preview))
    stamp = format_due(next_due_after(preview, now=clock, fallback_s=900), _sched(preview))
    journal = f"loop started - {label}, next {stamp}"

    if not run_now:
        state = _commit_control(
            desk,
            now=clock,
            journal=journal,
            last_result=f"loop armed - {label}, next {stamp}",
            phase="armed",
            **fields,
        )
        return {
            "did_work": False,
            "phase": "armed",
            "reason": state.get("last_result") or journal,
            "loop": state,
        }

    _merge_intent(desk, **fields)
    with trading_desk_lock(desk, wait_s=0) as got:
        if not got:
            desk.append_journal({"kind": "loop", "text": journal})
            return {
                "did_work": False,
                "phase": "busy",
                "reason": "a cycle is already running",
                "loop": peek_loop(desk, now=clock),
            }
        state = _apply_intent(
            with_default_schedule(desk.load_loop()),
            desk.load_loop_intent(),
            now=clock,
        )
        state["enabled"] = True
        state["next_due"] = 0
        state["phase"] = "armed"
        state["last_result"] = f"loop armed - {label}, next {stamp}"
        desk.save_loop(state)
        desk.append_journal({"kind": "loop", "text": journal})
        return _run_cycle(
            desk,
            clock=clock,
            force=True,
            http_get=http_get,
        )


def update_loop_schedule(
    store: TradingStore | None = None,
    *,
    schedule: dict[str, Any],
    now: float | None = None,
    tz: str | None = None,
) -> dict[str, Any]:
    desk = store or TradingStore()
    clock = now if now is not None else _now()
    normalized = normalize_schedule(schedule, tz=tz)
    preview = _apply_intent(
        with_default_schedule(desk.load_loop()),
        {**desk.load_loop_intent(), "schedule": normalized},
        now=clock,
    )
    label = describe_schedule(normalized)
    stamp = format_due(next_due_after(preview, now=clock, fallback_s=900), normalized)
    journal = (
        f"schedule updated - {label}, next {stamp}"
        if preview.get("enabled")
        else f"schedule saved - {label}"
    )
    return _commit_control(
        desk,
        now=clock,
        journal=journal,
        last_result=journal,
        phase="armed" if preview.get("enabled") else None,
        schedule=normalized,
    )


def stop_loop(store: TradingStore | None = None, *, now: float | None = None) -> dict[str, Any]:
    desk = store or TradingStore()
    clock = now if now is not None else _now()
    label = describe_schedule(_sched(peek_loop(desk, now=clock)))
    journal = f"loop paused - {label}"
    return _commit_control(
        desk,
        now=clock,
        journal=journal,
        last_result=journal,
        phase="paused",
        enabled=False,
    )


def desk_snapshot(
    store: TradingStore | None = None,
    *,
    http_get: HttpGetFn = None,
    live: bool = True,
) -> dict[str, Any]:
    desk = store or TradingStore()
    settings = desk.load_settings()
    strategy = desk.active_strategy()
    book = desk.load_portfolio()
    watch = desk.load_watchlist()
    mandate = resolve_mandate(settings.get("mandate") or strategy.get("mandate"))
    preview: list[str] = []
    tapes = list(mandate.get("active_tapes") or [])
    if tapes:
        for tape in tapes[:8]:
            for symbol in symbols_for_mandate(
                [str(tape.get("domain") or "equities")],
                [str(tape.get("country") or "US")],
                None,
            )[:3]:
                if symbol not in preview:
                    preview.append(symbol)
    else:
        for domain in mandate.get("active_domains") or ["equities"]:
            for symbol in symbols_for_mandate(
                [domain],
                list(mandate.get("active_countries") or []),
                None,
            )[:4]:
                if symbol not in preview:
                    preview.append(symbol)
    core = [
        *[str(pos.get("symbol")) for pos in (book.get("positions") or [])],
        *watch,
        str(settings.get("benchmark") or "QQQ"),
    ]
    symbols = list(dict.fromkeys([*core, *preview]))
    quotes = fetch_quotes(symbols, store=desk, http_get=http_get) if live and symbols else {}
    book_ccy = str(settings.get("currency") or "USD")
    fx: dict[str, float] = {book_ccy: 1.0}
    prices: dict[str, float] = {}
    if live and quotes:
        prices, fx = book_prices(quotes, book_ccy, store=desk, http_get=http_get)
    if live and prices:
        mark_to_market(desk, prices)
        book = desk.load_portfolio()
    bench_bars: list[dict[str, Any]] = []
    if live:
        try:
            bench_bars = fetch_ohlcv(str(settings.get("benchmark") or "QQQ"), store=desk, http_get=http_get)
        except TradingError:
            bench_bars = []
    try:
        from navin.trading.ai import routing as ai_routing

        ai = ai_routing(settings)
    except Exception:  # noqa: BLE001 - settings UI only
        ai = {"enabled": False, "routed": 0, "tasks": []}
    loop_state = peek_loop(desk)
    return {
        "settings": settings,
        "strategy": strategy,
        "strategies": desk.load_strategies(),
        "portfolio": book,
        "watchlist": watch,
        "quotes": quotes,
        "prices": prices,
        "fx": fx,
        "orders": desk.load_orders()[-60:],
        "research": desk.load_research(),
        "journal": desk.load_journal(80),
        "loop": loop_state,
        "guard": {
            "last_guard": loop_state.get("last_guard") or 0,
            "last_guard_result": loop_state.get("last_guard_result") or "",
            "interval_s": int((settings.get("execution") or {}).get("guard_interval_s") or 900),
        },
        "backtests": desk.load_backtests(),
        "metrics": book_metrics(desk, bench_bars or None),
        "broker": broker_status(desk),
        "ai": ai,
        "skills": [
            {"id": "value-investor", "name": "Value Investor"},
            {"id": "momentum-trader", "name": "Momentum Trader"},
            {"id": "crypto-swing", "name": "Crypto Swing"},
            {"id": "nft-collector", "name": "NFT Collector"},
            {"id": "real-estate-investor", "name": "Real Estate Investor"},
            {"id": "global-equities", "name": "Global Equities"},
            {"id": "dividend-portfolio", "name": "Dividend Portfolio"},
            {"id": "earnings-trader", "name": "Earnings Trader"},
            {"id": "macro-investor", "name": "Macro Investor"},
        ],
        "mandate": resolve_mandate(settings.get("mandate")),
        "catalog": catalog(),
        "channels": channel_readiness(),
    }


def live_prices(
    store: TradingStore,
    symbols: list[str],
    *,
    http_get: HttpGetFn = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, float], dict[str, float]]:
    """``(quotes, prices in book currency, fx)`` for held names plus *symbols*."""
    book = store.load_portfolio()
    wanted = list(dict.fromkeys([*[str(pos.get("symbol")) for pos in (book.get("positions") or [])], *[str(s).upper() for s in symbols if s]]))
    quotes = fetch_quotes(wanted, store=store, http_get=http_get) if wanted else {}
    book_ccy = str(book.get("currency") or "USD")
    prices, fx = book_prices(quotes, book_ccy, store=store, http_get=http_get) if quotes else ({}, {book_ccy: 1.0})
    return quotes, prices, fx


def run_symbol_research(
    store: TradingStore,
    symbol: str,
    *,
    http_get: HttpGetFn = None,
) -> dict[str, Any]:
    settings = store.load_settings()
    ai_on = settings.get("ai_assist") is not False
    quotes = fetch_quotes([symbol], store=store, http_get=http_get)
    snap = snapshot_symbol(symbol, store=store, http_get=http_get, quote=quotes.get(symbol.upper()), ai=ai_on)
    book = store.load_portfolio()
    result = run_specialists(snap, book)
    result["t"] = _now()
    result["held"] = any(str(pos.get("symbol") or "").upper() == symbol.upper() for pos in (book.get("positions") or []))
    result["screen"] = screen_candidate(snap, store.active_strategy())
    if ai_on:
        result["note"] = _ai_thesis(result, snap, settings)
    store.put_research(symbol.upper(), result)
    store.append_journal(
        {
            "kind": "research",
            "symbol": symbol.upper(),
            "text": f"{symbol.upper()} {result.get('action')} confidence {result.get('confidence')}",
        }
    )
    return result


def symbol_chart(
    store: TradingStore,
    symbol: str,
    *,
    http_get: HttpGetFn = None,
    range_code: str = "6mo",
) -> dict[str, Any]:
    """Bars + indicators for the desk chart. Read-only, cached like the loop."""
    from navin.trading.indicators import summarize_technicals

    symbol = symbol.upper()
    bars = fetch_ohlcv(symbol, store=store, http_get=http_get, range_code=range_code)
    tech = summarize_technicals(bars)
    from navin.trading.market import symbol_meta

    meta = symbol_meta(symbol, store)
    return {
        "symbol": symbol,
        "range": range_code,
        "currency": meta.get("currency") or "USD",
        "name": meta.get("name") or symbol,
        "bars": bars[-160:],
        "technical": tech,
    }


def run_symbol_backtest(
    store: TradingStore,
    symbol: str,
    *,
    http_get: HttpGetFn = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    opts = params or {}
    range_code = str(opts.get("range") or "1y")
    if range_code not in {"6mo", "1y", "2y", "5y"}:
        range_code = "1y"
    bars = fetch_ohlcv(symbol, store=store, http_get=http_get, range_code=range_code)
    settings = store.load_settings()
    bench_symbol = str(opts.get("benchmark") or settings.get("benchmark") or "QQQ").upper()
    try:
        bench_bars = fetch_ohlcv(bench_symbol, store=store, http_get=http_get, range_code=range_code) if bench_symbol != symbol.upper() else []
    except TradingError:
        bench_bars = []
    try:
        from navin.trading.market import fetch_fundamentals

        fundamentals = fetch_fundamentals(symbol, store=store, http_get=http_get)
    except TradingError:
        fundamentals = None
    return run_backtest(
        store,
        symbol=symbol,
        bars=bars,
        strategy=store.active_strategy(),
        fundamentals=fundamentals,
        bench_bars=bench_bars,
        benchmark=bench_symbol,
        fee_bps=opts.get("fee_bps"),
        slippage_bps=opts.get("slippage_bps"),
        range_code=range_code,
    )


# ---------------------------------------------------------------- intraday guard


def _guard_alerts(
    store: TradingStore,
    book: dict[str, Any],
    prices: dict[str, float],
    quotes: dict[str, dict[str, Any]],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Warnings the user wants before the hard engine acts, once per day each."""
    from navin.trading.risk import daily_pnl_pct, drawdown_pct, mark_equity

    alerts_cfg = settings.get("alerts") or {}
    limits = settings.get("risk") or {}
    out: list[dict[str, Any]] = []
    equity = mark_equity(book, prices)
    approach = as_float(alerts_cfg.get("stop_approach_pct"), 1.5)
    for pos in book.get("positions") or []:
        symbol = str(pos.get("symbol") or "").upper()
        px = prices.get(symbol)
        stop = pos.get("stop")
        if px and stop and float(stop) > 0:
            gap = (px - float(stop)) / px * 100.0
            if 0 <= gap <= approach:
                out.append({"key": f"stop-{symbol}", "level": "warning", "symbol": symbol, "text": f"{symbol} is {gap:.1f}% above its stop {float(stop):.2f}"})
    dd = drawdown_pct(book, equity)
    dd_warn = as_float(alerts_cfg.get("drawdown_warn_pct"), 6.0)
    if dd >= dd_warn:
        out.append({"key": "drawdown", "level": "warning", "text": f"book drawdown {dd:.1f}% (hard stop at {as_float(limits.get('max_drawdown_pct'), 10):.0f}%)"})
    day = daily_pnl_pct(book, equity)
    day_warn = as_float(alerts_cfg.get("daily_loss_warn_pct"), 1.2)
    if day <= -abs(day_warn):
        out.append({"key": "daily-loss", "level": "warning", "text": f"day P&L {day:.2f}% (hard stop at -{as_float(limits.get('max_daily_loss_pct'), 2):.1f}%)"})
    for level in alerts_cfg.get("price_levels") or []:
        if not isinstance(level, dict):
            continue
        symbol = str(level.get("symbol") or "").upper()
        target = as_float(level.get("price"), 0.0)
        px = prices.get(symbol)
        if not symbol or target <= 0 or px is None:
            continue
        above = str(level.get("when") or "above") == "above"
        if (above and px >= target) or (not above and px <= target):
            out.append({"key": f"level-{symbol}-{target:g}-{'up' if above else 'down'}", "level": "info", "symbol": symbol, "text": f"{symbol} {'crossed above' if above else 'fell below'} {target:g} (now {px:.2f})"})
    fresh: list[dict[str, Any]] = []
    stamp = time.strftime("%Y-%m-%d", time.gmtime())
    for row in out:
        if store.cache_get(f"alerted-{row['key']}-{stamp}", 86400) is not None:
            continue
        store.cache_set(f"alerted-{row['key']}-{stamp}", True)
        fresh.append(row)
    return fresh


def guard_tick(
    store: TradingStore | None = None,
    *,
    http_get: HttpGetFn = None,
    force: bool = False,
    clock: float | None = None,
) -> dict[str, Any]:
    """Light intraday pass: stops, venue fills and threshold alerts.

    The research cycle may run once a day; a 5% stop cannot wait for it. This
    pass only touches held names and open orders, runs every
    ``execution.guard_interval_s`` from the gateway supervisor and never
    opens a new line.
    """
    desk = store or TradingStore()
    now = clock if clock is not None else _now()
    state = desk.load_loop()
    settings = desk.load_settings()
    interval = as_float((settings.get("execution") or {}).get("guard_interval_s"), 900.0)
    last = as_float(state.get("last_guard"), 0.0)
    if not force and now - last < interval:
        return {"did_work": False, "reason": "not due", "next_guard": last + interval}
    if not force and not state.get("enabled"):
        return {"did_work": False, "reason": "loop paused"}
    book = desk.load_portfolio()
    open_orders = [row for row in desk.load_orders() if row.get("status") in {"submitted", "partial"}]
    level_symbols = [str(row.get("symbol") or "") for row in (settings.get("alerts") or {}).get("price_levels") or [] if isinstance(row, dict)]
    symbols = list(dict.fromkeys([*[str(row.get("symbol")) for row in open_orders], *level_symbols]))
    if not book.get("positions") and not open_orders and not level_symbols:
        state["last_guard"] = now
        state["last_guard_result"] = "nothing to guard"
        desk.save_loop(state)
        return {"did_work": False, "reason": "nothing to guard"}
    if cycle_is_live(desk):
        return {"did_work": False, "reason": "cycle running"}
    with trading_desk_lock(desk, wait_s=2.0) as held:
        if not held:
            return {"did_work": False, "reason": "busy"}
        try:
            quotes, prices, _fx = live_prices(desk, symbols, http_get=http_get)
        except TradingError as exc:
            state["last_guard"] = now
            state["last_guard_result"] = f"guard skipped: {exc.message}"
            desk.save_loop(state)
            return {"did_work": False, "reason": exc.message}
        reconciled = reconcile_orders(desk, prices)
        if prices:
            mark_to_market(desk, prices)
        stops = monitor_stops(desk, prices)
        alerts = _guard_alerts(desk, desk.load_portfolio(), prices, quotes, settings)
        summary = f"guard: {len(prices)} prices, {len(stops)} stops, {len(reconciled)} fills, {len(alerts)} alerts"
        state = desk.load_loop()
        state["last_guard"] = now
        state["last_guard_result"] = summary
        desk.save_loop(state)
        delivered = None
        if stops or reconciled or alerts:
            desk.append_journal({"kind": "watch", "text": summary})
            try:
                if stops or reconciled:
                    delivered = alert_cycle(desk, {"reason": "intraday guard", "orders": reconciled, "stops": stops})
                if alerts:
                    delivered = deliver_alert(
                        desk,
                        title="Trading Agent OS - guard",
                        detail="\n".join(row["text"] for row in alerts) + "\nOuvre #/trading pour ajuster stops ou mandat.",
                        level="warning" if any(row["level"] == "warning" for row in alerts) else "info",
                    )
            except Exception as exc:  # noqa: BLE001 - alerts never break the guard
                logger.warning("trading guard alert failed: {}", exc)
        return {
            "did_work": True,
            "reason": summary,
            "stops": stops,
            "fills": reconciled,
            "alerts": alerts,
            "delivered": delivered,
        }
