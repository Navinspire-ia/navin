# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Compact, redacted learning events in the existing project memory journal."""

import re


def safe_detail(value: str) -> str:
    value = re.sub(r"(?i)(authorization|password|secret|token|api[_-]?key)\s*[:=]?\s*\S+", r"\1 [redacted]", value)
    value = re.sub(r"(?i)bearer\s+\S+|\b(?:sk-|ghp_|github_pat_)[\w-]+", "[redacted]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+|https?://\S+|(?:/home/|/Users/|[A-Z]:\\)\S+", "[private]", value)
    return re.sub(r"\s+", " ", value).strip()[:160]


def remember(workspace, event, *, module=None, name="", detail=""):
    from navin.cognition.episodes import append_episode, build_episode
    from navin.cognition.settings import cognition_enabled
    if not cognition_enabled(workspace, "episodes"):
        return
    text = f"Skill learning {event} in {module or 'shared'}: {name}. {safe_detail(detail)}"
    episode = build_episode(user_text=text, reply=text, tools_used=[],
                            stop_reason="completed", channel="internal", chat_id="skill-learning",
                            session_key="skill-learning", metadata={"product_module": module, "learning_event": event})
    if episode is not None:
        append_episode(workspace, episode)
