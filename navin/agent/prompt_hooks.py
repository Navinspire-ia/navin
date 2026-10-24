# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Prompt hooks: a hook whose decision comes from the model, not from a script.

A hooks.json entry ``{"type": "prompt", "prompt": "...", "timeout": 30}``
asks the configured model to judge the event. The prompt can name fields of
the event, filled in before the call:

- ``$TOOL_INPUT`` (the whole input as JSON) and ``$TOOL_INPUT.<field>``
- ``$TOOL_NAME``, ``$TOOL_RESULT``, ``$USER_PROMPT``, ``$FINAL_RESPONSE``
- ``$TRANSCRIPT`` (the last turns as text), ``$TRANSCRIPT_PATH`` (same, kept
  for hooks written for other agents), ``$CWD``, ``$EVENT``
- ``$ARGUMENTS`` (the whole event payload as JSON)

The model answers ``{"decision": "approve" | "block" | "deny" | "ask",
"reason": "...", "systemMessage": "..."}``. The answer is turned into the
same (exit code, stdout, stderr) triple a command hook produces, so the rest
of the hook runtime treats both kinds alike.

A model that cannot be reached, times out or answers something unreadable
never blocks the agent: the hook approves and leaves a notice for the user.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections import OrderedDict
from typing import Any

from loguru import logger

# Events whose decision a prompt hook may take.
PROMPT_EVENTS = frozenset({"PreToolUse", "UserPromptSubmit", "Stop", "SubagentStop", "PermissionRequest"})

_MAX_FIELD = 4000
_MAX_PROMPT = 24000
_CACHE_TTL_S = 60.0
_CACHE_MAX = 128
_CACHE: OrderedDict[str, tuple[float, tuple[int, str, str]]] = OrderedDict()

_FIELD_RE = re.compile(r"\$(TOOL_INPUT)\.([A-Za-z_][A-Za-z0-9_]*)")
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_INSTRUCTIONS = """You are a hook of a coding agent. Judge the event below against the hook's rule.
Answer with one JSON object and nothing else:
{"decision": "approve" | "block" | "ask", "reason": "<one or two sentences>", "systemMessage": "<optional note for the user>"}
- approve: the event is fine, the agent goes on.
- block: the rule is broken; the reason goes back to the agent so it can change course.
- ask: the user must confirm first; the reason says what to confirm.
Judge only what the rule asks. When the rule does not apply to this event, approve."""


def _clip(text: str, limit: int = _MAX_FIELD) -> str:
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= limit else text[:limit] + f"... ({len(text) - limit} more chars)"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _transcript_text(payload: dict[str, Any]) -> str:
    rows = payload.get("transcript")
    if not isinstance(rows, list):
        return ""
    lines: list[str] = []
    for row in rows[-12:]:
        if isinstance(row, dict):
            role = str(row.get("role") or "?")
            lines.append(f"{role}: {_clip(_as_text(row.get('content')), 1500)}")
        else:
            lines.append(_clip(_as_text(row), 1500))
    return "\n".join(lines)


def render(template: str, event: str, payload: dict[str, Any]) -> str:
    """Fill the ``$NAME`` fields of a prompt hook from the event payload."""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}

    def field(match: re.Match[str]) -> str:
        return _clip(_as_text(tool_input.get(match.group(2), "")))

    text = _FIELD_RE.sub(field, template)
    transcript = _transcript_text(payload)
    values = {
        "$TOOL_INPUT": _clip(_as_text(tool_input)),
        "$TOOL_NAME": _as_text(payload.get("tool_name")),
        "$TOOL_RESULT": _clip(_as_text(payload.get("tool_response", payload.get("tool_result")))),
        "$USER_PROMPT": _clip(_as_text(payload.get("prompt"))),
        "$FINAL_RESPONSE": _clip(_as_text(payload.get("final_response", payload.get("last_assistant_message")))),
        "$TRANSCRIPT_PATH": transcript,
        "$TRANSCRIPT": transcript,
        "$CWD": _as_text(payload.get("cwd")),
        "$EVENT": event,
        "$ARGUMENTS": _clip(_as_text({k: v for k, v in payload.items() if k != "transcript"}), 8000),
    }
    # Longest names first so $TOOL_INPUT never eats $TOOL_INPUT_X style names.
    for name in sorted(values, key=len, reverse=True):
        text = text.replace(name, values[name])
    return text


def build_prompt(template: str, event: str, payload: dict[str, Any]) -> str:
    rule = render(template, event, payload)
    facts = {
        "event": event,
        "tool_name": payload.get("tool_name"),
        "tool_input": payload.get("tool_input"),
        "prompt": payload.get("prompt"),
        "final_response": payload.get("final_response", payload.get("last_assistant_message")),
    }
    facts = {k: v for k, v in facts.items() if v not in (None, "", {})}
    body = f"{_INSTRUCTIONS}\n\n## Hook rule\n{rule}\n\n## Event\n{_clip(_as_text(facts), 8000)}"
    return body[:_MAX_PROMPT]


def parse_answer(answer: str | None) -> dict[str, str] | None:
    """The model's decision, or None when the answer cannot be read."""
    if not answer:
        return None
    text = answer.strip()
    candidates = [text]
    found = _JSON_RE.search(text)
    if found:
        candidates.append(found.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            decision = str(data.get("decision") or "").strip().lower()
            if not decision and isinstance(data.get("ok"), bool):
                decision = "approve" if data["ok"] else "block"
            return {
                "decision": decision,
                "reason": str(data.get("reason") or "").strip(),
                "systemMessage": str(data.get("systemMessage") or "").strip(),
            }
    # A bare word answer ("approve", "deny") is accepted too.
    word = re.match(r"^\W*(approve|allow|block|deny|ask)\b", text, re.IGNORECASE)
    if word:
        return {"decision": word.group(1).lower(), "reason": text[word.end():].strip(" :.-\n"), "systemMessage": ""}
    return None


def to_hook_result(event: str, verdict: dict[str, str]) -> tuple[int, str, str]:
    """Translate a verdict into the (code, stdout, stderr) of a command hook."""
    decision = verdict.get("decision", "")
    reason = verdict.get("reason") or "Blocked by a prompt hook."
    out: dict[str, Any] = {}
    if verdict.get("systemMessage"):
        out["systemMessage"] = verdict["systemMessage"]
    if decision in ("block", "deny"):
        if event in ("PreToolUse", "PermissionRequest"):
            out["permissionDecision"] = "deny"
            out["permissionDecisionReason"] = reason
        out["decision"] = "block"
        out["reason"] = reason
    elif decision == "ask":
        ask = f"Confirm with the user before going on (use ask_user): {reason}"
        if event in ("PreToolUse", "PermissionRequest"):
            out["permissionDecision"] = "deny"
            out["permissionDecisionReason"] = ask
        out["decision"] = "block"
        out["reason"] = ask
    return 0, json.dumps(out) if out else "", ""


async def call_model(prompt: str, model: str | None, timeout: float) -> str | None:
    """One judging call on the configured provider; None on any failure."""
    try:
        from navin.config.loader import load_config_cached
        from navin.providers.factory import make_provider

        provider = make_provider(load_config_cached())
        response = await asyncio.wait_for(
            provider.chat(
                [{"role": "user", "content": prompt}],
                model=model or os.environ.get("NAVIN_PROMPT_HOOK_MODEL") or None,
                max_tokens=600,
                temperature=0.0,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning("Prompt hook timed out after {} s", timeout)
        return None
    except Exception as exc:  # noqa: BLE001 - a hook never breaks a turn
        logger.warning("Prompt hook call failed: {}", exc)
        return None
    if getattr(response, "finish_reason", "") == "error":
        return None
    return getattr(response, "content", None)


def _cache_key(template: str, event: str, payload: dict[str, Any], model: str | None) -> str:
    stable = {k: v for k, v in payload.items() if k not in ("transcript", "session_id")}
    raw = json.dumps([template, event, stable, model], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def evaluate(
    template: str, event: str, payload: dict[str, Any], *, timeout: float, model: str | None = None,
) -> tuple[int, str, str]:
    """Run one prompt hook; same return shape as a command hook."""
    if event not in PROMPT_EVENTS:
        return 0, "", ""
    key = _cache_key(template, event, payload, model)
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    answer = await call_model(build_prompt(template, event, payload), model, timeout)
    verdict = parse_answer(answer)
    if verdict is None or verdict["decision"] not in ("approve", "allow", "block", "deny", "ask"):
        note = "A prompt hook could not get a readable answer from the model; the action was allowed."
        return 0, json.dumps({"systemMessage": note}), ""
    result = to_hook_result(event, verdict)
    _CACHE[key] = (now, result)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result


def clear_cache() -> None:
    _CACHE.clear()
