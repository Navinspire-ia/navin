# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenRouter OAuth PKCE connect flow (Free onboarding path).

OpenRouter has no public API that would let Navin silently create an
OpenRouter account and key for an anonymous user; the supported route is
their OAuth PKCE flow, which also has the billing shape we want - the key
belongs to the USER's OpenRouter account, so free models cost them nothing
and paid models bill them, never us. (The site's Management API is the
opposite: sub-keys attached to OUR account, our bill; that one stays
reserved for paid managed plans.)

The handoff:

1. the WebUI asks ``/api/webui/openrouter/connect-url``; the gateway mints a
   PKCE verifier + S256 challenge and a one-time ``state`` nonce, then opens
   ``https://openrouter.ai/auth?callback_url=...`` in the OS browser;
2. the user signs in (or creates an OpenRouter account) and authorizes Navin;
3. OpenRouter redirects to the loopback ``/webui/openrouter/callback`` with a
   one-time ``code``;
4. the gateway exchanges ``code`` + verifier at ``POST /api/v1/auth/keys``
   for the user-owned API key, stores it as ``providers.openrouter.api_key``
   and installs the free default model presets.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote

from loguru import logger

from navin.config.loader import load_config, save_config
from navin.config.schema import ModelPresetConfig

_STATE_TTL_S = 15 * 60
_AUTH_URL = "https://openrouter.ai/auth"
_KEYS_URL = "https://openrouter.ai/api/v1/auth/keys"

# Installed on connect so the Free path lands on a working chat, not an empty
# model picker. Free-tier OpenRouter models; the user can add any other model
# from Settings > Models afterwards.
#
# Omni is multimodal (text / image / video analysis) and is wired as the
# ``vision`` task route when present - see install_free_presets.
FREE_VISION_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"

FREE_DEFAULT_MODELS: tuple[tuple[str, str], ...] = (
    # Nemotron Ultra remains the Free-path chat default.
    ("nvidia/nemotron-3-ultra-550b-a55b:free", "Nemotron 3 Ultra (free)"),
    ("nvidia/nemotron-3-super-120b-a12b:free", "Nemotron 3 Super (free)"),
    (FREE_VISION_MODEL, "Nemotron 3 Nano Omni (free)"),
    ("poolside/laguna-s-2.1:free", "Laguna S 2.1 (free)"),
)


class OpenRouterOAuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message = message
        self.status = status
        super().__init__(message)


def _preset_slug(model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return slug or "openrouter-free"


def normalize_site_lang(lang: str) -> str:
    """Locale prefix accepted by the navin.live confirmation page."""
    value = (lang or "").strip().lower()[:2]
    return value if value in ("en", "fr", "ar") else ""


def finish_redirect_url(*, ok: bool, lang: str = "") -> str:
    """Site confirmation page shown after the OpenRouter handoff.

    The browser lands on the loopback callback; redirecting it to navin.live
    keeps the raw 127.0.0.1 URL out of the user's face (same spirit as the
    account handoff, which never leaves the site at all).
    """
    from navin import license_client
    from navin.config.loader import load_config as _load_config

    base = license_client.server_url(_load_config()).rstrip("/")
    locale = normalize_site_lang(lang) or "en"
    status = "1" if ok else "0"
    return f"{base}/{locale}/connect/openrouter?ok={status}"


def _free_default_preset_name(config: Any) -> str | None:
    """Preset name for Nemotron 3 Ultra (free) when that preset is installed."""
    first_model = FREE_DEFAULT_MODELS[0][0]
    return next(
        (
            name
            for name, preset in config.model_presets.items()
            if preset.provider == "openrouter" and preset.model == first_model
        ),
        None,
    )


def _should_activate_free_default(config: Any) -> bool:
    """True when Free path should pin Nemotron Ultra as the chat default.

    Activates when nothing is selected, or when the current selection is still
    one of the bundled free OpenRouter models (e.g. legacy Laguna default).
    Never steals a paid / custom model choice.
    """
    defaults = config.agents.defaults
    free_models = {model for model, _ in FREE_DEFAULT_MODELS}
    preset_name = (defaults.model_preset or "").strip()
    if preset_name and preset_name in config.model_presets:
        active = config.model_presets[preset_name]
        if active.provider == "openrouter" and active.model in free_models:
            return active.model != FREE_DEFAULT_MODELS[0][0]
        # Explicit non-free / non-openrouter preset - leave alone.
        return False
    raw_model = (defaults.model or "").strip()
    if raw_model:
        if raw_model in free_models:
            return raw_model != FREE_DEFAULT_MODELS[0][0]
        return False
    return True


def install_free_presets(config: Any) -> int:
    """Add the free default presets to *config*; returns how many were added.

    Idempotent for already-present models. Activates Nemotron 3 Ultra (free)
    as the chat default on the Free OpenRouter path (empty selection or another
    bundled free model). Does not hijack a paid / custom model choice.
    """
    base = config.resolve_default_preset()
    existing_models = {
        preset.model
        for preset in config.model_presets.values()
        if preset.provider == "openrouter"
    }
    added = 0
    for model, label in FREE_DEFAULT_MODELS:
        name = _preset_slug(model)
        if model in existing_models or name in config.model_presets:
            continue
        config.model_presets[name] = ModelPresetConfig(
            label=label,
            model=model,
            provider="openrouter",
            max_tokens=base.max_tokens,
            context_window_tokens=base.context_window_tokens,
            temperature=base.temperature,
            reasoning_effort=base.reasoning_effort,
        )
        added += 1
    defaults = config.agents.defaults
    if _should_activate_free_default(config):
        target = _free_default_preset_name(config)
        if target and defaults.model_preset != target:
            defaults.model_preset = target
            defaults.model = ""
            defaults.provider = "openrouter"
    # Point the vision task route at Omni whenever that preset exists, without
    # overwriting an explicit user choice that already targets a non-default.
    vision_preset = next(
        (
            name
            for name, preset in config.model_presets.items()
            if preset.provider == "openrouter" and preset.model == FREE_VISION_MODEL
        ),
        None,
    )
    if vision_preset:
        current_vision = (config.model_routes or {}).get("vision")
        if not current_vision or current_vision in {
            "default",
            "light",
            "executor",
            "main",
            "expert",
        }:
            if config.model_routes.get("vision") != vision_preset:
                config.model_routes["vision"] = vision_preset
    return added


class OpenRouterOAuthService:
    """Owns the pending PKCE verifier; one active flow at a time.

    The exchange is blocking HTTP - the routes run it in a thread. The lock
    only protects the in-memory pending state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[str, float]] = {}

    def connect_url(self, *, callback: str, lang: str = "") -> str:
        """Mint the OpenRouter auth URL for a fresh PKCE flow.

        *lang* rides inside the callback URL so the post-connect redirect can
        land on the right locale of the site's confirmation page.
        """
        verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(24)
        with self._lock:
            # A new "Connect OpenRouter" click invalidates the previous flow:
            # one pending verifier, never a pile of them.
            self._pending = {state: (verifier, time.time())}
        # OpenRouter's flow has no state parameter of its own; carrying our
        # nonce inside the callback URL gives the callback the same one-shot
        # guarantee as the account handoff.
        callback_with_state = f"{callback}?state={quote(state)}"
        clean_lang = normalize_site_lang(lang)
        if clean_lang:
            callback_with_state += f"&lang={clean_lang}"
        return (
            f"{_AUTH_URL}?callback_url={quote(callback_with_state, safe='')}"
            f"&code_challenge={challenge}&code_challenge_method=S256"
        )

    def take_verifier(self, state: str) -> str | None:
        """Consume the pending verifier; each connect URL works exactly once."""
        with self._lock:
            entry = self._pending.pop((state or "").strip(), None)
        if entry is None:
            return None
        verifier, issued = entry
        if time.time() - issued > _STATE_TTL_S:
            return None
        return verifier

    # -- blocking operations (call via asyncio.to_thread) --------------------

    def exchange_and_store(self, *, code: str, verifier: str) -> None:
        key = self._exchange(code=code, verifier=verifier)
        self.apply_user_key(key)

    def _exchange(self, *, code: str, verifier: str) -> str:
        import httpx

        try:
            response = httpx.post(
                _KEYS_URL,
                json={
                    "code": code,
                    "code_verifier": verifier,
                    "code_challenge_method": "S256",
                },
                timeout=20.0,
            )
        except Exception as exc:
            raise OpenRouterOAuthError(
                "Could not reach OpenRouter to finish the connection. "
                "Check your internet access and try again.",
                status=502,
            ) from exc
        if response.status_code >= 400:
            raise OpenRouterOAuthError(
                f"OpenRouter rejected the connect code (HTTP {response.status_code}). "
                "Start again from Navin.",
                status=502,
            )
        try:
            body = response.json() or {}
        except ValueError:
            body = {}
        key = str(body.get("key") or "").strip()
        if not key:
            raise OpenRouterOAuthError(
                "OpenRouter returned no API key. Start again from Navin.",
                status=502,
            )
        return key

    def apply_user_key(self, key: str) -> None:
        """Persist the user-owned key and the free default presets."""
        config = load_config()
        config.providers.openrouter.api_key = key
        # Session-scoped: account disconnect wipes OAuth-obtained keys so the
        # next person on this machine never inherits someone else's key.
        config.providers.openrouter.oauth_key = True
        added = install_free_presets(config)
        save_config(config)
        logger.info(
            "OpenRouter connected via OAuth PKCE ({} free preset(s) installed)",
            added,
        )

    def status_payload(self) -> dict[str, Any]:
        config = load_config()
        key = (config.providers.openrouter.api_key or "").strip()
        # Refresh free presets on status checks so already-connected Free users
        # pick up newly published free models (Super / Omni) without
        # having to reconnect OpenRouter.
        if key and config.providers.openrouter.oauth_key:
            added = install_free_presets(config)
            if added:
                save_config(config)
                logger.info(
                    "OpenRouter free presets refreshed ({} new)",
                    added,
                )
        return {
            "connected": bool(key),
            "free_models": [model for model, _ in FREE_DEFAULT_MODELS],
        }


def callback_html(*, ok: bool, detail: str = "") -> str:
    """Tiny page shown in the external browser after the OpenRouter handoff.

    Scrubs ``code`` / ``state`` from the address bar so the one-time code
    never lingers in history or screenshots.
    """
    if ok:
        title = "OpenRouter connected"
        body = "You can close this tab and return to Navin."
    else:
        title = "Connection failed"
        body = detail or "Please try again from Navin."
    safe_title = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{safe_title}</title>
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
<style>
  body {{ font-family: system-ui, sans-serif; background: #0b0b0e; color: #e7e7ea;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  main {{ text-align: center; max-width: 26rem; padding: 2rem; }}
  h1 {{ font-size: 1.25rem; margin-bottom: .5rem; }}
  p {{ color: #9a9aa3; font-size: .95rem; }}
</style>
<script>
  // Drop the one-time code from the URL as soon as the page paints.
  try {{
    if (window.history && window.history.replaceState) {{
      window.history.replaceState(null, "", "/webui/openrouter/callback");
    }}
  }} catch (_) {{}}
</script>
</head>
<body><main><h1>{safe_title}</h1><p>{safe_body}</p></main></body></html>"""
