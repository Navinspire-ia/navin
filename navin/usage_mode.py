"""Soft budget router: apply navin.live usage.mode to live model selection.

The site computes ``normal`` / ``reduced`` / ``economy`` / ``exhausted`` from
monthly spend (see ``site/src/lib/plans.ts``). This module mirrors that policy
locally so the IDE throttles **Navin subscription** (managed-key) turns
without a gateway restart. BYOK / Free keys are never clamped.

From 50 % monthly usage, only the expensive flagships are paused:

- Claude Opus 5 and newer
- Claude Fable 5 and newer
- GPT 5.6 (Sol / Terra / Luna and the same family)

Everything else (Grok, Gemini, DeepSeek, MiniMax, GLM, vision readers
that are not those flagships) stays selectable. An allowlist that left
only Nemotron + Flash created panic and hid useful models while vision
rows slipped through. At 100 % the plan is empty: Nemotron only.

Token caps still follow the mode names (reduced / economy / exhausted).
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from loguru import logger

from navin.providers.managed_catalog import TIERS

USAGE_MODES = ("normal", "reduced", "economy", "exhausted")
# Keep in sync with site/src/lib/plans.ts USAGE_THRESHOLDS.
USAGE_THRESHOLDS = {"reduced": 0.8, "economy": 0.95}

# Highest tier a mode may still use (inclusive).
_MODE_TIER_CAP: dict[str, str] = {
    "normal": "expert",
    "reduced": "main",
    "economy": "executor",
    "exhausted": "light",
}

# Soft max_tokens ceilings when the plan has not stored outputTokensPerCall.
_MODE_TOKEN_FALLBACK: dict[str, int] = {
    "normal": 0,  # no soft cap
    "reduced": 8_192,
    "economy": 4_096,
    "exhausted": 2_048,
}

_TIER_RANK = {name: idx for idx, name in enumerate(TIERS)}

_GEMINI_37 = "google/gemini-3.7-flash"
_GROK_46 = "x-ai/grok-4.6"
_DS_FLASH = "deepseek/deepseek-v4-flash"
_NEMOTRON_ULTRA = "nvidia/nemotron-3-ultra-550b-a55b:free"

# Hide these families from 50 % (chat and vision, same rule).
_FLAGSHIP_CUTOFF_PERCENT = 50
_OPUS_VERSION = re.compile(r"opus[-_.\s]*(\d+)(?:[-_.](\d+))?", re.IGNORECASE)
_FABLE_VERSION = re.compile(r"fable[-_.\s]*(\d+)(?:[-_.](\d+))?", re.IGNORECASE)
_GPT_56 = re.compile(r"gpt[-_.]?5[-_.]?6\b", re.IGNORECASE)

# Freshest mode / % from the last report_usage (may beat a stale config.json).
_live_mode: str | None = None
_live_percent: int | None = None


def _is_nemotron(slug: str) -> bool:
    return "nemotron" in (slug or "").lower()


def _parsed_family_version(match: re.Match[str]) -> tuple[int, int]:
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    return major, minor


def is_expensive_flagship_slug(slug: str) -> bool:
    """True for Opus 5+, Fable 5+, and GPT 5.6. Opus 4.8 stays available."""
    text = (slug or "").strip()
    if not text:
        return False
    if _GPT_56.search(text):
        return True
    opus = _OPUS_VERSION.search(text)
    if opus and _parsed_family_version(opus) >= (5, 0):
        return True
    fable = _FABLE_VERSION.search(text)
    if fable and _parsed_family_version(fable) >= (5, 0):
        return True
    return False


def _mode_percent_floor(mode: str) -> int:
    return {"reduced": 80, "economy": 95, "exhausted": 100}.get(
        normalize_usage_mode(mode), 0
    )


def budget_used_percent(config: Any, mode: str | None = None) -> int:
    """Monthly budget used (0-100).

    Takes the highest signal among: in-memory snapshot from the last
    validate/report, the on-disk percent, spent/budget, and the mode floor.
    A stale config.json at 70 % must not keep Opus runnable after the live
    usage has already crossed 80 %.
    """
    candidates: list[int] = []
    if _live_percent is not None:
        candidates.append(_live_percent)

    lic = getattr(config, "license", None) if config is not None else None
    raw = getattr(lic, "usage_used_percent", 0) if lic else 0
    try:
        pct = int(raw or 0)
    except (TypeError, ValueError):
        pct = 0
    if pct > 0:
        candidates.append(min(100, max(0, pct)))

    budget = getattr(lic, "usage_budget_micro_usd", 0) if lic else 0
    spent = getattr(lic, "usage_spent_micro_usd", 0) if lic else 0
    try:
        budget_i = int(budget or 0)
        spent_i = int(spent or 0)
    except (TypeError, ValueError):
        budget_i, spent_i = 0, 0
    if budget_i > 0 and spent_i > 0:
        candidates.append(min(100, round((spent_i / budget_i) * 100)))

    resolved = normalize_usage_mode(mode) if mode else current_usage_mode(config)
    floor = _mode_percent_floor(resolved)
    if floor:
        candidates.append(floor)
    if not candidates:
        return 0
    return min(100, max(candidates))


def slug_allowed_for_budget(slug: str, percent: int) -> bool:
    """Whether a managed slug may still run at this spend percentage."""
    s = (slug or "").strip()
    if not s:
        return False
    if percent < _FLAGSHIP_CUTOFF_PERCENT:
        return True
    if _is_nemotron(s):
        return True
    if percent >= 100:
        return False
    return not is_expensive_flagship_slug(s)


def preferred_budget_slugs(percent: int) -> tuple[str, ...]:
    """Fallback slugs when the live model is a paused flagship."""
    if percent >= 100:
        return (_NEMOTRON_ULTRA,)
    if percent >= _FLAGSHIP_CUTOFF_PERCENT:
        return (_GROK_46, _DS_FLASH, _GEMINI_37, _NEMOTRON_ULTRA)
    return ()


def normalize_usage_mode(value: Any) -> str:
    text = str(value or "normal").strip().lower()
    return text if text in USAGE_MODES else "normal"


def set_live_usage_mode(mode: str | None) -> None:
    global _live_mode
    _live_mode = normalize_usage_mode(mode) if mode else None


def set_live_usage_percent(percent: int | None) -> None:
    global _live_percent
    if percent is None:
        _live_percent = None
        return
    try:
        _live_percent = min(100, max(0, int(percent)))
    except (TypeError, ValueError):
        _live_percent = None


def clear_live_usage_mode() -> None:
    global _live_mode, _live_percent
    _live_mode = None
    _live_percent = None


def current_usage_mode(config: Any) -> str:
    if _live_mode is not None:
        return _live_mode
    lic = getattr(config, "license", None)
    return normalize_usage_mode(getattr(lic, "usage_mode", "normal") if lic else "normal")


def usage_mode_from_ratio(spent: float, budget: float) -> str:
    """Mirror site ``usageMode`` for tests / offline estimation."""
    if budget <= 0:
        return "exhausted"
    ratio = spent / budget
    if ratio >= 1:
        return "exhausted"
    if ratio >= USAGE_THRESHOLDS["economy"]:
        return "economy"
    if ratio >= USAGE_THRESHOLDS["reduced"]:
        return "reduced"
    return "normal"


def effective_mode_from_usage(usage: Mapping[str, Any] | None) -> str:
    if not isinstance(usage, dict):
        return "normal"
    mode = normalize_usage_mode(usage.get("mode"))
    # Daily anti-abuse cap: never stay on full expert burn for the day.
    if usage.get("dailyCapReached"):
        if _mode_rank(mode) < _mode_rank("economy"):
            return "economy"
    return mode


def _mode_rank(mode: str) -> int:
    try:
        return USAGE_MODES.index(normalize_usage_mode(mode))
    except ValueError:
        return 0


def apply_usage_summary(config: Any, usage: Mapping[str, Any] | None) -> bool:
    """Persist usage mode + budget snapshot on config.license.

    Returns True when any stored field changed. The snapshot mirrors
    ``UsageSummary`` from navin.live so the IDE Account page can show the
    same bar as the dashboard.
    """
    mode = effective_mode_from_usage(usage)
    set_live_usage_mode(mode)
    lic = getattr(config, "license", None)
    if lic is None:
        return False

    changed = False
    if getattr(lic, "usage_mode", "normal") != mode:
        lic.usage_mode = mode
        changed = True
        logger.info("Soft budget mode → {}", mode)

    if not isinstance(usage, dict):
        return changed

    def _int(key: str, *aliases: str) -> int | None:
        for name in (key, *aliases):
            raw = usage.get(name)
            if raw is None:
                continue
            try:
                return max(0, int(raw))
            except (TypeError, ValueError):
                continue
        return None

    budget = _int("budgetMicroUsd", "budget_micro_usd")
    spent = _int("spentMicroUsd", "spent_micro_usd")
    percent = _int("usedPercent", "used_percent")
    remaining_tokens = _int(
        "remainingEquivalentTokens",
        "remaining_equivalent_tokens",
        "usageRemainingTokens",
    )

    if budget is not None and getattr(lic, "usage_budget_micro_usd", 0) != budget:
        lic.usage_budget_micro_usd = budget
        changed = True
    if spent is not None and getattr(lic, "usage_spent_micro_usd", 0) != spent:
        lic.usage_spent_micro_usd = spent
        changed = True
    if percent is not None:
        clamped = min(100, percent)
        set_live_usage_percent(clamped)
        if getattr(lic, "usage_used_percent", 0) != clamped:
            lic.usage_used_percent = clamped
            changed = True
    elif budget is not None and budget > 0 and spent is not None:
        clamped = min(100, round((spent / budget) * 100))
        set_live_usage_percent(clamped)
        if getattr(lic, "usage_used_percent", 0) != clamped:
            lic.usage_used_percent = clamped
            changed = True
    if remaining_tokens is not None and getattr(lic, "usage_remaining_tokens", 0) != remaining_tokens:
        lic.usage_remaining_tokens = remaining_tokens
        changed = True

    return changed


def apply_period_end_from_response(config: Any, body: dict[str, Any] | None) -> bool:
    """Persist ``expiresAt`` (unix seconds) from validate onto license.period_end."""
    if not isinstance(body, dict):
        return False
    lic = getattr(config, "license", None)
    if lic is None:
        return False
    raw = body.get("expiresAt", body.get("expires_at"))
    try:
        value = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return False
    if value < 0:
        value = 0
    if getattr(lic, "period_end", 0) == value:
        return False
    lic.period_end = value
    return True


def apply_usage_from_response(config: Any, body: dict[str, Any] | None) -> bool:
    """Read ``usage`` from a validate / report payload and store the mode."""
    if not isinstance(body, dict):
        return False
    usage = body.get("usage")
    if usage is None:
        # Valid free plan / no budget: stay normal (BYOK path).
        return False
    return apply_usage_summary(config, usage if isinstance(usage, dict) else None)


def clamp_preset_for_mode(
    preset: str | None,
    mode: str,
    known_presets: Mapping[str, Any] | None = None,
) -> str | None:
    """Downgrade a catalog tier preset when the soft budget requires it.

    Non-tier presets (user-named / ``default``) are left alone - we only
    rewrite the managed catalog names ``light`` / ``executor`` / ``main`` /
    ``expert``.
    """
    mode = normalize_usage_mode(mode)
    if mode == "normal" or not preset:
        return preset
    name = str(preset).strip()
    if name not in _TIER_RANK:
        return preset
    cap = _MODE_TIER_CAP[mode]
    if _TIER_RANK[name] <= _TIER_RANK[cap]:
        return name
    # Walk down to the highest allowed tier that exists in config.
    known = set((known_presets or {}).keys()) | set(TIERS)
    for tier in reversed(TIERS[: _TIER_RANK[cap] + 1]):
        if tier in known or not known_presets:
            return tier
    return cap


def _clamp_candidates(
    clamped: str,
    known_presets: Mapping[str, Any] | None,
) -> list[str]:
    """The clamp target followed by every lower tier that exists in config."""
    if clamped not in _TIER_RANK:
        return [clamped]
    known = set((known_presets or {}).keys()) or set(TIERS)
    return [
        tier
        for tier in reversed(TIERS[: _TIER_RANK[clamped] + 1])
        if tier in known
    ]


def max_tokens_for_mode(
    mode: str,
    current: int | None,
    *,
    plan_output_cap: int = 0,
) -> int | None:
    """Return a capped max_tokens, or None when no change is needed."""
    mode = normalize_usage_mode(mode)
    soft = _MODE_TOKEN_FALLBACK.get(mode, 0)
    if mode == "reduced" and plan_output_cap > 0:
        soft = max(1_024, plan_output_cap // 2)
    elif mode in ("economy", "exhausted") and plan_output_cap > 0:
        soft = min(soft or plan_output_cap, max(1_024, plan_output_cap // 4))
    if soft <= 0:
        return None
    if current is None or current <= 0:
        return soft
    if current <= soft:
        return None
    return soft


def _preset_provider(preset: Any) -> str:
    provider = getattr(preset, "provider", None)
    if isinstance(preset, Mapping):
        provider = preset.get("provider", provider)
    return str(provider or "").strip().lower()


def _preset_is_managed(name: str, preset: Any) -> bool:
    """True for Navin catalog presets. BYOK / custom providers are never this."""
    if str(name) in TIERS:
        return True
    return _preset_provider(preset) == "navin"


def runtime_uses_managed_key(runtime: Any, config: Any) -> bool:
    """True only when this turn's provider is billed on the Navin managed key.

    A Plus account that also has Anthropic/OpenAI/OpenRouter BYOK must keep
    those turns untouched: they are at the user's expense.
    """
    from navin.config.secrets import unlocked_secret

    lic = getattr(config, "license", None) if config is not None else None
    managed_key = unlocked_secret(getattr(lic, "managed_api_key", None))
    if not managed_key:
        return False
    runtime_key = unlocked_secret(provider_api_key(getattr(runtime, "provider", None)))
    if runtime_key:
        return runtime_key == managed_key
    # No key on this runtime: do not fall back to "the account has a
    # managed key". That reclassified BYOK turns as plan quota.
    return False


def provider_api_key(provider: Any) -> str | None:
    """The key a provider signs its calls with, seen through failover wrappers.

    A FallbackProvider keeps the chosen model's provider on ``_primary``; an
    older wrapper without a delegating ``api_key`` would otherwise make every
    managed turn look keyless, i.e. billed to the user.
    """
    current = provider
    for _ in range(3):
        if current is None:
            return None
        key = getattr(current, "api_key", None)
        if isinstance(key, str) and key:
            return key
        current = getattr(current, "_primary", None)
    return None


def _runtime_uses_managed_key(runtime: Any, config: Any) -> bool:
    return runtime_uses_managed_key(runtime, config)


def _preset_model(preset: Any) -> str:
    model = getattr(preset, "model", None)
    if isinstance(preset, Mapping):
        model = preset.get("model", model)
    return str(model or "").strip()


def _preset_name_for_slug(slug: str, known_presets: Mapping[str, Any] | None) -> str | None:
    from navin.providers.managed_catalog import slug_preset_key

    if known_presets:
        managed_hit: str | None = None
        other_hit: str | None = None
        for name, preset in known_presets.items():
            if _preset_model(preset) != slug:
                continue
            if _preset_is_managed(str(name), preset):
                managed_hit = str(name)
                break
            if other_hit is None:
                other_hit = str(name)
        if managed_hit:
            return managed_hit
        key = slug_preset_key(slug)
        if key in known_presets and _preset_is_managed(key, known_presets[key]):
            return key
        return None
    return slug_preset_key(slug)


def _budget_preset_names(
    known_presets: Mapping[str, Any] | None,
    percent: int,
    current_slug: str,
) -> list[str]:
    """Preset names to try, preferred allowlist slugs first then any leftover."""
    from navin.providers.managed_catalog import slug_preset_key

    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str | None) -> None:
        key = (name or "").strip()
        if key and key not in seen:
            seen.add(key)
            names.append(key)

    for slug in preferred_budget_slugs(percent):
        if not slug or slug == current_slug:
            continue
        name = _preset_name_for_slug(slug, known_presets)
        if name:
            if known_presets:
                preset = known_presets.get(name)
                if preset is not None and not _preset_is_managed(name, preset):
                    continue
            _add(name)
        elif not known_presets:
            _add(slug_preset_key(slug))
    if known_presets:
        for name, preset in known_presets.items():
            if not _preset_is_managed(str(name), preset):
                continue
            model = _preset_model(preset)
            if not model or model == current_slug:
                continue
            if slug_allowed_for_budget(model, percent):
                _add(str(name))
    return names


def _resolve_budget_runtime(
    resolve_preset: Any,
    known_presets: Mapping[str, Any] | None,
    percent: int,
    current_slug: str,
) -> Any | None:
    """Pick the first remaining allowlist slug that resolves as a chat runtime."""
    for name in _budget_preset_names(known_presets, percent, current_slug):
        try:
            runtime = resolve_preset(name)
        except Exception:
            logger.debug("Soft budget preset {} unusable for chat", name)
            continue
        if runtime is None:
            continue
        slug = str(getattr(runtime, "model", "") or "")
        if slug and not slug_allowed_for_budget(slug, percent):
            continue
        logger.info("Soft budget {}% → {}", percent, slug or name)
        return runtime
    return None


def apply_usage_mode_to_runtime(
    runtime: Any,
    config: Any,
    *,
    resolve_preset: Any | None = None,
    known_presets: Mapping[str, Any] | None = None,
    uses_managed: bool | None = None,
    user_pinned: bool = False,
) -> Any:
    """Clamp a resolved LLMRuntime for the current soft-budget mode.

    ``resolve_preset`` is ``ModelRuntimeResolver.resolve_preset`` when a tier
    downgrade is required. No-op for BYOK / normal mode.

    ``user_pinned`` is honoured unless the pin is a paused flagship
    (Opus 5+, Fable 5+, GPT 5.6). Grok and DeepSeek stay. BYOK /
    third-party keys are never clamped, even on a paid Navin account:
    those turns are billed to the user, not Navin.
    """
    if uses_managed is None:
        try:
            from navin.license_client import uses_managed_key
        except ImportError:
            return runtime

        uses_managed = uses_managed_key(config) and _runtime_uses_managed_key(
            runtime, config
        )
    if not uses_managed:
        return runtime

    mode = current_usage_mode(config)
    percent = budget_used_percent(config, mode)
    if mode == "normal" and percent < _FLAGSHIP_CUTOFF_PERCENT:
        return runtime
    current_slug = str(getattr(runtime, "model", "") or "")
    presets = known_presets
    if presets is None and resolve_preset is not None:
        owner = getattr(resolve_preset, "__self__", None)
        presets = getattr(owner, "model_presets", None)

    # Pause only the expensive flagships. A pin on Grok stays; a pin on
    # Opus 5 does not.
    must_swap = percent >= _FLAGSHIP_CUTOFF_PERCENT and not slug_allowed_for_budget(
        current_slug, percent
    )
    if must_swap:
        user_pinned = False
    swapped_by_slug = False
    if must_swap and resolve_preset is not None:
        swapped = _resolve_budget_runtime(
            resolve_preset, presets, percent, current_slug
        )
        if swapped is not None:
            runtime = swapped
            swapped_by_slug = True
        else:
            logger.warning(
                "Soft budget {}% could not swap off {}", percent, current_slug
            )

    live_slug = str(getattr(runtime, "model", "") or current_slug)
    keep_slug = bool(live_slug) and slug_allowed_for_budget(live_slug, percent)
    preset = getattr(runtime, "model_preset", None)
    clamped = None
    if not user_pinned and not swapped_by_slug and not keep_slug:
        clamped = clamp_preset_for_mode(preset, mode, presets)
    if clamped and clamped != preset and resolve_preset is not None:
        # A tier preset can be corrupted (a degraded catalog once mapped
        # 'executor' to an STT model) and then fail to resolve as a chat
        # runtime. Walk further down the tier ladder instead of silently
        # keeping the expensive model the budget was meant to protect.
        for candidate in _clamp_candidates(clamped, presets):
            try:
                runtime = resolve_preset(candidate)
                if candidate != clamped:
                    logger.warning(
                        "Soft budget tier {} unusable for chat; clamped to {}",
                        clamped,
                        candidate,
                    )
                break
            except Exception:
                logger.debug("Soft budget preset clamp skipped for {}", candidate)

    plan_cap = int(getattr(getattr(config, "license", None), "output_tokens_per_call", 0) or 0)
    current_max = getattr(getattr(runtime, "generation", None), "max_tokens", None)
    capped = max_tokens_for_mode(mode, current_max, plan_output_cap=plan_cap)
    if capped is not None and hasattr(runtime, "with_generation_overrides"):
        runtime = runtime.with_generation_overrides(max_tokens=capped)
    return runtime
