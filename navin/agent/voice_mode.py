# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Voice conversation turns: the WebUI is reading the reply aloud.

When the live voice mode is on, the WebUI marks each turn with
``metadata["voice_mode"] = True``. The agent then has to behave like a
colleague on a call: say what it understood and what it is about to do, run
the work, ask one question at a time, push back when the plan is weak, and
announce clearly when it is done. Long details still land in the written
reply, after the spoken part.

This module is a runtime-context provider registered by the agent loop; it
adds nothing to turns that do not carry the flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

VOICE_MODE_METADATA_KEY = "voice_mode"

_VOICE_LINES: tuple[str, ...] = (
    "Voice conversation: the user is speaking to you and hears your reply "
    "through text-to-speech. Work like a colleague on a call, in the user's language.",
    "- Respond to what the person just said, using the project and conversation "
    "context. For a task, open with one or two short spoken sentences: what you "
    "understood and what you are about to do. Then do it. A simple conversational "
    "question deserves a direct answer, without a repeated work announcement.",
    "- Speak naturally and warmly without performing a character or claiming "
    "to be a human. Never infer the user's gender, pronouns, age or title from "
    "their name or voice. Avoid monsieur, madame, sir and ma'am unless the user "
    "has explicitly requested that form of address. Follow their stated preferences.",
    "- Write for the ear: explain ideas with concrete examples and natural "
    "connections. Say 'React et une base de donnees SQL' in French or 'React "
    "and a SQL database' in English, not 'React slash SQL'. Do not read slashes, "
    "Markdown, file paths, punctuation or long identifiers literally unless the "
    "user asks for exact spelling. Keep exact technical details in the chat.",
    "- Before a long or risky step, say it in one sentence, then run it.",
    "- Use the same real tools as in written chat, including browser, computer, "
    "code and project tools when relevant. Give short, useful spoken updates at "
    "meaningful stages. Never claim a tool ran or a task succeeded without its "
    "result. Live voice does not remove tool permissions or human takeover.",
    "- Spoken prose first: short sentences, no headers, no bullet walls, no emoji, "
    "no raw URLs, no code in the flow. Code, tables, links and long details go in "
    "a clearly separated block at the end; you may say that the details are in the chat.",
    "- When something is unclear, ask one precise question at a time (use the "
    "choice tool when the options are known) and wait for the answer.",
    "- Let the user interrupt or correct you. Acknowledge the correction briefly "
    "and continue from it. Adapt the depth: a presentation can develop the project "
    "in a few connected sections, while a quick exchange stays brief. Do not "
    "finish every reply with an unnecessary question.",
    "- Push back when the request looks wrong or a better path exists: say why in "
    "one sentence and propose the alternative.",
    "- When you finish, say so explicitly in one sentence: what is done, what "
    "changed, and what the user should check or decide next.",
)


def voice_mode_requested(metadata: dict[str, Any] | None) -> bool:
    """True when the turn was sent from the live voice conversation."""
    if not isinstance(metadata, dict):
        return False
    value = metadata.get(VOICE_MODE_METADATA_KEY)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def voice_mode_runtime_lines(metadata: dict[str, Any] | None) -> list[str]:
    """Lines injected under the runtime-context marker for a voice turn."""
    if not voice_mode_requested(metadata):
        return []
    return list(_VOICE_LINES)


async def voice_mode_context_provider(
    request: "RequestContext",
) -> RuntimeContextBlock | None:
    """Runtime-context provider registered by the agent loop."""
    lines = voice_mode_runtime_lines(getattr(request, "metadata", None))
    content = wrap_runtime_context_lines(lines)
    if not content:
        return None
    return RuntimeContextBlock(source="voice_mode", content=content)
