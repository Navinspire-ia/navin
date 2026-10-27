# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""``/rename-chat [hint]``: the model names the current chat, once, without asking.

Slash-only. The model reads the recent exchange (the optional hint steers the
wording) and returns a 2 to 5 word sentence-case title; the engine stores it as
the session title (marked user-edited so auto-titles leave it alone), drops a
stale WebUI sidebar override for this chat and tells connected WebUIs to
refresh the row (``session_updated`` with scope ``title``). Works the same
from the CLI: the title lands in the session metadata the TUI lists.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from navin.bus.events import OutboundMessage
from navin.command.router import CommandContext

TITLE_MAX_CHARS = 200
_MIN_WORDS = 2
# Same cap as every other title source (navin.session.webui_turns.TITLE_MAX_WORDS).
_MAX_WORDS = 5
_BANNED_WORDS = frozenset({"chat", "conversation", "discussion", "session"})
_RECENT_MESSAGES = 12
_MESSAGE_CHARS = 600


def _recent_exchange(session: Any) -> str:
    """The last user / assistant lines, oldest first, as plain text."""
    from navin.session.webui_turns import title_source_from_user_text

    lines: list[str] = []
    for message in reversed(list(getattr(session, "messages", None) or [])):
        if not isinstance(message, dict) or message.get("_command") is True:
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = title_source_from_user_text(content) if role == "user" else content.strip()
        text = " ".join(text.split())
        if not text or text.startswith("/rename-chat"):
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {text[:_MESSAGE_CHARS]}")
        if len(lines) >= _RECENT_MESSAGES:
            break
    return "\n".join(reversed(lines))


def polish_title(raw: str) -> str:
    """Clean a model answer into a 2 to 5 word sentence-case title ('' if unusable)."""
    from navin.session.webui_turns import clean_generated_title, is_usable_generated_title

    text = clean_generated_title(raw.splitlines()[0] if raw and raw.strip() else "")
    text = text.rstrip("…").strip()
    if not text or not is_usable_generated_title(text):
        return ""
    words = [word for word in text.split() if word.strip("\"'.,:;!?").lower() not in _BANNED_WORDS]
    if len(words) < 2:
        return ""
    words = words[:_MAX_WORDS]
    title = " ".join(words).strip(" -,:;")
    if not title:
        return ""
    title = title[0].upper() + title[1:]
    return title[:TITLE_MAX_CHARS].rstrip()


async def generate_title(provider: Any, model: str, exchange: str, hint: str) -> str:
    prompt = (
        "Name this chat for a sidebar.\n"
        f"Rules:\n- {_MIN_WORDS} to {_MAX_WORDS} words, sentence case (only the first word capitalized, "
        "plus proper nouns and acronyms).\n"
        "- Same language as the user.\n"
        "- Never use the words Chat or Conversation.\n"
        "- No quotes, no ending punctuation, no emoji.\n"
        "- Return only the title.\n"
    )
    if hint:
        prompt += f"- The user wants the title to reflect: {hint[:300]}\n"
    prompt += f"\n{exchange or '(no messages yet)'}"
    response = await provider.chat_with_retry(
        [
            {"role": "system", "content": "You write short chat titles. Return only the title text."},
            {"role": "user", "content": prompt},
        ],
        tools=None,
        model=model,
        max_tokens=64,
        temperature=0.2,
        retry_mode="standard",
    )
    if getattr(response, "finish_reason", "") == "error":
        raise RuntimeError("the model did not answer")
    return polish_title(str(getattr(response, "content", "") or ""))


def _drop_sidebar_override(session_key: str) -> None:
    """A manual WebUI rename would hide the new title; forget it for this chat."""
    try:
        from navin.webui.sidebar_state import read_webui_sidebar_state, write_webui_sidebar_state

        state = read_webui_sidebar_state()
        overrides = dict(state.get("title_overrides") or {})
        if session_key not in overrides:
            return
        overrides.pop(session_key, None)
        state["title_overrides"] = overrides
        write_webui_sidebar_state(state)
    except Exception:  # noqa: BLE001 - the session title is already saved
        logger.debug("rename-chat: could not update the sidebar override", exc_info=True)


def _reply(ctx: CommandContext, content: str) -> OutboundMessage:
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_rename_chat(ctx: CommandContext) -> OutboundMessage:
    loop = ctx.loop
    sessions = getattr(loop, "sessions", None)
    if sessions is None:
        return _reply(ctx, "Could not rename this chat: no session is open.")
    session = ctx.session or sessions.get_or_create(ctx.key)
    runtime = ctx.runtime
    provider = getattr(runtime, "provider", None) or getattr(loop, "provider", None)
    model = str(getattr(runtime, "model", "") or getattr(loop, "model", "") or "")
    if provider is None:
        return _reply(ctx, "Could not rename this chat: no model is available right now.")
    hint = " ".join((ctx.args or "").split())
    try:
        title = await generate_title(provider, model, _recent_exchange(session), hint)
    except Exception as exc:  # noqa: BLE001 - reported plainly below
        logger.debug("rename-chat: title generation failed", exc_info=True)
        return _reply(ctx, f"Could not rename this chat: the model did not return a title ({type(exc).__name__}).")
    if not title:
        return _reply(ctx, "Could not rename this chat: the model did not return a usable title. Try /rename-chat with a hint.")
    try:
        saved = sessions.set_title(ctx.key, title)
    except Exception as exc:  # noqa: BLE001
        logger.debug("rename-chat: saving the title failed", exc_info=True)
        return _reply(ctx, f"Could not rename this chat: the title could not be saved ({type(exc).__name__}).")
    stored = sessions.get_or_create(ctx.key)
    if ctx.session is not None and ctx.session is not stored:
        # The turn saves its own copy later; keep it from writing the old title back.
        from navin.session.webui_turns import (
            WEBUI_TITLE_METADATA_KEY,
            WEBUI_TITLE_PROVISIONAL_METADATA_KEY,
            WEBUI_TITLE_USER_EDITED_METADATA_KEY,
        )

        ctx.session.metadata[WEBUI_TITLE_METADATA_KEY] = saved
        ctx.session.metadata[WEBUI_TITLE_USER_EDITED_METADATA_KEY] = True
        ctx.session.metadata.pop(WEBUI_TITLE_PROVISIONAL_METADATA_KEY, None)
    _drop_sidebar_override(ctx.key)
    bus = getattr(loop, "bus", None)
    if bus is not None and ctx.msg.channel == "websocket":
        from navin.bus.outbound_events import SessionUpdatedEvent, outbound_message_for_event

        try:
            await bus.publish_outbound(
                outbound_message_for_event(
                    channel=ctx.msg.channel,
                    chat_id=ctx.msg.chat_id,
                    event=SessionUpdatedEvent(scope="title"),
                    metadata=dict(ctx.msg.metadata or {}),
                )
            )
        except Exception:  # noqa: BLE001 - the list refreshes on the next update
            logger.debug("rename-chat: session_updated publish failed", exc_info=True)
    return _reply(ctx, f"Renamed this chat to: {saved}")
