# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Turn a spoken utterance into the message the agent actually reads.

In the live voice conversation the user talks: fillers, restarts, half
sentences, transcription noise. Dropping that raw transcript in the chat reads
badly and the agent has to guess the intent. This module rewrites the
utterance into the written message the user would have typed: same language,
same words where they matter, nothing added. Short answers stay short.

The rewrite races a few fast models (hedged: the next one starts when the
previous is slow). When nothing faithful comes back within the budget, the
raw transcript is used as-is so the conversation never stalls.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

VOICE_PROMPT_MAX_TRANSCRIPT_CHARS = 4_000
VOICE_PROMPT_MAX_CONTEXT_CHARS = 700
VOICE_PROMPT_TIMEOUT_S = 7.0
# A candidate still silent after this long gets company: the next model starts
# alongside it and the first faithful answer wins (hedged request).
VOICE_PROMPT_HEDGE_DELAY_S = 2.0
VOICE_PROMPT_MAX_TOKENS = 500

# Below this many words a rewrite cannot improve anything worth the latency.
_SHORT_UTTERANCE_WORDS = 3

# The default chat model can be a slow reasoning model; a rewrite has to come
# back in a second or two to feel like a conversation. On OpenRouter-compatible
# keys (managed Navin key included) these non-reasoning flash models answer in
# about 1-2 s and return the sentence, not a thinking trace.
_FAST_REWRITE_MODELS: dict[str, tuple[str, ...]] = {
    "navin": ("google/gemini-3.7-flash", "deepseek/deepseek-v4.1-flash"),
    "openrouter": ("google/gemini-3.7-flash", "deepseek/deepseek-v4.1-flash"),
}
# The fast candidates are cheap (a few hundred tokens): this many start at
# once, the rest join one by one when those stay silent.
VOICE_PROMPT_PARALLEL_STARTS = 2


def rewrite_model_candidates(config: Any) -> list[str | None]:
    """Models to try, in order; ``None`` means the provider's default model.

    ``voice.prompt_model`` in the config pins one model. Otherwise the fast
    candidates for the active provider come first and the default model last.
    """
    voice_cfg = getattr(config, "voice", None)
    pinned = getattr(voice_cfg, "prompt_model", None)
    if isinstance(pinned, str) and pinned.strip():
        return [pinned.strip()]
    provider_name = ""
    try:
        resolved = config.resolve_preset()
        provider_name = (config.get_provider_name(resolved.model, preset=resolved) or "").lower()
    except Exception:  # pragma: no cover - config shapes vary in tests
        provider_name = ""
    return [*_FAST_REWRITE_MODELS.get(provider_name, ()), None]


_SYSTEM_PROMPT = (
    "You clean up speech-to-text transcripts of what a user said to an AI coding "
    "assistant, so the text can be posted in the chat as the user's message.\n"
    "Rules:\n"
    "- Keep the user's language, meaning, point of view and level of detail. "
    "First person stays first person; a question stays a question; an order "
    "stays an order.\n"
    "- Remove fillers (um, euh, like, genre, voila), false starts, repetitions "
    "and obvious transcription noise. Fix punctuation, casing and sentence "
    "breaks.\n"
    "- Keep every name, number, path, command and technical term exactly as "
    "said.\n"
    "- Never answer the user, never add ideas, options, steps, explanations or "
    "politeness that were not said. Do not summarize away details.\n"
    '- A short answer or acknowledgement stays short ("Yes, go ahead.", '
    '"The second one.").\n'
    "- If the transcript is already clean, return it unchanged.\n"
    "- Output only the rewritten message: no quotes, no label, no commentary."
)

_LABEL_PREFIX_RE = re.compile(
    r"^\s*(?:rewritten(?: message| text)?|message|prompt|output|user|texte|r[ée]sultat)\s*:\s*",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^\s*```[\w-]*\s*|\s*```\s*$")
_WS_RE = re.compile(r"\s+")


def normalize_transcript(text: Any) -> str:
    """Whitespace-normalized transcript, or an empty string."""
    if not isinstance(text, str):
        return ""
    return _WS_RE.sub(" ", text).strip()


def rewrite_worth_it(transcript: str) -> bool:
    """A couple of words ("oui", "vas-y") do not need a model round trip."""
    words = [word for word in transcript.split(" ") if word]
    return len(words) > _SHORT_UTTERANCE_WORDS


def clean_rewrite_output(raw: Any) -> str:
    """Strip fences, labels and wrapping quotes a model may add around the text."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    text = _FENCE_RE.sub("", text).strip()
    text = _LABEL_PREFIX_RE.sub("", text).strip()
    if len(text) >= 2 and text[0] in "\"«“'" and text[-1] in "\"»”'":
        text = text[1:-1].strip()
    return text


def rewrite_is_faithful(transcript: str, candidate: str) -> bool:
    """Reject outputs that are empty, ballooned, or clearly an answer instead of a rewrite."""
    if not candidate:
        return False
    if len(candidate) > len(transcript) * 2 + 60:
        return False
    if "\n\n" in candidate and "\n" not in transcript and len(candidate) > len(transcript) + 40:
        return False
    return True


def build_rewrite_messages(transcript: str, context: str | None) -> list[dict[str, str]]:
    user_parts: list[str] = []
    ctx = normalize_transcript(context)
    if ctx:
        ctx = ctx[:VOICE_PROMPT_MAX_CONTEXT_CHARS]
        user_parts.append(
            "For reference only, the assistant's last message (do not answer it, "
            f"do not merge it into the output):\n{ctx}\n"
        )
    user_parts.append(f"Transcript:\n{transcript}")
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


async def _rewrite_once(
    provider: Any,
    messages: list[dict[str, str]],
    *,
    model: str | None,
    timeout_s: float,
) -> str | None:
    """One model attempt: the cleaned text, or None when it should not be used."""
    kwargs: dict[str, Any] = {
        "tools": None,
        "max_tokens": VOICE_PROMPT_MAX_TOKENS,
        "temperature": 0.0,
        "retry_mode": "standard",
    }
    if model:
        kwargs["model"] = model
    response = await asyncio.wait_for(
        provider.chat_with_retry(messages, **kwargs), timeout=timeout_s
    )
    if getattr(response, "finish_reason", "stop") in {"length", "error"}:
        # A reasoning model spent the budget thinking: nothing usable came back.
        return None
    return clean_rewrite_output(getattr(response, "content", None))


async def rewrite_voice_transcript(
    transcript: str,
    *,
    context: str | None = None,
    provider: Any | None = None,
    config: Any | None = None,
    models: list[str | None] | None = None,
    timeout_s: float = VOICE_PROMPT_TIMEOUT_S,
    hedge_delay_s: float = VOICE_PROMPT_HEDGE_DELAY_S,
) -> str:
    """Return the cleaned-up message for *transcript*; the transcript itself on any failure."""
    text = normalize_transcript(transcript)
    if not text:
        return ""
    if len(text) > VOICE_PROMPT_MAX_TRANSCRIPT_CHARS or not rewrite_worth_it(text):
        return text

    if provider is None:
        try:
            from navin.config.loader import load_config
            from navin.providers.factory import make_provider

            config = config or load_config()
            provider = make_provider(config)
        except Exception as exc:  # pragma: no cover - depends on the local config
            logger.debug("voice prompt rewrite: no provider ({})", exc)
            return text
    if models is None:
        models = rewrite_model_candidates(config) if config is not None else [None]

    messages = build_rewrite_messages(text, context)
    result = await _hedged_rewrite(
        provider,
        messages,
        models=list(models),
        timeout_s=timeout_s,
        hedge_delay_s=hedge_delay_s,
        original=text,
    )
    return result if result is not None else text


async def _hedged_rewrite(
    provider: Any,
    messages: list[dict[str, str]],
    *,
    models: list[str | None],
    timeout_s: float,
    hedge_delay_s: float,
    original: str,
) -> str | None:
    """Race the candidate models: start the next one when the previous is slow.

    Candidates launch in order. A new one joins whenever the running ones have
    been silent for ``hedge_delay_s`` or have all failed. The first faithful
    rewrite wins and the rest are cancelled; ``None`` when nothing usable
    arrives before ``timeout_s``.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    queue = list(models)
    running: dict[asyncio.Task[str | None], str | None] = {}

    def launch() -> None:
        model = queue.pop(0)
        remaining = max(0.05, deadline - loop.time())
        task = asyncio.create_task(
            _rewrite_once(provider, messages, model=model, timeout_s=remaining)
        )
        running[task] = model

    try:
        launch()
        while queue and len(running) < VOICE_PROMPT_PARALLEL_STARTS:
            launch()
        while running:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            wait_s = min(remaining, hedge_delay_s) if queue else remaining
            done, _ = await asyncio.wait(
                running, timeout=wait_s, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                model = running.pop(task) or "default"
                try:
                    candidate = task.result()
                except TimeoutError:
                    logger.info("voice prompt rewrite timed out ({})", model)
                    continue
                except Exception as exc:
                    logger.info("voice prompt rewrite failed on {}: {}", model, exc)
                    continue
                if candidate is None:
                    continue
                if not rewrite_is_faithful(original, candidate):
                    logger.debug("voice prompt rewrite rejected ({}: {!r})", model, candidate[:80])
                    continue
                logger.debug(
                    "voice prompt rewrite by {} in {:.1f}s",
                    model,
                    loop.time() - (deadline - timeout_s),
                )
                return candidate
            if queue and (not running or not done):
                # Everything failed, or the running ones are past the hedge delay.
                launch()
        if not running:
            return None
        logger.info("voice prompt rewrite too slow; sending the transcript")
        return None
    finally:
        for task in running:
            task.cancel()
