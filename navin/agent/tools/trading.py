# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool over the same Trading Agent OS store as Studio #/trading."""

from __future__ import annotations

import json
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import StringSchema, tool_parameters_schema


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "Trading desk action. status/journal/research are reads. "
            "strategy compiles a natural-language mandate. "
            "start/stop/schedule/tick drive the desk hunt loop on a wall-clock "
            "calendar (not heartbeat). watch is the silent heartbeat. "
            "approve/reject pending orders. buy/sell place an order the user asked for "
            "(qty or notional in book currency); close sells a held name; stop moves its "
            "stop/take; chart returns bars; guard runs the intraday stop/alert pass. "
            "The risk engine can block a trade; you cannot override it.",
            enum=[
                "status",
                "snapshot",
                "journal",
                "research",
                "chart",
                "strategy",
                "mandate",
                "watch",
                "tick",
                "guard",
                "start",
                "stop",
                "schedule",
                "approve",
                "reject",
                "buy",
                "sell",
                "close",
                "stop_order",
                "backtest",
            ],
        ),
        brief=StringSchema("Natural-language mandate for action=strategy."),
        symbol=StringSchema("Ticker such as NVDA, BTC-USD, NFT-BAYC, or LI.PA."),
        id=StringSchema("Pending order id for approve/reject."),
        qty=StringSchema("Quantity for buy/sell/close. Leave empty with notional to size by amount."),
        notional=StringSchema("Amount in book currency for buy when qty is empty."),
        stop_price=StringSchema("New stop price for action=stop_order."),
        take_price=StringSchema("New take-profit price for action=stop_order."),
        thesis=StringSchema("Why the user wants this order (kept on the fill)."),
        domains=StringSchema("Comma domains: equities,crypto,nft,realestate. agent/all lets the agent pick."),
        countries=StringSchema("Comma ISO countries such as US,FR,AE. agent/all lets the agent pick."),
        risk_mode=StringSchema("user or agent. Agent applies conservative code limits."),
        target_mode=StringSchema("user or agent for the profit target."),
        target_return_pct=StringSchema("Target return percent when the user locks it, e.g. 12."),
        telegram=StringSchema("Chat id to alert on Telegram. Empty disables."),
        whatsapp=StringSchema("WhatsApp destination. Empty disables."),
        email=StringSchema("Email destination. Empty disables."),
        schedule=StringSchema(
            "JSON loop schedule for start/schedule: kind (daily, weekdays, "
            "weekend, weekly, monthly), hour, minute, weekday, day, tz."
        ),
        run_now=StringSchema("true to cycle immediately when starting the loop."),
        tz=StringSchema("IANA timezone for the Trading loop schedule."),
        force=StringSchema("true to cycle now on action=tick, even if the next slot is later."),
        required=["action"],
    )
)
class TradingTool(Tool):
    """Read and steer the paper Trading Agent OS. Never invent fills or prices."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "trading"

    @property
    def description(self) -> str:
        return (
            "Navin Trading Agent OS (Studio #/trading). Same paper book as the desk: "
            "equities, crypto, NFT floors, listed real-estate, countries, risk/target modes, "
            "Telegram/WhatsApp/email alerts. Use status first. Compile with action=strategy "
            "or persist structured picks with action=mandate. "
            "Autonomous cycle is start/stop/schedule/tick (desk loop: same store "
            "as Studio #/trading, Tauri, navin trading, and "
            "python -m navin.trading.desk_cli). "
            "watch is the silent heartbeat. Never invent fills or floors."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = str((arguments or {}).get("action") or "").strip().lower()
        return action in {"status", "snapshot", "journal", "research", "watch", "chart"}

    async def execute(self, **kwargs: Any) -> Any:
        from navin.agent.tools.context import is_heartbeat_turn
        from navin.trading.errors import TradingError
        from navin.trading.heartbeat import HEARTBEAT_TRADING_ACTIONS
        from navin.webui.trading_api import handle_trading_action

        action = str(kwargs.get("action") or "").strip().lower()
        if is_heartbeat_turn() and action not in HEARTBEAT_TRADING_ACTIONS:
            return ToolResult.error(
                "Refused on heartbeat. Trading silent checks may only run "
                "status/snapshot/journal/watch. "
                "Start, stop, schedule and tick stay on the desk or a user chat."
            )
        body = {
            "brief": kwargs.get("brief") or "",
            "symbol": kwargs.get("symbol") or "",
            "id": kwargs.get("id") or "",
            "run_now": str(kwargs.get("run_now") or "").strip().lower() in {"1", "true", "yes"},
            "tz": str(kwargs.get("tz") or "").strip() or None,
            "force": str(kwargs.get("force") or "true").strip().lower() in {"1", "true", "yes"}
            if action in {"tick", "guard"}
            else False,
        }
        for key in ("qty", "notional", "thesis"):
            if kwargs.get(key) not in (None, ""):
                body[key] = kwargs.get(key)
        if action == "stop_order":
            action = "set_stop"
            if kwargs.get("stop_price") not in (None, ""):
                body["stop"] = kwargs.get("stop_price")
            if kwargs.get("take_price") not in (None, ""):
                body["take"] = kwargs.get("take_price")
        raw_schedule = str(kwargs.get("schedule") or "").strip()
        if raw_schedule:
            try:
                parsed = json.loads(raw_schedule)
            except json.JSONDecodeError:
                return ToolResult.error("schedule must be JSON")
            if isinstance(parsed, dict):
                body["schedule"] = parsed
        mandate: dict[str, Any] = {}

        def _mode_list(raw: str) -> tuple[list[str], str]:
            text = raw.strip()
            if text.lower() in {"", "agent", "auto", "all", "*", "managed"}:
                return [], "agent"
            return [item.strip() for item in text.split(",") if item.strip()], "user"

        if "domains" in kwargs:
            items, mode = _mode_list(str(kwargs.get("domains") or ""))
            mandate["domains"] = items
            mandate["domains_mode"] = mode
        if "countries" in kwargs:
            items, mode = _mode_list(str(kwargs.get("countries") or ""))
            mandate["countries"] = items
            mandate["countries_mode"] = mode
        if kwargs.get("risk_mode"):
            mandate["risk_mode"] = str(kwargs.get("risk_mode") or "user")
        if kwargs.get("target_mode"):
            mandate["target_mode"] = str(kwargs.get("target_mode") or "user")
        if kwargs.get("target_return_pct") not in (None, ""):
            mandate["target_return_pct"] = kwargs.get("target_return_pct")
            mandate["target_mode"] = mandate.get("target_mode") or "user"
        channels: dict[str, Any] = {}
        for key in ("telegram", "whatsapp", "email"):
            dest = str(kwargs.get(key) or "").strip()
            if dest:
                channels[key] = True
                channels[f"{key}_to"] = dest
        if channels:
            mandate["channels"] = channels
        if mandate and action in {"mandate", "strategy", "settings"}:
            body["mandate"] = mandate
        if action == "status":
            action = "snapshot"
        try:
            payload = handle_trading_action(action, body)
        except TradingError as exc:
            return ToolResult.error(f"Error: {exc.message}")
        return payload
