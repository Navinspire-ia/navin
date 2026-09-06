"""One routed model call for the product desks (marketing, trading, ...).

Same contract as the Tenders desk: a model is an option, never a dependency.
Each call picks its preset from Settings -> Models -> Task routing by role
("docs", "deep", "fast", "dev"). When no route is configured, the call fails
or the answer is not usable, the caller keeps its deterministic template.
Nothing here loops over a whole book; one object, one call.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_FALLBACK_ROLES: tuple[str, ...] = ("dev", "fast")
_DEFAULT_MAX_TOKENS = 900
_DEFAULT_TIMEOUT_S = 60.0
_REASONING_FLOOR = 2400
_REASONING_CEILING = 6000
_MAX_PRESETS = 2
_OFF = {"0", "off", "false", "no"}
# Presets seen spending a whole budget on reasoning; remembered for the process lifetime.
_THINKING_PRESETS: set[str] = set()


def ai_enabled(env_flag: str, profile: dict[str, Any] | None = None) -> bool:
    """False keeps a desk fully deterministic (env kill switch or profile flag)."""
    if str(os.environ.get(env_flag) or "").strip().lower() in _OFF:
        return False
    return (profile or {}).get("ai_assist") is not False


def route_presets(role: str, routes: dict[str, str] | None = None) -> list[str]:
    """Distinct presets to try for *role*: its own route first, then the generalists."""
    from navin.agent.model_routes import resolve_model_route

    presets: list[str] = []
    for candidate in (str(role or "dev"), *_FALLBACK_ROLES):
        preset = resolve_model_route(candidate, routes=routes)
        if preset and preset not in presets:
            presets.append(preset)
    return presets


def route_preset(role: str, routes: dict[str, str] | None = None) -> str | None:
    """Preset for *role*, then the cheaper generalists, or None when unrouted."""
    presets = route_presets(role, routes)
    return presets[0] if presets else None


def preset_model(preset: str | None, config: Any = None) -> str:
    if not preset:
        return ""
    try:
        if config is None:
            from navin.config.loader import load_config

            config = load_config()
        return str(config.resolve_preset(preset).model or "")
    except Exception:
        return ""


def routing_snapshot(tasks: dict[str, str], *, env_flag: str, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a desk would call per task, for the settings UI."""
    enabled = ai_enabled(env_flag, profile)
    config = None
    routes: dict[str, str] = {}
    try:
        from navin.config.loader import load_config

        config = load_config()
        routes = dict(config.model_routes)
    except Exception:
        enabled = False
    rows = []
    for task, role in tasks.items():
        preset = route_preset(role, routes) if enabled else None
        rows.append({"task": task, "role": role, "preset": preset or "", "model": preset_model(preset, config)})
    return {"enabled": enabled, "routed": sum(1 for row in rows if row["preset"]), "tasks": rows}


def _run(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _chat(preset: str, system: str, user: str, max_tokens: int, temperature: float, timeout_s: float) -> tuple[str, bool]:
    """One completion. Returns ``(text, starved)``; starved means a reasoning
    model spent the whole budget thinking and produced no answer."""
    from navin.providers.factory import load_provider_snapshot

    snapshot = await asyncio.to_thread(load_provider_snapshot, preset_name=preset)
    response = await asyncio.wait_for(
        snapshot.provider.chat_with_retry(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=snapshot.model,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
        timeout=timeout_s,
    )
    text = str(response.content or "")
    if text.strip():
        return text, False
    usage = response.usage if isinstance(response.usage, dict) else {}
    thought = bool(response.reasoning_content) or int(usage.get("reasoning_tokens") or 0) > 0
    return "", str(response.finish_reason or "") == "length" and thought


def _starved_budget(max_tokens: int) -> int:
    """Second attempt for a reasoning model: room to think and still answer."""
    return min(max(max_tokens * 4, _REASONING_FLOOR), _REASONING_CEILING)


def strip_fences(text: str) -> str:
    body = (text or "").strip()
    if not body.startswith("```"):
        return text or ""
    lines = body.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def ask(
    role: str,
    system: str,
    user: str,
    *,
    env_flag: str = "NAVIN_DESK_AI",
    profile: dict[str, Any] | None = None,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    temperature: float = 0.3,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> tuple[str, str]:
    """One routed call. Returns ``(text, model)``; ``("", "")`` means fall back.

    The role's preset goes first. A reasoning model that spends the whole
    budget thinking gets one more try with room to answer; a preset that
    errors or stays silent hands over to the next routed generalist.
    """
    if not ai_enabled(env_flag, profile):
        return "", ""
    for preset in route_presets(role)[:_MAX_PRESETS]:
        # A preset that starved once is a thinking model: start with room to answer.
        budget = _starved_budget(max_tokens) if preset in _THINKING_PRESETS else max_tokens
        for _attempt in range(2):
            try:
                text, starved = _run(_chat(preset, system, user, budget, temperature, timeout_s))
            except Exception:
                break
            body = strip_fences(text).strip()
            if body:
                return body, preset_model(preset) or preset
            if not starved or _starved_budget(budget) <= budget:
                break
            _THINKING_PRESETS.add(preset)
            budget = _starved_budget(budget)
    return "", ""


_JSON_BLOCK = re.compile(r"(\{.*\}|\[.*\])", re.S)


def parse_json(text: str) -> Any:
    """First JSON object/array in a model answer, or None."""
    body = strip_fences(text or "").strip()
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(body)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def ask_json(
    role: str,
    system: str,
    user: str,
    **kwargs: Any,
) -> tuple[Any, str]:
    """Routed call whose answer must be JSON. ``(None, "")`` means fall back."""
    text, model = ask(role, system + "\nAnswer with JSON only, no prose.", user, **kwargs)
    if not text:
        return None, ""
    data = parse_json(text)
    if data is None:
        return None, ""
    return data, model
