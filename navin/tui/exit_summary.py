# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Exit receipt, collected in memory without slowing down streamed turns."""

from __future__ import annotations

import asyncio
import os
import shlex
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from weakref import WeakKeyDictionary

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from navin.agent.hook import AgentHook, AgentHookContext


@dataclass
class UsageTotals:
    input: int = 0
    output: int = 0
    cached: int = 0
    reasoning: int = 0
    has_cached: bool = False
    has_reasoning: bool = False
    estimated: bool = False

    def add(self, usage: dict) -> None:
        def count(key):
            try:
                return max(0, int(usage.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        self.input += count("prompt_tokens")
        self.output += count("completion_tokens")
        self.cached += count("cached_tokens") or count("cache_read_input_tokens")
        self.reasoning += count("reasoning_tokens")
        self.has_cached |= "cached_tokens" in usage or "cache_read_input_tokens" in usage
        self.has_reasoning |= "reasoning_tokens" in usage
        self.estimated |= bool(usage.get("estimated_tokens"))

    def line(self) -> str:
        # Providers normalize prompt tokens to include cached input. Reasoning
        # is likewise already included in output: never add either twice.
        cached = f"{self.cached:,} included" if self.has_cached else "not reported"
        reasoning = f"{self.reasoning:,}" if self.has_reasoning else "not reported"
        return (
            f"Token usage (this run): total={self.input + self.output:,} "
            f"input={self.input:,} (cached {cached}) "
            f"output={self.output:,} (reasoning {reasoning})"
            + (" [includes estimates]" if self.estimated else "")
        )


class CliUsageHook(AgentHook):
    accounts_for_usage = True

    def __init__(self) -> None:
        super().__init__()
        self.sessions: dict[str, UsageTotals] = defaultdict(UsageTotals)
        # Keep counters only, not contexts holding whole conversation histories.
        self._last: WeakKeyDictionary[asyncio.Task, tuple[int, dict]] = WeakKeyDictionary()

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._last[asyncio.current_task()] = (id(context), {})

    async def after_iteration(self, context: AgentHookContext) -> None:
        if context.session_key and context.usage:
            task = asyncio.current_task()
            previous_context, previous = self._last.get(task, (None, {}))
            if previous_context != id(context):
                previous = {}
            delta = {key: max(0, value - previous.get(key, 0))
                     for key, value in context.usage.items() if isinstance(value, int)}
            self.sessions[context.session_key].add(delta)
            self._last[task] = (id(context), dict(context.usage))

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        # Capture the model call even if the user quits during a long tool.
        await self.after_iteration(context)


def resume_command(workspace: Path, session_key: str, config_path: Path | None = None, executable: Path | None = None) -> str:
    args = [str(executable) if executable else "navin-cli", str(workspace), "--session", session_key]
    if config_path is not None:
        args.extend(["--config", str(config_path)])
    if os.name == "nt":
        # PowerShell is the desktop terminal default. Single quotes also keep
        # dollar signs and backticks in project names literal.
        return " ".join("'" + arg.replace("'", "''") + "'" if any(c in arg for c in " ';$`&()") else arg for arg in args)
    return shlex.join(args)


def print_exit_summary(
    *, session_key: str, workspace: Path, elapsed: float, usage: UsageTotals,
    title: str = "", config_path: Path | None = None, warnings: list[str] = (),
    executable: Path | None = None,
    console: Console | None = None,
) -> None:
    console = console or Console()
    seconds = max(0, int(elapsed))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    duration = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    console.print(Rule(Text(f"Worked for {duration}", style="bold"), align="left", style="dim"))
    console.print(Text(usage.line(), style="dim"))
    console.print("To continue this session, run:")
    console.print(Text("  " + resume_command(workspace, session_key, config_path, executable), style="bold cyan"), soft_wrap=True)
    choice = " ".join(title.split()) or session_key
    console.print(Text(f"Or open navin-cli and press Ctrl+S to select {choice}.", style="dim"))
    for warning in warnings:
        console.print(Text(warning, style="yellow"))
