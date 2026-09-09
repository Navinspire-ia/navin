# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Create LLM providers from config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from navin.config.schema import Config, InlineFallbackConfig, ModelPresetConfig, ProviderConfig
from navin.providers.base import GenerationSettings, LLMProvider
from navin.providers.fallback_provider import FallbackProvider
from navin.providers.registry import ProviderSpec, create_dynamic_spec, find_by_name


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: LLMProvider
    model: str
    context_window_tokens: int
    signature: tuple[object, ...]
    generation: GenerationSettings | None = None


def _resolve_model_preset(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> ModelPresetConfig:
    return preset if preset is not None else config.resolve_preset(preset_name)


def _provider_extra_headers(
    spec: ProviderSpec | None,
    provider_config: ProviderConfig | None,
) -> dict[str, str] | None:
    headers = dict(spec.default_extra_headers) if spec else {}
    if provider_config and provider_config.extra_headers:
        headers.update(provider_config.extra_headers)
    return headers or None


@dataclass(frozen=True)
class _ResolvedBackend:
    """Everything a preset needs before a provider object can be built."""

    resolved: ModelPresetConfig
    model: str
    provider_name: str | None
    provider_config: ProviderConfig | None
    spec: ProviderSpec | None
    backend: str
    api_key: str | None


def _resolve_backend(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
) -> _ResolvedBackend:
    """Resolve provider, backend and credentials for a preset.

    Raises ``ValueError`` with the same messages the provider constructor used
    to raise, so both ``make_provider`` and the automatic failover chain agree
    on which presets are usable right now.
    """
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    model = (model or resolved.model).strip()
    if not model:
        # Auto-detection would otherwise fall back to the first provider holding a
        # key and send it an empty model name.
        raise ValueError(
            "No model is configured. Pick a provider and a model in "
            "Settings > Models, or set agents.defaults.model in config.json."
        )
    provider_name = config.get_provider_name(model, preset=resolved)
    p = config.get_provider(model, preset=resolved)
    spec = find_by_name(provider_name) if provider_name else None
    if provider_name and not spec and p:
        if not p.api_base:
            raise ValueError(f"Provider '{provider_name}' requires api_base in config.")
        spec = create_dynamic_spec(provider_name, thinking_style=(p.thinking_style or "") if p else "")
    if spec and spec.is_transcription_only:
        raise ValueError(f"Provider '{provider_name}' only supports transcription.")
    backend = spec.backend if spec else "openai_compat"
    if p and getattr(p, "wire_protocol", None) == "anthropic":
        backend = "anthropic"
    if p and p.proxy and backend not in {"openai_compat", "openai_codex", "xai_oauth"}:
        raise ValueError(
            f"providers.{provider_name}.proxy is only supported for "
            "OpenAI-compatible providers, OpenAI Codex, and Grok (x.ai subscription)."
        )

    if backend == "azure_openai":
        if not p or not p.api_base:
            raise ValueError("Azure OpenAI requires api_base in config.")
    elif (
        spec
        and spec.is_direct
        and spec.backend in {"openai_compat", "anthropic"}
        and not spec.default_api_base
        and not (p and p.api_base)
    ):
        raise ValueError(f"Provider '{provider_name}' requires api_base in config.")
    elif (
        backend == "openai_compat"
        and spec
        and spec.is_local
        and not (spec.default_api_base and spec.route_via_default_base)
        and not (p and p.api_base)
    ):
        raise ValueError(f"Provider '{provider_name}' requires api_base in config.")
    # The resolved key: from config, or from the environment (env_key and its
    # aliases, e.g. KIMI_API_KEY for Moonshot) when the config carries none.
    from navin.config.secrets import unlocked_secret

    api_key = unlocked_secret(p.api_key if p else None) or config.get_api_key(
        model, preset=resolved
    )

    if backend == "openai_compat" and not model.startswith("bedrock/"):
        needs_key = not api_key
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            raise ValueError(
                f"No API key configured for provider '{provider_name}'. "
                f"Set it in Settings > Providers or export "
                f"{spec.env_key if spec and spec.env_key else 'its API key variable'} "
                "in the environment."
            )
    return _ResolvedBackend(
        resolved=resolved,
        model=model,
        provider_name=provider_name,
        provider_config=p,
        spec=spec,
        backend=backend,
        api_key=api_key,
    )


def _preset_is_usable(config: Config, preset: ModelPresetConfig) -> bool:
    """True when a provider can be built for *preset* with the current config.

    Stricter than ``make_provider`` on one point: an Anthropic-wire preset
    without a key is constructible but only ever answers 401, so it is not a
    fallback worth a call.
    """
    try:
        target = _resolve_backend(config, preset=preset)
    except (ValueError, KeyError, TypeError):
        return False
    if target.backend == "anthropic" and not target.api_key:
        spec = target.spec
        if not (spec and (spec.is_direct or spec.is_oauth)):
            return False
    return True


def _make_provider_core(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Create a plain LLM provider without failover wrapping."""
    target = _resolve_backend(config, preset_name=preset_name, preset=preset, model=model)
    resolved = target.resolved
    model = target.model
    provider_name = target.provider_name
    p = target.provider_config
    spec = target.spec
    backend = target.backend
    api_key = target.api_key

    if backend == "openai_codex":
        from navin.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(
            default_model=model,
            proxy=getattr(p, "proxy", None) if p else None,
            extra_body=p.extra_body if p else None,
        )
    elif backend == "azure_openai":
        from navin.providers.azure_openai_provider import AzureOpenAIProvider

        provider = AzureOpenAIProvider(
            api_key=api_key or "",
            api_base=p.api_base,
            default_model=model,
        )
    elif backend == "github_copilot":
        from navin.providers.github_copilot_provider import GitHubCopilotProvider

        provider = GitHubCopilotProvider(default_model=model)
    elif backend == "xai_oauth":
        from navin.providers.xai_oauth_provider import XaiOAuthProvider

        provider = XaiOAuthProvider(
            default_model=model,
            proxy=getattr(p, "proxy", None) if p else None,
        )
    elif backend == "anthropic":
        from navin.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=api_key or ("no-key" if spec and spec.is_direct else None),
            api_base=config.get_api_base(model, preset=resolved),
            default_model=model,
            extra_headers=_provider_extra_headers(spec, p),
        )
    elif backend == "bedrock":
        from navin.providers.bedrock_provider import BedrockProvider

        provider = BedrockProvider(
            api_key=api_key,
            api_base=p.api_base if p else None,
            default_model=model,
            region=getattr(p, "region", None) if p else None,
            profile=getattr(p, "profile", None) if p else None,
            extra_body=p.extra_body if p else None,
        )
    else:
        from navin.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=api_key,
            api_base=config.get_api_base(model, preset=resolved),
            default_model=model,
            extra_headers=_provider_extra_headers(spec, p),
            spec=spec,
            extra_body=p.extra_body if p else None,
            api_type=p.api_type if p and provider_name == "openai" else "auto",
            extra_query=p.extra_query if p else None,
            proxy=p.proxy if p else None,
        )

    provider.generation = resolved.to_generation_settings()
    return provider


def _inline_fallback_preset(
    primary: ModelPresetConfig,
    fallback: InlineFallbackConfig,
) -> ModelPresetConfig:
    return ModelPresetConfig(
        model=fallback.model,
        provider=fallback.provider,
        max_tokens=fallback.max_tokens if fallback.max_tokens is not None else primary.max_tokens,
        context_window_tokens=(
            fallback.context_window_tokens
            if fallback.context_window_tokens is not None
            else primary.context_window_tokens
        ),
        temperature=(
            fallback.temperature if fallback.temperature is not None else primary.temperature
        ),
        reasoning_effort=fallback.reasoning_effort,
    )


# Bound the automatic failover chains: each extra candidate adds retry
# latency before the user finally sees an error.
_MAX_AUTO_FREE_FALLBACKS = 3
_MAX_AUTO_PRESET_FALLBACKS = 5


def _catalog_slugs(*, free: bool) -> list[str]:
    """Slugs from the embedded managed catalog (root models)."""
    from navin.providers.managed_catalog import FALLBACK_CATALOG_PAYLOAD

    slugs: list[str] = []
    models = FALLBACK_CATALOG_PAYLOAD.get("models")
    if isinstance(models, list):
        for row in models:
            if isinstance(row, dict):
                slug = row.get("slug")
                if isinstance(slug, str) and slug.endswith(":free") == free:
                    slugs.append(slug)
    return slugs


def _bundled_free_slugs() -> list[str]:
    """Free-tier slugs from the embedded managed catalog (root models)."""
    return _catalog_slugs(free=True)


def _bundled_managed_slugs() -> list[str]:
    """Paid catalog slugs used when a Navin subscription model saturates."""
    return _catalog_slugs(free=False)


def _is_chat_preset(preset: ModelPresetConfig) -> bool:
    if (getattr(preset, "modality", "text") or "text") != "text":
        return False
    return preset.enabled is not False


def _auto_free_fallback_presets(
    config: Config,
    primary: ModelPresetConfig,
    *,
    model: str | None = None,
) -> list[ModelPresetConfig]:
    """Failover chain for free-tier models when none is configured.

    Free OpenRouter models saturate routinely (429 / at capacity). Instead of
    surfacing a hard error after the retry budget, chain the other known free
    models so the turn transparently lands on whichever one has capacity.
    Only kicks in for ``:free`` primaries; paid/BYOK model choices are never
    silently substituted.
    """
    active = ((model or primary.model) or "").strip()
    if not active.endswith(":free"):
        return []

    from navin.providers.managed_catalog import FREE_VISION_MODEL

    candidates: list[tuple[str, str]] = []
    for preset in config.model_presets.values():
        if not _is_chat_preset(preset):
            continue
        slug = (preset.model or "").strip()
        if slug.endswith(":free"):
            candidates.append((slug, preset.provider))
    if not candidates:
        candidates = [(slug, primary.provider) for slug in _bundled_free_slugs()]

    # The vision model is routed for image/video analysis, not chat failover.
    seen = {active, FREE_VISION_MODEL}
    chain: list[ModelPresetConfig] = []
    for slug, provider in candidates:
        if slug in seen:
            continue
        seen.add(slug)
        chain.append(
            ModelPresetConfig(
                model=slug,
                provider=provider,
                max_tokens=primary.max_tokens,
                context_window_tokens=primary.context_window_tokens,
                temperature=primary.temperature,
            )
        )
        if len(chain) >= _MAX_AUTO_FREE_FALLBACKS:
            break
    return chain


def _vendor_key(provider: str | None, slug: str) -> tuple[str, str]:
    """Group presets by who actually serves them.

    On a gateway (OpenRouter, the Navin subscription) the slug prefix names the
    upstream vendor: ``z-ai/glm-5.3-flash`` and ``z-ai/glm-5.2`` go down
    together, so the chain should not line them up back to back.
    """
    vendor = slug.split("/", 1)[0].lower() if "/" in slug else ""
    return ((provider or "").strip().lower(), vendor)


def _interleave_by_vendor(
    entries: list[tuple[str, ModelPresetConfig]],
) -> list[tuple[str, ModelPresetConfig]]:
    """Round-robin the catalog across vendors, keeping each vendor's own order."""
    buckets: dict[tuple[str, str], list[tuple[str, ModelPresetConfig]]] = {}
    for name, preset in entries:
        buckets.setdefault(_vendor_key(preset.provider, preset.model), []).append((name, preset))
    out: list[tuple[str, ModelPresetConfig]] = []
    queues = list(buckets.values())
    while queues:
        next_round: list[list[tuple[str, ModelPresetConfig]]] = []
        for queue in queues:
            out.append(queue.pop(0))
            if queue:
                next_round.append(queue)
        queues = next_round
    return out


def _auto_preset_fallback_presets(
    config: Config,
    primary: ModelPresetConfig,
    *,
    model: str | None = None,
) -> list[ModelPresetConfig]:
    """Failover chain from the other configured chat models when none is set.

    The model list is the fallback list. When the chosen model blocks (rate
    limit, outage, expired key, dropped slug, refusal), the step continues on
    another configured text model whose provider holds credentials, and the
    switch is announced. Order: the default preset, then the presets the
    routes name (the operator's explicit picks), then the rest of the catalog
    alternating vendors so a vendor-wide outage is not tried three times in a
    row. Free-tier slugs come last. The chain is capped: a total outage should
    cost a handful of calls, not a tour of thirty presets.

    Every entry carries the primary's generation settings and context window,
    so the chain never shrinks the window the turn was planned against.
    """
    active = ((model or primary.model) or "").strip()
    if not active or active.endswith(":free"):
        return []

    from navin.providers.managed_catalog import FREE_VISION_MODEL

    ordered: list[tuple[str, ModelPresetConfig]] = []
    taken: set[str] = set()

    def _take(name: str | None) -> None:
        if not name or name in taken:
            return
        preset = config.model_presets.get(name)
        if preset is None:
            return
        taken.add(name)
        ordered.append((name, preset))

    _take(config.agents.defaults.model_preset)
    for route_target in config.model_routes.values():
        _take(route_target)
    rest = [
        (name, preset)
        for name, preset in config.model_presets.items()
        if name not in taken and _is_chat_preset(preset) and (preset.model or "").strip()
    ]
    ordered.extend(_interleave_by_vendor(rest))

    seen = {active, FREE_VISION_MODEL}
    paid: list[ModelPresetConfig] = []
    free: list[ModelPresetConfig] = []
    for _name, preset in ordered:
        if not _is_chat_preset(preset):
            continue
        slug = (preset.model or "").strip()
        if not slug or slug in seen:
            continue
        if not _preset_is_usable(config, preset):
            continue
        seen.add(slug)
        candidate = ModelPresetConfig(
            model=slug,
            provider=preset.provider or primary.provider,
            max_tokens=primary.max_tokens,
            context_window_tokens=primary.context_window_tokens,
            temperature=primary.temperature,
        )
        (free if slug.endswith(":free") else paid).append(candidate)
        if len(paid) >= _MAX_AUTO_PRESET_FALLBACKS:
            break

    chain = (paid + free)[:_MAX_AUTO_PRESET_FALLBACKS]
    if chain or (primary.provider or "").strip().lower() != "navin":
        return chain

    # A subscription with an empty catalog still has the bundled managed
    # models on the same key.
    for slug in _bundled_managed_slugs():
        if slug in seen:
            continue
        seen.add(slug)
        chain.append(
            ModelPresetConfig(
                model=slug,
                provider=primary.provider,
                max_tokens=primary.max_tokens,
                context_window_tokens=primary.context_window_tokens,
                temperature=primary.temperature,
            )
        )
        if len(chain) >= _MAX_AUTO_PRESET_FALLBACKS:
            break
    return chain


def _configured_fallback_presets(
    config: Config, primary: ModelPresetConfig
) -> list[ModelPresetConfig]:
    presets: list[ModelPresetConfig] = []
    for fallback in config.agents.defaults.fallback_models:
        if isinstance(fallback, str):
            presets.append(config.model_presets[fallback])
        else:
            presets.append(_inline_fallback_preset(primary, fallback))
    return presets


def _resolve_fallback_presets(
    config: Config,
    primary: ModelPresetConfig,
    *,
    model: str | None = None,
) -> list[ModelPresetConfig]:
    """The models tried, in order, when the chosen one blocks.

    An explicit ``fallback_models`` list wins. Otherwise the list of configured
    presets is the fallback list: free primaries hop across the other free
    slugs, every other primary continues on the other usable text presets.
    """
    presets = _configured_fallback_presets(config, primary)
    if not presets:
        presets = _auto_free_fallback_presets(config, primary, model=model)
    if not presets:
        presets = _auto_preset_fallback_presets(config, primary, model=model)
    return presets


def make_provider(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
    model: str | None = None,
) -> LLMProvider:
    """Create the LLM provider implied by config.

    When *model* is given, it overrides the resolved/preset model - used by
    the failover path to create providers for fallback models.
    """
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    provider = _make_provider_core(config, preset_name=preset_name, preset=preset, model=model)
    fallback_presets = _resolve_fallback_presets(config, resolved, model=model)

    if fallback_presets:
        # The automatic free-tier chain exists precisely to hop on rate limits, so
        # waiting there would only slow every turn down. A configured fallback backs
        # up a model the operator chose, and that choice is worth retrying for.
        from navin.providers.fallback_provider import PRIMARY_STICKY_RETRIES
        from navin.providers.model_switch_notice import notify_model_switch

        # Free :free pools are built to hop immediately. A model the subscriber
        # picked (DeepSeek Flash, Gemini, ...) deserves a short retry first.
        active = ((model or resolved.model) or "").strip()
        is_auto_free_chain = (
            not _configured_fallback_presets(config, resolved)
            and active.endswith(":free")
        )
        provider = FallbackProvider(
            primary=provider,
            fallback_presets=fallback_presets,
            provider_factory=lambda fb: _make_provider_core(
                config, preset_name=preset_name, preset=fb
            ),
            on_model_switch=notify_model_switch,
            sticky_retries=0 if is_auto_free_chain else PRIMARY_STICKY_RETRIES,
        )

    return provider


def provider_signature(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> tuple[object, ...]:
    """Return the config fields that affect the active provider chain."""
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    p = config.get_provider(resolved.model, preset=resolved)
    fallback_presets = _resolve_fallback_presets(config, resolved)

    def _fallback_signature(fallback: ModelPresetConfig) -> tuple[object, ...]:
        fp = config.get_provider(fallback.model, preset=fallback)
        provider_name = config.get_provider_name(fallback.model, preset=fallback)
        return (
            fallback.model,
            fallback.provider,
            provider_name,
            config.get_api_key(fallback.model, preset=fallback),
            config.get_api_base(fallback.model, preset=fallback),
            _provider_extra_headers(find_by_name(provider_name) if provider_name else None, fp),
            fp.extra_body if fp else None,
            fp.api_type if fp else "auto",
            fp.extra_query if fp else None,
            getattr(fp, "region", None) if fp else None,
            getattr(fp, "profile", None) if fp else None,
            fallback.max_tokens,
            fallback.temperature,
            fallback.reasoning_effort,
            fallback.context_window_tokens,
            getattr(fp, "proxy", None) if fp else None,
        )

    provider_name = config.get_provider_name(resolved.model, preset=resolved)
    return (
        resolved.model,
        resolved.provider,
        provider_name,
        config.get_api_key(resolved.model, preset=resolved),
        config.get_api_base(resolved.model, preset=resolved),
        _provider_extra_headers(find_by_name(provider_name) if provider_name else None, p),
        p.extra_body if p else None,
        p.api_type if p else "auto",
        p.extra_query if p else None,
        getattr(p, "region", None) if p else None,
        getattr(p, "profile", None) if p else None,
        resolved.max_tokens,
        resolved.temperature,
        resolved.reasoning_effort,
        resolved.context_window_tokens,
        getattr(p, "proxy", None) if p else None,
        tuple(_fallback_signature(fallback) for fallback in fallback_presets),
    )


def build_provider_snapshot(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> ProviderSnapshot:
    resolved = _resolve_model_preset(config, preset_name=preset_name, preset=preset)
    fallback_windows = [
        fallback.context_window_tokens
        for fallback in _resolve_fallback_presets(config, resolved)
    ]
    return ProviderSnapshot(
        provider=make_provider(config, preset=resolved),
        model=resolved.model,
        context_window_tokens=min([resolved.context_window_tokens, *fallback_windows]),
        signature=provider_signature(config, preset=resolved),
        generation=resolved.to_generation_settings(),
    )


def unconfigured_provider_snapshot(reason: str = "") -> ProviderSnapshot:
    """Snapshot used when the gateway may start before provider setup."""
    from navin.providers.unconfigured import UNCONFIGURED_MODEL, UnconfiguredProvider

    return ProviderSnapshot(
        provider=UnconfiguredProvider(reason=reason),
        model=UNCONFIGURED_MODEL,
        context_window_tokens=200_000,
        signature=("unconfigured", reason),
        generation=None,
    )


def build_provider_snapshot_allowing_unconfigured(
    config: Config,
    *,
    preset_name: str | None = None,
    preset: ModelPresetConfig | None = None,
) -> ProviderSnapshot:
    """Build a real provider snapshot, or a setup placeholder on config errors."""
    try:
        return build_provider_snapshot(config, preset_name=preset_name, preset=preset)
    except ValueError as exc:
        return unconfigured_provider_snapshot(str(exc))


def load_provider_snapshot(
    config_path: Path | None = None,
    *,
    preset_name: str | None = None,
) -> ProviderSnapshot:
    from navin.config.loader import load_config, resolve_config_env_vars

    return build_provider_snapshot(
        resolve_config_env_vars(load_config(config_path)),
        preset_name=preset_name,
    )


def load_provider_snapshot_allowing_unconfigured(
    config_path: Path | None = None,
    *,
    preset_name: str | None = None,
) -> ProviderSnapshot:
    """Reload config for runtime refresh; tolerate incomplete provider setup."""
    from navin.config.loader import load_config, resolve_config_env_vars

    return build_provider_snapshot_allowing_unconfigured(
        resolve_config_env_vars(load_config(config_path)),
        preset_name=preset_name,
    )
