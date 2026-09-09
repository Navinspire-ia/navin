# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Inline AI assistance for the editor: ghost-text completion and Cmd+K edits.

- ``complete`` continues the code at the cursor (fill-in-the-middle).
  Prefer a native FIM endpoint when the model supports it (Codestral,
  DeepSeek-Coder, local HF FIM tokens), else chat with a ``<CURSOR>`` marker.
  Prefer the WebSocket stream path so the ghost can paint token-by-token;
  the HTTP JSON path stays as a one-shot fallback.
- ``edit`` rewrites a selection according to an instruction.
  Prefer the WebSocket stream path so Cmd+K can preview token-by-token;
  the HTTP JSON path stays as a one-shot fallback.

Both prefer the ``code`` model route (then ``code-fast``, ``fast``, ``dev``,
default) because these run on a typing budget. Output is constrained to raw
code - no prose, no fences - and stripped defensively.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from navin.webui.assist_fim import (
    FimError,
    fim_complete,
    fim_complete_stream,
    provider_fim_credentials,
    resolve_fim_mode,
)

_MAX_CONTEXT_CHARS = 8000
_MAX_RELATED_CHARS = 2400
_MAX_RECENT_EDITS = 6
_MAX_RECENT_EDIT_CHARS = 1600
_MAX_SELECTION_CHARS = 8000
_MAX_COMPLETION_TOKENS = 128
_MAX_EDIT_TOKENS = 2048
# Cmd+K can wait longer; Tab completion must feel instant (~Cursor budget).
_REQUEST_TIMEOUT_S = 20.0
_COMPLETION_TIMEOUT_S = 3.0

_COMPLETION_SYSTEM = (
    "You are a code completion engine inside an editor (fill-in-the-middle).\n"
    "The caret is marked <CURSOR>. Output ONLY the text to insert there.\n"
    "Rules:\n"
    "- No explanation, no markdown fences, no restating code before/after "
    "the cursor.\n"
    "- Match the file's style, indentation, naming and language.\n"
    "- Prefer a short completion (current token, statement, or small block). "
    "Stop as soon as the local intent is satisfied.\n"
    "- Respect the code after the cursor: do not duplicate it.\n"
    "- If nothing sensible follows, output nothing at all."
)

_EDIT_SYSTEM = (
    "You are a code editing engine inside an editor. Rewrite the code the user "
    "selected so it satisfies their instruction.\n"
    "Rules:\n"
    "- Output ONLY the replacement code. No explanation, no markdown fences.\n"
    "- Preserve the surrounding indentation level of the original selection.\n"
    "- Change only what the instruction requires; keep everything else "
    "byte-identical.\n"
    "- Keep the same language and style as the surrounding file."
)


class AssistError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _assist_preset() -> str | None:
    """Preset for Tab / Cmd+K: code → code-fast → fast → dev → default."""
    from navin.agent.model_routes import resolve_model_route

    return (
        resolve_model_route("code")
        or resolve_model_route("code-fast")
        or resolve_model_route("fast")
        or resolve_model_route("dev")
    )


# Back-compat for tests and callers that still import the old name.
_fast_preset = _assist_preset


def _load_snapshot(preset_name: str | None) -> Any:
    from navin.providers.factory import load_provider_snapshot

    return load_provider_snapshot(preset_name=preset_name)


def _route_label(preset: str | None) -> str:
    return preset or "default"


async def _ask(
    system: str,
    user: str,
    *,
    max_tokens: int,
    timeout_s: float = _REQUEST_TIMEOUT_S,
) -> tuple[str, str, str]:
    """One non-streaming model call. Returns (text, model name, route)."""
    preset = _assist_preset()
    try:
        snapshot = await asyncio.to_thread(_load_snapshot, preset)
    except Exception as exc:
        raise AssistError(f"no model configured: {exc}", status=503) from exc

    try:
        response = await asyncio.wait_for(
            snapshot.provider.chat_with_retry(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=snapshot.model,
                max_tokens=max_tokens,
                temperature=0.0,
            ),
            timeout=timeout_s,
        )
    except TimeoutError as exc:
        raise AssistError("the model did not answer in time", status=504) from exc
    except Exception as exc:
        raise AssistError(f"model call failed: {exc}", status=502) from exc

    return (response.content or ""), str(snapshot.model or ""), _route_label(preset)


async def _ask_stream(
    system: str,
    user: str,
    *,
    max_tokens: int,
    on_delta: Callable[[str], Awaitable[None]],
    timeout_s: float = _REQUEST_TIMEOUT_S,
) -> tuple[str, str, str]:
    """Stream a model call; returns (text, model name, route)."""
    preset = _assist_preset()
    try:
        snapshot = await asyncio.to_thread(_load_snapshot, preset)
    except Exception as exc:
        raise AssistError(f"no model configured: {exc}", status=503) from exc

    chunks: list[str] = []

    async def _capture(delta: str) -> None:
        if not delta:
            return
        chunks.append(delta)
        await on_delta(delta)

    try:
        response = await asyncio.wait_for(
            snapshot.provider.chat_stream_with_retry(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=snapshot.model,
                max_tokens=max_tokens,
                temperature=0.0,
                on_content_delta=_capture,
            ),
            timeout=timeout_s,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError as exc:
        raise AssistError("the model did not answer in time", status=504) from exc
    except Exception as exc:
        raise AssistError(f"model call failed: {exc}", status=502) from exc

    text = (response.content or "") or "".join(chunks)
    return text, str(snapshot.model or ""), _route_label(preset)


def _completion_prompt(
    *,
    path: str,
    prefix: str,
    suffix: str,
    language: str = "",
    related_files: list[dict[str, Any]] | None = None,
    recent_edits: list[dict[str, Any]] | None = None,
) -> tuple[str, str, str]:
    """Return ``(user_prompt, before, after)`` for FIM completion."""
    before = _trim_context(prefix, keep_end=True)
    after = _trim_context(suffix, keep_end=False)
    label = language or Path(path or "").suffix.lstrip(".") or "text"
    related = _format_related(related_files)
    edits = format_recent_edits(recent_edits)
    user = (
        f"{related}"
        f"{edits}"
        f"File: {path or 'untitled'}\nLanguage: {label}\n\n"
        f"{before}<CURSOR>{after}"
    )
    return user, before, after


def finalize_completion(before: str, after: str, raw: str) -> str:
    """Sanitize model output into an insertable ghost suggestion."""
    completion = _drop_overlap(before, _strip_fences(raw))
    completion = _drop_suffix_overlap(completion, after)
    return completion.rstrip("\n")


def partial_completion(before: str, raw: str) -> str:
    """Best-effort sanitize for mid-stream ghost updates."""
    completion = _drop_overlap(before, _strip_fences(raw))
    return completion.rstrip("\r")


def _strip_fences(text: str) -> str:
    """Remove markdown fencing a model may add despite being told not to."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2:
        return ""
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _trim_context(text: str, *, keep_end: bool) -> str:
    """Keep the part of the context nearest the cursor."""
    if len(text) <= _MAX_CONTEXT_CHARS:
        return text
    return text[-_MAX_CONTEXT_CHARS:] if keep_end else text[:_MAX_CONTEXT_CHARS]


def _drop_overlap(prefix: str, completion: str) -> str:
    """Drop a leading repeat of what the user already typed.

    Models often restate the current line before continuing it, which would
    duplicate text on insert.
    """
    if not completion:
        return completion
    tail = prefix[-200:]
    longest = min(len(tail), len(completion))
    for size in range(longest, 3, -1):
        if tail.endswith(completion[:size]):
            return completion[size:]
    return completion


def _drop_suffix_overlap(completion: str, suffix: str) -> str:
    """Drop a trailing repeat of what already follows the caret (FIM)."""
    if not completion or not suffix:
        return completion
    head = suffix[:200]
    longest = min(len(head), len(completion))
    for size in range(longest, 3, -1):
        if completion.endswith(head[:size]):
            return completion[:-size]
    return completion


def sanitize_recent_edits(raw: Any) -> list[dict[str, Any]]:
    """Validate the ``recent_edits`` list sent by the editor.

    Each entry describes one user edit: file path, 1-based line, removed and
    inserted text. Anything malformed is dropped; sizes are capped so a paste
    of thousands of lines cannot blow up the prompt.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[-_MAX_RECENT_EDITS:]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        removed = item.get("removed")
        inserted = item.get("inserted")
        if not isinstance(removed, str):
            removed = ""
        if not isinstance(inserted, str):
            inserted = ""
        if not path or (not removed and not inserted):
            continue
        line = item.get("line")
        out.append(
            {
                "path": path[:300],
                "line": int(line) if isinstance(line, (int, float)) else 0,
                "removed": removed[:400],
                "inserted": inserted[:400],
            }
        )
    return out


def format_recent_edits(edits: list[dict[str, Any]] | None) -> str:
    """Render recent user edits as a compact diff block for the prompt.

    Cursor-grade Tab quality comes from seeing what the user just changed:
    the next edit is usually a continuation of the previous ones (renames,
    call-site updates, symmetrical branches...).
    """
    if not edits:
        return ""
    lines: list[str] = ["Recent edits by the user (oldest first):"]
    budget = _MAX_RECENT_EDIT_CHARS
    for edit in edits[-_MAX_RECENT_EDITS:]:
        path = str(edit.get("path") or "").strip()
        removed = str(edit.get("removed") or "")
        inserted = str(edit.get("inserted") or "")
        if not path or (not removed and not inserted):
            continue
        line = edit.get("line")
        location = f"{path}:{line}" if isinstance(line, int) and line > 0 else path
        block: list[str] = [f"@ {location}"]
        for row in removed.splitlines()[:6]:
            block.append(f"- {row}")
        for row in inserted.splitlines()[:6]:
            block.append(f"+ {row}")
        text = "\n".join(block)
        if len(text) > budget:
            break
        lines.append(text)
        budget -= len(text)
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


def _format_related(related: list[dict[str, Any]] | None) -> str:
    """Compact snippets of related files for the completion prompt."""
    if not related:
        return ""
    chunks: list[str] = []
    budget = _MAX_RELATED_CHARS
    for item in related[:4]:
        if budget <= 0:
            break
        path = str(item.get("path") or "").strip()
        body = str(item.get("content") or "")
        if not path or not body.strip():
            continue
        snippet = body[: min(len(body), budget, 800)]
        chunks.append(f"// related: {path}\n{snippet}")
        budget -= len(snippet)
    if not chunks:
        return ""
    return "Related files (read-only context):\n" + "\n\n".join(chunks) + "\n\n"


def _project_rel(project_root: Path, path: str) -> str:
    """Best-effort project-relative key for an editor path."""
    from navin.utils.path import normalize_relative_path

    text = (path or "").strip().replace("\\", "/")
    if not text:
        return ""
    try:
        root = project_root.expanduser().resolve(strict=False)
        candidate = Path(text).expanduser()
        if candidate.is_absolute():
            return candidate.resolve(strict=False).relative_to(root).as_posix()
    except Exception:
        pass
    return normalize_relative_path(text)


def resolve_related_files(
    project_root: Path | str | None,
    path: str,
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Pick 2-4 import-graph neighbors for Tab context (index-backed).

    Order: direct dependencies first, then dependents. Failures are silent so a
    cold/missing index never blocks ghost text.
    """
    if not project_root or not path or limit <= 0:
        return []
    try:
        from navin.index import get_index

        root = Path(project_root).expanduser().resolve(strict=False)
        rel = _project_rel(root, path)
        if not rel:
            return []
        index = get_index(root)
        index.ensure()
        ranked: list[str] = []
        for candidate in index.dependencies(rel) + index.dependents(rel):
            if candidate == rel or candidate in ranked:
                continue
            ranked.append(candidate)
            if len(ranked) >= limit:
                break
        out: list[dict[str, Any]] = []
        budget = _MAX_RELATED_CHARS
        for candidate in ranked:
            if budget <= 0:
                break
            absolute = root / candidate
            try:
                raw = absolute.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not raw.strip():
                continue
            snippet = raw[: min(len(raw), budget, 800)]
            out.append({"path": candidate, "content": snippet})
            budget -= len(snippet)
        return out
    except Exception:
        return []


def fim_prefix_with_context(
    before: str,
    related: list[dict[str, Any]] | None,
    recent_edits: list[dict[str, Any]] | None,
) -> str | None:
    """Prefix for the *native* FIM call, with extra context riding along.

    Native FIM endpoints only see prefix/suffix, so related snippets and the
    recent-edit trail are prepended above the real code. Returns ``None``
    when there is no extra context (pure prefix stays byte-identical).
    """
    header = _format_related(related) + format_recent_edits(recent_edits)
    if not header:
        return None
    return header + before


def _with_related(
    *,
    path: str,
    related_files: list[dict[str, Any]] | None,
    project_root: Path | str | None,
) -> list[dict[str, Any]] | None:
    if related_files:
        return related_files
    if project_root is None:
        return None
    resolved = resolve_related_files(project_root, path)
    return resolved or None


async def _ask_completion_native_or_chat(
    *,
    before: str,
    after: str,
    user_prompt: str,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    stream: bool,
    fim_prefix: str | None = None,
) -> tuple[str, str, str, str]:
    """Try native FIM first; fall back to chat ``<CURSOR>`` prompting.

    ``fim_prefix`` optionally replaces ``before`` for the native FIM call so
    extra context (related files, recent edits) can ride along; the chat path
    already carries that context in ``user_prompt``.

    Returns ``(text, model, route, mode)`` where ``mode`` is ``fim`` or ``chat``.
    """
    preset = _assist_preset()
    try:
        snapshot = await asyncio.to_thread(_load_snapshot, preset)
    except Exception as exc:
        raise AssistError(f"no model configured: {exc}", status=503) from exc

    model = str(snapshot.model or "")
    route = _route_label(preset)
    api_base, api_key = provider_fim_credentials(snapshot.provider)
    fim_mode = resolve_fim_mode(model, api_base)

    if fim_mode and api_base:
        native_prefix = fim_prefix if fim_prefix is not None else before
        try:
            if stream:
                text = await fim_complete_stream(
                    api_base=api_base,
                    api_key=api_key,
                    model=model,
                    prefix=native_prefix,
                    suffix=after,
                    max_tokens=_MAX_COMPLETION_TOKENS,
                    mode=fim_mode,
                    timeout_s=_COMPLETION_TIMEOUT_S,
                    on_delta=on_delta,
                )
            else:
                text = await fim_complete(
                    api_base=api_base,
                    api_key=api_key,
                    model=model,
                    prefix=native_prefix,
                    suffix=after,
                    max_tokens=_MAX_COMPLETION_TOKENS,
                    mode=fim_mode,
                    timeout_s=_COMPLETION_TIMEOUT_S,
                )
            return text, model, route, "fim"
        except FimError:
            # Native path unavailable for this host/key - chat fallback below.
            pass

    if stream:
        if on_delta is None:
            async def _noop(_delta: str) -> None:
                return None

            delta_cb = _noop
        else:
            delta_cb = on_delta
        text, model, route = await _ask_stream(
            _COMPLETION_SYSTEM,
            user_prompt,
            max_tokens=_MAX_COMPLETION_TOKENS,
            on_delta=delta_cb,
            timeout_s=_COMPLETION_TIMEOUT_S,
        )
    else:
        text, model, route = await _ask(
            _COMPLETION_SYSTEM,
            user_prompt,
            max_tokens=_MAX_COMPLETION_TOKENS,
            timeout_s=_COMPLETION_TIMEOUT_S,
        )
    return text, model, route, "chat"


async def completion_payload(
    *,
    path: str,
    prefix: str,
    suffix: str,
    language: str = "",
    related_files: list[dict[str, Any]] | None = None,
    project_root: Path | str | None = None,
    recent_edits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ghost-text continuation for the caret between ``prefix`` and ``suffix``."""
    if not prefix.strip() and not suffix.strip():
        return {"completion": "", "model": "", "reason": "empty file", "mode": ""}

    related = _with_related(
        path=path, related_files=related_files, project_root=project_root
    )
    edits = sanitize_recent_edits(recent_edits)
    user, before, after = _completion_prompt(
        path=path,
        prefix=prefix,
        suffix=suffix,
        language=language,
        related_files=related,
        recent_edits=edits,
    )
    started = time.perf_counter()
    text, model, route, mode = await _ask_completion_native_or_chat(
        before=before,
        after=after,
        user_prompt=user,
        on_delta=None,
        stream=False,
        fim_prefix=fim_prefix_with_context(before, related, edits),
    )
    completion = finalize_completion(before, after, text)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "completion": completion,
        "model": model,
        "route": route,
        "mode": mode,
        "latency_ms": latency_ms,
        "reason": "" if completion else "no suggestion",
    }


async def stream_completion(
    *,
    path: str,
    prefix: str,
    suffix: str,
    language: str = "",
    related_files: list[dict[str, Any]] | None = None,
    project_root: Path | str | None = None,
    recent_edits: list[dict[str, Any]] | None = None,
    on_partial: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Stream a completion; ``on_partial`` receives sanitized cumulative text."""
    if not prefix.strip() and not suffix.strip():
        return {"completion": "", "model": "", "reason": "empty file", "mode": ""}

    related = _with_related(
        path=path, related_files=related_files, project_root=project_root
    )
    edits = sanitize_recent_edits(recent_edits)
    user, before, after = _completion_prompt(
        path=path,
        prefix=prefix,
        suffix=suffix,
        language=language,
        related_files=related,
        recent_edits=edits,
    )
    accumulated = ""
    last_sent = ""
    started = time.perf_counter()
    ttft_ms: int | None = None

    async def _on_delta(delta: str) -> None:
        nonlocal accumulated, last_sent, ttft_ms
        accumulated += delta
        if on_partial is None:
            return
        partial = partial_completion(before, accumulated)
        if not partial or partial == last_sent:
            return
        # Avoid flooding the socket: grow by at least a few chars each time.
        if len(partial) - len(last_sent) < 2 and not delta.endswith(("\n", " ", "(", ")")):
            return
        if ttft_ms is None:
            ttft_ms = int((time.perf_counter() - started) * 1000)
        last_sent = partial
        await on_partial(partial)

    text, model, route, mode = await _ask_completion_native_or_chat(
        before=before,
        after=after,
        user_prompt=user,
        on_delta=_on_delta,
        stream=True,
        fim_prefix=fim_prefix_with_context(before, related, edits),
    )
    completion = finalize_completion(before, after, text or accumulated)
    if on_partial is not None and completion and completion != last_sent:
        await on_partial(completion)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "completion": completion,
        "model": model,
        "route": route,
        "mode": mode,
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms if ttft_ms is not None else latency_ms,
        "reason": "" if completion else "no suggestion",
    }


def _edit_prompt(
    *,
    path: str,
    selection: str,
    instruction: str,
    prefix: str = "",
    suffix: str = "",
    language: str = "",
) -> tuple[str, str]:
    """Return ``(user_prompt, cleaned_instruction)`` for a Cmd+K edit."""
    cleaned_instruction = (instruction or "").strip()
    if not cleaned_instruction:
        raise AssistError("missing instruction")
    if len(selection) > _MAX_SELECTION_CHARS:
        raise AssistError(
            f"selection is too large ({len(selection)} chars, "
            f"maximum {_MAX_SELECTION_CHARS})"
        )

    label = language or Path(path or "").suffix.lstrip(".") or "text"
    parts = [f"File: {path or 'untitled'}", f"Language: {label}", ""]
    if prefix:
        parts += ["Code before the selection:", _trim_context(prefix, keep_end=True), ""]
    if suffix:
        parts += ["Code after the selection:", _trim_context(suffix, keep_end=False), ""]
    parts += [
        "Selected code to rewrite:",
        selection if selection.strip() else "(empty selection - insert new code here)",
        "",
        f"Instruction: {cleaned_instruction}",
    ]
    return "\n".join(parts), cleaned_instruction


def partial_edit(raw: str) -> str:
    """Best-effort sanitize for mid-stream Cmd+K preview (opening fence only)."""
    stripped = raw.lstrip()
    if not stripped.startswith("```"):
        return raw
    lines = stripped.splitlines()
    if not lines:
        return ""
    body = "\n".join(lines[1:])
    if body.rstrip().endswith("```"):
        body = body.rstrip()
        body = body[: body.rfind("```")]
    return body


def finalize_edit(raw: str) -> str:
    """Sanitize model output into an apply-ready replacement."""
    return _strip_fences(raw)


async def edit_payload(
    *,
    path: str,
    selection: str,
    instruction: str,
    prefix: str = "",
    suffix: str = "",
    language: str = "",
) -> dict[str, Any]:
    """Rewrite ``selection`` per ``instruction``, using the file as context."""
    user, _cleaned = _edit_prompt(
        path=path,
        selection=selection,
        instruction=instruction,
        prefix=prefix,
        suffix=suffix,
        language=language,
    )
    started = time.perf_counter()
    text, model, route = await _ask(_EDIT_SYSTEM, user, max_tokens=_MAX_EDIT_TOKENS)
    replacement = finalize_edit(text)
    if not replacement.strip():
        raise AssistError("the model returned no replacement code", status=502)
    return {
        "replacement": replacement,
        "model": model,
        "route": route,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "original_length": len(selection),
    }


async def stream_edit(
    *,
    path: str,
    selection: str,
    instruction: str,
    prefix: str = "",
    suffix: str = "",
    language: str = "",
    on_partial: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Stream a Cmd+K rewrite; ``on_partial`` receives sanitized cumulative text."""
    user, _cleaned = _edit_prompt(
        path=path,
        selection=selection,
        instruction=instruction,
        prefix=prefix,
        suffix=suffix,
        language=language,
    )
    accumulated = ""
    last_sent = ""

    async def _on_delta(delta: str) -> None:
        nonlocal accumulated, last_sent
        accumulated += delta
        if on_partial is None:
            return
        partial = partial_edit(accumulated)
        if not partial or partial == last_sent:
            return
        if len(partial) - len(last_sent) < 4 and not delta.endswith(("\n", " ", "(", ")")):
            return
        last_sent = partial
        await on_partial(partial)

    started = time.perf_counter()
    ttft_ms: int | None = None

    async def _timed_delta(delta: str) -> None:
        nonlocal ttft_ms
        if ttft_ms is None and delta:
            ttft_ms = int((time.perf_counter() - started) * 1000)
        await _on_delta(delta)

    text, model, route = await _ask_stream(
        _EDIT_SYSTEM,
        user,
        max_tokens=_MAX_EDIT_TOKENS,
        on_delta=_timed_delta,
        timeout_s=45.0,
    )
    replacement = finalize_edit(text or accumulated)
    if not replacement.strip():
        raise AssistError("the model returned no replacement code", status=502)
    if on_partial is not None and replacement != last_sent:
        await on_partial(replacement)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {
        "replacement": replacement,
        "model": model,
        "route": route,
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms if ttft_ms is not None else latency_ms,
        "original_length": len(selection),
    }
