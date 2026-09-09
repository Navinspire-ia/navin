"""HTTP route adapter for WebUI Settings APIs.

Keep WebUI Settings route handlers here, not in ``channels/websocket.py``.
The websocket channel owns transport concerns; this module owns WebUI Settings
request mapping and response shaping.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from navin.agent.tools.mcp import request_mcp_reload
from navin.api.runtime import ApiRuntime, ApiStartOptions, api_runtime_paths
from navin.bus.queue import MessageBus
from navin.channels._setup import channel_setup_spec
from navin.config.loader import get_config_path, load_config, save_config
from navin.optional_features import (
    OptionalFeatureError,
    extra_installed,
    optional_dependency_groups,
)
from navin.pairing import approve_code, deny_code, list_pending
from navin.providers.ollama_setup import OllamaSetupError
from navin.providers.omniroute_setup import OmniRouteSetupError
from navin.update import (
    UpdateError,
    check_for_update,
    download_update,
    install_update,
    update_status,
)
from navin.webui.channel_login import (
    ChannelLoginError,
    cancel_channel_login,
    login_snapshot,
    start_channel_login,
)
from navin.webui.channel_validation import validate_channel_config
from navin.webui.cli_apps_api import cli_apps_action, cli_apps_payload
from navin.webui.http_utils import is_local_browser_request as _is_local_browser_request
from navin.webui.http_utils import query_first as _query_first
from navin.webui.mcp_presets_api import mcp_presets_settings_action
from navin.webui.navin_features_api import navin_features_action, navin_features_payload
from navin.webui.ollama_setup_api import ollama_setup_action, ollama_setup_status
from navin.webui.omniroute_setup_api import omniroute_setup_action, omniroute_setup_status
from navin.webui.route_cache import CoalescingCache
from navin.webui.settings_api import (
    WebUISettingsError,
    create_model_configuration,
    decorate_settings_payload,
    delete_model_configuration,
    import_model_configurations,
    login_oauth_provider,
    logout_oauth_provider,
    oauth_login_status,
    provider_models_payload,
    reasoning_effort_values_payload,
    settings_payload,
    settings_usage_payload,
    test_provider_connection,
    update_agent_settings,
    update_api_settings,
    update_image_generation_settings,
    update_live_voice_settings,
    update_model_configuration,
    update_model_route,
    update_music_generation_settings,
    update_network_safety_settings,
    update_provider_settings,
    update_transcription_settings,
    update_video_generation_settings,
    update_voice_settings,
    update_web_search_settings,
)

QueryParams = dict[str, list[str]]

# The update check is a network round trip; reusing it for a few minutes is
# invisible to the user and keeps the shared thread pool free.
_VERSION_CHECK_CACHE_TTL_S = 300.0
# The model catalog fetch reaches navin.live. Awaited on the gateway loop it
# froze every request and the WebSocket for the resolver's 10 s each time the
# WSL network dropped (26 stalls of 10 to 20 s in one log; "engine not
# reachable" in every window). It now runs in its own thread: the route waits
# this long for a fresh catalog, then serves the current one while the sync
# finishes in the background and lands on the next read.
_CATALOG_SYNC_WAIT_S = 2.5
_CATALOG_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-catalog")
_MCP_VALUES_HEADER = "X-Navin-MCP-Values"
_MCP_VALUES_HEADER_MAX_BYTES = 64 * 1024
_CHANNEL_VALUES_HEADER = "X-Navin-Channel-Values"
_CHANNEL_VALUES_HEADER_MAX_BYTES = 64 * 1024
_API_SERVICE_VALUES_HEADER = "X-Navin-API-Service-Values"
_API_SERVICE_VALUES_HEADER_MAX_BYTES = 8 * 1024

_SKIP_FIELD = object()


def _sync_model_catalog_now() -> bool:
    """Fetch and apply the managed catalog; runs in ``_CATALOG_EXECUTOR``."""
    config = load_config()
    if not config.model_catalog.enabled:
        return False
    from navin.providers.managed_catalog import sync_managed_catalog

    return sync_managed_catalog(config, force=True, min_interval_s=0)


_MCP_PRESET_ACTIONS_BY_PATH = {
    "/api/settings/mcp-presets/enable": "enable",
    "/api/settings/mcp-presets/remove": "remove",
    "/api/settings/mcp-presets/test": "test",
    "/api/settings/mcp-presets/custom": "custom",
    "/api/settings/mcp-presets/import": "import",
    "/api/settings/mcp-presets/import-cursor": "import-cursor",
    "/api/settings/mcp-presets/tools": "tools",
}


class WebUISettingsRouter:
    """Route WebUI Settings HTTP requests behind a transport-neutral boundary."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        logger: Any,
        check_api_token: Callable[[WsRequest], bool],
        parse_query: Callable[[str], QueryParams],
        json_response: Callable[[dict[str, Any]], Response],
        error_response: Callable[[int, str | None], Response],
        runtime_surface: str,
        runtime_capabilities: dict[str, Any],
        channel_feature_action: Callable[..., Any] | None = None,
    ) -> None:
        self.bus = bus
        self.logger = logger
        self._check_api_token = check_api_token
        self._parse_query = parse_query
        self._json_response = json_response
        self._error_response = error_response
        self._runtime_surface = runtime_surface
        self._runtime_capabilities = runtime_capabilities
        self._channel_feature_action = channel_feature_action
        self._restart_sections: set[str] = set()
        # The update check reaches the network and every window asks for it.
        self._route_cache = CoalescingCache()
        # One catalog sync at a time; concurrent Settings opens share it.
        self._catalog_sync: asyncio.Future[bool] | None = None

    async def dispatch(self, connection: Any, request: WsRequest, path: str) -> Response | None:
        if path == "/api/settings":
            return await self._handle_settings(request)
        if path == "/api/settings/usage":
            return self._handle_settings_usage(request)
        if path == "/api/settings/reasoning-effort-values":
            return self._handle_settings_reasoning_effort_values(request)
        if path == "/api/settings/update":
            response = self._handle_settings_update(request)
            if response.status_code == 200:
                from navin.webui.computer_api import close_disabled_computer_sessions

                await close_disabled_computer_sessions(self._query(request))
            return response
        if path in {"/api/settings/computer/doctor", "/api/settings/computer/permissions",
                    "/api/settings/computer/stop", "/api/settings/computer/go",
                    "/api/settings/computer/models", "/api/settings/computer/model"}:
            return await self._handle_computer_action(connection, request, path.rsplit("/", 1)[-1])
        if path == "/api/settings/model-configurations/create":
            return self._handle_settings_model_configuration_create(request)
        if path == "/api/settings/model-configurations/import":
            return self._handle_settings_model_configuration_import(request)
        if path == "/api/settings/model-configurations/update":
            return self._handle_settings_model_configuration_update(request)
        if path == "/api/settings/model-configurations/delete":
            return self._handle_settings_model_configuration_delete(request)
        if path == "/api/settings/model-routes/update":
            return self._handle_settings_model_route_update(request)
        if path == "/api/settings/provider/update":
            return self._handle_settings_provider_update(request)
        if path == "/api/settings/provider/test":
            return await self._handle_settings_provider_test(request)
        if path == "/api/settings/provider-models":
            return await self._handle_settings_provider_models(request)
        if path == "/api/settings/provider/oauth-login":
            return await self._handle_settings_provider_oauth(request, "login")
        if path == "/api/settings/provider/oauth-logout":
            return await self._handle_settings_provider_oauth(request, "logout")
        if path == "/api/settings/provider/oauth-status":
            return await self._handle_settings_provider_oauth(request, "status")
        if path == "/api/settings/ollama":
            return await self._handle_settings_ollama(request)
        if path == "/api/settings/ollama/install":
            return await self._handle_settings_ollama_action(request, "install")
        if path == "/api/settings/ollama/start":
            return await self._handle_settings_ollama_action(request, "start")
        if path == "/api/settings/ollama/pull":
            return await self._handle_settings_ollama_action(request, "pull")
        if path == "/api/settings/ollama/configure":
            return await self._handle_settings_ollama_action(request, "configure")
        if path == "/api/settings/omniroute":
            return await self._handle_settings_omniroute(request)
        if path == "/api/settings/omniroute/install":
            return await self._handle_settings_omniroute_action(request, "install")
        if path == "/api/settings/omniroute/start":
            return await self._handle_settings_omniroute_action(request, "start")
        if path == "/api/settings/omniroute/configure":
            return await self._handle_settings_omniroute_action(request, "configure")
        if path == "/api/settings/web-search/update":
            return self._handle_settings_web_search_update(request)
        if path == "/api/settings/api-service":
            return self._handle_settings_api_service(request)
        if path == "/api/settings/api-service/start":
            return await self._handle_settings_api_service_start(connection, request)
        if path == "/api/settings/api-service/stop":
            return await self._handle_settings_api_service_stop(request)
        if path == "/api/settings/image-generation/update":
            return self._handle_settings_image_generation_update(request)
        if path == "/api/settings/video-generation/update":
            return self._handle_settings_video_generation_update(request)
        if path == "/api/settings/music-generation/update":
            return self._handle_settings_music_generation_update(request)
        if path == "/api/settings/transcription/update":
            return self._handle_settings_transcription_update(request)
        if path == "/api/settings/voice/update":
            return self._handle_settings_voice_update(request)
        if path == "/api/settings/voice/live/update":
            return self._handle_settings_live_voice_update(request)
        if path == "/api/settings/voice/preview":
            return await self._handle_settings_voice_preview(request)
        if path == "/api/settings/network-safety/update":
            return self._handle_settings_network_safety_update(request)
        if path == "/api/settings/cli-apps":
            return await self._handle_settings_cli_apps(request)
        if path == "/api/settings/cli-apps/install":
            return await self._handle_settings_cli_apps_action(request, "install")
        if path == "/api/settings/cli-apps/update":
            return await self._handle_settings_cli_apps_action(request, "update")
        if path == "/api/settings/cli-apps/uninstall":
            return await self._handle_settings_cli_apps_action(request, "uninstall")
        if path == "/api/settings/cli-apps/test":
            return await self._handle_settings_cli_apps_action(request, "test")
        if path == "/api/settings/navin-features":
            return await self._handle_settings_navin_features(request)
        if path == "/api/settings/navin-features/enable":
            return await self._handle_settings_navin_features_action(connection, request, "enable")
        if path == "/api/settings/navin-features/disable":
            return await self._handle_settings_navin_features_action(connection, request, "disable")
        if path == "/api/settings/channels/validate":
            return await self._handle_settings_channel_validate(request)
        if path == "/api/settings/channels/configure":
            return await self._handle_settings_channel_configure(connection, request)
        if path == "/api/settings/channels/login/start":
            return await self._handle_settings_channel_login_start(request)
        if path == "/api/settings/channels/login/status":
            return await self._handle_settings_channel_login_status(request)
        if path == "/api/settings/channels/login/cancel":
            return await self._handle_settings_channel_login_cancel(request)
        if path == "/api/settings/pairing":
            return self._handle_settings_pairing(request)
        if path == "/api/settings/pairing/approve":
            return self._handle_settings_pairing_action(request, "approve")
        if path == "/api/settings/pairing/deny":
            return self._handle_settings_pairing_action(request, "deny")
        if path == "/api/settings/mcp-presets":
            return await self._handle_settings_mcp_presets(request)
        if path == "/api/settings/version-check":
            return await self._handle_settings_version_check(request)
        if path == "/api/settings/update-status":
            return self._handle_settings_update_status(request)
        if path == "/api/settings/update-download":
            return await self._handle_settings_update_download(request)
        if path == "/api/settings/update-install":
            return await self._handle_settings_update_install(connection, request)
        if path == "/api/settings/update-preferences":
            return self._handle_settings_update_preferences(request)
        mcp_action = _MCP_PRESET_ACTIONS_BY_PATH.get(path)
        if mcp_action is not None:
            return await self._handle_settings_mcp_presets(request, mcp_action)
        return None

    def _query(self, request: WsRequest) -> QueryParams:
        return self._parse_query(request.path)

    def _authorized(self, request: WsRequest) -> bool:
        return self._check_api_token(request)

    def _unauthorized(self) -> Response:
        return self._error_response(401, "Unauthorized")

    def _with_restart_state(
        self,
        payload: dict[str, Any],
        *,
        section: str | None = None,
    ) -> dict[str, Any]:
        """Keep restart-required state alive for this gateway process."""
        if section and payload.get("requires_restart"):
            self._restart_sections.add(section)
        sections = sorted(self._restart_sections)
        payload = dict(payload)
        if sections:
            payload["requires_restart"] = True
        return decorate_settings_payload(
            payload,
            surface=self._runtime_surface,
            runtime_capability_overrides=self._runtime_capabilities,
            restart_required_sections=sections,
        )

    def _parse_mcp_settings_query(self, request: WsRequest) -> QueryParams:
        query = self._query(request)
        raw = request.headers.get(_MCP_VALUES_HEADER)
        if not raw:
            return query
        if len(raw.encode("utf-8")) > _MCP_VALUES_HEADER_MAX_BYTES:
            raise WebUISettingsError("MCP settings payload is too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebUISettingsError("invalid MCP settings payload") from exc
        if not isinstance(payload, dict):
            raise WebUISettingsError("MCP settings payload must be a JSON object")
        merged = {key: list(values) for key, values in query.items()}
        for key, value in payload.items():
            if not isinstance(key, str) or not key:
                raise WebUISettingsError("MCP settings payload contains an invalid key")
            if value is None:
                continue
            if isinstance(value, str):
                text = value.strip()
            else:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if text:
                merged[key] = [text]
        return merged

    async def _sync_catalog_bounded(self) -> None:
        """Refresh the managed catalog without ever parking the gateway loop.

        Waits at most ``_CATALOG_SYNC_WAIT_S`` for the fetch; past that the
        current catalog is served and the sync keeps running in its thread so
        the next Settings read sees the result.
        """
        loop = asyncio.get_running_loop()
        future = self._catalog_sync
        if future is None or future.done():
            future = loop.run_in_executor(_CATALOG_EXECUTOR, _sync_model_catalog_now)
            self._catalog_sync = future
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=_CATALOG_SYNC_WAIT_S)
        except asyncio.TimeoutError:
            self.logger.warning(
                "model catalog sync still running after {:.1f}s; serving the current catalog",
                _CATALOG_SYNC_WAIT_S,
            )
        except Exception as exc:
            self.logger.warning("model catalog sync failed: {}", exc)

    async def _handle_settings(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        sync_raw = (_query_first(query, "sync_catalog") or _query_first(query, "syncCatalog") or "").strip().lower()
        if sync_raw in {"1", "true", "yes"}:
            await self._sync_catalog_bounded()
        return self._json_response(
            self._with_restart_state(
                settings_payload(
                    surface=self._runtime_surface,
                    runtime_capability_overrides=self._runtime_capabilities,
                )
            )
        )

    def _handle_settings_usage(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(settings_usage_payload())

    def _handle_settings_reasoning_effort_values(self, request: WsRequest) -> Response:
        """Thinking ladder for the provider+model the Models form currently shows."""
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        provider = (_query_first(query, "provider") or "").strip()
        model = (_query_first(query, "model") or "").strip()
        return self._json_response(reasoning_effort_values_payload(provider, model))

    def _handle_settings_pairing(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(_pairing_payload())

    def _handle_settings_pairing_action(self, request: WsRequest, action: str) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        code = (_query_first(query, "code") or "").strip()
        if not code:
            return self._error_response(400, "Missing pairing code")

        if action == "approve":
            result = approve_code(code)
            if result is None:
                return self._error_response(404, "Pairing code not found or expired")
            channel, sender_id = result
            return self._json_response(
                _pairing_payload({
                    "ok": True,
                    "action": "approve",
                    "message": f"Approved {sender_id} for {channel}",
                    "channel": channel,
                    "sender_id": sender_id,
                    "code": code,
                })
            )

        if not deny_code(code):
            return self._error_response(404, "Pairing code not found or expired")
        return self._json_response(
            _pairing_payload({
                "ok": True,
                "action": "deny",
                "message": f"Denied pairing code {code}",
                "code": code,
            })
        )

    async def _handle_computer_action(
        self, connection: Any, request: WsRequest, action: str
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        if action == "permissions" and not _is_local_browser_request(connection, request.headers):
            return self._error_response(403, "Open OS permissions from Navin on the computer being controlled")
        from navin.computer.base import ComputerError
        from navin.webui.computer_api import computer_control, probe_computer

        try:
            if action in {"models", "model"}:
                from navin.webui.computer_models import (
                    computer_models_payload,
                    update_computer_model,
                )

                handler = computer_models_payload if action == "models" else update_computer_model
                payload = await asyncio.to_thread(handler, self._query(request))
            elif action in {"stop", "go"}:
                payload = await computer_control(action)
            elif action == "permissions":
                kind = _query_first(self._query(request), "kind") or "screen_recording"
                if kind not in {"all", "screen_recording", "accessibility", "automation"}:
                    return self._error_response(400, "Unknown desktop permission")
                payload = await probe_computer(kind)
            else:
                payload = await probe_computer(passive=_query_first(self._query(request), "passive") == "true")
        except ComputerError as exc:
            return self._error_response(400, str(exc))
        except WebUISettingsError as exc:
            return self._error_response(exc.status, exc.message)
        return self._json_response(payload)

    def _handle_settings_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_agent_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    def _handle_settings_model_configuration_create(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = create_model_configuration(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_model_configuration_import(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = import_model_configurations(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_model_configuration_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_model_configuration(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_model_configuration_delete(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = delete_model_configuration(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_model_route_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_model_route(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_provider_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_provider_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="image"))

    async def _handle_settings_provider_test(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(test_provider_connection, self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to test provider connection")
            return self._error_response(500, "failed to test provider connection")
        return self._json_response(payload)

    async def _handle_settings_provider_models(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(provider_models_payload, self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to load provider model list")
            return self._error_response(500, "failed to load provider model list")
        return self._json_response(payload)

    async def _handle_settings_provider_oauth(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        try:
            if action == "login":
                payload = await asyncio.to_thread(login_oauth_provider, query)
            elif action == "status":
                payload = await asyncio.to_thread(oauth_login_status, query)
            else:
                payload = await asyncio.to_thread(logout_oauth_provider, query)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    async def _handle_settings_ollama(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(ollama_setup_status)
        except Exception:
            self.logger.exception("failed to load Ollama status")
            return self._error_response(500, "failed to load Ollama status")
        return self._json_response(payload)

    async def _handle_settings_ollama_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(
                ollama_setup_action,
                action,
                self._query(request),
            )
        except OllamaSetupError as e:
            return self._error_response(e.status, e.message)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("Ollama action '{}' failed", action)
            return self._error_response(500, f"Ollama {action} failed")
        return self._json_response(payload)

    async def _handle_settings_omniroute(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(omniroute_setup_status)
        except Exception:
            self.logger.exception("failed to load OmniRoute status")
            return self._error_response(500, "failed to load OmniRoute status")
        return self._json_response(payload)

    async def _handle_settings_omniroute_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(
                omniroute_setup_action,
                action,
                self._query(request),
            )
        except OmniRouteSetupError as e:
            return self._error_response(e.status, e.message)
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("OmniRoute action '{}' failed", action)
            return self._error_response(500, f"OmniRoute {action} failed")
        return self._json_response(payload)

    def _handle_settings_web_search_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_web_search_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="browser"))

    def _handle_settings_api_service(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(self._api_service_payload())

    async def _handle_settings_api_service_start(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            await asyncio.to_thread(
                navin_features_action,
                "enable",
                {"name": ["api"]},
                allow_install=self._allow_feature_package_install(connection, request),
            )
            update_api_settings(self._parse_api_service_settings_query(request))
            config = load_config()
            runtime = self._api_runtime()
            options = ApiStartOptions(
                host=config.api.host,
                port=config.api.port,
                workspace=str(config.workspace_path),
                config_path=str(get_config_path().expanduser().resolve(strict=False)),
            )
            current = runtime.status()
            result = await asyncio.to_thread(
                runtime.restart if current.running else runtime.start_background,
                options,
            )
            if not result.ok:
                return self._error_response(500, self._api_runtime_message(result.message))
        except (WebUISettingsError, OptionalFeatureError) as e:
            return self._error_response(getattr(e, "status", 400), getattr(e, "message", str(e)))
        except Exception as e:
            self.logger.exception("failed to start managed API service")
            return self._error_response(500, str(e))
        return self._json_response(self._api_service_payload(last_action="started"))

    def _parse_api_service_settings_query(self, request: WsRequest) -> QueryParams:
        query = self._query(request)
        if "api_key" in query or "apiKey" in query:
            raise WebUISettingsError("API service API key must be provided in the private header")
        raw = request.headers.get(_API_SERVICE_VALUES_HEADER)
        if not raw:
            return query
        if len(raw.encode("utf-8")) > _API_SERVICE_VALUES_HEADER_MAX_BYTES:
            raise WebUISettingsError("API service settings payload is too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebUISettingsError("invalid API service settings payload") from exc
        if not isinstance(payload, dict):
            raise WebUISettingsError("API service settings payload must be a JSON object")

        unknown = set(payload) - {"api_key"}
        if unknown:
            raise WebUISettingsError("API service settings payload contains an invalid key")
        api_key = payload.get("api_key")
        if api_key is not None and not isinstance(api_key, str):
            raise WebUISettingsError("API service API key must be a string")

        merged = {key: list(values) for key, values in query.items() if key != "api_key"}
        if api_key is not None:
            merged["api_key"] = [api_key]
        return merged

    async def _handle_settings_api_service_stop(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            result = await asyncio.to_thread(self._api_runtime().stop)
        except Exception as e:
            self.logger.exception("failed to stop managed API service")
            return self._error_response(500, str(e))
        if not result.ok and result.message != "api_not_running":
            return self._error_response(500, self._api_runtime_message(result.message))
        return self._json_response(self._api_service_payload(last_action="stopped"))

    @staticmethod
    def _api_runtime() -> ApiRuntime:
        config_path = get_config_path().expanduser().resolve(strict=False)
        return ApiRuntime(paths=api_runtime_paths(config_path))

    def _api_service_payload(self, *, last_action: str | None = None) -> dict[str, Any]:
        config = load_config()
        status = self._api_runtime().status()
        extras = optional_dependency_groups()
        connect_host = "127.0.0.1" if config.api.host in {"0.0.0.0", "::"} else config.api.host
        payload = {
            "installed": extra_installed("api", extras.get("api")),
            "running": status.running,
            "managed": status.running,
            "host": config.api.host,
            "port": config.api.port,
            "timeout": config.api.timeout,
            "api_key_hint": self._masked_secret(config.api.api_key),
            "endpoint": f"http://{connect_host}:{config.api.port}/v1",
            "command": "navin serve",
            "log_path": str(status.log_path),
        }
        if last_action:
            payload["last_action"] = last_action
        return payload

    @staticmethod
    def _masked_secret(value: str) -> str | None:
        value = value.strip()
        if not value:
            return None
        return f"{value[:3]}...{value[-4:]}" if len(value) > 8 else "configured"

    @staticmethod
    def _api_runtime_message(message: str) -> str:
        known = {
            "api_exited_during_startup": "API server exited during startup. Check its log for details.",
            "api_stop_timeout": "API server did not stop in time.",
            "api_state_stale": "API server state was stale; try starting it again.",
        }
        if message in known:
            return known[message]
        if message.startswith("api_"):
            return f"API server {message.removeprefix('api_').replace('_', ' ')}"
        return message.replace("_", " ")

    def _handle_settings_image_generation_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_image_generation_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="image"))

    def _handle_settings_video_generation_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_video_generation_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="video"))

    def _handle_settings_music_generation_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_music_generation_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="voice"))

    def _handle_settings_transcription_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_transcription_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_voice_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_voice_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_live_voice_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_live_voice_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload))

    def _handle_settings_network_safety_update(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = update_network_safety_settings(self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    async def _handle_settings_cli_apps(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        installed_only = (_query_first(self._query(request), "installed_only") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        try:
            payload = await cli_apps_payload(installed_only=installed_only)
        except Exception:
            self.logger.exception("failed to load CLI Apps payload")
            return self._error_response(500, "failed to load CLI Apps")
        return self._json_response(payload)

    async def _handle_settings_voice_preview(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        from navin.webui.voice_api import voice_preview_payload

        try:
            payload = await voice_preview_payload(self._query(request))
        except WebUISettingsError as exc:
            return self._error_response(exc.status, exc.message)
        except Exception:
            self.logger.exception("voice preview failed")
            return self._error_response(502, "voice preview failed")
        return self._json_response(payload)

    async def _handle_settings_cli_apps_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(cli_apps_action, action, self._query(request))
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception as e:
            status = getattr(e, "status", 500)
            message = getattr(e, "message", str(e))
            if status >= 500:
                self.logger.exception("CLI Apps action '{}' failed", action)
            return self._error_response(status, message)
        return self._json_response(payload)

    async def _handle_settings_navin_features(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(navin_features_payload)
        except Exception:
            self.logger.exception("failed to load navin features")
            return self._error_response(500, "failed to load navin features")
        return self._json_response(payload)

    async def _handle_settings_navin_features_action(
        self,
        connection: Any,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await asyncio.to_thread(
                navin_features_action,
                action,
                self._query(request),
                allow_install=action != "enable"
                or self._allow_feature_package_install(connection, request),
            )
        except OptionalFeatureError as e:
            return self._error_response(e.status, e.message)
        except Exception as e:
            status = getattr(e, "status", 500)
            message = getattr(e, "message", str(e))
            if status >= 500:
                self.logger.exception("navin feature action '{}' failed", action)
            return self._error_response(status, message)
        payload = await self._apply_navin_feature_runtime_change(
            action,
            self._query(request),
            payload,
        )
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    async def _apply_navin_feature_runtime_change(
        self,
        action: str,
        query: QueryParams,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self._channel_feature_action is None:
            return payload

        name = (_query_first(query, "name") or "").strip()
        if not name:
            return payload

        try:
            instance_id = (_query_first(query, "instance_id") or "").strip()
            runtime_name = f"{name}.{instance_id}" if instance_id and instance_id != "default" else name
            result = self._channel_feature_action(action, runtime_name)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self.logger.exception("failed to apply channel '{}' without restart", name)
            return self._feature_runtime_fallback(
                payload,
                message=f"{name} channel config was saved, but hot reload failed: {exc}",
            )

        if not isinstance(result, dict) or not result.get("handled"):
            return payload

        payload = dict(payload)
        if result.get("requires_restart"):
            payload["requires_restart"] = True
        else:
            payload["requires_restart"] = False

        message = result.get("message")
        if isinstance(message, str) and message:
            last_action = dict(payload.get("last_action") or {})
            previous = last_action.get("message")
            if isinstance(previous, str) and previous:
                last_action["message"] = f"{previous}. {message}"
            else:
                last_action["message"] = message
            last_action["hot_reload"] = not payload["requires_restart"]
            payload["last_action"] = last_action
        return payload

    @staticmethod
    def _feature_runtime_fallback(payload: dict[str, Any], *, message: str) -> dict[str, Any]:
        payload = dict(payload)
        payload["requires_restart"] = True
        last_action = dict(payload.get("last_action") or {})
        previous = last_action.get("message")
        last_action["message"] = f"{previous}. {message}" if isinstance(previous, str) and previous else message
        last_action["hot_reload"] = False
        payload["last_action"] = last_action
        return payload

    async def _handle_settings_channel_configure(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        name = (_query_first(query, "name") or "").strip()
        instance_id = (_query_first(query, "instance_id") or "default").strip()
        enable = (_query_first(query, "enable") or "").strip().lower() in {"1", "true", "yes"}
        try:
            saved = await asyncio.to_thread(
                self._save_channel_config_values,
                name,
                self._parse_channel_values_header(request),
                instance_id,
            )
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to save channel '{}' settings", name)
            return self._error_response(500, "failed to save channel settings")

        payload: dict[str, Any] = {
            "name": name,
            "saved": True,
            "saved_keys": saved,
        }
        if not enable:
            return self._json_response(payload)

        feature_query = {"name": [name]}
        if instance_id and instance_id != "default":
            feature_query["instance_id"] = [instance_id]

        try:
            features = await asyncio.to_thread(
                navin_features_action,
                "enable",
                feature_query,
                allow_install=self._allow_feature_package_install(connection, request),
            )
        except OptionalFeatureError as e:
            return self._error_response(e.status, f"Settings saved, but {e.message}")
        except Exception as e:
            self.logger.exception("failed to enable channel '{}' after settings save", name)
            return self._error_response(500, f"Settings saved, but enabling {name} failed: {e}")

        features = await self._apply_navin_feature_runtime_change(
            "enable",
            feature_query,
            features,
        )
        payload["navin_features"] = self._with_restart_state(features, section="runtime")
        return self._json_response(payload)

    async def _handle_settings_channel_validate(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        name = (_query_first(query, "name") or "").strip()
        instance_id = (_query_first(query, "instance_id") or "default").strip()
        try:
            payload = await asyncio.to_thread(
                validate_channel_config,
                name,
                self._parse_channel_values_header(request),
                instance_id=instance_id,
            )
        except WebUISettingsError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to validate channel '{}' settings", name)
            return self._error_response(500, "failed to validate channel settings")
        return self._json_response(payload)

    async def _handle_settings_channel_login_start(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        name = (_query_first(query, "name") or "").strip()
        force = (_query_first(query, "force") or "").strip().lower() in {"1", "true", "yes"}
        try:
            payload = await start_channel_login(name, force=force)
        except ChannelLoginError as e:
            return self._error_response(e.status, e.message)
        except Exception:
            self.logger.exception("failed to start channel login for '{}'", name)
            return self._error_response(500, "failed to start channel login")
        return self._json_response(payload)

    async def _handle_settings_channel_login_status(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        name = (_query_first(self._query(request), "name") or "").strip()
        return self._json_response(login_snapshot(name))

    async def _handle_settings_channel_login_cancel(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        name = (_query_first(self._query(request), "name") or "").strip()
        try:
            payload = await cancel_channel_login(name)
        except Exception:
            self.logger.exception("failed to cancel channel login for '{}'", name)
            return self._error_response(500, "failed to cancel channel login")
        return self._json_response(payload)

    def _parse_channel_values_header(self, request: WsRequest) -> dict[str, Any]:
        raw = request.headers.get(_CHANNEL_VALUES_HEADER)
        if not raw:
            return {}
        if len(raw.encode("utf-8")) > _CHANNEL_VALUES_HEADER_MAX_BYTES:
            raise WebUISettingsError("channel settings payload is too large")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WebUISettingsError("invalid channel settings payload") from exc
        if not isinstance(payload, dict):
            raise WebUISettingsError("channel settings payload must be a JSON object")
        return payload

    def _save_channel_config_values(
        self,
        name: str,
        raw_values: dict[str, Any],
        instance_id: str = "default",
    ) -> list[str]:
        if not name:
            raise WebUISettingsError("missing channel name")
        setup_spec = channel_setup_spec(name)
        if setup_spec is None:
            raise WebUISettingsError(f"channel '{name}' cannot be configured from WebUI", status=404)
        field_types = setup_spec.route_field_types
        if not raw_values:
            return []

        _ = instance_id
        config = load_config()
        section = getattr(config.channels, name, None)
        if hasattr(section, "model_dump"):
            channel_config = section.model_dump(mode="json", by_alias=True)
        elif isinstance(section, dict):
            channel_config = dict(section)
        else:
            channel_config = {}

        saved: list[str] = []
        prefix = f"channels.{name}."
        for raw_key, raw_value in raw_values.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise WebUISettingsError("channel settings payload contains an invalid key")
            field = raw_key[len(prefix):] if raw_key.startswith(prefix) else raw_key
            value_type = field_types.get(field)
            if value_type is None:
                raise WebUISettingsError(f"'{raw_key}' cannot be configured from WebUI")
            value = self._coerce_channel_value(raw_key, raw_value, value_type)
            if value is _SKIP_FIELD:
                continue
            self._assign_channel_config_value(channel_config, field, value)
            saved.append(raw_key)

        setattr(config.channels, name, channel_config)
        save_config(config)
        return saved

    @staticmethod
    def _coerce_channel_value(raw_key: str, raw_value: Any, value_type: Any) -> Any:
        if isinstance(value_type, tuple):
            kind = value_type[0]
            allowed = value_type[1]
        else:
            kind = value_type
            allowed = None

        if kind in {"string", "secret"}:
            value = raw_value.strip() if isinstance(raw_value, str) else str(raw_value)
            if kind == "secret" and not value:
                return _SKIP_FIELD
            return value

        if kind == "list":
            if raw_value is None:
                return []
            if isinstance(raw_value, str):
                return [item.strip() for item in raw_value.split(",") if item.strip()]
            if isinstance(raw_value, list):
                return [str(item).strip() for item in raw_value if str(item).strip()]
            raise WebUISettingsError(f"'{raw_key}' must be a comma-separated list")

        if kind == "int":
            if raw_value in (None, ""):
                return _SKIP_FIELD
            try:
                return int(raw_value)
            except (TypeError, ValueError) as exc:
                raise WebUISettingsError(f"'{raw_key}' must be a number") from exc

        if kind == "bool":
            if isinstance(raw_value, bool):
                return raw_value
            value = str(raw_value).strip().lower()
            if value in {"true", "1", "yes", "on"}:
                return True
            if value in {"false", "0", "no", "off"}:
                return False
            raise WebUISettingsError(f"'{raw_key}' must be true or false")

        if kind == "enum":
            value = raw_value.strip() if isinstance(raw_value, str) else str(raw_value)
            if not value:
                return _SKIP_FIELD
            if value not in allowed:
                options = ", ".join(sorted(allowed))
                raise WebUISettingsError(f"'{raw_key}' must be one of: {options}")
            return value

        raise WebUISettingsError(f"'{raw_key}' has an unsupported field type")

    @staticmethod
    def _assign_channel_config_value(channel_config: dict[str, Any], field: str, value: Any) -> None:
        target = channel_config
        parts = field.split(".")
        for part in parts[:-1]:
            current = target.get(part)
            if not isinstance(current, dict):
                current = {}
                target[part] = current
            target = current
        target[parts[-1]] = value


    def _allow_feature_package_install(self, connection: Any, request: WsRequest) -> bool:
        if _is_local_browser_request(connection, request.headers):
            return True
        try:
            return bool(load_config().tools.webui_allow_remote_package_install)
        except Exception:
            self.logger.exception("failed to load remote package install policy")
            return False

    async def _handle_settings_mcp_presets(
        self,
        request: WsRequest,
        action: str | None = None,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            payload = await mcp_presets_settings_action(
                action,
                self._parse_mcp_settings_query(request),
                reload_mcp=lambda: request_mcp_reload(self.bus),
            )
        except Exception as e:
            status = getattr(e, "status", 500)
            message = getattr(e, "message", str(e))
            if status >= 500:
                self.logger.exception("MCP preset action '{}' failed", action or "list")
            return self._error_response(status, message)
        if action is None:
            return self._json_response(payload)
        return self._json_response(self._with_restart_state(payload, section="runtime"))

    async def _handle_settings_version_check(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            force = _query_first(self._query(request), "force") in {"1", "true"}
            payload = await self._route_cache.get(
                "settings:version-check",
                lambda: asyncio.to_thread(check_for_update, force=force),
                ttl=_VERSION_CHECK_CACHE_TTL_S,
                force=force,
            )
            return self._json_response(payload)
        except UpdateError as exc:
            return self._error_response(exc.status, str(exc))

    def _handle_settings_update_status(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        return self._json_response(update_status())

    async def _handle_settings_update_download(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        try:
            return self._json_response(await asyncio.to_thread(download_update))
        except UpdateError as exc:
            return self._error_response(exc.status, str(exc))

    async def _handle_settings_update_install(
        self,
        connection: Any,
        request: WsRequest,
    ) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        if not _is_local_browser_request(connection, request.headers):
            return self._error_response(403, "updates can only be installed locally")
        try:
            return self._json_response(await asyncio.to_thread(install_update))
        except UpdateError as exc:
            return self._error_response(exc.status, str(exc))

    def _handle_settings_update_preferences(self, request: WsRequest) -> Response:
        if not self._authorized(request):
            return self._unauthorized()
        query = self._query(request)
        config = load_config()
        channel = _query_first(query, "channel")
        auto_check = _query_first(query, "autoCheck")
        skipped = _query_first(query, "skippedVersion")
        if channel is not None:
            if channel not in {"stable", "beta"}:
                return self._error_response(400, "invalid update channel")
            config.updates.channel = channel
        if auto_check is not None:
            if auto_check not in {"true", "false", "1", "0"}:
                return self._error_response(400, "invalid autoCheck value")
            config.updates.auto_check = auto_check in {"true", "1"}
        if skipped is not None:
            config.updates.skipped_version = skipped.strip()
        save_config(config)
        return self._json_response({
            "autoCheck": config.updates.auto_check,
            "channel": config.updates.channel,
            "skippedVersion": config.updates.skipped_version,
        })


def _pairing_payload(last_action: dict[str, Any] | None = None) -> dict[str, Any]:
    now = time.time()
    requests = []
    for item in list_pending():
        expires_at = float(item.get("expires_at", 0) or 0)
        created_at = float(item.get("created_at", 0) or 0)
        requests.append({
            "code": str(item.get("code", "")),
            "channel": str(item.get("channel", "")),
            "sender_id": str(item.get("sender_id", "")),
            "created_at_ms": int(created_at * 1000) if created_at else None,
            "expires_at_ms": int(expires_at * 1000) if expires_at else None,
            "expires_in_seconds": max(0, int(expires_at - now)) if expires_at else None,
        })
    payload: dict[str, Any] = {"requests": requests}
    if last_action is not None:
        payload["last_action"] = last_action
    return payload
