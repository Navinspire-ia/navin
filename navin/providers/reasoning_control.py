# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Saying "do not think" out loud, on every wire navin speaks.

The failure this module prevents was measured, not imagined. On 2026-09-02 an
agent step routed to ``reasoning_effort="none"`` reached the provider with no
reasoning parameter at all, because "none" was expressed by omitting it. The
endpoint applied its default, the model spent 726 reasoning tokens before its
first tool call, and every step of every turn paid that silence. The same
omission existed on every other wire: GPT-5 defaults to effort "medium",
Gemini 2.5/3 to dynamic thinking, Ollama switches thinking on for any capable
model, Groq's Qwen3 reasons unless told "none", and every OpenAI-compatible
gateway forwards whatever default sits behind it.

Three facts about a model decide what to send:

* what the endpoint does when nobody says anything (thinks / does not /
  always, whatever the request says);
* which words this endpoint understands for "off" (OpenAI's
  ``reasoning_effort: "none"``, OpenRouter's ``reasoning.enabled: false``,
  Anthropic's ``thinking.type: "disabled"``, vendor toggles);
* which floor it accepts when it refuses to be silent (``minimal``, ``low``).

The tables below are the known cases. They are allowed to be wrong about a
model nobody has met yet, because the wire corrects them: a refused value
steps down to the next floor, an unknown parameter ends the attempts, and the
``reasoning_tokens`` the provider reports let the agent loop flag any step
that was routed to "none" and reasoned anyway.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from loguru import logger

# What a family does when the request says nothing about reasoning.
DEFAULT_ON = "on"        # thinks unless told not to (GPT-5, Gemini 2.5+, GLM 4.5+, Qwen3)
DEFAULT_OFF = "off"      # does not think unless asked (GPT-4o, Llama, Mistral Large)
ALWAYS = "always"        # thinks whatever we say, and rejects the knob (Grok 4, Magistral)
# Thinks whatever we say, but the knob sets how deep: Grok 4.3+ (low..xhigh,
# defaults to high, cannot be disabled), Perplexity Sonar Deep Research. The
# knob must reach the wire here: the Settings ladder offers these levels, and
# dropping the parameter leaves the model at its costliest default.
ALWAYS_TUNABLE = "always_tunable"
UNKNOWN = "unknown"      # never met: treated as DEFAULT_ON, the wire will tell

# Which request format the shapes target.
WIRE_CHAT = "chat"            # /chat/completions kwargs (extra_body nested)
WIRE_RESPONSES = "responses"  # OpenAI Responses API body

# How an endpoint refused the reasoning request.
REJECT_VALUE = "value"              # the parameter exists, this value does not
REJECT_UNSUPPORTED = "unsupported"  # the parameter itself is unknown here

_ALWAYS_MARKERS = (
    "grok-4",
    "grok-code",
    "magistral",
    "deepseek-r1",
    "deepseek-reasoner",
    "kimi-k2-thinking",
    "kimi-k2.7-code",
    "qwq",
    "sonar-reasoning",
    "minimax-m",
)
# Grok 4 ships explicit non-reasoning variants; the marker above must not
# swallow them.
_ALWAYS_EXCEPTIONS = ("non-reasoning",)
# Always-on families whose knob still means something (checked before the
# blanket markers above). Grok 4.0 to 4.2 removed the reasoning_effort that
# grok-3-mini had; xAI brought it back from 4.3 on, so the version decides.
_ALWAYS_TUNABLE_MARKERS = ("multi-agent", "sonar-deep-research")
# One or two digits: "grok-4-0709" is a dated Grok 4.0 snapshot, not 4.709.
_GROK_4_MINOR = re.compile(r"grok-4[.\-](\d{1,2})(?!\d)")
_GROK_TUNABLE_FROM_MINOR = 3

# Families observed not to reason without an explicit request. Sending them
# an "off" would at best be noise and at worst a 400 on strict endpoints.
_OFF_BY_DEFAULT_MARKERS = (
    "gpt-4",
    "gpt-3.5",
    "chatgpt-4o",
    "llama",
    # Mistral's chat families are deliberately absent: the platform accepts
    # reasoning_effort on all of them, and an explicit "none" is the contract
    # every chat wire must honour (see off_shapes).
    "codestral",
    "devstral",
    "pixtral",
    "ministral",
    "gemini-1.",
    "gemini-2.0",
    "qwen2",
    "qwen-plus",
    "qwen-turbo",
    "qwen-max",
    "qwen-vl",
    "kimi-k2-instruct",
    "kimi-k2-0711",
    "kimi-k2-0905",
    "mimo-v2-flash",
    "grok-2",
    "grok-3",
    "grok-beta",
    "command-r",
    "command-a",
    "jamba",
    "nova-",
    "dbrx",
    "deepseek-chat",
    "deepseek-coder",
    "glm-4-",
    "glm-4v",
    "phi-3",
)
# Reasoning variants hiding inside an otherwise non-reasoning family name.
_OFF_EXCEPTIONS = ("nemotron", "grok-3-mini", "thinking", "reasoning", "-r1")

_UNSUPPORTED_MARKERS = (
    "unrecognized request argument",
    "unrecognized_request_argument",
    "unknown parameter",
    "unknown field",
    "unknown argument",
    "unexpected parameter",
    "unexpected field",
    "extra inputs are not permitted",
    "extra_forbidden",
    "additional properties",
    "unsupported parameter",
    "not supported",
    "does not support",
    "not available for this model",
)
_VALUE_MARKERS = (
    "reasoning is mandatory",
    "cannot be disabled",
    "can't be disabled",
    "cannot be turned off",
    "invalid value",
    "unsupported value",
    "invalid_value",
    "supported values",
    "must be one of",
    "input should be",  # Anthropic / Bedrock validation wording
    "not a valid",
    "'none'",
    '"none"',
    "'minimal'",
    '"minimal"',
    "budget",
)
# A refusal is about reasoning when it names the knob or quotes one of the
# "off" values we send: OpenAI's "Invalid value: 'none'. Supported values
# are: 'minimal', ..." never says the word "reasoning".
_TOPIC_MARKERS = (
    "reasoning",
    "effort",
    "thinking",
    "think",
    "'none'",
    '"none"',
    "'minimal'",
    '"minimal"',
)


def model_slug(model: str | None) -> str:
    return (model or "").lower().rsplit("/", 1)[-1]


# OpenAI families with no reasoning at all: the chat/search snapshots of
# GPT-5/6 and every GPT-4 / 3.5 model. A reasoning field is a 400 there.
_OPENAI_NON_REASONING = re.compile(
    r"(?:^|[/.\-_])(?:gpt-4|gpt-3\.5|chatgpt-)|gpt-[56][\w.\-]*-(?:chat|search)(?:$|[\-.:])"
)


def openai_rejects_reasoning(model: str | None) -> bool:
    """True for OpenAI models that take no reasoning parameter at all."""
    slug = model_slug(model)
    if not slug or any(x in slug for x in _OFF_EXCEPTIONS):
        return False
    return bool(_OPENAI_NON_REASONING.search(slug))


def reasoning_default(model: str | None) -> str:
    """What this model does about reasoning when the request says nothing."""
    slug = model_slug(model)
    if not slug:
        return UNKNOWN
    if openai_rejects_reasoning(slug):
        return DEFAULT_OFF
    if any(x in slug for x in _ALWAYS_EXCEPTIONS):
        pass
    elif any(m in slug for m in _ALWAYS_TUNABLE_MARKERS):
        return ALWAYS_TUNABLE
    elif any(m in slug for m in _ALWAYS_MARKERS):
        grok_minor = _GROK_4_MINOR.search(slug)
        if grok_minor and int(grok_minor.group(1)) >= _GROK_TUNABLE_FROM_MINOR:
            return ALWAYS_TUNABLE
        return ALWAYS
    if any(m in slug for m in _OFF_BY_DEFAULT_MARKERS) and not any(
        x in slug for x in _OFF_EXCEPTIONS
    ):
        return DEFAULT_OFF
    return DEFAULT_ON


def always_reasons(model: str | None) -> bool:
    """True for models that reason regardless and reject the effort knob."""
    return reasoning_default(model) == ALWAYS


def _effort_shape(effort: str, wire: str) -> dict[str, Any]:
    if wire == WIRE_RESPONSES:
        return {"reasoning": {"effort": effort}}
    return {"reasoning_effort": effort}


def off_shapes(
    model: str | None,
    *,
    spec_name: str = "",
    base_url: str = "",
    wire: str = WIRE_CHAT,
) -> list[dict[str, Any]]:
    """Ordered request patches that mean "no thinking" to this endpoint.

    The first entry is the real "off"; each following one is the floor that
    remains after the previous was refused. An empty list means there is
    nothing to say: the model never reasons, or reasons whatever we say.
    Patches are merged into the request without overriding keys already set
    by a provider-native control.
    """
    kind = reasoning_default(model)
    if kind in (ALWAYS, DEFAULT_OFF):
        return []
    slug = model_slug(model)
    base = (base_url or "").lower()
    spec = (spec_name or "").lower()

    if kind == ALWAYS_TUNABLE and "openrouter" not in base:
        # Cannot be switched off, only turned down: "low" is the floor, and
        # leaving the field out would hand the model its default ("high" on
        # Grok 4.6). OpenRouter keeps its own vocabulary below and negotiates.
        return [_effort_shape("low", wire)]

    if wire == WIRE_RESPONSES:
        # GPT-5.1+ accept "none"; GPT-5 stops at "minimal"; o-series at "low".
        return _openai_off_shapes(slug, wire)

    if "openrouter" in base:
        return [
            {"extra_body": {"reasoning": {"enabled": False}}},
            {"extra_body": {"reasoning": {"effort": "minimal"}}},
            {"extra_body": {"reasoning": {"effort": "low"}}},
        ]
    if spec in ("xai", "xai_oauth") or "x.ai" in base or "grok.com" in base:
        # grok-3-mini has a knob that starts at "low"; Grok 4.3+ took the
        # ALWAYS_TUNABLE branch above; the rest of the line has no knob.
        return [_effort_shape("low", wire)] if "mini" in slug else []
    if (
        spec in ("gemini", "groq", "ollama")
        or "generativelanguage" in base
        or "groq.com" in base
        or ":11434" in base
    ):
        # Documented vocabularies: Google (none for 2.5, low floor on 3.x),
        # Groq (none for Qwen3, low floor for gpt-oss), Ollama (none = off).
        # None of them know "minimal", so skip that round trip.
        return [_effort_shape("none", wire), _effort_shape("low", wire)]
    if spec == "mistral" or "mistral.ai" in base:
        return [_effort_shape("none", wire)]
    if spec == "vllm" or ":8000" in base:
        # Qwen3-style templates read enable_thinking; harmony models read
        # reasoning_effort. Sending both costs nothing when one is ignored.
        template_off = {"chat_template_kwargs": {"enable_thinking": False}}
        return [
            {"reasoning_effort": "none", "extra_body": template_off},
            {"extra_body": template_off},
        ]
    if "claude" in slug:
        from navin.providers.claude_capabilities import claude_cannot_disable_thinking

        if claude_cannot_disable_thinking(slug):
            # Opus 5.5, Sonnet 5.5, Fable, Mythos refuse "disabled": through
            # an OpenAI-wire gateway (LiteLLM) the closest off is "low".
            return [_effort_shape("low", wire)]
    # OpenAI vocabulary for OpenAI itself, Copilot, LM Studio, NVIDIA, HF,
    # SiliconFlow, Novita, OpenCode and every custom endpoint.
    return _openai_off_shapes(slug, wire)


def _openai_off_shapes(slug: str, wire: str) -> list[dict[str, Any]]:
    """The none -> minimal -> low floors, minus those an OpenAI model refuses.

    A documented ladder saves refused round trips: GPT-5 has no "none",
    GPT-5.1+ no "minimal", Codex and o-series start at "low", Pro at
    "medium" (or "high" for the original GPT-5 Pro).
    """
    floors = ("none", "minimal", "low")
    known = openai_known_efforts(slug)
    if known:
        kept = [e for e in floors if e in known]
        if not kept:
            kept = [min(known, key=EFFORT_LADDER.index)]
        floors = tuple(kept)
    return [_effort_shape(e, wire) for e in floors]


def apply_shape(target: dict[str, Any], shape: dict[str, Any]) -> None:
    """Merge *shape* into *target* without overriding what is already there."""
    for key, value in shape.items():
        if key == "extra_body" and isinstance(value, dict):
            extra = target.setdefault("extra_body", {})
            for inner_key, inner_value in value.items():
                extra.setdefault(inner_key, inner_value)
        else:
            target.setdefault(key, value)


_HISTORY_MARKERS = (
    "signature",
    "bound to a different conversation",
    "expected thinking",
    "expected `thinking`",
    "redacted_thinking",
)


def classify_rejection(error: Exception | str) -> str | None:
    """Tell a refused reasoning request from every other error."""
    if isinstance(error, Exception):
        body = getattr(error, "body", None)
        text = f"{body} {error}".lower()
    else:
        text = str(error).lower()
    if not any(topic in text for topic in _TOPIC_MARKERS):
        return None
    if any(marker in text for marker in _HISTORY_MARKERS):
        # A replayed thinking block was refused (bad or foreign signature,
        # missing block): the history is at fault, not the effort setting.
        # Stepping the effort down here would quietly stop reasoning.
        return None
    if any(marker in text for marker in _VALUE_MARKERS):
        return REJECT_VALUE
    if any(marker in text for marker in _UNSUPPORTED_MARKERS):
        return REJECT_UNSUPPORTED
    if "400" in text or "invalid" in text or "bad request" in text:
        # The endpoint objected to something about reasoning; stepping down
        # one floor is the conservative move.
        return REJECT_VALUE
    return None


def suggested_floor(error: Exception | str) -> str | None:
    """The lowest effort a refusal says it accepts, if it names one.

    Z.ai: "cannot be disabled; please use low, high, or max". OpenAI:
    "Supported values are: 'low', 'medium', and 'high'".
    """
    if isinstance(error, Exception):
        text = f"{getattr(error, 'body', None)} {error}".lower()
    else:
        text = str(error).lower()
    for effort in ("low", "medium", "high"):
        if re.search(rf"\b{effort}\b", text):
            return effort
    return None


def _negotiation_store_path() -> Path | None:
    override = os.environ.get("NAVIN_REASONING_NEGOTIATION_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        return Path.home() / ".navin" / "cache" / "reasoning_negotiation.json"
    except (OSError, RuntimeError):
        return None


_STORE_LOCK = threading.Lock()
_STORE_CACHE: dict[str, int] | None = None


def _load_store() -> dict[str, int]:
    global _STORE_CACHE
    if _STORE_CACHE is not None:
        return _STORE_CACHE
    path = _negotiation_store_path()
    data: dict[str, int] = {}
    if path is not None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): int(v) for k, v in raw.items() if isinstance(v, int)}
        except (OSError, ValueError, TypeError):
            data = {}
    _STORE_CACHE = data
    return data


def _save_store(data: dict[str, int]) -> None:
    path = _negotiation_store_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("reasoning negotiation store not written: {}", exc)


def stored_flag(key: str) -> bool:
    """A yes/no fact an endpoint taught us, kept in the same store."""
    with _STORE_LOCK:
        return bool(_load_store().get(key))


def remember_flag(key: str) -> None:
    with _STORE_LOCK:
        store = _load_store()
        if store.get(key) != 1:
            store[key] = 1
            _save_store(store)


def forget_reasoning_negotiations() -> None:
    """Drop what every endpoint taught us (tests, or a provider upgrade)."""
    global _STORE_CACHE
    with _STORE_LOCK:
        _STORE_CACHE = {}
        _save_store({})


class ReasoningOffNegotiator:
    """Remembers, per model, how far down the "off" ladder the endpoint pushed us.

    What an endpoint refused yesterday it refuses today, so the learned step
    is also written to ``~/.navin/cache/reasoning_negotiation.json`` under
    ``<scope>|<model>``. A fresh process (a ``navin agent -m`` run, a gateway
    restart) then sends the accepted shape first instead of paying one
    refused round trip per model. ``scope`` is the endpoint: the same model
    behind OpenRouter and behind its vendor does not answer the same way.
    """

    def __init__(self, scope: str | None = None) -> None:
        self._step: dict[str, int] = {}
        self._scope = (scope or "").strip().lower()

    def _stored_key(self, key: str) -> str:
        return f"{self._scope}|{key}" if self._scope else key

    def _current_step(self, key: str) -> int:
        step = self._step.get(key)
        if step is not None:
            return step
        with _STORE_LOCK:
            stored = _load_store().get(self._stored_key(key))
        step = int(stored) if stored is not None else 0
        self._step[key] = step
        return step

    def _remember(self, key: str, step: int) -> None:
        self._step[key] = step
        with _STORE_LOCK:
            store = _load_store()
            if store.get(self._stored_key(key)) != step:
                store[self._stored_key(key)] = step
                _save_store(store)

    def shape(self, key: str, shapes: list[dict[str, Any]]) -> dict[str, Any] | None:
        step = self._current_step(key)
        if step < len(shapes):
            return shapes[step]
        return None

    def is_omitting(self, key: str, shapes: list[dict[str, Any]]) -> bool:
        return self._current_step(key) >= len(shapes)

    def register_rejection(
        self,
        key: str,
        error: Exception | str,
        shapes: list[dict[str, Any]],
        *,
        knob_known: bool = False,
    ) -> bool:
        """Step down after a refusal. True when the caller should retry.

        ``knob_known`` says the endpoint documents the parameter, so any
        objection can only be about the value: keep walking the floors
        instead of giving up on the first "not supported" wording.
        """
        if not shapes or self.is_omitting(key, shapes):
            return False
        kind = classify_rejection(error)
        if kind is None:
            return False
        if knob_known:
            kind = REJECT_VALUE
        step = self._current_step(key)
        new_step = len(shapes) if kind == REJECT_UNSUPPORTED else step + 1
        self._remember(key, new_step)
        if new_step < len(shapes):
            logger.info(
                "Model {} refused reasoning request {}; retrying with {}",
                key,
                shapes[step],
                shapes[new_step],
            )
        else:
            logger.info(
                "Model {} accepts no reasoning-off request ({}); leaving its default on",
                key,
                kind,
            )
        return True


# --- Effort values each family takes ------------------------------------------
#
# A routed effort ("xhigh" for a hard step, "none" for a cheap one) must reach
# every model as a value it accepts, or the step fails. Measured 2026-10-07:
# Gemini 3.x takes none..high (no xhigh/max) and its -lite models refuse
# "none"; GLM 5.3 always thinks and takes low/high/max only. Anything else a
# model refuses is learned from the error, which names the valid values.

EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# "adaptive" and "ultra" are Anthropic-side efforts (adaptive thinking, the
# deepest tier). Other wires reject them with a 400 on every attempt, so they
# go out as the closest ladder value; nearest_effort then fits "max" down to
# what the model takes.
_NON_ANTHROPIC_EFFORT_ALIASES = {"adaptive": "high", "ultra": "max", "off": "none"}


def non_anthropic_effort(effort: str | None) -> str | None:
    """Map Anthropic-only effort names onto the shared ladder."""
    if not isinstance(effort, str):
        return effort
    return _NON_ANTHROPIC_EFFORT_ALIASES.get(effort.strip().lower(), effort)

_KNOWN_EFFORTS: tuple[tuple[tuple[str, ...], frozenset[str]], ...] = (
    (("gemini", "-lite"), frozenset({"minimal", "low", "medium", "high"})),
    (("gemini",), frozenset({"none", "minimal", "low", "medium", "high"})),
    (("glm-5.3",), frozenset({"none", "minimal", "low", "high", "max"})),
    (("glm-5-3",), frozenset({"none", "minimal", "low", "high", "max"})),
)
_learned_efforts: dict[str, frozenset[str]] = {}
_learned_lock = threading.Lock()

# OpenAI reasoning ladders. Mirrors navin/webui/reasoning_catalog.json (the
# Settings ladder), kept in sync by tests/test_reasoning_matrix_gpt_claude.py.
_E_GPT5 = frozenset({"minimal", "low", "medium", "high"})
_E_LOW_HIGH = frozenset({"low", "medium", "high"})
_E_NONE_HIGH = frozenset({"none", "low", "medium", "high"})
_E_LOW_XHIGH = frozenset({"low", "medium", "high", "xhigh"})
_E_NONE_XHIGH = frozenset({"none", "low", "medium", "high", "xhigh"})
_E_PRO_XHIGH = frozenset({"medium", "high", "xhigh"})
_E_NONE_MAX = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_E_PRO_MAX = frozenset({"medium", "high", "xhigh", "max"})
_GPT5_MINOR = re.compile(r"gpt-5[.\-](\d{1,2})(?!\d)")
_O_SERIES = re.compile(r"(?:^|[/\-_.])o[134](?:-|$|[.\d])")


def openai_known_efforts(model: str | None) -> frozenset[str] | None:
    """Effort values an OpenAI reasoning model documents; None = not OpenAI."""
    slug = model_slug(model)
    if not slug or openai_rejects_reasoning(slug):
        return None
    pro = "pro" in slug
    codex = "codex" in slug
    if "gpt-6" in slug:
        return _E_PRO_MAX if pro else _E_NONE_MAX
    if "gpt-5" in slug:
        match = _GPT5_MINOR.search(slug)
        minor = int(match.group(1)) if match else 0
        if minor >= 6:
            return _E_PRO_MAX if pro else _E_NONE_MAX
        if minor >= 2:
            if codex:
                return _E_LOW_XHIGH
            return _E_PRO_XHIGH if pro else _E_NONE_XHIGH
        if minor == 1:
            if "codex-max" in slug:
                return _E_LOW_XHIGH
            return _E_LOW_HIGH if codex else _E_NONE_HIGH
        if pro:
            return frozenset({"high"})
        return _E_LOW_HIGH if codex else _E_GPT5
    if _O_SERIES.search(slug):
        return _E_LOW_HIGH
    return None


def accepted_efforts(
    model: str | None, key: str | None = None, *, include_openai: bool = True,
) -> frozenset[str] | None:
    """Effort values this model takes, when known or learned; None = unknown.

    ``include_openai=False`` leaves the documented OpenAI ladders out (the
    "none" path walks its own floors in the endpoint's vocabulary).
    """
    if key:
        with _learned_lock:
            learned = _learned_efforts.get(key)
        if learned:
            return learned
    slug = model_slug(model)
    for markers, values in _KNOWN_EFFORTS:
        if all(marker in slug for marker in markers):
            return values
    return openai_known_efforts(slug) if include_openai else None


def nearest_effort(effort: str, accepted: frozenset[str] | None) -> str:
    """The accepted value closest to ``effort`` (ties go to the deeper one).

    "minimal" is the exception: it asks for as little thinking as possible,
    so between "none" and "low" it takes "none" (GPT-5.1 replaced "minimal"
    with "none" as its lowest level).
    """
    if not accepted or effort not in EFFORT_LADDER or effort in accepted:
        return effort
    index = EFFORT_LADDER.index(effort)
    direction = 1 if effort == "minimal" else -1
    ranked = sorted(
        (value for value in accepted if value in EFFORT_LADDER),
        key=lambda value: (
            abs(EFFORT_LADDER.index(value) - index),
            direction * EFFORT_LADDER.index(value),
        ),
    )
    return ranked[0] if ranked else effort


def learn_accepted_efforts(key: str, effort: str, error: Exception | str) -> bool:
    """Remember the values a refusal lists; True when a retry can succeed."""
    if classify_rejection(error) != REJECT_VALUE:
        return False
    if isinstance(error, Exception):
        body = getattr(error, "body", None)
        # A missing body must not read as the effort "none".
        text = f"{body if body is not None else ''} {error}".lower()
    else:
        text = str(error).lower()
    named = {value for value in EFFORT_LADDER if re.search(rf"(?<![\w-]){value}(?![\w-])", text)}
    named.discard(effort)
    if not named:
        return False
    accepted = frozenset(named)
    with _learned_lock:
        if _learned_efforts.get(key) == accepted:
            return False
        _learned_efforts[key] = accepted
    logger.info("Model {} refused effort {!r}; it accepts {}", key, effort, sorted(accepted))
    return True
