"""``/compact [focus]``: summarize older turns now instead of waiting.

Automatic consolidation runs when the prompt nears the context window. This
command does the same on request: the older messages are archived, a handoff
note (steered by the optional focus text) replaces them, and the most recent
turns stay verbatim so the next message still has its immediate context.
"""

from __future__ import annotations

from typing import Any

# Messages kept verbatim after the note; the suffix is widened to a user turn.
KEEP_RECENT = 4


def _reply(ctx: Any, text: str) -> Any:
    from navin.bus.events import OutboundMessage

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id, content=text,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def _turn_running(loop: Any, key: str) -> bool:
    tasks = getattr(loop, "_active_tasks", {}).get(key, [])
    return any(not task.done() for task in tasks)


async def cmd_compact(ctx: Any) -> Any:
    loop = ctx.loop
    focus = (ctx.args or "").strip()
    if _turn_running(loop, ctx.key):
        return _reply(
            ctx,
            "A turn is still running. Wait for it to finish, or /stop it, "
            "then run /compact again.",
        )
    session = loop.sessions.get_or_create(ctx.key)
    before = len(session.messages)
    if before <= KEEP_RECENT:
        return _reply(ctx, "Nothing to compact yet: the conversation is still short.")
    runtime = getattr(ctx, "runtime", None) or loop.llm_runtime()
    summary = await loop.consolidator.compact_idle_session(
        ctx.key, runtime=runtime, max_suffix=KEEP_RECENT, focus=focus or None,
    )
    after = len(loop.sessions.get_or_create(ctx.key).messages)
    if after >= before:
        return _reply(
            ctx,
            "Nothing was compacted: the conversation changed meanwhile "
            "or has no older turn to summarize.",
        )
    lines = [f"Conversation compacted: {before - after} older messages summarized, {after} kept."]
    if focus:
        lines.append(f"Summary focus: {focus}")
    if summary is None:
        lines.append("The summary model failed; the older messages were archived as raw text.")
    return _reply(ctx, "\n".join(lines))


SUMMARY_PREFIX = "[Summary of {count} earlier messages, {side} checkpoint {name}]\n"


async def summarize_around_checkpoint(ctx: Any, store: Any, session: Any, rest: str) -> str:
    """``/checkpoint summarize <name> [upto|from] [focus]``.

    ``upto`` (default) replaces the messages before the checkpoint with a
    handoff note and keeps what came after; ``from`` keeps the conversation up
    to the checkpoint and summarizes what followed. Returns the reply text.
    """
    loop = ctx.loop
    name, _, tail = rest.partition(" ")
    if not name:
        return "Usage: `/checkpoint summarize <name> [upto|from] [focus]`"
    direction, _, focus = tail.strip().partition(" ")
    if direction.lower() not in ("upto", "from"):
        direction, focus = "upto", tail.strip()
    direction = direction.lower()
    focus = focus.strip()
    if _turn_running(loop, ctx.key):
        return "A turn is still running. Wait for it to finish, or /stop it, then summarize."
    marker = len(store.load(ctx.key, name).get("messages") or [])
    messages = list(session.messages)
    marker = min(marker, len(messages))
    chunk = messages[:marker] if direction == "upto" else messages[marker:]
    if len(chunk) < 2:
        return f"Nothing to summarize {'before' if direction == 'upto' else 'after'} `{name}`."
    runtime = getattr(ctx, "runtime", None) or loop.llm_runtime()
    brief = await loop.consolidator.handoff_brief(chunk, runtime=runtime, focus=focus or None)
    if not brief:
        return "The summary model failed; the conversation was left unchanged."
    side = "up to" if direction == "upto" else "after"
    note = {
        "role": "user",
        "content": SUMMARY_PREFIX.format(count=len(chunk), side=side, name=name) + brief,
    }
    if direction == "upto":
        # The summarized turns leave the session; the chat keeps showing them.
        loop.sessions.keep_earlier_messages(session, chunk)
        session.messages = [note, *messages[marker:]]
    else:
        session.messages = [*messages[:marker], note]
    session.last_consolidated = 0
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    lines = [
        f"Summarized {len(chunk)} messages {side} `{name}`; "
        f"the conversation now has {len(session.messages)} messages."
    ]
    if focus:
        lines.append(f"Summary focus: {focus}")
    return "\n".join(lines)
