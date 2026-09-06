"""JSON store for the Trading Agent OS under the instance data dir."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from navin.config.paths import get_runtime_subdir
from navin.trading.errors import TradingError
from navin.trading.mandate import default_mandate, normalize_mandate, resolve_mandate

SCHEMA = 1
DEFAULT_CASH_EUR = 20_000.0
DEFAULT_STRATEGY_ID = "nasdaq-growth-swing"


def _now() -> float:
    return time.time()


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    fd, tmp = tempfile.mkstemp(prefix=".navin-trading-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return raw if raw is not None else fallback


def default_risk() -> dict[str, Any]:
    return {
        "max_position_pct": 3.0,
        "max_sector_pct": 20.0,
        "max_daily_loss_pct": 2.0,
        "max_drawdown_pct": 10.0,
        "stop_loss_pct": 5.0,
        "take_profit_pct": None,
        "leverage": False,
        "max_daily_orders": 8,
        "min_cash_pct": 5.0,
        "approval_notional": 2000.0,
        "allowed_assets": [],
        "blocked_assets": [],
    }


BROKERS = ("paper", "alpaca", "binance")


def default_execution() -> dict[str, Any]:
    """Where fills come from. Paper is the default and needs no key.

    Paper costs start at zero so the book matches the tape exactly; set
    ``fee_bps`` / ``slippage_bps`` to model a venue. Backtests carry their
    own default costs.
    """
    return {
        "broker": "paper",
        "alpaca_paper": True,
        "binance_testnet": False,
        "fee_bps": 0.0,
        "slippage_bps": 0.0,
        "guard_interval_s": 900,
    }


def normalize_execution(raw: Any) -> dict[str, Any]:
    merged = default_execution()
    if isinstance(raw, dict):
        broker = str(raw.get("broker") or "paper").strip().lower()
        merged["broker"] = broker if broker in BROKERS else "paper"
        merged["alpaca_paper"] = bool(raw.get("alpaca_paper", True))
        merged["binance_testnet"] = bool(raw.get("binance_testnet", False))
        for key in ("fee_bps", "slippage_bps", "guard_interval_s"):
            try:
                merged[key] = max(0.0, float(raw.get(key, merged[key])))
            except (TypeError, ValueError):
                pass
    merged["guard_interval_s"] = int(merged["guard_interval_s"])
    return merged


def default_alerts() -> dict[str, Any]:
    """Guard thresholds: the intraday pass warns before the hard engine blocks."""
    return {
        "stop_approach_pct": 1.5,
        "drawdown_warn_pct": 6.0,
        "daily_loss_warn_pct": 1.2,
        "price_levels": [],
    }


def default_strategy() -> dict[str, Any]:
    return {
        "id": DEFAULT_STRATEGY_ID,
        "name": "Nasdaq growth swing",
        "skill": "momentum-trader",
        "brief": (
            "Scan Nasdaq-100 each cycle. Keep names with revenue growth above 15%, "
            "reasonable debt and an uptrend. Never more than 3% of equity per name. "
            "Stop 5%. Paper fills under the 2000 notional cap. Ask only above that."
        ),
        "universe": "NASDAQ100",
        "symbols": [],
        "horizon": "swing",
        "objective": "growth",
        "min_revenue_growth": 15.0,
        "max_debt_equity": 1.5,
        "require_uptrend": True,
        "min_confidence": 55.0,
        "require_consensus": "2/5",
        "debate_agents": 5,
        "fundamental": True,
        "technical": True,
        "sentiment": True,
        "macro": True,
        "execution_mode": "autonomous",
        "scan_interval_s": 900,
        "portfolio_interval_s": 3600,
        "research_interval_s": 86400,
        "price_move_pct": 0.8,
        "analyze_budget": 8,
        "active": True,
    }


def apply_paper_autonomy(row: dict[str, Any]) -> bool:
    """One-shot: paper fills under the notional cap. Research/recommend stay as-is."""
    mode = str(row.get("execution_mode") or "approval").strip().lower()
    if mode in {"research", "recommend"}:
        return False
    changed = False
    if mode != "autonomous":
        row["execution_mode"] = "autonomous"
        changed = True
    try:
        conf = float(row.get("min_confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 80:
        row["min_confidence"] = 55.0
        changed = True
    cons = str(row.get("require_consensus") or "")
    if cons in {"4/5", "5/5"}:
        row["require_consensus"] = "2/5"
        changed = True
    return changed


class TradingStore:
    """On-disk trading book. One instance per Navin data directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else get_runtime_subdir("trading")
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.root / name

    def load_settings(self) -> dict[str, Any]:
        data = _read_json(self.path("settings.json"), {})
        created = not isinstance(data, dict) or not data
        if created:
            data = {
                "schema": SCHEMA,
                "starting_cash": DEFAULT_CASH_EUR,
                "benchmark": "QQQ",
                "risk": default_risk(),
                "mandate": default_mandate(),
                "created_at": _now(),
            }
        data.setdefault("risk", default_risk())
        data.setdefault("starting_cash", DEFAULT_CASH_EUR)
        data.setdefault("benchmark", "QQQ")
        data["mandate"] = normalize_mandate(data.get("mandate") or default_mandate())
        data["currency"] = resolve_mandate(data["mandate"]).get("currency") or "USD"
        data.setdefault("execution_mode", "autonomous")
        data["execution"] = normalize_execution(data.get("execution"))
        data.setdefault("alerts", default_alerts())
        if not data.get("paper_autonomy_v1"):
            data = self._migrate_paper_autonomy(data)
        elif created:
            data["paper_autonomy_v1"] = True
            self.save_settings(data)
        return data

    def _migrate_paper_autonomy(self, settings: dict[str, Any]) -> dict[str, Any]:
        settings["paper_autonomy_v1"] = True
        settings["execution_mode"] = "autonomous"
        raw = _read_json(self.path("strategies.json"), None)
        rows = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
        dirty = False
        if not rows:
            rows = [default_strategy()]
            dirty = True
        else:
            for row in rows:
                if apply_paper_autonomy(row):
                    dirty = True
        if dirty:
            self.save_strategies(rows)
        self.save_settings(settings)
        return settings

    def save_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        data["schema"] = SCHEMA
        data["updated_at"] = _now()
        _atomic_write(self.path("settings.json"), data)
        return data

    def load_portfolio(self) -> dict[str, Any]:
        settings = self.load_settings()
        ccy = str(settings.get("currency") or "USD").upper()
        data = _read_json(self.path("portfolio.json"), {})
        if not isinstance(data, dict) or not data:
            cash = float(settings.get("starting_cash") or DEFAULT_CASH_EUR)
            data = {
                "cash": cash,
                "currency": ccy,
                "positions": [],
                "equity_curve": [{"t": _now(), "equity": cash}],
                "high_water": cash,
                "day_start_equity": cash,
                "day_stamp": time.strftime("%Y-%m-%d", time.gmtime()),
                "realized_pnl": 0.0,
            }
            self.save_portfolio(data)
            return data
        data["currency"] = ccy
        return data

    def save_portfolio(self, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        data["updated_at"] = _now()
        _atomic_write(self.path("portfolio.json"), data)
        return data

    def load_watchlist(self) -> list[str]:
        raw = _read_json(self.path("watchlist.json"), ["NVDA", "AAPL", "MSFT", "BTC-USD"])
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            symbol = str(item or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            out.append(symbol)
        return out

    def save_watchlist(self, symbols: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in symbols:
            symbol = str(item or "").strip().upper()
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            cleaned.append(symbol)
        _atomic_write(self.path("watchlist.json"), cleaned)
        return cleaned

    def load_strategies(self) -> list[dict[str, Any]]:
        settings = _read_json(self.path("settings.json"), {})
        if not isinstance(settings, dict) or not settings.get("paper_autonomy_v1"):
            self.load_settings()
        raw = _read_json(self.path("strategies.json"), None)
        if not isinstance(raw, list) or not raw:
            raw = [default_strategy()]
            self.save_strategies(raw)
        return [row for row in raw if isinstance(row, dict)]

    def save_strategies(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _atomic_write(self.path("strategies.json"), rows)
        return rows

    def active_strategy(self) -> dict[str, Any]:
        rows = self.load_strategies()
        for row in rows:
            if row.get("active"):
                return row
        return rows[0] if rows else default_strategy()

    def upsert_strategy(self, payload: dict[str, Any], *, make_active: bool = True) -> dict[str, Any]:
        rows = self.load_strategies()
        sid = str(payload.get("id") or f"strat-{uuid.uuid4().hex[:8]}")
        payload = {**payload, "id": sid}
        found = False
        next_rows: list[dict[str, Any]] = []
        for row in rows:
            if row.get("id") == sid:
                merged = {**row, **payload}
                if make_active:
                    merged["active"] = True
                next_rows.append(merged)
                payload = merged
                found = True
            else:
                if make_active:
                    row = {**row, "active": False}
                next_rows.append(row)
        if not found:
            if make_active:
                payload["active"] = True
            next_rows.append(payload)
        self.save_strategies(next_rows)
        return payload

    def load_orders(self) -> list[dict[str, Any]]:
        raw = _read_json(self.path("orders.json"), [])
        return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []

    def save_orders(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _atomic_write(self.path("orders.json"), rows)
        return rows

    def load_research(self) -> dict[str, Any]:
        raw = _read_json(self.path("research.json"), {})
        return raw if isinstance(raw, dict) else {}

    def save_research(self, data: dict[str, Any]) -> dict[str, Any]:
        _atomic_write(self.path("research.json"), data)
        return data

    def put_research(self, symbol: str, report: dict[str, Any]) -> dict[str, Any]:
        book = self.load_research()
        book[symbol.upper()] = report
        return self.save_research(book)

    def load_loop(self) -> dict[str, Any]:
        raw = _read_json(self.path("loop.json"), {})
        if not isinstance(raw, dict) or not raw:
            raw = {
                "enabled": False,
                "phase": "idle",
                "next_due": 0.0,
                "last_tick": 0.0,
                "last_fingerprint": "",
                "last_result": "loop is paused - start it from the Trading desk",
                "cycle": 0,
                "analyzed": [],
                "skipped_reason": "",
                "schedule": {
                    "kind": "daily",
                    "hour": 9,
                    "minute": 0,
                    "weekday": 1,
                    "day": 1,
                    "tz": None,
                    "expr": "0 9 * * *",
                },
            }
            self.save_loop(raw)
        return raw

    def save_loop(self, data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        data["updated_at"] = _now()
        _atomic_write(self.path("loop.json"), data)
        return data

    def loop_intent_path(self) -> Path:
        return self.path("loop.intent.json")

    def load_loop_intent(self) -> dict[str, Any]:
        """Start/stop/schedule written while a cycle holds the desk lock."""
        raw = _read_json(self.loop_intent_path(), {})
        return raw if isinstance(raw, dict) else {}

    def save_loop_intent(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = {key: value for key, value in dict(data).items() if key != "updated_at"}
        payload["updated_at"] = _now()
        _atomic_write(self.loop_intent_path(), payload)
        return payload

    def clear_loop_intent(self) -> None:
        try:
            self.loop_intent_path().unlink()
        except FileNotFoundError:
            return

    def load_backtests(self) -> list[dict[str, Any]]:
        raw = _read_json(self.path("backtests.json"), [])
        return [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []

    def save_backtests(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        _atomic_write(self.path("backtests.json"), rows[-40:])
        return rows[-40:]

    def append_journal(self, entry: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": f"j-{uuid.uuid4().hex[:10]}",
            "t": _now(),
            **entry,
        }
        path = self.path("journal.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def load_journal(self, limit: int = 80) -> list[dict[str, Any]]:
        path = self.path("journal.jsonl")
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        rows.reverse()
        return rows

    # Broker keys live next to the book, 0600, never in settings.json or a snapshot.
    def secrets_path(self) -> Path:
        return self.path("secrets.json")

    def load_secrets(self) -> dict[str, str]:
        raw = _read_json(self.secrets_path(), {})
        if not isinstance(raw, dict):
            return {}
        return {str(key): str(value) for key, value in raw.items() if str(value).strip()}

    def save_secret(self, name: str, value: str) -> None:
        key = str(name or "").strip()
        if not key:
            raise TradingError("secret name is required", status=400)
        secrets_map = self.load_secrets()
        text = str(value or "").strip()
        if text:
            secrets_map[key] = text
        else:
            secrets_map.pop(key, None)
        _atomic_write(self.secrets_path(), secrets_map)
        try:
            os.chmod(self.secrets_path(), 0o600)
        except OSError:
            pass

    def get_secret(self, name: str) -> str:
        return self.load_secrets().get(str(name or "").strip(), "")

    def has_secret(self, name: str) -> bool:
        return bool(self.get_secret(name))

    def cache_get(self, key: str, max_age_s: float) -> Any | None:
        path = self.root / "cache" / f"{key}.json"
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        stamped = float(raw.get("t") or 0)
        if stamped <= 0 or (_now() - stamped) > max_age_s:
            return None
        return raw.get("v")

    def cache_set(self, key: str, value: Any) -> None:
        _atomic_write(self.root / "cache" / f"{key}.json", {"t": _now(), "v": value})


def require_store(root: Path | None = None) -> TradingStore:
    try:
        return TradingStore(root)
    except OSError as exc:
        raise TradingError(f"trading store unavailable: {exc}", status=500) from exc
