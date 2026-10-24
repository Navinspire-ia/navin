# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``/loop``: run a prompt now, then again on a schedule.

A thin layer over the cron service the ``cron`` tool already drives:

- **fixed** (``/loop 5m check deploy``, ``check deploy every 5m``,
  ``every weekday at 9am standup``): the prompt runs once now, then a
  recurring session-bound cron task named ``loop-<slug>`` runs it on every
  tick and reports to the origin chat. Re-running ``/loop`` with the same
  prompt replaces the existing loop instead of stacking a second one.
- **dynamic** (``/loop watch the deploy``, no interval): the prompt runs now
  and the agent arms a one-shot ``at`` job for the next tick itself, picking
  the delay from what it saw (short while something changes, long when idle)
  or stopping when the work is done.

``/loop list`` and ``/loop stop <name|all>`` manage the loops of this chat.
Silent background checks that only notify on change belong in
``.navin/HEARTBEAT.md`` instead (see the cron skill).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from navin.bus.events import OutboundMessage
from navin.command.router import CommandContext

LOOP_NAME_PREFIX = "loop-"
MIN_INTERVAL_SECONDS = 30

LOOP_USAGE = (
    "Usage: /loop [interval] <prompt>\n\n"
    "Runs the prompt now, then again on a schedule, reporting here each time.\n"
    "  /loop 5m check the deploy          fixed interval (s, m, h, d)\n"
    "  /loop run tests every 10 minutes   trailing interval (also: toutes les 10 minutes)\n"
    "  /loop every weekday at 9am standup cron-style schedule\n"
    "  /loop watch the CI run             no interval: the agent picks each next tick\n"
    "  /loop list                         loops of this chat\n"
    "  /loop stop <name|all>              stop a loop\n\n"
    "For silent checks that only notify on change, use .navin/HEARTBEAT.md instead."
)

_UNIT_SECONDS: dict[str, int] = {}
for _words, _secs in (
    (("s", "sec", "secs", "second", "seconds", "seconde", "secondes"), 1),
    (("m", "min", "mins", "minute", "minutes"), 60),
    (("h", "hr", "hrs", "hour", "hours", "heure", "heures"), 3600),
    (("d", "day", "days", "jour", "jours"), 86400),
):
    for _word in _words:
        _UNIT_SECONDS[_word] = _secs

_UNIT_ALT = "|".join(sorted(map(re.escape, _UNIT_SECONDS), key=len, reverse=True))
_EVERY = r"(?:every|each|toutes\s+les|tous\s+les|chaque)"

_LEADING_INTERVAL = re.compile(
    rf"^(?:{_EVERY}\s+(?:(?P<n>\d+)\s*)?|(?P<n2>\d+)\s*)(?P<unit>{_UNIT_ALT})\b[\s,:]+(?P<prompt>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_TRAILING_INTERVAL = re.compile(
    rf"^(?P<prompt>.+?)[\s,]+{_EVERY}\s+(?:(?P<n>\d+)\s*)?(?P<unit>{_UNIT_ALT})\s*[.!]?$",
    re.IGNORECASE | re.DOTALL,
)

# Cron day-of-week field per day phrase (cron: Sunday = 0).
_DAY_FIELDS: dict[str, str] = {
    "day": "*", "days": "*", "jour": "*", "jours": "*",
    "weekday": "1-5", "weekdays": "1-5",
    "business day": "1-5", "business days": "1-5",
    "jour ouvré": "1-5", "jours ouvrés": "1-5",
    "jour ouvre": "1-5", "jours ouvres": "1-5",
    "weekend": "0,6", "weekends": "0,6", "week-end": "0,6", "week-ends": "0,6",
}
for _index, (_en, _fr) in enumerate((
    ("sunday", "dimanche"),
    ("monday", "lundi"),
    ("tuesday", "mardi"),
    ("wednesday", "mercredi"),
    ("thursday", "jeudi"),
    ("friday", "vendredi"),
    ("saturday", "samedi"),
)):
    for _name in (_en, f"{_en}s", _fr, f"{_fr}s"):
        _DAY_FIELDS[_name] = str(_index)
_BARE_DAY_FIELDS = {
    "daily": "*",
    "quotidiennement": "*",
    "weekdays": "1-5",
    "en semaine": "1-5",
}

_DAY_ALT = "|".join(
    sorted((re.escape(k).replace(r"\ ", r"\s+") for k in _DAY_FIELDS), key=len, reverse=True)
)
_BARE_ALT = "|".join(
    sorted((re.escape(k).replace(r"\ ", r"\s+") for k in _BARE_DAY_FIELDS), key=len, reverse=True)
)
_TIME = (
    r"(?P<hour>\d{1,2})(?:(?::|h)(?P<minute>\d{2})?)?\s*"
    r"(?P<ampm>am|pm|a\.m\.|p\.m\.)?"
)
_SCHEDULE = (
    rf"(?:(?:every|each|on|tous\s+les|toutes\s+les|chaque|le|les)\s+(?P<day>{_DAY_ALT})"
    rf"|(?P<bare>{_BARE_ALT}))\s+(?:at|à|a|vers)\s+{_TIME}"
)
_TRAILING_SCHEDULE = re.compile(
    rf"^(?P<prompt>.+?)[\s,]+{_SCHEDULE}\s*[.!]?$", re.IGNORECASE | re.DOTALL
)
_LEADING_SCHEDULE = re.compile(
    rf"^{_SCHEDULE}(?![\w:])[\s,:]+(?P<prompt>.+)$", re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True)
class LoopSpec:
    """A parsed ``/loop`` request."""

    prompt: str
    every_seconds: int | None = None
    cron_expr: str | None = None

    @property
    def mode(self) -> str:
        return "fixed" if (self.every_seconds or self.cron_expr) else "dynamic"

    @property
    def label(self) -> str:
        if self.cron_expr:
            return f"on cron `{self.cron_expr}`"
        if self.every_seconds:
            return f"every {format_interval(self.every_seconds)}"
        return "at a pace the agent picks each tick"


def format_interval(seconds: int) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _interval_seconds(number: str | None, unit: str) -> int:
    count = int(number) if number else 1
    return max(MIN_INTERVAL_SECONDS, count * _UNIT_SECONDS[unit.lower()])


def _day_field(match: re.Match[str]) -> str | None:
    day = match.group("day")
    table = _DAY_FIELDS
    if day is None:
        day = match.group("bare")
        table = _BARE_DAY_FIELDS
    key = re.sub(r"\s+", " ", (day or "").lower())
    return table.get(key)


def _cron_from_match(match: re.Match[str]) -> str | None:
    dow = _day_field(match)
    if dow is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    ampm = (match.group("ampm") or "").lower().replace(".", "")
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return f"{minute} {hour} * * {dow}"


def parse_loop_args(text: str) -> LoopSpec | None:
    """Parse ``[interval] <prompt>``; None when there is no prompt at all."""
    raw = (text or "").strip()
    if not raw:
        return None

    for pattern in (_TRAILING_SCHEDULE, _LEADING_SCHEDULE):
        match = pattern.match(raw)
        if match:
            expr = _cron_from_match(match)
            prompt = match.group("prompt").strip()
            if expr and prompt:
                return LoopSpec(prompt=prompt, cron_expr=expr)

    match = _LEADING_INTERVAL.match(raw)
    if match and match.group("prompt").strip():
        return LoopSpec(
            prompt=match.group("prompt").strip(),
            every_seconds=_interval_seconds(match.group("n") or match.group("n2"), match.group("unit")),
        )

    match = _TRAILING_INTERVAL.match(raw)
    if match and match.group("prompt").strip():
        return LoopSpec(
            prompt=match.group("prompt").strip(),
            every_seconds=_interval_seconds(match.group("n"), match.group("unit")),
        )

    return LoopSpec(prompt=raw)


_SLUG_STOPWORDS = frozenset({
    "the", "a", "an", "and", "of", "to", "for", "on", "in",
    "le", "la", "les", "de", "du", "des", "et", "un", "une", "pour",
})


def loop_name(prompt: str) -> str:
    """Stable ``loop-<slug>`` name: the same prompt always maps to one loop."""
    text = unicodedata.normalize("NFKD", prompt.lower()).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text)
    kept = [w for w in words if w not in _SLUG_STOPWORDS] or words
    slug = "-".join(kept[:4])[:40].strip("-")
    return f"{LOOP_NAME_PREFIX}{slug or 'task'}"


def fixed_tick_message(name: str, spec: LoopSpec) -> str:
    """Cron payload for a fixed loop: executed by the agent on every tick."""
    return (
        f"Loop tick ({name}, {spec.label}). Run this task now and report the result:\n\n"
        f"{spec.prompt}\n\n"
        "Rules for this loop:\n"
        "- Navin already schedules this loop: do not create, change or remove cron jobs for it.\n"
        "- If the task is a slash command (e.g. /board status), do what that command does.\n"
        "- Keep the report short and say plainly when nothing changed.\n"
        f"- If the task is finished for good, say so and suggest `/loop stop {name}`."
    )


def dynamic_tick_message(name: str, prompt: str) -> str:
    """Self-contained payload for a dynamic loop tick.

    The agent re-arms the next tick by passing this exact text back as the
    cron message, so every tick carries the same instructions.
    """
    return (
        f"Dynamic loop {name}. Task: {prompt}\n\n"
        "Each tick: do the task now and report the result briefly. Then decide the next tick yourself:\n"
        f'- Re-arm with cron(action="add", name="{name}", at="<ISO datetime = now + delay>", '
        "message=<this whole message, copied verbatim>).\n"
        "- Pick the delay from what you just saw: 1-5 minutes while something is actively "
        "changing (deploy rolling out, build or CI running, a value moving); 15-60 minutes when "
        "it is stable or idle; never under 1 minute, never over 24 hours.\n"
        "- Stop instead (do not re-arm) when the task is done, the watched thing reached a final "
        "state, or several ticks in a row found nothing useful. Tell the user the loop ended.\n"
        f"- Keep at most one pending job named {name}. The user can stop it with `/loop stop {name}`.\n"
        '- End the report with one line: "Next tick in <delay>" or "Loop stopped".'
    )


def loop_args_start_turn(args: str) -> bool:
    """True when ``/loop <args>`` runs an agent turn (a prompt), not list/stop/usage."""
    words = (args or "").split()
    if not words:
        return False
    head = words[0].lower()
    if head in {"list", "ls"} and len(words) == 1:
        return False
    return head not in {"stop", "cancel", "rm"}


def _session_route(ctx: CommandContext) -> tuple[str, str, str]:
    from navin.session.keys import UNIFIED_SESSION_KEY

    channel = ctx.msg.channel or ""
    chat_id = ctx.msg.chat_id or ""
    raw_key = f"{channel}:{chat_id}" if channel and chat_id else ""
    session_key = raw_key if ctx.key == UNIFIED_SESSION_KEY else (ctx.key or raw_key)
    return session_key, channel, chat_id


def _reply(ctx: CommandContext, content: str) -> OutboundMessage:
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _cron_service(ctx: CommandContext) -> Any | None:
    return getattr(ctx.loop, "cron_service", None) if ctx.loop is not None else None


def session_loop_jobs(cron: Any, session_key: str) -> list[Any]:
    """Loop jobs (``loop-*``) owned by this chat session."""
    return [
        job
        for job in cron.list_jobs(include_disabled=True)
        if str(getattr(job, "name", "")).startswith(LOOP_NAME_PREFIX)
        and getattr(job.payload, "session_key", None) == session_key
    ]


def _describe_job(job: Any) -> str:
    schedule = job.schedule
    if schedule.kind == "every" and schedule.every_ms:
        timing = f"every {format_interval(schedule.every_ms // 1000)}"
    elif schedule.kind == "cron":
        timing = f"cron {schedule.expr}" + (f" ({schedule.tz})" if schedule.tz else "")
    else:
        timing = "dynamic, next tick armed"
    state = "" if job.enabled else ", paused"
    return f"- {job.name} ({timing}{state})"


def _loop_list(ctx: CommandContext, cron: Any, session_key: str) -> OutboundMessage:
    jobs = session_loop_jobs(cron, session_key)
    if not jobs:
        return _reply(ctx, "No loops in this chat. Start one with /loop [interval] <prompt>.")
    lines = [f"Loops in this chat ({len(jobs)}):", *(_describe_job(j) for j in jobs)]
    lines.append("\nStop one with /loop stop <name>, or all with /loop stop all.")
    return _reply(ctx, "\n".join(lines))


def _loop_stop(ctx: CommandContext, cron: Any, session_key: str, target: str) -> OutboundMessage:
    target = target.strip()
    if not target:
        return _reply(ctx, "Usage: /loop stop <name|all>")
    jobs = session_loop_jobs(cron, session_key)
    if target.lower() != "all":
        wanted = target.lower()
        if not wanted.startswith(LOOP_NAME_PREFIX):
            wanted = f"{LOOP_NAME_PREFIX}{wanted}"
        jobs = [j for j in jobs if j.name.lower() == wanted]
        if not jobs:
            return _reply(ctx, f"No loop named {wanted} in this chat. See /loop list.")
    if not jobs:
        return _reply(ctx, "No loops to stop in this chat.")
    stopped = sorted({j.name for j in jobs if cron.remove_job(j.id) == "removed"})
    if not stopped:
        return _reply(ctx, "Nothing was stopped.")
    return _reply(ctx, "Stopped: " + ", ".join(stopped))


def _remove_same_name(cron: Any, session_key: str, name: str) -> bool:
    replaced = False
    for job in session_loop_jobs(cron, session_key):
        if job.name == name and cron.remove_job(job.id) == "removed":
            replaced = True
    return replaced


async def cmd_loop(ctx: CommandContext) -> OutboundMessage | None:
    """``/loop [interval] <prompt>``, ``/loop list``, ``/loop stop <name|all>``."""
    args = ctx.args.strip()
    if not args:
        return _reply(ctx, LOOP_USAGE)

    head, _, rest = args.partition(" ")
    is_side_channel = not loop_args_start_turn(args)
    is_stop = is_side_channel and head.lower() in {"stop", "cancel", "rm"}
    is_list = is_side_channel and not is_stop
    cron = _cron_service(ctx)
    if cron is None:
        return _reply(ctx, "Loops need the scheduler, which is not running in this session.")
    session_key, channel, chat_id = _session_route(ctx)
    if not session_key or not channel or not chat_id:
        return _reply(ctx, "Loops must be started from a chat session.")
    if is_list:
        return _loop_list(ctx, cron, session_key)
    if is_stop:
        return _loop_stop(ctx, cron, session_key, rest)

    spec = parse_loop_args(args)
    if spec is None:
        return _reply(ctx, LOOP_USAGE)
    if ctx.session is None:
        return _reply(
            ctx,
            "A task is already running for this chat. Use /stop first, then send /loop again.",
        )

    from navin.cron.types import CronSchedule

    name = loop_name(spec.prompt)
    origin_metadata = dict(ctx.msg.metadata or {})
    replaced = _remove_same_name(cron, session_key, name)

    if spec.mode == "fixed":
        if spec.cron_expr:
            tz = getattr(getattr(ctx.loop, "context", None), "timezone", None) or "UTC"
            schedule = CronSchedule(kind="cron", expr=spec.cron_expr, tz=tz)
        else:
            schedule = CronSchedule(kind="every", every_ms=int(spec.every_seconds or 0) * 1000)
        cron.add_job(
            name=name,
            schedule=schedule,
            message=fixed_tick_message(name, spec),
            session_key=session_key,
            origin_channel=channel,
            origin_chat_id=chat_id,
            origin_metadata=origin_metadata,
        )
        verb = "updated" if replaced else "scheduled"
        content = (
            f"[Loop] (/loop) Recurring loop `{name}` is {verb} {spec.label} and reports "
            "here on every tick. Run the first tick now.\n\n"
            f"Task: {spec.prompt}\n\n"
            "Do the task and report the result. Do not schedule anything: the loop is "
            f"already armed (the user stops it with `/loop stop {name}`)."
        )
    else:
        content = (
            f"[Loop] (/loop) Start dynamic loop `{name}`. This is the first tick.\n\n"
            f"{dynamic_tick_message(name, spec.prompt)}"
        )

    from navin.command.modules import PRELOAD_SKILLS_METADATA_KEY

    ctx.msg.metadata = {
        **origin_metadata,
        "original_command": "/loop",
        "original_content": ctx.raw,
        # Dynamic ticks re-arm themselves through the cron tool; fixed ones
        # must not touch it, so only the dynamic brief preloads the skill.
        PRELOAD_SKILLS_METADATA_KEY: ["cron"] if spec.mode == "dynamic" else [],
        "loop_name": name,
        "loop_mode": spec.mode,
    }
    ctx.msg.content = content
    return None
