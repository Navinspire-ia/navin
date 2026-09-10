# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""HTTP API handler extracted from WebSocketChannel.

Handles all non-WebSocket HTTP routes: bootstrap, sessions, settings,
media, commands, sidebar state, static file serving, and token management.

Also houses shared HTTP utility functions used by both this module and
``websocket.py`` to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from loguru import logger
from websockets.http11 import Request as WsRequest
from websockets.http11 import Response

from navin.agent.tools.context import (
    RequestContext,
    bind_request_context,
    request_context,
    reset_request_context,
)
from navin.agent.tools.shell import request_exec_policy_reload
from navin.board.notify import publish_board_update
from navin.board.store import BoardError
from navin.command.builtin import builtin_command_palette
from navin.cron.recurrence import RecurrenceError, recurrence_from_payload
from navin.cron.session_turns import is_bound_cron_job
from navin.cron.types import CronJob, CronLimits, CronSchedule
from navin.dap.session import DebugSessionError
from navin.optional_live import live_modules_available
from navin.runtime_context import public_history_messages
from navin.templates.apps.install import AppTemplateError
from navin.triggers.local_types import LocalTrigger
from navin.utils.document_templates import (
    list_templates_payload as list_document_templates_payload,
)
from navin.utils.document_templates import (
    template_file as document_template_file,
)
from navin.utils.media_templates import list_media_templates
from navin.utils.subagent_channel_display import scrub_subagent_messages_for_channel

if live_modules_available():
    try:
        from navin.webui.account_api import (
            AccountApiError,
            WebUIAccountService,
        )
        from navin.webui.account_api import (
            callback_html as account_callback_html,
        )
    except ImportError:
        AccountApiError = Exception  # type: ignore[misc,assignment]
        WebUIAccountService = None  # type: ignore[misc,assignment]
        account_callback_html = None
else:
    AccountApiError = Exception  # type: ignore[misc,assignment]
    WebUIAccountService = None  # type: ignore[misc,assignment]
    account_callback_html = None
from navin.webui.app_templates_api import (
    app_template_detail_payload,
    create_app_template_payload,
    install_app_template_payload,
    list_app_templates_payload,
)
from navin.webui.assist_api import AssistError, completion_payload, edit_payload
from navin.webui.board_api import (
    board_autonomy_update_payload,
    board_payload,
    board_update_payload,
    github_issues_payload,
    github_issues_repo_payload,
    github_pr_sync_payload,
)
from navin.webui.continuity_api import (
    ContinuityApiError,
    leave_handoff_payload,
    resume_seed_payload,
)
from navin.webui.debug_api import debug_dispatch
from navin.webui.exec_policy_api import (
    ExecPolicyError,
    exec_policy_payload,
    update_exec_policy,
)
from navin.webui.file_preview import (
    WebUIFilePreviewError,
    content_disposition_attachment,
    file_body_from_headers,
    file_create_payload,
    file_delete_payload,
    file_download_payload,
    file_paste_payload,
    file_preview_availability_payload,
    file_preview_payload,
    file_rename_payload,
    file_save_payload,
    office_render_payload,
)
from navin.webui.file_search_api import FileSearchError, file_search_payload
from navin.webui.file_tree import WebUIFileTreeError, file_tree_payload
from navin.webui.gateway_tokens import GatewayTokenStore, token_response_payload
from navin.webui.github_pr_api import (
    GithubPrError,
    github_ci_status_payload,
    github_fix_ci_prompt_payload,
    github_pr_create_payload,
    github_pr_view_payload,
)
from navin.webui.http_utils import (
    case_insensitive_header as _case_insensitive_header,
)
from navin.webui.http_utils import (
    host_for_url as _host_for_url,
)
from navin.webui.http_utils import (
    http_error as _http_error,
)
from navin.webui.http_utils import (
    http_json_response as _http_json_response,
)
from navin.webui.http_utils import (
    http_response as _http_response,
)
from navin.webui.http_utils import (
    is_local_browser_request as _is_local_browser_request,
)
from navin.webui.http_utils import (
    is_loopback_host as _is_loopback_host,
)
from navin.webui.http_utils import (
    is_same_machine_client as _is_same_machine_client,
)
from navin.webui.http_utils import (
    issue_route_secret_matches as _issue_route_secret_matches,
)
from navin.webui.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from navin.webui.http_utils import (
    parse_query as _parse_query,
)
from navin.webui.http_utils import (
    parse_request_path as _parse_request_path,
)
from navin.webui.http_utils import (
    query_all as _query_all,
)
from navin.webui.http_utils import (
    query_first as _query_first,
)
from navin.webui.http_utils import (
    safe_host_header as _safe_host_header,
)
from navin.webui.ingress_policy import WebUIIngressPolicy
from navin.webui.lsp_api import (
    LspApiError,
    code_actions_payload,
    definition_at_payload,
    hover_payload,
    references_at_payload,
    rename_payload,
    signature_help_payload,
)
from navin.webui.lsp_api import (
    completion_payload as lsp_completion_payload,
)
from navin.webui.media_gateway import WebUIMediaGateway
from navin.webui.meeting_api import STORE_MODES as MEETING_STORE_MODES
from navin.webui.meeting_api import MeetingError
from navin.webui.meeting_api import answer_payload as meeting_answer_payload
from navin.webui.meeting_api import report_payload as meeting_report_payload
from navin.webui.meeting_api import speakers_payload as meeting_speakers_payload
from navin.webui.meeting_api import store_payload as meeting_store_payload
from navin.webui.metagraph import MetagraphError, metagraph_payload
from navin.webui.notes_api import dispatch_notes_route
from navin.webui.onboarding_state import (
    read_webui_onboarding,
    write_webui_onboarding,
)
from navin.webui.openrouter_oauth import (
    OpenRouterOAuthError,
    OpenRouterOAuthService,
)
from navin.webui.openrouter_oauth import (
    callback_html as openrouter_callback_html,
)
from navin.webui.openrouter_oauth import (
    finish_redirect_url as openrouter_finish_redirect_url,
)
from navin.webui.plugins_api import (
    PluginsApiError,
    install_plugin,
    maybe_reload_mcp,
    remove_plugin,
    set_plugin_enabled,
    webui_plugins_payload,
)
from navin.webui.project_brain import ProjectBrainError, project_brain_payload
from navin.webui.project_rules_api import (
    ProjectRulesError,
    project_rules_payload,
    save_project_rule_payload,
)
from navin.webui.project_search import (
    ProjectSearchError,
    git_blame_payload,
    git_branch_payload,
    git_changes_payload,
    git_commit_detail_payload,
    git_commit_payload,
    git_conflict_action_payload,
    git_diff_payload,
    git_discard_payload,
    git_fetch_payload,
    git_log_payload,
    git_pull_payload,
    git_stage_payload,
    git_stash_payload,
    git_sync_payload,
    git_undo_last_commit_payload,
    replace_payload,
    search_payload,
)
from navin.webui.review_api import (
    ReviewApiError,
    review_action_payload,
    review_changes_payload,
    review_file_payload,
)
from navin.webui.route_cache import CoalescingCache
from navin.webui.session_automations import (
    all_automations_payload,
    serialize_automation_jobs,
    session_automation_jobs,
    session_automations_payload,
)
from navin.webui.session_list_index import list_webui_sessions
from navin.webui.sidebar_state import (
    read_webui_sidebar_state,
    write_webui_sidebar_state,
)
from navin.webui.skills_api import (
    SkillsApiError,
    create_workspace_skill,
    delete_workspace_skill,
    discover_workspace_skills,
    import_workspace_skills,
    resolve_skill_apply_root,
    resolve_skill_scan_root,
    skill_body_from_headers,
    update_workspace_skill,
    webui_skill_detail_payload,
    webui_skills_payload,
)
from navin.webui.stall_watch import StallReporter
from navin.webui.symbols_api import (
    SymbolsError,
    goto_definition_payload,
    outline_payload,
    symbols_payload,
)
from navin.webui.system_loops import is_configurable_system_loop, update_system_loop
from navin.webui.thread_disk import delete_webui_thread
from navin.webui.transcript import (
    WEBUI_TRANSCRIPT_SCHEMA_VERSION,
    build_webui_thread_response,
)
from navin.webui.ui_zoom_debug import handle_ui_zoom_debug
from navin.webui.workspaces import WebUIWorkspaceController

_SLOW_WEBUI_HTTP_LOG_MS = 1_000

# How long a polled network answer stays good enough to reuse. Each is well
# under the interval a human would notice being stale, and well over the burst
# of near-simultaneous polls that one window refresh produces.
_ACCOUNT_CACHE_TTL_S = 20.0
_GITHUB_CHECKS_CACHE_TTL_S = 30.0
_LSP_SEARCH_CACHE_TTL_S = 60.0
_VERSION_CHECK_CACHE_TTL_S = 300.0
_CONTEXT_USAGE_CACHE_TTL_S = 8.0
_GIT_STATUS_CACHE_TTL_S = 4.0
_FILE_TREE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="file-tree")
# Routes that block on a remote host (navin.live, forge APIs) for seconds. Their
# own small pool is what stops one slow or unreachable remote from occupying the
# default to_thread threads that every local route shares: a stalled account
# validation was measured dragging the file tree, the board and the workspace
# diagnostics from milliseconds to twenty seconds and beyond.
_REMOTE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="webui-remote")
_GIT_CHANGES_CACHE_TTL_S = 4.0
_REVIEW_CHANGES_CACHE_TTL_S = 2.0
_SESSIONS_CACHE_TTL_S = 3.0
_SKILLS_CACHE_TTL_S = 20.0
_TEMPLATES_CACHE_TTL_S = 30.0
_HEAVY_ROUTE_CONCURRENCY = 4


def bootstrap_refusal(
    *,
    secret: str,
    is_local_browser: bool,
    bind_host: str,
    headers: Any,
) -> tuple[int, str] | None:
    """Why a bootstrap request must be refused, or None to let it through.

    A secret guards the gateway from the network, not the user from their own
    machine. Requiring it from a browser on the loopback interface of a gateway
    that only listens there turned opening the printed URL into a prompt for a
    password nobody had chosen - the installer generates one unattended.

    The bind address is half of the test on purpose. Once the gateway listens
    somewhere other than loopback, a request that looks local can be a proxy on
    the same host relaying the outside world, and the secret is the only thing
    left standing between that and a token.
    """
    local_bypass = is_local_browser and _is_loopback_host(bind_host)
    if secret and not local_bypass:
        if not _issue_route_secret_matches(headers, secret):
            return (401, "Unauthorized")
        return None
    if not secret and not is_local_browser:
        return (403, "bootstrap is localhost-only")
    return None


def token_issue_refusal(
    *,
    secret: str,
    is_local_browser: bool,
    bind_host: str,
    headers: Any,
) -> tuple[int, str] | None:
    """Why a token-issue request must be refused, or None to let it through.

    Same policy as :func:`bootstrap_refusal`, fail-closed: without a secret
    this route would hand a connection token to anyone who can reach the
    port, so it used to warn and issue anyway. A loopback peer of a
    loopback-bound gateway is the machine owner and stays welcome; every
    other secretless request is refused instead of warned about.
    """
    if secret:
        if not _issue_route_secret_matches(headers, secret):
            return (401, "Unauthorized")
        return None
    if is_local_browser and _is_loopback_host(bind_host):
        return None
    return (403, "token issue requires token_issue_secret")


_AUTOMATION_VALUES_HEADER = "X-Navin-Automation-Values"

if TYPE_CHECKING:
    from navin.bus.queue import MessageBus
    from navin.cron.service import CronService
    from navin.session.manager import SessionManager
    from navin.triggers.local_store import LocalTriggerStore


def _query_flag(query: Any, key: str) -> bool:
    return (_query_first(query, key) or "").strip().lower() in {"1", "true", "yes", "on"}


def _decode_api_key(raw_key: str) -> str | None:
    key = unquote(raw_key)
    _api_key_re = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
    if _api_key_re.match(key) is None:
        return None
    return key


_MAX_EXTRA_FILE_ROOTS = 12


def _normalize_file_root(raw: str | Path) -> Path | None:
    text = unquote(str(raw or "")).strip()
    if not text:
        return None
    try:
        root = Path(text).expanduser().resolve(strict=False)
    except OSError:
        return None
    if not root.is_dir():
        return None
    return root


def _extra_file_roots(query: Any, scope: Any) -> list[Path]:
    """Roots to search besides the chat session workspace.

    Code can have a project open that is not the chat folder. Agents also cite
    files from the Navin install (``ppt_qa.py`` in ``navin/documents``). A
    basename-only preview must still find that file when the session is
    scoped to another project.
    """
    roots: list[Path] = []
    seen: set[Path] = set()
    try:
        session_root = Path(scope.project_path).expanduser().resolve(strict=False)
    except OSError:
        session_root = None

    def add(raw: str | Path | None) -> None:
        if raw is None:
            return
        root = _normalize_file_root(raw)
        if root is None or root in seen:
            return
        if session_root is not None and root == session_root:
            return
        seen.add(root)
        roots.append(root)

    for raw in _query_all(query, "root"):
        add(raw)

    try:
        from navin.webui.sidebar_state import read_webui_sidebar_state

        for item in read_webui_sidebar_state().get("recent_projects") or []:
            if isinstance(item, dict):
                add(item.get("path"))
    except Exception:
        pass

    from navin.security.workspace_access import effective_restrict_to_workspace

    if not effective_restrict_to_workspace(
        getattr(scope, "restrict_to_workspace", True),
        read_config=True,
    ):
        try:
            import navin as _navin

            add(Path(_navin.__file__).resolve().parent.parent)
        except Exception:
            pass
        cwd = Path.cwd()
        if (cwd / ".git").is_dir() or (cwd / "pyproject.toml").is_file():
            add(cwd)

    return roots[:_MAX_EXTRA_FILE_ROOTS]


def _default_model_name_from_config() -> str | None:
    try:
        from navin.config.loader import load_config

        model = load_config().resolve_preset().model.strip()
        return model or None
    except Exception as e:
        logger.debug("bootstrap model_name could not load from config: {}", e)
        return None


def _resolve_bootstrap_model_name(
    runtime_name: Callable[[], str | None] | None,
) -> str:
    if runtime_name is not None:
        try:
            raw = runtime_name()
        except Exception as e:
            logger.debug("bootstrap runtime model resolver failed: {}", e)
        else:
            if isinstance(raw, str):
                stripped = raw.strip()
                if stripped:
                    return stripped
    return _default_model_name_from_config() or ""


# ---------------------------------------------------------------------------
# GatewayHTTPHandler
# ---------------------------------------------------------------------------


class GatewayHTTPHandler:
    """Handles all HTTP routes served alongside the WebSocket endpoint.

    Routes HTTP requests and delegates stateful work to explicit gateway
    services owned by the composition layer.
    """

    def __init__(
        self,
        *,
        config: Any,  # WebSocketConfig
        session_manager: SessionManager | None,
        static_dist_path: Path | None,
        runtime_model_name: Callable[[], str | None] | None,
        runtime_surface: str,
        runtime_capabilities_overrides: dict[str, Any] | None,
        bus: MessageBus,
        tokens: GatewayTokenStore,
        media: WebUIMediaGateway,
        ingress: WebUIIngressPolicy,
        workspaces: WebUIWorkspaceController,
        skills_workspace_path: Path,
        disabled_skills: set[str] | None = None,
        cron_service: CronService | None = None,
        local_trigger_store: LocalTriggerStore | None = None,
        cron_pending_job_ids: Callable[[str], set[str]] | None = None,
        local_trigger_pending_ids: Callable[[str], set[str]] | None = None,
        channel_feature_action: Callable[..., Any] | None = None,
        tool_registry: Callable[[], Any] | None = None,
        log: Any = logger,
    ) -> None:
        self.config = config
        self.session_manager = session_manager
        # The serving agent's ToolRegistry, when the gateway shares it. Lets a
        # project-level opt-in (Guardrails > Memory) add or drop the recall
        # tool at runtime instead of waiting for a restart.
        self.tool_registry = tool_registry
        self.static_dist_path = static_dist_path
        self.runtime_model_name = runtime_model_name
        self.bus = bus
        self.tokens = tokens
        self.media = media
        self.ingress = ingress
        self.workspaces = workspaces
        self.skills_workspace_path = skills_workspace_path
        # skills_workspace_path is the workspace root; the pending-review
        # store must resolve to the same folder the AgentLoop writes to.
        from navin.agent.review import PendingReviewStore

        self.pending_review = PendingReviewStore(skills_workspace_path)
        self.account = WebUIAccountService() if WebUIAccountService is not None else None
        # Network-backed routes every window polls on a timer. Without this,
        # each window pays its own multi-second call and they queue in the
        # ten-thread default executor, starving everything else including the
        # chat turn the user is waiting on.
        self.route_cache = CoalescingCache()
        self._heavy_routes = asyncio.Semaphore(_HEAVY_ROUTE_CONCURRENCY)
        # A route past the client's timeout gets its thread stacks logged
        # while it is still stuck: "slow route" alone never said why.
        self._stalls = StallReporter(log)
        self.openrouter_oauth = OpenRouterOAuthService()
        self.disabled_skills = disabled_skills or set()
        self.cron_service = cron_service
        self.local_trigger_store = local_trigger_store
        self.cron_pending_job_ids = cron_pending_job_ids
        self.local_trigger_pending_ids = local_trigger_pending_ids
        self._log = log
        self._runtime_surface = runtime_surface

        from navin.webui.settings_api import runtime_capabilities as _rc
        from navin.webui.settings_routes import WebUISettingsRouter

        self._capabilities = _rc(runtime_surface, runtime_capabilities_overrides or {})
        self.settings_routes = WebUISettingsRouter(
            bus=bus,
            logger=self._log,
            check_api_token=self.check_api_token,
            parse_query=_parse_query,
            json_response=_http_json_response,
            error_response=_http_error,
            runtime_surface=runtime_surface,
            runtime_capabilities=self._capabilities,
            channel_feature_action=channel_feature_action,
        )
        # A project that opted in before this boot gets its tool back without
        # a hand-written restart; a gateway without a registry changes nothing.
        self._sync_recall_tool()
        self._sync_world_tool()
        self._sync_policy_tool()

    def _registry(self) -> Any | None:
        accessor = getattr(self, "tool_registry", None)
        if accessor is None:
            return None
        try:
            return accessor()
        except Exception:
            return None

    def _sync_recall_tool(self, *extra: Path) -> bool | None:
        """Align the recall tool with the projects' flags; None when unknown."""
        registry = self._registry()
        if registry is None:
            return None
        from navin.cognition import sync_recall_tool

        return sync_recall_tool(registry, workspace=self.skills_workspace_path, extra=extra)

    def workspace_controls_available(self, connection: Any) -> bool:
        return self._runtime_surface == "native" or _is_same_machine_client(connection)

    # -- Token management ---------------------------------------------------

    def check_api_token(self, request: WsRequest) -> bool:
        return self.tokens.check_api_token(request)

    def _referer_token_valid(self, request: WsRequest) -> bool:
        """Accept the API token carried by the Referer query string.

        Template HTML previews are rendered in an iframe; their relative
        sub-resources (images, CSS) cannot append the token themselves, but
        the browser sends the parent document URL, which contains it.
        """
        referer = request.headers.get("Referer") or ""
        if "/api/document-templates/" not in referer:
            return False
        token = _query_first(_parse_query(referer), "token")
        return self.tokens.check_api_token_value(token)

    # -- Main dispatch ------------------------------------------------------

    async def dispatch(self, connection: Any, request: WsRequest) -> Any | None:
        """Route an HTTP request. Returns Response or None."""
        got, _ = _parse_request_path(request.path)
        started = time.perf_counter()
        response: Any | None = None

        try:
            response = await self._stalls.watch(
                got, self._dispatch_resolved(connection, request, got)
            )
            return response
        except Exception:
            # This coroutine is the websockets `process_request` hook: an
            # exception escaping here makes the library answer 500 with
            # "Failed to open a WebSocket connection. See server log for more
            # information." Panels print that body verbatim, so a server-side
            # bug reads to the user as a broken WebSocket. Keep the traceback
            # in the log and hand the UI a shaped error it can translate.
            self._log.exception("unhandled error in webui http route path={}", got)
            response = _http_json_response(
                {"error": "internal server error", "path": got}, status=500
            )
            return response
        finally:
            self._log_slow_http(got, response, started)

    async def _dispatch_resolved(
        self,
        connection: Any,
        request: WsRequest,
        got: str,
    ) -> Any | None:
        # Health probe (same port as WebSocket / WebUI HTTP)
        if got == "/health":
            return _http_json_response({"status": "ok"})

        # Localhost debug route: drive CSS zoom / layout on the desktop webview.
        # It is unauthenticated by design (the webview polls it), so it must
        # never answer a peer that is not the local machine.
        if got == "/api/debug/ui-zoom":
            if not _is_local_browser_request(connection, request.headers):
                return _http_error(403, "ui-zoom debug route is localhost-only")
            return handle_ui_zoom_debug(request)

        # Token issue endpoint
        if self.config.token_issue_path:
            issue_expected = _normalize_config_path(self.config.token_issue_path)
            if got == issue_expected:
                return self._handle_token_issue(connection, request)

        # Bootstrap
        if got == "/webui/bootstrap":
            return self._handle_bootstrap(connection, request)

        # navin.live connect handoff (browser redirect target, loopback only)
        if got == "/webui/account/callback":
            return await self._handle_webui_account_callback(connection, request)

        # OpenRouter OAuth PKCE handoff (browser redirect target, loopback only)
        if got == "/webui/openrouter/callback":
            return await self._handle_webui_openrouter_callback(connection, request)

        if got == "/api/marketing/oauth/callback":
            return await self._handle_marketing_oauth_callback(request)

        # Settings routes (delegated)
        response = await self.settings_routes.dispatch(connection, request, got)
        if response is not None:
            return response

        # Session routes
        response = await self._dispatch_session_routes(request, got)
        if response is not None:
            return response

        # Media routes
        response = self._dispatch_media_routes(request, got)
        if response is not None:
            return response

        # Notes module routes
        response = await dispatch_notes_route(got, request, check_token=self.check_api_token)
        if response is not None:
            return response

        # Automation routes
        response = await self._dispatch_automation_routes(request, got)
        if response is not None:
            return response

        # Misc routes
        response = await self._dispatch_misc_routes(connection, request, got)
        if response is not None:
            return response

        # API 404 (never serve SPA for /api/ routes)
        if got.startswith("/api/"):
            return _http_error(404, "API route not found")

        # Static SPA serving
        if self.static_dist_path is not None:
            response = self._serve_static(got)
            if response is not None:
                return response

        return connection.respond(404, "Not Found")

    def _log_slow_http(self, path: str, response: Any | None, started: float) -> None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if elapsed_ms < _SLOW_WEBUI_HTTP_LOG_MS:
            return
        if not (path.startswith("/api/") or path == "/webui/bootstrap"):
            return
        status = getattr(response, "status_code", None)
        self._log.warning(
            "slow webui http route path={} status={} duration_ms={}",
            path,
            status if status is not None else "none",
            elapsed_ms,
        )

    # -- Token issue --------------------------------------------------------

    def _handle_token_issue(self, connection: Any, request: Any) -> Any:
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        refusal = token_issue_refusal(
            secret=secret,
            is_local_browser=_is_local_browser_request(connection, request.headers),
            bind_host=self.config.host,
            headers=request.headers,
        )
        if refusal is not None:
            status, reason = refusal
            if status == 403:
                self._log.error(
                    "token issue refused: token_issue_secret is empty and the "
                    "request is not loopback-to-loopback. Set token_issue_secret "
                    "to serve non-local clients."
                )
            return connection.respond(status, reason)
        if not self.tokens.can_issue():
            self._log.error(
                "too many outstanding issued tokens ({}), rejecting issuance",
                len(self.tokens.issued_tokens),
            )
            return _http_json_response({"error": "too many outstanding tokens"}, status=429)
        token_value = self.tokens.issue_token(self.config.token_ttl_s)
        return _http_json_response(token_response_payload(token_value, self.config.token_ttl_s))

    # -- Bootstrap ----------------------------------------------------------

    def _handle_bootstrap(self, connection: Any, request: Any) -> Response:
        secret = self.config.token_issue_secret.strip() or self.config.token.strip()
        is_local_browser = _is_local_browser_request(connection, request.headers)
        refusal = bootstrap_refusal(
            secret=secret,
            is_local_browser=is_local_browser,
            bind_host=self.config.host,
            headers=request.headers,
        )
        if refusal is not None:
            return _http_error(*refusal)

        api_token_allowed = bool(secret) or is_local_browser
        if not self.tokens.can_issue(include_api_token=api_token_allowed):
            return _http_response(
                json.dumps({"error": "too many outstanding tokens"}).encode("utf-8"),
                status=429,
                content_type="application/json; charset=utf-8",
            )
        token = self.tokens.issue_token(self.config.token_ttl_s)
        api_token = (
            self.tokens.issue_api_token(self.config.token_ttl_s) if api_token_allowed else None
        )

        ws_url = self._bootstrap_ws_url(request)
        expected_path = _normalize_config_path(self.config.path)
        payload = {
            "token": token,
            "ws_path": expected_path,
            "ws_url": ws_url,
            "expires_in": self.config.token_ttl_s,
            "limits": self.ingress.bootstrap_limits(
                max_frame_bytes=self.config.max_message_bytes,
            ),
            "model_name": _resolve_bootstrap_model_name(self.runtime_model_name),
            "runtime_surface": self._runtime_surface,
            "runtime_capabilities": self._capabilities,
        }
        if api_token is not None:
            payload["api_token"] = api_token
        return _http_json_response(payload)

    def _bootstrap_ws_url(self, request: Any) -> str:
        headers = getattr(request, "headers", {}) or {}
        host = _safe_host_header(_case_insensitive_header(headers, "Host"))
        if not host:
            host = _host_for_url(self.config.host, self.config.port)
        proto = _case_insensitive_header(headers, "X-Forwarded-Proto")
        proto = proto.split(",", 1)[0].strip().lower()
        secure = proto in {"https", "wss"} or bool(self.config.ssl_certfile.strip())
        scheme = "wss" if secure else "ws"
        expected_path = _normalize_config_path(self.config.path)
        return f"{scheme}://{host}{expected_path}"

    # -- Session routes -----------------------------------------------------

    async def _dispatch_session_routes(self, request: WsRequest, got: str) -> Response | None:
        m = re.match(r"^/api/sessions/([^/]+)/messages$", got)
        if m:
            # JSONL parse + history scrub can be hundreds of ms on a long
            # chat; keep the gateway loop free for streams and tools.
            return await asyncio.to_thread(
                self._handle_session_messages, request, m.group(1),
            )

        m = re.match(r"^/api/sessions/([^/]+)/webui-thread$", got)
        if m:
            return await asyncio.to_thread(
                self._handle_webui_thread_get, request, m.group(1),
            )

        m = re.match(r"^/api/sessions/([^/]+)/file-preview$", got)
        if m:
            return await asyncio.to_thread(
                self._handle_file_preview, request, m.group(1),
            )

        m = re.match(r"^/api/sessions/([^/]+)/file-download$", got)
        if m:
            return self._handle_file_download(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-render$", got)
        if m:
            # LibreOffice takes seconds on a large deck: off the gateway loop.
            return await asyncio.to_thread(
                self._handle_file_render, request, m.group(1),
            )

        m = re.match(r"^/api/sessions/([^/]+)/file-tree$", got)
        if m:
            return await self._handle_file_tree(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-save$", got)
        if m:
            return self._handle_file_save(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-format$", got)
        if m:
            return await self._handle_file_format(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-create$", got)
        if m:
            return self._handle_file_create(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-delete$", got)
        if m:
            return self._handle_file_delete(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-rename$", got)
        if m:
            return self._handle_file_rename(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-paste$", got)
        if m:
            return self._handle_file_paste(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/metagraph$", got)
        if m:
            return await self._handle_metagraph(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/board$", got)
        if m:
            return await self._handle_board(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/project-brain$", got)
        if m:
            return await self._handle_project_brain(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/resume-seed$", got)
        if m:
            return await self._handle_resume_seed(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/leave-handoff$", got)
        if m:
            return await self._handle_leave_handoff(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/project-rules$", got)
        if m:
            return await self._handle_project_rules(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/project-rules/save$", got)
        if m:
            return await self._handle_project_rules_save(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/board/update$", got)
        if m:
            return await self._handle_board_update(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/board/autonomy$", got)
        if m:
            return await self._handle_board_autonomy(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/cognition$", got)
        if m:
            return await self._handle_cognition(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/agi$", got)
        if m:
            return await self._handle_agi(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/agi/action$", got)
        if m:
            return await self._handle_agi_action(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/agi/draft$", got)
        if m:
            return await self._handle_agi_draft(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/world$", got)
        if m:
            return await self._handle_world(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/world/action$", got)
        if m:
            return await self._handle_world_action(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/policy$", got)
        if m:
            return await self._handle_policy(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/policy/action$", got)
        if m:
            return await self._handle_policy_action(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/transfer$", got)
        if m:
            return await self._handle_transfer(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/transfer/action$", got)
        if m:
            return await self._handle_transfer_action(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/github/issues$", got)
        if m:
            return await self._handle_github_issues(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/github/issues/repo$", got)
        if m:
            return await self._handle_github_issues_repo(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/github/pr-sync$", got)
        if m:
            return await self._handle_github_pr_sync(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/symbols$", got)
        if m:
            return await self._handle_symbols(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/lsp$", got)
        if m:
            return await self._handle_lsp(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/file-search$", got)
        if m:
            return await self._handle_file_search(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/tests$", got)
        if m:
            return await self._handle_test_explorer(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/crm$", got)
        if m:
            return await self._handle_crm(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/assist$", got)
        if m:
            return await self._handle_assist(request, m.group(1))

        if re.match(r"^/api/tenders$", got):
            return await self._handle_tenders(request)
        if re.match(r"^/api/career$", got):
            return await self._handle_career(request)
        if re.match(r"^/api/leads$", got):
            return await self._handle_leads(request)
        if re.match(r"^/api/trading$", got):
            return await self._handle_trading(request)
        if re.match(r"^/api/marketing$", got):
            return await self._handle_marketing_desk(request)
        if re.match(r"^/api/meeting$", got):
            return await self._handle_meeting(request, None)
        if re.match(r"^/api/meetings$", got):
            # Authenticated durable CRUD and integrations use the same
            # mode-based envelope as the established singular endpoint.
            return await self._handle_meeting(request, None)
        m = re.match(r"^/api/sessions/([^/]+)/meeting$", got)
        if m:
            return await self._handle_meeting(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/search$", got)
        if m:
            return await self._handle_project_search(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/replace$", got)
        if m:
            return await self._handle_project_replace(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-changes$", got)
        if m:
            return await self._handle_git_changes(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-commit-message$", got)
        if m:
            return await self._handle_git_commit_message(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-commit$", got)
        if m:
            return await self._handle_git_commit(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-stage$", got)
        if m:
            return await self._handle_git_stage(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-pull$", got)
        if m:
            return await self._handle_git_pull(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-undo(?:-commit)?$", got)
        if m:
            return await self._handle_git_undo_commit(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-sync$", got)
        if m:
            return await self._handle_git_sync(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-discard$", got)
        if m:
            return await self._handle_git_discard(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-stash$", got)
        if m:
            return await self._handle_git_stash(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-fetch$", got)
        if m:
            return await self._handle_git_fetch(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-branch$", got)
        if m:
            return await self._handle_git_branch(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-conflict$", got)
        if m:
            return await self._handle_git_conflict(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/github/pr$", got)
        if m:
            return await self._handle_github_pr(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/github/pr/create$", got)
        if m:
            return await self._handle_github_pr_create(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/github/checks$", got)
        if m:
            return await self._handle_github_checks(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/github/fix-ci$", got)
        if m:
            return await self._handle_github_fix_ci(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/review-prompt$", got)
        if m:
            return await self._handle_review_prompt(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/checkpoints(?:/(create|diff|restore))?$", got)
        if m:
            return await self._handle_checkpoints(request, m.group(1), m.group(2) or "list")

        m = re.match(r"^/api/sessions/([^/]+)/multitask-spawn$", got)
        if m:
            return await self._handle_multitask_spawn(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/debug$", got)
        if m:
            return await self._handle_debug(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-log$", got)
        if m:
            return await self._handle_git_log(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/context-usage$", got)
        if m:
            return await self._handle_context_usage(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-diff$", got)
        if m:
            return await self._handle_git_diff(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/git-blame$", got)
        if m:
            return await self._handle_git_blame(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/review-changes$", got)
        if m:
            return await self._handle_review_changes(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/review-file$", got)
        if m:
            return await self._handle_review_file(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/review-action$", got)
        if m:
            return await self._handle_review_action(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/automations$", got)
        if m:
            return self._handle_session_automations(request, m.group(1))

        m = re.match(r"^/api/sessions/([^/]+)/delete$", got)
        if m:
            return self._handle_session_delete(request, m.group(1))

        return None

    async def _handle_sessions_list(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        payload = await self.route_cache.get(
            "webui:sessions",
            lambda: self._gated(asyncio.to_thread(self._sessions_list_payload)),
            ttl=_SESSIONS_CACHE_TTL_S,
        )
        return _http_json_response(payload)

    async def _gated(self, work: Awaitable[Any]) -> Any:
        """Run one heavy computation under the shared concurrency cap.

        Only the caller that actually computes takes a permit. Taking it around
        the cache lookup instead let four windows waiting on the *same* skills
        scan hold every permit, so the sessions list queued behind a result it
        did not need - one slow route turned into three.
        """
        async with self._heavy_routes:
            return await work

    def _sessions_list_payload(self) -> dict[str, Any]:
        assert self.session_manager is not None
        sessions = list_webui_sessions(self.session_manager)
        from navin.session.webui_turns import websocket_turn_wall_started_at

        default_scope = self.workspaces.default_scope().payload()
        cleaned = []
        for s in sessions:
            key = s.get("key")
            if not (isinstance(key, str) and key.startswith("websocket:")):
                continue
            row = {k: v for k, v in s.items() if k not in {"path", "workspace_scope"}}
            chat_id = key.split(":", 1)[1]
            started_at = websocket_turn_wall_started_at(chat_id)
            if started_at is not None:
                row["run_started_at"] = started_at
            row["workspace_scope"] = self.workspaces.list_scope_payload(
                s.get("workspace_scope"),
                default=default_scope,
            )
            cleaned.append(row)
        return {"sessions": cleaned}

    def _handle_session_messages(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        data = self.session_manager.read_session_file(decoded_key)
        if data is None:
            return _http_error(404, "session not found")
        messages = data.get("messages")
        if isinstance(messages, list):
            scrub_subagent_messages_for_channel(messages)
            data["messages"] = public_history_messages(
                message for message in messages if isinstance(message, dict)
            )
        self.media.augment_media_urls(data)
        return _http_json_response(data)

    def _handle_webui_thread_get(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        scope = self.workspaces.scope_for_session_key(decoded_key)
        session_messages: list[dict[str, Any]] | None = None
        if self.session_manager is not None:
            session_data = self.session_manager.read_session_file(decoded_key)
            raw_messages = session_data.get("messages") if isinstance(session_data, dict) else None
            if isinstance(raw_messages, list):
                session_messages = [m for m in raw_messages if isinstance(m, dict)]
        query = _parse_query(request.path)
        raw_limit = _query_first(query, "limit")
        limit: int | None = None
        if raw_limit is not None and raw_limit.strip():
            try:
                limit = int(raw_limit)
            except ValueError:
                return _http_error(400, "invalid limit")
        direction = _query_first(query, "direction")
        if direction is not None and direction not in {"latest"}:
            return _http_error(400, "invalid direction")
        before = _query_first(query, "before")
        data = build_webui_thread_response(
            decoded_key,
            augment_user_media=self.media.augment_transcript_media,
            augment_assistant_media=self.media.augment_transcript_media,
            augment_assistant_text=lambda text: self.media.rewrite_local_markdown_images(
                text,
                workspace_path=scope.project_path,
            ),
            session_messages=session_messages,
            limit=limit,
            direction=direction,
            before=before,
        )
        if data is None:
            # A chat where nothing has been said yet has an empty thread, not a
            # missing one. Answering 404 made every new session, and every poll
            # of one, a red line in the browser console for a normal state.
            data = {
                "schemaVersion": WEBUI_TRANSCRIPT_SCHEMA_VERSION,
                "sessionKey": decoded_key,
                "messages": [],
                "has_pending_tool_calls": False,
            }
        data["workspace_scope"] = scope.payload()
        return _http_json_response(data)

    def _handle_file_preview(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        is_probe = _query_first(query, "probe") == "1"
        allow_any = _query_first(query, "any") == "1"
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            extra_roots = _extra_file_roots(query, scope)
            if is_probe:
                payload = file_preview_availability_payload(
                    path,
                    scope=scope,
                    extra_roots=extra_roots,
                )
            else:
                payload = file_preview_payload(
                    path,
                    scope=scope,
                    allow_any=allow_any,
                    extra_roots=extra_roots,
                )
        except WebUIFilePreviewError as e:
            if is_probe and e.status in {400, 403, 404, 415}:
                return _http_json_response({"available": False})
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _handle_file_download(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            extra_roots = _extra_file_roots(query, scope)
            body, content_type, filename, _meta = file_download_payload(
                path,
                scope=scope,
                extra_roots=extra_roots,
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_response(
            body,
            content_type=content_type,
            extra_headers=[
                ("Content-Disposition", content_disposition_attachment(filename)),
                ("X-Content-Type-Options", "nosniff"),
                ("Cache-Control", "private, no-store"),
            ],
        )

    def _handle_file_render(self, request: WsRequest, key: str) -> Response:
        """A Word / PowerPoint / Excel file as the PDF the viewer can show."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            extra_roots = _extra_file_roots(query, scope)
            body, content_type, filename = office_render_payload(
                path,
                scope=scope,
                extra_roots=extra_roots,
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_response(
            body,
            content_type=content_type,
            extra_headers=[
                ("Content-Disposition", content_disposition_attachment(filename)),
                ("X-Content-Type-Options", "nosniff"),
                ("Cache-Control", "private, no-store"),
            ],
        )

    def _handle_file_save(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        try:
            content = file_body_from_headers(request.headers)
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = file_save_payload(path, content, scope=scope)
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_file_format(self, request: WsRequest, key: str) -> Response:
        """Format Document: run the project's formatter over the sent buffer."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path") or ""
        from navin.webui.format_api import FormatApiError, format_payload

        try:
            content = file_body_from_headers(request.headers)
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(format_payload, scope, path=path, content=content)
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        except FormatApiError as e:
            return _http_error(e.status, e.message)
        except LspApiError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _handle_file_create(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        kind = (_query_first(query, "kind") or "file").strip()
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = file_create_payload(path, kind=kind, scope=scope)
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _handle_file_delete(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = file_delete_payload(_query_first(query, "path"), scope=scope)
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _handle_file_rename(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = file_rename_payload(
                _query_first(query, "path"),
                _query_first(query, "name"),
                scope=scope,
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _handle_file_paste(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = file_paste_payload(
                _query_first(query, "path"),
                _query_first(query, "parent"),
                move=_query_first(query, "move") in {"1", "true", "yes"},
                scope=scope,
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_file_tree(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            # Own pool so expand never waits behind git status / CI / settings
            # to_thread work. Do not go through route_cache: coalescing with an
            # in-flight listing started before the agent wrote the file would
            # keep the explorer showing an empty folder.
            payload = await asyncio.get_running_loop().run_in_executor(
                _FILE_TREE_EXECUTOR,
                partial(file_tree_payload, path, scope=scope),
            )
        except WebUIFileTreeError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_metagraph(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        view = "packages" if (_query_first(query, "view") or "").strip() == "packages" else "files"
        aspect = 16 / 9
        raw_aspect = _query_first(query, "aspect")
        if raw_aspect:
            try:
                aspect = max(0.25, min(4.0, float(raw_aspect)))
            except (TypeError, ValueError):
                aspect = 16 / 9
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(metagraph_payload, scope, view=view, aspect=aspect)
        except MetagraphError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_board(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(board_payload, scope, decoded_key)
        except BoardError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_project_brain(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(project_brain_payload, scope)
        except ProjectBrainError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_resume_seed(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(resume_seed_payload, scope)
        except ContinuityApiError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_leave_handoff(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        body = _query_first(query, "body") or ""
        try:
            from navin.webui.file_preview import WebUIFilePreviewError, file_body_from_headers

            try:
                body = file_body_from_headers(request.headers) or body
            except WebUIFilePreviewError:
                pass
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                leave_handoff_payload,
                scope,
                body=body,
                session_key=decoded_key,
            )
        except ContinuityApiError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_project_rules(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(project_rules_payload, scope)
        except ProjectRulesError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_project_rules_save(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        try:
            from navin.webui.file_preview import WebUIFilePreviewError, file_body_from_headers

            content = file_body_from_headers(request.headers)
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                save_project_rule_payload,
                scope,
                name=name,
                content=content,
            )
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        except ProjectRulesError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_board_update(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        raw_op = _query_first(query, "op")
        if raw_op is None:
            return _http_error(400, "missing op")
        try:
            op = json.loads(raw_op)
        except json.JSONDecodeError:
            return _http_error(400, "op must be JSON")
        if not isinstance(op, dict):
            return _http_error(400, "op must be an object")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(board_update_payload, scope, op, decoded_key)
        except BoardError as e:
            return _http_error(e.status, e.message)
        publish_board_update(self.bus, str(scope.project_path))
        return _http_json_response(payload)

    async def _handle_board_autonomy(self, request: WsRequest, key: str) -> Response:
        """Read or update the project's board autonomy consent and toggles."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        raw_fields = _query_first(query, "fields")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            if raw_fields is None:
                from navin.board.autonomy import autonomy_state

                state = await asyncio.to_thread(
                    autonomy_state, Path(scope.project_path).expanduser()
                )
                return _http_json_response(state)
            try:
                fields = json.loads(raw_fields)
            except json.JSONDecodeError:
                return _http_error(400, "fields must be JSON")
            if not isinstance(fields, dict):
                return _http_error(400, "fields must be an object")
            actor = _query_first(query, "actor")
            state = await asyncio.to_thread(
                board_autonomy_update_payload, scope, fields, actor=actor
            )
        except BoardError as e:
            return _http_error(e.status, e.message)
        publish_board_update(self.bus, str(scope.project_path))
        return _http_json_response(state)

    async def _handle_cognition(self, request: WsRequest, key: str) -> Response:
        """Read or update the project's cognition opt-in (.navin/cognition.json)."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        from navin.cognition import cognition_state, recall_registered, update_settings

        query = _parse_query(request.path)
        raw_fields = _query_first(query, "fields")
        scope = self.workspaces.scope_for_session_key(decoded_key)
        project = Path(scope.project_path).expanduser()
        registry = self._registry()

        def _state() -> dict[str, Any]:
            present = recall_registered(registry) if registry is not None else None
            return cognition_state(project, recall_registered=present)

        if raw_fields is None:
            return _http_json_response(await asyncio.to_thread(_state))
        try:
            fields = json.loads(raw_fields)
        except json.JSONDecodeError:
            return _http_error(400, "fields must be JSON")
        if not isinstance(fields, dict):
            return _http_error(400, "fields must be an object")
        try:
            await asyncio.to_thread(update_settings, project, fields)
        except ValueError as e:
            return _http_error(400, str(e))
        except OSError as e:
            return _http_error(500, f"could not persist cognition settings: {e}")
        # Registry writes stay on the event loop thread, next to the agent.
        self._sync_recall_tool(project)
        return _http_json_response(await asyncio.to_thread(_state))

    def _agi_project(self, request: WsRequest, key: str) -> tuple[Path | None, Response | None]:
        if not self.check_api_token(request):
            return None, _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return None, _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return None, _http_error(404, "session not found")
        scope = self.workspaces.scope_for_session_key(decoded_key)
        return Path(scope.project_path).expanduser(), None

    async def _handle_agi(self, request: WsRequest, key: str) -> Response:
        """Read or update the project's skills evolution flag (.navin/skills-evolve.json).

        The AGI panel reads the whole state (flag, drafts, scores, journal)
        and writes one switch at a time through ``?fields={...}``.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.skills_evolve.state import AgiActionError, agi_state, agi_update

        query = _parse_query(request.path)
        raw_fields = _query_first(query, "fields")
        if raw_fields is None:
            return _http_json_response(await asyncio.to_thread(agi_state, project))
        try:
            fields = json.loads(raw_fields)
        except json.JSONDecodeError:
            return _http_error(400, "fields must be JSON")
        if not isinstance(fields, dict):
            return _http_error(400, "fields must be an object")
        try:
            state = await asyncio.to_thread(agi_update, project, fields)
        except AgiActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"could not persist skills-evolve settings: {e}")
        return _http_json_response(state)

    async def _handle_agi_action(self, request: WsRequest, key: str) -> Response:
        """Press one AGI button: draft, run, exam, promote, force, publish, rollback, discard, guard.

        ``publish`` and ``force`` need ``actor=human``; the panel sends it, the
        engine has no route here.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.skills_evolve.state import AgiActionError, agi_action

        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "").strip()
        name = (_query_first(query, "name") or "").strip() or None
        actor = (_query_first(query, "actor") or "auto").strip() or "auto"
        raw_brief = _query_first(query, "brief")
        brief: dict[str, Any] | None = None
        if raw_brief:
            try:
                parsed = json.loads(raw_brief)
            except json.JSONDecodeError:
                return _http_error(400, "brief must be JSON")
            if not isinstance(parsed, dict):
                return _http_error(400, "brief must be an object")
            brief = parsed
        if not action:
            return _http_error(400, "action is required")
        try:
            payload = await asyncio.to_thread(
                agi_action, project, action, name=name, actor=actor, brief=brief
            )
        except AgiActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"skills-evolve action failed: {e}")
        return _http_json_response(payload)

    async def _handle_agi_draft(self, request: WsRequest, key: str) -> Response:
        """The SKILL.md text of one draft, for the panel's preview."""
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.skills_evolve.state import draft_text

        query = _parse_query(request.path)
        name = (_query_first(query, "name") or "").strip()
        if not name:
            return _http_error(400, "name is required")
        text = await asyncio.to_thread(draft_text, project, name)
        if text is None:
            return _http_error(404, f"no draft named {name}")
        return _http_json_response({"name": name, "markdown": text})

    def _sync_world_tool(self, *extra: Path) -> bool | None:
        """Align the world_predict tool with the projects' advice gates; None when unknown."""
        registry = self._registry()
        if registry is None:
            return None
        from navin.world_model.registration import sync_world_tool

        return sync_world_tool(registry, workspace=self.skills_workspace_path, extra=extra)

    async def _handle_world(self, request: WsRequest, key: str) -> Response:
        """Read or update the project's world model flag (.navin/world-model.json).

        The AGI panel reads the whole state (flag, journal size, frozen set,
        checkpoints, scores, gate, beliefs) and writes one switch at a time
        through ``?fields={...}``. ``advise: true`` answers 409 while the gate
        is closed.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.world_model.state import WorldActionError, world_state, world_update

        query = _parse_query(request.path)
        raw_fields = _query_first(query, "fields")
        if raw_fields is None:
            state = await asyncio.to_thread(world_state, project)
            # A flag flipped from the CLI reaches the serving agent at the next
            # panel read; the answer says whether the tool is live right now.
            state["tool_registered"] = self._sync_world_tool(project)
            return _http_json_response(state)
        try:
            fields = json.loads(raw_fields)
        except json.JSONDecodeError:
            return _http_error(400, "fields must be JSON")
        if not isinstance(fields, dict):
            return _http_error(400, "fields must be an object")
        try:
            state = await asyncio.to_thread(world_update, project, fields)
        except WorldActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"could not persist world-model settings: {e}")
        # Registry writes stay on the event loop thread, next to the agent.
        state["tool_registered"] = self._sync_world_tool(project)
        return _http_json_response(state)

    async def _handle_world_action(self, request: WsRequest, key: str) -> Response:
        """Press one world model button: train, run, exam, freeze, rollback, ab, beliefs, discard_belief.

        The human-only ones (train, freeze, rollback, discard_belief) need
        ``actor=human``; the panel sends it, the engine has no route here.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.world_model.state import WorldActionError, world_action

        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "").strip()
        actor = (_query_first(query, "actor") or "auto").strip() or "auto"
        belief_key = (_query_first(query, "key") or "").strip() or None
        if not action:
            return _http_error(400, "action is required")
        try:
            payload = await asyncio.to_thread(world_action, project, action, actor=actor, key=belief_key)
        except WorldActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"world-model action failed: {e}")
        registered = self._sync_world_tool(project)
        if isinstance(payload.get("state"), dict):
            payload["state"]["tool_registered"] = registered
        return _http_json_response(payload)

    def _sync_policy_tool(self, *extra: Path) -> bool | None:
        """Align the policy_next tool with the projects' steer gates; None when unknown."""
        registry = self._registry()
        if registry is None:
            return None
        from navin.policy.registration import sync_policy_tool

        return sync_policy_tool(registry, workspace=self.skills_workspace_path, extra=extra)

    async def _handle_policy(self, request: WsRequest, key: str) -> Response:
        """Read or update the project's policy flag (.navin/policy.json).

        The AGI panel reads the whole state (flag, radar, battery,
        trajectories, frozen set, adapters, verdicts, gate, A/B, live) and
        writes one switch at a time through ``?fields={...}``.
        ``enabled: true`` answers 409 while the world model radar is not up;
        ``steer: true`` answers 409 while the gate is closed.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.policy.state import PolicyActionError, policy_state, policy_update

        query = _parse_query(request.path)
        raw_fields = _query_first(query, "fields")
        if raw_fields is None:
            state = await asyncio.to_thread(policy_state, project)
            state["tool_registered"] = self._sync_policy_tool(project)
            return _http_json_response(state)
        try:
            fields = json.loads(raw_fields)
        except json.JSONDecodeError:
            return _http_error(400, "fields must be JSON")
        if not isinstance(fields, dict):
            return _http_error(400, "fields must be an object")
        try:
            state = await asyncio.to_thread(policy_update, project, fields)
        except PolicyActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"could not persist policy settings: {e}")
        state["tool_registered"] = self._sync_policy_tool(project)
        return _http_json_response(state)

    async def _handle_policy_action(self, request: WsRequest, key: str) -> Response:
        """Press one policy button: train, run, exam, freeze, rollback, force, ab, publish, unpublish, adopt.

        The human-only ones need ``actor=human``; the panel sends it, the
        engine has no route here. ``train`` runs in a child process.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.policy.state import PolicyActionError, policy_action

        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "").strip()
        actor = (_query_first(query, "actor") or "auto").strip() or "auto"
        name = (_query_first(query, "name") or "").strip() or None
        raw_number = (_query_first(query, "number") or "").strip()
        number: int | None = None
        if raw_number:
            try:
                number = int(raw_number)
            except ValueError:
                return _http_error(400, "number must be an integer")
        if not action:
            return _http_error(400, "action is required")
        try:
            payload = await asyncio.to_thread(policy_action, project, action, actor=actor, name=name, number=number)
        except PolicyActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"policy action failed: {e}")
        registered = self._sync_policy_tool(project)
        if isinstance(payload.get("state"), dict):
            payload["state"]["tool_registered"] = registered
        return _http_json_response(payload)

    async def _handle_transfer(self, request: WsRequest, key: str) -> Response:
        """Read or update the project's transfer flag (.navin/transfer.json, S5).

        The AGI panel reads the whole state (flag, prerequisites, suites
        counts and lock, campaign per family, safety dossier, claim) and
        writes one field at a time through ``?fields={...}``.
        ``enabled: true`` answers 409 while S2, S3.3 or S4.3 are not up.
        Nothing here registers a tool or touches a turn.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.transfer.state import TransferActionError, transfer_state, transfer_update

        query = _parse_query(request.path)
        raw_fields = _query_first(query, "fields")
        if raw_fields is None:
            return _http_json_response(await asyncio.to_thread(transfer_state, project))
        try:
            fields = json.loads(raw_fields)
        except json.JSONDecodeError:
            return _http_error(400, "fields must be JSON")
        if not isinstance(fields, dict):
            return _http_error(400, "fields must be an object")
        try:
            state = await asyncio.to_thread(transfer_update, project, fields)
        except TransferActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"could not persist transfer settings: {e}")
        return _http_json_response(state)

    async def _handle_transfer_action(self, request: WsRequest, key: str) -> Response:
        """Press one transfer button: campaign, replay, safety, kill_drill, verify.

        The human-only ones need ``actor=human``; the panel sends it, the
        engine has no route here. ``campaign`` runs in a child process.
        ``freeze`` needs names and lives in the CLI.
        """
        project, failure = self._agi_project(request, key)
        if failure is not None or project is None:
            return failure or _http_error(500, "no project")
        from navin.transfer.state import TransferActionError, transfer_action

        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "").strip()
        actor = (_query_first(query, "actor") or "auto").strip() or "auto"
        name = (_query_first(query, "name") or "").strip() or None
        if not action:
            return _http_error(400, "action is required")
        if action == "freeze":
            return _http_error(400, "freeze needs authors and an attester: use `navin agi transfer freeze`")
        try:
            payload = await asyncio.to_thread(transfer_action, project, action, actor=actor, name=name)
        except TransferActionError as e:
            return _http_error(e.status, str(e))
        except OSError as e:
            return _http_error(500, f"transfer action failed: {e}")
        return _http_json_response(payload)

    async def _handle_github_issues(self, request: WsRequest, key: str) -> Response:
        """List the bound project's forge issues (GitHub, GitLab, Forgejo)."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        state = _query_first(query, "state") or "open"
        project_override = (_query_first(query, "project") or "").strip() or None
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                github_issues_payload,
                scope,
                state=state,
                project_path=project_override,
            )
        except BoardError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_github_issues_repo(self, request: WsRequest, key: str) -> Response:
        """Set (``repo=owner/name``) or clear (``repo=``) the project's tracker."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        if "repo" not in query:
            return _http_error(400, "missing repo")
        repo = _query_first(query, "repo") or ""
        project_override = (_query_first(query, "project") or "").strip() or None
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                github_issues_repo_payload,
                scope,
                repo,
                project_path=project_override,
                actor=_query_first(query, "actor"),
            )
        except BoardError as e:
            return _http_error(e.status, e.message)
        publish_board_update(self.bus, str(payload.get("project_path") or scope.project_path))
        return _http_json_response(payload)

    async def _handle_github_pr_sync(self, request: WsRequest, key: str) -> Response:
        """Suggest board updates when linked task PRs have merged (no auto-close)."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(github_pr_sync_payload, scope)
        except BoardError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_symbols(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        try:
            limit = int(_query_first(query, "limit") or 50)
        except ValueError:
            limit = 50
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            # F12 / Ctrl-click: ?symbol=Name resolves definitions.
            # Outline panel: ?outline=1&path=file.py lists document symbols.
            symbol = _query_first(query, "symbol")
            outline = (_query_first(query, "outline") or "").strip().lower()
            if symbol:
                payload = await asyncio.to_thread(
                    goto_definition_payload,
                    scope,
                    symbol=symbol,
                    path=path,
                    limit=limit,
                )
            elif outline in {"1", "true", "yes"}:
                if not path:
                    return _http_error(400, "missing path")
                payload = await asyncio.to_thread(
                    outline_payload,
                    scope,
                    path=path,
                    limit=limit,
                )
            else:
                term = _query_first(query, "q") or ""
                payload = await asyncio.to_thread(
                    symbols_payload,
                    scope,
                    term,
                    path=path,
                    limit=limit,
                )
        except SymbolsError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_lsp(self, request: WsRequest, key: str) -> Response:
        """Editor LSP: hover, definition-at-caret, rename preview/apply."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "").strip().lower()
        path = _query_first(query, "path") or ""
        try:
            line = int(_query_first(query, "line") or "0")
            col = int(_query_first(query, "col") or "0")
        except ValueError:
            return _http_error(400, "invalid line/col")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            try:
                content = file_body_from_headers(request.headers)
            except WebUIFilePreviewError as exc:
                if exc.message != "missing file content":
                    return _http_error(exc.status, exc.message)
                content = None
            if action == "hover":
                payload = await asyncio.to_thread(
                    hover_payload,
                    scope,
                    path=path,
                    line=line,
                    col=col,
                    content=content,
                )
            elif action == "definition":
                try:
                    limit = int(_query_first(query, "limit") or 20)
                except ValueError:
                    limit = 20
                payload = await asyncio.to_thread(
                    definition_at_payload,
                    scope,
                    path=path,
                    line=line,
                    col=col,
                    limit=limit,
                    content=content,
                )
            elif action == "references":
                try:
                    limit = int(_query_first(query, "limit") or 60)
                except ValueError:
                    limit = 60
                payload = await asyncio.to_thread(
                    references_at_payload,
                    scope,
                    path=path,
                    line=line,
                    col=col,
                    limit=limit,
                    content=content,
                )
            elif action == "rename":
                new_name = (_query_first(query, "new_name") or "").strip()
                apply_raw = (_query_first(query, "apply") or "").strip().lower()
                apply = apply_raw in {"1", "true", "yes", "on"}
                payload = await asyncio.to_thread(
                    rename_payload,
                    scope,
                    path=path,
                    line=line,
                    col=col,
                    new_name=new_name,
                    apply=apply,
                    content=content,
                )
            elif action == "completion":
                trigger = (_query_first(query, "trigger") or "").strip() or None
                try:
                    limit = int(_query_first(query, "limit") or 80)
                except ValueError:
                    limit = 80
                payload = await asyncio.to_thread(
                    lsp_completion_payload,
                    scope,
                    path=path,
                    line=line,
                    col=col,
                    content=content,
                    trigger=trigger,
                    limit=limit,
                )
            elif action in {"signature", "signaturehelp", "signature_help"}:
                trigger = (_query_first(query, "trigger") or "").strip() or None
                payload = await asyncio.to_thread(
                    signature_help_payload,
                    scope,
                    path=path,
                    line=line,
                    col=col,
                    content=content,
                    trigger=trigger,
                )
            elif action in {"codeaction", "code_action", "codeactions", "code_actions"}:
                try:
                    end_line = int(_query_first(query, "end_line") or line)
                    end_col = int(_query_first(query, "end_col") or col)
                except ValueError:
                    end_line, end_col = line, col
                try:
                    limit = int(_query_first(query, "limit") or 40)
                except ValueError:
                    limit = 40
                payload = await asyncio.to_thread(
                    code_actions_payload,
                    scope,
                    path=path,
                    line=line,
                    col=col,
                    end_line=end_line,
                    end_col=end_col,
                    content=content,
                    limit=limit,
                )
            else:
                return _http_error(
                    400,
                    "unknown lsp action (use hover, definition, references, "
                    "rename, completion, signature, or codeaction)",
                )
        except LspApiError as e:
            return _http_error(e.status, e.message)
        except SymbolsError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_file_search(self, request: WsRequest, key: str) -> Response:
        """Fuzzy file and folder lookup for @-mentions in the composer."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        term = _query_first(query, "q") or ""
        try:
            limit = int(_query_first(query, "limit") or 12)
        except ValueError:
            limit = 12
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                file_search_payload,
                scope,
                term,
                limit=limit,
            )
        except FileSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_test_explorer(self, request: WsRequest, key: str) -> Response:
        """Test Explorer: collect suites/tests or run a targeted selection."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        from navin.webui.test_explorer_api import (
            TestExplorerError,
            collect_payload,
            run_payload,
        )

        query = _parse_query(request.path)
        mode = (_query_first(query, "mode") or "collect").strip()
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            if mode == "collect":
                # Collection imports test modules; minutes on a monorepo, so
                # keep it off the event loop like a full run.
                payload = await asyncio.to_thread(collect_payload, scope)
            elif mode == "run":
                payload = await asyncio.to_thread(
                    run_payload,
                    scope,
                    runner=_query_first(query, "runner") or "",
                    target=_query_first(query, "target"),
                )
            else:
                return _http_error(400, f"unknown mode: {mode!r}")
        except TestExplorerError as e:
            return _http_error(e.status, e.message)
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        except Exception as exc:
            self._log.exception("test explorer failed mode={}", mode)
            return _http_error(500, str(exc) or "could not collect tests")
        return _http_json_response(payload)

    async def _handle_crm(self, request: WsRequest, key: str) -> Response:
        """Project CRM: contacts, companies, leads, opportunities, activities."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        from navin.crm.store import CrmError
        from navin.webui.crm_api import (
            accept_payload,
            audit_payload,
            calendar_payload,
            channels_payload,
            convert_payload,
            create_payload,
            dashboard_payload,
            delete_payload,
            followups_payload,
            get_payload,
            insights_payload,
            invite_payload,
            kick_payload,
            lines_payload,
            list_payload,
            members_payload,
            outreach_payload,
            products_payload,
            role_payload,
            search_payload,
            settings_payload,
            sync_payload,
            timeline_payload,
            update_payload,
            update_settings_payload,
        )

        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "list").strip()
        kind = (_query_first(query, "kind") or "contacts").strip()
        record_id = (_query_first(query, "id") or "").strip()
        actor = (_query_first(query, "actor") or "").strip()
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            body: dict[str, Any] = {}
            if action in {
                "create",
                "update",
                "convert",
                "activity",
                "invite",
                "accept",
                "role",
                "kick",
                "outreach",
                "followups",
                "update_settings",
            }:
                try:
                    raw = file_body_from_headers(request.headers) or "{}"
                    parsed = json.loads(raw)
                    body = parsed if isinstance(parsed, dict) else {}
                except (WebUIFilePreviewError, json.JSONDecodeError) as exc:
                    if isinstance(exc, WebUIFilePreviewError):
                        return _http_error(exc.status, exc.message)
                    return _http_error(400, "invalid CRM payload")
            if actor and "actor" not in body:
                body["actor"] = actor
            if action == "list":
                payload = await asyncio.to_thread(list_payload, scope, kind)
            elif action == "get":
                payload = await asyncio.to_thread(get_payload, scope, kind, record_id)
            elif action == "create" or action == "activity":
                payload = await asyncio.to_thread(
                    create_payload, scope, "activities" if action == "activity" else kind, body
                )
            elif action == "update":
                payload = await asyncio.to_thread(update_payload, scope, kind, record_id, body)
            elif action == "delete":
                payload = await asyncio.to_thread(
                    delete_payload, scope, kind, record_id, actor or str(body.get("actor") or "")
                )
            elif action == "convert":
                payload = await asyncio.to_thread(
                    convert_payload,
                    scope,
                    record_id or str(body.get("id") or ""),
                    str(body.get("owner") or ""),
                    str(body.get("actor") or actor or ""),
                    body,
                )
            elif action == "dashboard":
                payload = await asyncio.to_thread(dashboard_payload, scope)
            elif action == "settings":
                payload = await asyncio.to_thread(
                    settings_payload, scope, actor or str(body.get("actor") or "")
                )
            elif action == "update_settings":
                payload = await asyncio.to_thread(update_settings_payload, scope, body)
            elif action == "insights":
                payload = await asyncio.to_thread(insights_payload, scope, kind, record_id)
            elif action == "timeline":
                payload = await asyncio.to_thread(timeline_payload, scope, kind, record_id)
            elif action == "search":
                payload = await asyncio.to_thread(
                    search_payload, scope, (_query_first(query, "q") or "").strip()
                )
            elif action == "audit":
                payload = await asyncio.to_thread(audit_payload, scope, kind, record_id)
            elif action == "members":
                payload = await asyncio.to_thread(members_payload, scope, actor or str(body.get("actor") or ""))
            elif action == "invite":
                payload = await asyncio.to_thread(invite_payload, scope, body)
            elif action == "accept":
                payload = await asyncio.to_thread(accept_payload, scope, body)
            elif action == "role":
                payload = await asyncio.to_thread(role_payload, scope, body)
            elif action == "kick":
                payload = await asyncio.to_thread(kick_payload, scope, body)
            elif action == "products":
                payload = await asyncio.to_thread(products_payload, scope)
            elif action == "lines":
                payload = await asyncio.to_thread(
                    lines_payload, scope, record_id or str(body.get("opportunityId") or "")
                )
            elif action == "followups":
                payload = await asyncio.to_thread(
                    followups_payload,
                    scope,
                    days=int(body.get("days") or _query_first(query, "days") or 7),
                    create=bool(body.get("create") or _query_first(query, "create") == "1"),
                    actor=actor or str(body.get("actor") or ""),
                )
            elif action == "sync":
                payload = await asyncio.to_thread(sync_payload, scope)
            elif action == "calendar":
                start = int(_query_first(query, "start") or 0)
                end = int(_query_first(query, "end") or 0)
                payload = await asyncio.to_thread(calendar_payload, scope, start, end)
            elif action == "outreach":
                payload = await asyncio.to_thread(outreach_payload, scope, body)
            elif action == "channels":
                payload = await asyncio.to_thread(channels_payload)
            else:
                return _http_error(400, f"unknown CRM action: {action!r}")
        except CrmError as exc:
            return _http_error(exc.status, exc.message)
        except ProjectSearchError as exc:
            return _http_error(exc.status, exc.message)
        except ValueError as exc:
            return _http_error(400, str(exc))
        return _http_json_response(payload)

    async def _handle_assist(self, request: WsRequest, key: str) -> Response:
        """Inline AI completion and Cmd+K edits for the code editor."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        mode = (_query_first(query, "mode") or "complete").strip()
        path = _query_first(query, "path") or ""
        language = _query_first(query, "language") or ""
        try:
            # Editor context is too large for a query string and the gateway's
            # HTTP layer has no request body, so it arrives base64-chunked in
            # headers like file-save does.
            raw = file_body_from_headers(request.headers)
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        try:
            body = json.loads(raw)
        except ValueError:
            return _http_error(400, "invalid assist payload")
        if not isinstance(body, dict):
            return _http_error(400, "invalid assist payload")

        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            project_root = scope.project_path
            related_raw = body.get("related_files")
            related_files = related_raw if isinstance(related_raw, list) else None
            if mode == "complete":
                from navin.webui.assist_api import sanitize_recent_edits

                payload = await completion_payload(
                    path=path,
                    prefix=str(body.get("prefix") or ""),
                    suffix=str(body.get("suffix") or ""),
                    language=language,
                    related_files=related_files,
                    project_root=project_root,
                    recent_edits=sanitize_recent_edits(body.get("recent_edits")),
                )
            elif mode == "edit":
                payload = await edit_payload(
                    path=path,
                    selection=str(body.get("selection") or ""),
                    instruction=str(body.get("instruction") or ""),
                    prefix=str(body.get("prefix") or ""),
                    suffix=str(body.get("suffix") or ""),
                    language=language,
                )
            else:
                return _http_error(400, f"unknown assist mode: {mode}")
        except AssistError as e:
            if mode == "complete" and e.status in (502, 504):
                # Ghost text is opportunistic: a slow or unreachable model just
                # means "no suggestion". Answering 200 keeps the browser console
                # free of 504 noise while the editor moves on silently.
                return _http_json_response(
                    {
                        "completion": "",
                        "model": "",
                        "route": "",
                        "mode": "",
                        "reason": e.message,
                    }
                )
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _studio_request_context(
        self,
        module: str,
        action: str,
        query: dict[str, list[str]],
        body: dict[str, Any],
        *,
        session_key: str | None = None,
    ) -> RequestContext:
        """Use the desk's bound project when loading task-specific skills."""
        raw_key = str(
            session_key
            or _query_first(query, "session_key")
            or body.get("session_key")
            or ""
        ).strip()
        key = _decode_api_key(raw_key) if raw_key else None
        if raw_key and (key is None or not _is_websocket_channel_session_key(key)):
            raise ValueError("invalid session key")
        controller = getattr(self, "workspaces", None)
        scope = (
            controller.scope_for_session_key(key) if key else controller.default_scope()
        ) if controller is not None else None
        return RequestContext(
            channel="websocket",
            chat_id=key.partition(":")[2] if key else module,
            session_key=key,
            workspace=Path(scope.project_path) if scope is not None else None,
            metadata={
                "product_module": module,
                "action": action,
                "disabled_skills": sorted(getattr(self, "disabled_skills", set())),
            },
        )

    async def _handle_tenders(self, request: WsRequest) -> Response:
        """Navin Tenders desk. Same store as the `tenders` tool."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "snapshot").strip()
        body: dict[str, Any] = {}
        try:
            raw = file_body_from_headers(request.headers) or ""
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
        except WebUIFilePreviewError as exc:
            if exc.message != "missing file content":
                return _http_error(exc.status, exc.message)
        except ValueError:
            return _http_error(400, "invalid tenders payload")
        from navin.tenders.errors import TenderError
        from navin.webui.tenders_api import handle_tenders_action

        try:
            context = self._studio_request_context("tenders", action, query, body)
        except ValueError as exc:
            return _http_error(400, str(exc))
        try:
            with request_context(context):
                payload = await asyncio.to_thread(handle_tenders_action, action, body)
        except TenderError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            self._log.exception("tenders desk failed action={}", action)
            return _http_error(500, str(exc) or "tenders desk failed")
        return _http_json_response(payload)

    async def _handle_leads(self, request: WsRequest) -> Response:
        """Navin Leads desk. Waterfall discovery + BYOK enrichment."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "snapshot").strip()
        body: dict[str, Any] = {}
        try:
            raw = file_body_from_headers(request.headers) or ""
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
        except WebUIFilePreviewError as exc:
            if exc.message != "missing file content":
                return _http_error(exc.status, exc.message)
        except ValueError:
            return _http_error(400, "invalid leads payload")
        from navin.leads.errors import LeadsError
        from navin.webui.leads_api import handle_leads_action

        try:
            payload = await asyncio.to_thread(handle_leads_action, action, body)
        except LeadsError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            self._log.exception("leads desk failed action={}", action)
            return _http_error(500, str(exc) or "leads desk failed")
        return _http_json_response(payload)

    async def _handle_career(self, request: WsRequest) -> Response:
        """Navin Career desk (Freelance + Jobs). Same store as the `career` tool."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "snapshot").strip()
        body: dict[str, Any] = {}
        try:
            raw = file_body_from_headers(request.headers) or ""
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
        except WebUIFilePreviewError as exc:
            if exc.message != "missing file content":
                return _http_error(exc.status, exc.message)
        except ValueError:
            return _http_error(400, "invalid career payload")
        from navin.career.errors import CareerError
        from navin.webui.career_api import handle_career_action

        try:
            context = self._studio_request_context("career", action, query, body)
        except ValueError as exc:
            return _http_error(400, str(exc))
        try:
            with request_context(context):
                payload = await asyncio.to_thread(handle_career_action, action, body)
        except CareerError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            self._log.exception("career desk failed action={}", action)
            return _http_error(500, str(exc) or "career desk failed")
        return _http_json_response(payload)

    async def _handle_trading(self, request: WsRequest) -> Response:
        """Paper Trading Agent OS desk. Same store as the `trading` tool."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "snapshot").strip()
        body: dict[str, Any] = {}
        try:
            raw = file_body_from_headers(request.headers) or ""
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
        except WebUIFilePreviewError as exc:
            if exc.message != "missing file content":
                return _http_error(exc.status, exc.message)
        except ValueError:
            return _http_error(400, "invalid trading payload")
        from navin.trading.errors import TradingError
        from navin.webui.trading_api import handle_trading_action

        try:
            payload = await asyncio.to_thread(handle_trading_action, action, body)
        except TradingError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            self._log.exception("trading desk failed action={}", action)
            return _http_error(500, str(exc) or "trading desk failed")
        return _http_json_response(payload)

    async def _handle_marketing_desk(self, request: WsRequest) -> Response:
        """Marketing Agent OS desk. Same store as the `marketing` tool."""
        query = _parse_query(request.path)
        action = (_query_first(query, "action") or "snapshot").strip()
        if action == "file":
            from navin.marketing.assets import mime_for, resolve_asset
            from navin.marketing.store import MarketingStore

            try:
                path = resolve_asset(MarketingStore(), _query_first(query, "name") or "")
                if path is None:
                    return _http_error(404, "asset not found")
                return _http_response(
                    path.read_bytes(),
                    content_type=mime_for(path.name),
                    extra_headers=[("Cache-Control", "public, max-age=3600")],
                )
            except Exception as exc:
                self._log.exception("marketing asset failed")
                return _http_error(500, str(exc) or "asset failed")
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        body: dict[str, Any] = {}
        try:
            raw = file_body_from_headers(request.headers) or ""
            if raw.strip():
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    body = parsed
        except WebUIFilePreviewError as exc:
            if exc.message != "missing file content":
                return _http_error(exc.status, exc.message)
        except ValueError:
            return _http_error(400, "invalid marketing payload")
        from navin.marketing.errors import MarketingError
        from navin.webui.marketing_desk_api import handle_marketing_action

        try:
            context = self._studio_request_context("marketing", action, query, body)
        except ValueError as exc:
            return _http_error(400, str(exc))
        try:
            if action == "oauth-connect":
                from urllib.parse import urlsplit

                from navin.marketing.oauth import connection_status
                from navin.marketing.store import MarketingStore

                target = connection_status(MarketingStore(), str(body.get("provider") or ""))["redirect_uri"]
                host = _safe_host_header(_case_insensitive_header(request.headers, "Host"))
                if target and urlsplit(target).netloc.lower() != host.lower():
                    origin = f"{urlsplit(target).scheme}://{urlsplit(target).netloc}"
                    return _http_error(
                        400,
                        f"Open Navin at {origin} before connecting. Vite :5173 is not the callback origin.",
                    )
            with request_context(context):
                payload = await asyncio.to_thread(handle_marketing_action, action, body)
        except MarketingError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            self._log.exception("marketing desk failed action={}", action)
            return _http_error(500, str(exc) or "marketing desk failed")
        cookie = (payload.get("oauth") or {}).pop("_set_cookie", "")
        response = _http_json_response(payload)
        response.headers["Cache-Control"] = "no-store"
        if cookie:
            response.headers["Set-Cookie"] = cookie
        return response

    async def _handle_marketing_oauth_callback(self, request: WsRequest) -> Response:
        """Public provider return, gated by one-use state and an HttpOnly cookie."""
        from urllib.parse import urlencode

        from navin.marketing.errors import MarketingError
        from navin.marketing.oauth import CALLBACK_PATH, cookie_name, finish_connection
        from navin.marketing.store import MarketingStore

        query = {key: values[0] for key, values in _parse_query(request.path).items() if values}
        destination = {"pane": "settings"}
        landing = ""
        try:
            result = await asyncio.to_thread(
                finish_connection, MarketingStore(), query,
                _case_insensitive_header(request.headers, "Cookie"),
            )
            landing = str(result.get("return_to") or "")
            if result.get("session_key"):
                destination["chat"] = str(result["session_key"])
        except MarketingError as exc:
            destination["oauth_error"] = exc.message
        location = (
            "/#/settings?section=channels"
            if landing == "channels"
            else "/#/marketing?" + urlencode(destination)
        )
        if landing == "channels" and destination.get("oauth_error"):
            location += "?" + urlencode({"oauth_error": destination["oauth_error"]})
        response = _http_response(
            b"", status=303,
            extra_headers=[
                ("Location", location),
                ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"),
            ],
        )
        if query.get("state"):
            response.headers["Set-Cookie"] = f"{cookie_name(query['state'])}=; Path={CALLBACK_PATH}; Max-Age=0; HttpOnly; SameSite=Lax"
        return response

    async def _handle_meeting(self, request: WsRequest, key: str | None) -> Response:
        """Meeting minutes and grounded answers, without going through the agent."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if key is not None:
            decoded_key = _decode_api_key(key)
            if decoded_key is None:
                return _http_error(400, "invalid session key")
            if not _is_websocket_channel_session_key(decoded_key):
                return _http_error(404, "session not found")
        query = _parse_query(request.path)
        mode = (_query_first(query, "mode") or "report").strip()
        try:
            raw = file_body_from_headers(request.headers)
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        try:
            body = json.loads(raw)
        except ValueError:
            return _http_error(400, "invalid meeting payload")
        if not isinstance(body, dict):
            return _http_error(400, "invalid meeting payload")

        speakers_raw = body.get("speakers")
        speakers = [str(x) for x in speakers_raw] if isinstance(speakers_raw, list) else []
        try:
            context = self._studio_request_context("meeting", mode, query, body, session_key=key)
        except ValueError as exc:
            return _http_error(400, str(exc))
        context_token = bind_request_context(context)
        try:
            if mode == "report":
                raw_duration = body.get("duration_min")
                duration_min = (
                    int(raw_duration)
                    if isinstance(raw_duration, (int, float)) and raw_duration > 0
                    else None
                )
                payload = await meeting_report_payload(
                    title=str(body.get("title") or ""),
                    template_name=str(body.get("template_name") or ""),
                    template_instructions=str(body.get("template_instructions") or ""),
                    transcript=str(body.get("transcript") or ""),
                    notes=str(body.get("notes") or ""),
                    speakers=speakers,
                    language=str(body.get("language") or ""),
                    meeting_date=str(body.get("meeting_date") or ""),
                    duration_min=duration_min,
                )
            elif mode == "speakers":
                payload = await meeting_speakers_payload(
                    transcript=str(body.get("transcript") or ""),
                    speakers=speakers,
                    language=str(body.get("language") or ""),
                )
            elif mode == "ask":
                payload = await meeting_answer_payload(
                    question=str(body.get("question") or ""),
                    title=str(body.get("title") or ""),
                    transcript=str(body.get("transcript") or ""),
                    notes=str(body.get("notes") or ""),
                    summary=str(body.get("summary") or ""),
                    speakers=speakers,
                    language=str(body.get("language") or ""),
                )
            elif mode == "bot_start":
                # Lazy import: playwright is an optional dependency and the
                # module reports a clean 503 install hint when it is missing.
                from navin.webui.meeting_bot import bot_start_payload

                payload = await bot_start_payload(
                    bot_id=str(body.get("bot_id") or ""),
                    url=str(body.get("url") or ""),
                    name=str(body.get("name") or ""),
                    language=str(body.get("language") or ""),
                )
            elif mode == "bot_status":
                from navin.webui.meeting_bot import bot_status_payload

                raw_cursor = body.get("cursor")
                cursor = int(raw_cursor) if isinstance(raw_cursor, (int, float)) else 0
                payload = bot_status_payload(str(body.get("bot_id") or ""), cursor)
            elif mode == "bot_stop":
                from navin.webui.meeting_bot import bot_stop_payload

                payload = await bot_stop_payload(str(body.get("bot_id") or ""))
            elif mode in MEETING_STORE_MODES:
                # Disk work off the event loop: a search re-reads every record
                # and an export zips the audio folder; inline they stalled every
                # other WebUI session for the duration.
                payload = await asyncio.to_thread(meeting_store_payload, mode, body)
            elif mode in {"translate_transcript", "translate_report", "cleanup", "docx", "calendar_sync"}:
                from navin.meetings.services import (
                    calendar_payload,
                    cleanup_payload,
                    docx_payload,
                    translate_payload,
                )

                if mode.startswith("translate_"):
                    payload = await translate_payload(
                        text=str(body.get("text") or ""),
                        target_language=str(body.get("target_language") or ""),
                        source_language=str(body.get("source_language") or ""),
                        kind=mode.removeprefix("translate_"),
                    )
                elif mode == "cleanup":
                    payload = await cleanup_payload(
                        transcript=str(body.get("transcript") or ""),
                        language=str(body.get("language") or ""),
                    )
                elif mode == "docx":
                    payload = docx_payload(
                        title=str(body.get("title") or ""),
                        report=str(body.get("report") or ""),
                        transcript=str(body.get("transcript") or ""),
                        notes=str(body.get("notes") or ""),
                    )
                else:
                    events = body.get("events")
                    payload = calendar_payload(
                        events if isinstance(events, list) else [],
                        provider=str(body.get("provider") or "ics"),
                        credentials=body.get("credentials")
                        if isinstance(body.get("credentials"), dict)
                        else None,
                        allow_network=False,
                    )
            else:
                return _http_error(400, f"unknown meeting mode: {mode}")
        except MeetingError as e:
            return _http_error(e.status, e.message)
        finally:
            reset_request_context(context_token)
        return _http_json_response(payload)

    async def _handle_project_search(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        term = _query_first(query, "q")
        regex = _query_first(query, "regex") == "1"
        case_sensitive = _query_first(query, "case") == "1"
        semantic = _query_first(query, "semantic") == "1"
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            if semantic:
                from navin.webui.semantic_search_api import semantic_search_payload

                payload = await semantic_search_payload(scope, term or "")
            else:
                payload = await asyncio.to_thread(
                    search_payload,
                    scope,
                    term,
                    regex=regex,
                    case_sensitive=case_sensitive,
                    include=_query_first(query, "include"),
                    exclude=_query_first(query, "exclude"),
                )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_project_replace(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        paths = [p for p in query.get("path", []) if p]
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                replace_payload,
                scope,
                _query_first(query, "q"),
                _query_first(query, "replace") or "",
                regex=_query_first(query, "regex") == "1",
                case_sensitive=_query_first(query, "case") == "1",
                include=_query_first(query, "include"),
                exclude=_query_first(query, "exclude"),
                paths=paths or None,
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_changes(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await self.route_cache.get(
                f"git:changes:{scope.project_path}",
                lambda: asyncio.to_thread(git_changes_payload, scope),
                ttl=_GIT_CHANGES_CACHE_TTL_S,
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_commit_message(self, request: WsRequest, key: str) -> Response:
        """AI commit message from the pending working-tree diff."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        paths = [p for p in query.get("path", []) if p]
        lang = (_query_first(query, "lang") or "en").strip() or "en"
        try:
            from navin.webui.git_commit_message import generate_commit_message_payload

            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await generate_commit_message_payload(
                scope, paths or None, lang=lang
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        except AssistError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_commit(self, request: WsRequest, key: str) -> Response:
        """One-click Commit / Commit & Push from the source-control panel."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        paths = [p for p in query.get("path", []) if p]
        # staged_only=1 => commit index as-is (empty paths list).
        staged_only = _query_first(query, "staged_only") == "1"
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_commit_payload,
                scope,
                _query_first(query, "message"),
                push=_query_first(query, "push") == "1",
                commit=_query_first(query, "commit") != "0",
                paths=[] if staged_only else (paths or None),
                amend=_query_first(query, "amend") == "1",
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_stage(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        paths = [p for p in query.get("path", []) if p]
        stage = _query_first(query, "stage") != "0"
        all_files = _query_first(query, "all") == "1"
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_stage_payload,
                scope,
                paths,
                stage=stage,
                all_files=all_files,
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_pull(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_pull_payload,
                scope,
                rebase=_query_first(query, "rebase") == "1",
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_undo_commit(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(git_undo_last_commit_payload, scope)
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_sync(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(git_sync_payload, scope)
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_discard(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        paths = [p for p in query.get("path", []) if p]
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_discard_payload,
                scope,
                paths or None,
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_stash(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        index_raw = _query_first(query, "index")
        index: int | None = None
        if index_raw not in (None, ""):
            try:
                index = int(index_raw)
            except (TypeError, ValueError):
                return _http_error(400, "stash index must be an integer")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_stash_payload,
                scope,
                op=_query_first(query, "op") or "push",
                index=index,
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_fetch(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(git_fetch_payload, scope)
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_branch(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_branch_payload,
                scope,
                op=_query_first(query, "op") or "list",
                name=_query_first(query, "name"),
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_conflict(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_conflict_action_payload,
                scope,
                action=_query_first(query, "action") or "",
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_github_pr(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(github_pr_view_payload, scope)
        except (ProjectSearchError, GithubPrError) as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_github_pr_create(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                github_pr_create_payload,
                scope,
                title=_query_first(query, "title"),
                body=_query_first(query, "body"),
                draft=_query_first(query, "draft") != "0",
            )
        except (ProjectSearchError, GithubPrError) as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_github_checks(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            # Keyed by project, not by session: several chats open on the same
            # repository were each running their own chain of `gh` calls.
            payload = await self.route_cache.get(
                f"github:checks:{scope.project_path}",
                lambda: asyncio.get_running_loop().run_in_executor(
                    _REMOTE_EXECUTOR,
                    partial(github_ci_status_payload, scope),
                ),
                ttl=_GITHUB_CHECKS_CACHE_TTL_S,
            )
        except (ProjectSearchError, GithubPrError) as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_github_fix_ci(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        mode = (_query_first(query, "mode") or "fix").strip() or "fix"
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(github_fix_ci_prompt_payload, scope, mode)
        except (ProjectSearchError, GithubPrError) as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_review_prompt(self, request: WsRequest, key: str) -> Response:
        """Structured prompt seed for the pre-commit 'Review changes' action."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        from navin.webui.code_review_api import review_prompt_payload

        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(review_prompt_payload, scope)
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_multitask_spawn(self, request: WsRequest, key: str) -> Response:
        """Multitask: run a queued composer prompt in a parallel subagent.

        The prompt travels in chunked base64 headers (the gateway HTTP layer
        only accepts GET) as a JSON object:
        {"prompt": ..., "label": ..., "client_key": ...}.
        """
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        try:
            raw = file_body_from_headers(request.headers)
        except WebUIFilePreviewError as e:
            return _http_error(e.status, e.message)
        try:
            # file_body_from_headers already returns decoded text.
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return _http_error(400, "invalid JSON body")
        if not isinstance(body, dict):
            return _http_error(400, "invalid JSON body")
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return _http_error(400, "a prompt is required")
        label = str(body.get("label") or "").strip()[:60]
        client_key = str(body.get("client_key") or "").strip()[:80]

        from navin.agent.subagent import request_multitask_spawn

        result = await request_multitask_spawn(
            self.bus, decoded_key, prompt, label=label, client_key=client_key
        )
        if not result.get("ok"):
            error = str(result.get("error") or "spawn failed")
            # 504: the control is already on the bus, so a retry would
            # duplicate the spawn. 409: the prompt was definitely refused.
            status = 504 if result.get("timed_out") else 409
            return _http_error(status, error)
        return _http_json_response(result)

    async def _handle_checkpoints(self, request: WsRequest, key: str, action: str) -> Response:
        """Workspace checkpoints: list / create / diff / restore snapshots."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        from navin.webui import checkpoints_api

        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        root = scope.project_path
        query = _parse_query(request.path)
        try:
            if action == "create":
                label = (_query_first(query, "label") or "").strip()[:80]
                payload = await asyncio.to_thread(
                    checkpoints_api.create_checkpoint, root, label=label
                )
            elif action == "diff":
                checkpoint_id = (_query_first(query, "id") or "").strip()
                payload = await asyncio.to_thread(
                    checkpoints_api.diff_checkpoint, root, checkpoint_id
                )
            elif action == "restore":
                checkpoint_id = (_query_first(query, "id") or "").strip()
                payload = await asyncio.to_thread(
                    checkpoints_api.restore_checkpoint, root, checkpoint_id
                )
            else:
                payload = await asyncio.to_thread(checkpoints_api.list_checkpoints, root)
        except checkpoints_api.CheckpointError as exc:
            if exc.code:
                # Named failures (no WSL, distribution asleep) carry a code so
                # the panel can show a translated, actionable sentence instead
                # of raw git output.
                return _http_response(
                    json.dumps({"error": exc.message, "code": exc.code}).encode("utf-8"),
                    status=exc.status,
                    content_type="application/json; charset=utf-8",
                )
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_debug(self, request: WsRequest, key: str) -> Response:
        """DAP workbench ops: start/stop/step/breakpoints/stack/variables."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        op = _query_first(query, "op") or "state"
        params: dict[str, Any] = {
            k: v[0] if isinstance(v, list) and v else v for k, v in query.items() if k != "op"
        }
        # Complex payloads (breakpoint lines, args) may arrive as JSON body headers.
        raw_op = _query_first(query, "payload")
        if raw_op:
            try:
                extra = json.loads(raw_op)
                if isinstance(extra, dict):
                    params.update(extra)
            except json.JSONDecodeError:
                return _http_error(400, "payload must be JSON")
        try:
            from navin.webui.file_preview import WebUIFilePreviewError, file_body_from_headers

            try:
                body = file_body_from_headers(request.headers)
            except WebUIFilePreviewError:
                body = ""
            if body.strip():
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    params.update(parsed)
        except json.JSONDecodeError:
            return _http_error(400, "debug body must be JSON")
        except Exception:
            pass
        # Normalise repeated path-style list fields.
        if "lines" in params and isinstance(params["lines"], str):
            try:
                params["lines"] = json.loads(params["lines"])
            except json.JSONDecodeError:
                params["lines"] = [
                    int(x) for x in params["lines"].split(",") if x.strip().isdigit()
                ]
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(debug_dispatch, scope, op=op, params=params)
        except DebugSessionError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_log(self, request: WsRequest, key: str) -> Response:
        """Commit history graph for the source-control timeline.

        Without arguments it returns the multi-branch log; with ``commit=<hash>``
        it returns the detail (message + per-file stats) for that one commit.
        """
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        commit = _query_first(query, "commit")
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            if commit:
                payload = await asyncio.to_thread(
                    git_commit_detail_payload,
                    scope,
                    commit,
                )
            else:
                raw_limit = _query_first(query, "limit") or ""
                limit = int(raw_limit) if raw_limit.isdigit() else 200
                payload = await asyncio.to_thread(git_log_payload, scope, limit)
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_context_usage(self, request: WsRequest, key: str) -> Response:
        """Estimated context-window fill for the chat, shown in the Dev footer."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")

        from navin.webui.context_usage import context_usage_payload

        async def _compute() -> dict[str, Any]:
            session_data = await asyncio.to_thread(
                self.session_manager.read_session_file,
                decoded_key,
            )
            project_path: str | None = None
            try:
                scope = self.workspaces.scope_for_session_key(decoded_key)
                project_path = str(scope.project_path or "") or None
            except Exception:
                project_path = None
            return await asyncio.to_thread(
                context_usage_payload,
                session_data,
                project_path=project_path,
            )

        payload = await self.route_cache.get(
            f"context-usage:{decoded_key}",
            _compute,
            ttl=_CONTEXT_USAGE_CACHE_TTL_S,
        )
        return _http_json_response(payload)

    async def _handle_git_diff(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        file_arg = _query_first(query, "file")
        against_arg = _query_first(query, "against")
        ignore_ws = _query_first(query, "ignore_whitespace") == "1"
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await asyncio.to_thread(
                git_diff_payload,
                scope,
                file_arg,
                against_arg,
                ignore_whitespace=ignore_ws,
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_git_blame(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        file_arg = _query_first(query, "file") or _query_first(query, "path") or ""
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            extra = _extra_file_roots(query, scope)
            extra_root = extra[0] if extra else None
            payload = await asyncio.to_thread(
                git_blame_payload,
                scope,
                file_arg,
                extra_root,
                extra,
            )
        except ProjectSearchError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    # -- Pending agent-edit review -------------------------------------------

    def _review_session_key(self, key: str) -> str | Response:
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        return decoded_key

    async def _handle_review_changes(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = self._review_session_key(key)
        if isinstance(decoded_key, Response):
            return decoded_key
        try:
            scope = self.workspaces.scope_for_session_key(decoded_key)
            payload = await self.route_cache.get(
                f"review:changes:{decoded_key}:{scope.project_path}",
                lambda: asyncio.to_thread(
                    review_changes_payload,
                    self.pending_review,
                    decoded_key,
                    scope,
                ),
                ttl=_REVIEW_CHANGES_CACHE_TTL_S,
            )
        except ReviewApiError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_review_file(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = self._review_session_key(key)
        if isinstance(decoded_key, Response):
            return decoded_key
        query = _parse_query(request.path)
        path = _query_first(query, "path")
        try:
            payload = await asyncio.to_thread(
                review_file_payload,
                self.pending_review,
                decoded_key,
                path,
            )
        except ReviewApiError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_review_action(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = self._review_session_key(key)
        if isinstance(decoded_key, Response):
            return decoded_key
        query = _parse_query(request.path)
        action = _query_first(query, "action")
        path = _query_first(query, "path")
        hunk = _query_first(query, "hunk")
        try:
            payload = await asyncio.to_thread(
                review_action_payload,
                self.pending_review,
                decoded_key,
                action,
                path,
                hunk,
            )
        except ReviewApiError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    def _handle_session_automations(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        pending_job_ids = self._pending_automation_ids_for_session(decoded_key)
        return _http_json_response(
            session_automations_payload(
                self.cron_service,
                decoded_key,
                local_trigger_store=self.local_trigger_store,
                pending_job_ids=pending_job_ids,
            )
        )

    def _handle_session_delete(self, request: WsRequest, key: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.session_manager is None:
            return _http_error(503, "session manager unavailable")
        decoded_key = _decode_api_key(key)
        if decoded_key is None:
            return _http_error(400, "invalid session key")
        if not _is_websocket_channel_session_key(decoded_key):
            return _http_error(404, "session not found")
        query = _parse_query(request.path)
        delete_automations = (_query_first(query, "delete_automations") or "").lower()
        automation_jobs = session_automation_jobs(
            self.cron_service,
            decoded_key,
            local_trigger_store=self.local_trigger_store,
        )
        if automation_jobs and delete_automations not in {"1", "true", "yes"}:
            return _http_json_response(
                {
                    "deleted": False,
                    "blocked_by_automations": True,
                    "automations": serialize_automation_jobs(automation_jobs),
                }
            )
        if automation_jobs:
            for job in automation_jobs:
                if isinstance(job, LocalTrigger):
                    if self.local_trigger_store is not None:
                        self.local_trigger_store.delete(job.id)
                elif self.cron_service is not None:
                    self.cron_service.remove_job(job.id)
        deleted = self.session_manager.delete_session(decoded_key)
        delete_webui_thread(decoded_key)
        self.route_cache.invalidate("webui:sessions")
        return _http_json_response({"deleted": bool(deleted)})

    # -- Automation routes --------------------------------------------------

    async def _dispatch_automation_routes(
        self,
        request: WsRequest,
        got: str,
    ) -> Response | None:
        if got == "/api/webui/automations":
            return self._handle_webui_automations(request)
        m = re.match(r"^/api/webui/automations/(enable|disable|delete|run|update)$", got)
        if m:
            return await self._handle_webui_automation_action(request, m.group(1))
        return None

    def _pending_cron_job_ids_for_all(self) -> set[str]:
        if self.cron_service is None or self.cron_pending_job_ids is None:
            return set()
        pending: set[str] = set()
        for job in self.cron_service.list_jobs(include_disabled=True):
            session_key = job.payload.session_key
            if not session_key and job.payload.origin_channel and job.payload.origin_chat_id:
                session_key = f"{job.payload.origin_channel}:{job.payload.origin_chat_id}"
            if session_key:
                pending.update(self.cron_pending_job_ids(session_key))
        return pending

    def _pending_local_trigger_ids_for_all(self) -> set[str]:
        if self.local_trigger_store is None or self.local_trigger_pending_ids is None:
            return set()
        pending: set[str] = set()
        for trigger in self.local_trigger_store.list_triggers(include_disabled=True):
            session_key = trigger.session_key
            if not session_key and trigger.channel and trigger.chat_id:
                session_key = f"{trigger.channel}:{trigger.chat_id}"
            if session_key:
                pending.update(self.local_trigger_pending_ids(session_key))
        return pending

    def _pending_automation_ids_for_session(self, session_key: str) -> set[str]:
        pending: set[str] = set()
        if self.cron_pending_job_ids is not None:
            pending.update(self.cron_pending_job_ids(session_key))
        if self.local_trigger_pending_ids is not None:
            pending.update(self.local_trigger_pending_ids(session_key))
        return pending

    def _handle_webui_automations(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        pending_job_ids = self._pending_cron_job_ids_for_all()
        pending_job_ids.update(self._pending_local_trigger_ids_for_all())
        return _http_json_response(
            all_automations_payload(
                self.cron_service,
                local_trigger_store=self.local_trigger_store,
                session_manager=self.session_manager,
                pending_job_ids=pending_job_ids,
            )
        )

    async def _handle_webui_automation_action(
        self,
        request: WsRequest,
        action: str,
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.cron_service is None and self.local_trigger_store is None:
            return _http_error(503, "automation service unavailable")

        query = _parse_query(request.path)
        job_id = (_query_first(query, "id") or _query_first(query, "job_id") or "").strip()
        if not job_id:
            return _http_error(400, "missing automation id")
        trigger = self.local_trigger_store.get(job_id) if self.local_trigger_store else None
        if trigger is not None:
            return self._handle_local_trigger_action(request, action, trigger)

        if self.cron_service is None:
            return _http_error(404, "automation not found")
        job = self.cron_service.get_job(job_id)
        if job is None:
            return _http_error(404, "automation not found")
        if job.payload.kind == "system_event":
            return self._handle_system_loop_action(request, action, job)
        if action in {"enable", "run"} and not is_bound_cron_job(job):
            return _http_error(409, "automation has no linked chat")

        if action == "enable":
            if self.cron_service.enable_job(job_id, enabled=True) is None:
                return _http_error(404, "automation not found")
        elif action == "disable":
            if self.cron_service.enable_job(job_id, enabled=False) is None:
                return _http_error(404, "automation not found")
        elif action == "delete":
            result = self.cron_service.remove_job(job_id)
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        elif action == "run":
            if not job.enabled:
                return _http_error(409, "automation is disabled")
            task = asyncio.create_task(self.cron_service.run_job(job_id, force=False))
            task.add_done_callback(self._log_automation_run_result)
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_automation_update(values, current_job=job)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            try:
                result = self.cron_service.update_job(job_id, **parsed)
            except ValueError as exc:
                return _http_error(400, str(exc))
            if result == "not_found":
                return _http_error(404, "automation not found")
            if result == "protected":
                return _http_error(403, "system automation is protected")
        else:
            return _http_error(404, "unknown automation action")

        return self._handle_webui_automations(request)

    def _handle_system_loop_action(
        self,
        request: WsRequest,
        action: str,
        job: CronJob,
    ) -> Response:
        """Reconfigure a loop navin runs for itself.

        Its name and message belong to the runtime, and deleting it would only
        bring it back on the next start, so only the schedule, the limits and
        whether it runs at all can be changed.
        """
        if self.cron_service is None:
            return _http_error(503, "automation service unavailable")
        if not is_configurable_system_loop(job.id):
            return _http_error(403, "system automation is protected")
        if action in {"delete", "run"}:
            return _http_error(403, "system automation is protected")

        schedule: CronSchedule | None = None
        limits: CronLimits | None = None
        enabled: bool | None = None

        if action in {"enable", "disable"}:
            enabled = action == "enable"
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_automation_update(values, current_job=job)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            unsupported = sorted(set(parsed) - {"schedule", "limits", "delete_after_run"})
            if unsupported:
                return _http_error(
                    400,
                    f"a built-in loop only accepts schedule and limits, not {unsupported[0]}",
                )
            schedule = parsed.get("schedule")
            limits = parsed.get("limits")
        else:
            return _http_error(404, "unknown automation action")

        error = update_system_loop(job.id, schedule=schedule, limits=limits, enabled=enabled)
        if error:
            return _http_error(400, error)
        self.cron_service.reschedule_system_job(
            job.id,
            schedule=schedule,
            limits=limits,
            enabled=enabled,
        )
        return self._handle_webui_automations(request)

    def _handle_local_trigger_action(
        self,
        request: WsRequest,
        action: str,
        trigger: LocalTrigger,
    ) -> Response:
        if self.local_trigger_store is None:
            return _http_error(503, "trigger service unavailable")
        if action == "enable":
            if self.local_trigger_store.enable(trigger.id, enabled=True) is None:
                return _http_error(404, "automation not found")
        elif action == "disable":
            if self.local_trigger_store.enable(trigger.id, enabled=False) is None:
                return _http_error(404, "automation not found")
        elif action == "delete":
            if not self.local_trigger_store.delete(trigger.id):
                return _http_error(404, "automation not found")
        elif action == "run":
            return _http_error(409, "local trigger requires a CLI message")
        elif action == "update":
            values = _automation_values_from_request(request)
            if values is None:
                return _http_error(400, "invalid automation update payload")
            parsed = _parse_local_trigger_update(values)
            if isinstance(parsed, str):
                return _http_error(400, parsed)
            if parsed:
                if self.local_trigger_store.update(trigger.id, **parsed) is None:
                    return _http_error(404, "automation not found")
        else:
            return _http_error(404, "unknown automation action")

        return self._handle_webui_automations(request)

    @staticmethod
    def _log_automation_run_result(task: asyncio.Task[bool]) -> None:
        try:
            ran = task.result()
        except Exception:
            logger.exception("WebUI automation run-now task failed")
            return
        if not ran:
            logger.warning("WebUI automation run-now task did not execute")

    # -- Media routes -------------------------------------------------------

    def _dispatch_media_routes(self, request: WsRequest, got: str) -> Response | None:
        m = re.match(r"^/api/media/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$", got)
        if m:
            return self._handle_media_fetch(m.group(1), m.group(2), request)
        return None

    def _handle_media_fetch(
        self, sig: str, payload: str, request: WsRequest | None = None
    ) -> Response:
        return self.media.serve_signed_media(
            sig,
            payload,
            request=request,
        )

    # -- Misc routes --------------------------------------------------------

    async def _dispatch_misc_routes(
        self, connection: Any, request: WsRequest, got: str
    ) -> Response | None:
        if got == "/api/sessions":
            return await self._handle_sessions_list(request)
        if got == "/api/commands":
            return self._handle_commands(request)
        if got == "/api/workspaces":
            return self._handle_workspaces(connection, request)
        if got == "/api/webui/app-templates":
            return self._handle_webui_app_templates(request)
        if got == "/api/webui/app-templates/install":
            return self._handle_webui_app_templates_install(request)
        if got == "/api/webui/app-templates/create":
            return self._handle_webui_app_templates_create(request)
        m_app = re.match(r"^/api/webui/app-templates/([^/]+)$", got)
        if m_app:
            return self._handle_webui_app_template_detail(request, m_app.group(1))
        if got == "/api/webui/skills":
            return await self._handle_webui_skills(request)
        if got == "/api/webui/skills/create":
            return self._handle_webui_skills_create(request)
        if got == "/api/webui/skills/update":
            return self._handle_webui_skills_update(request)
        if got == "/api/webui/skills/delete":
            return self._handle_webui_skills_delete(request)
        if got == "/api/webui/skills/setup":
            return await self._handle_webui_skills_setup(connection, request)
        if got == "/api/webui/skills/discover":
            return self._handle_webui_skills_discover(request)
        if got == "/api/webui/skills/import-workspace":
            return self._handle_webui_skills_import_workspace(request)
        if got == "/api/webui/preview-discover":
            return await self._handle_webui_preview_discover(request)
        if got == "/api/webui/migration/scan":
            return await self._handle_webui_migration(connection, request, "scan")
        if got == "/api/webui/migration/import":
            return await self._handle_webui_migration(connection, request, "import")
        if got == "/api/webui/preview-proxy/start":
            return await self._handle_webui_preview_proxy_start(connection, request)
        if got == "/api/webui/preview-screenshot":
            return await self._handle_webui_preview_screenshot(connection, request)
        if got == "/api/webui/preview-logs":
            return self._handle_webui_preview_logs(request)
        if got == "/api/webui/preview-logs/clear":
            return self._handle_webui_preview_logs_clear(request)
        if got == "/api/webui/processes":
            return await self._handle_webui_processes(request, "list")
        if got == "/api/webui/processes/kill":
            return await self._handle_webui_processes(request, "kill")
        m = re.match(r"^/api/webui/skills/([^/]+)$", got)
        if m:
            return self._handle_webui_skill_detail(request, m.group(1))
        if got == "/api/webui/fs/roots":
            return self._handle_webui_fs_roots(request)
        if got == "/api/webui/fs/list":
            return await self._handle_webui_fs_list(request)
        if got == "/api/webui/fs/mkdir":
            return await self._handle_webui_fs_mkdir(request)
        if got == "/api/webui/evolve/overview":
            return await self._handle_webui_evolve(request, "overview")
        if got == "/api/webui/evolve/status":
            return await self._handle_webui_evolve(request, "status")
        if got == "/api/webui/evolve/verify":
            return await self._handle_webui_evolve(request, "verify")
        if got == "/api/webui/evolve/merge":
            return await self._handle_webui_evolve(request, "merge")
        if got == "/api/webui/evolve/pr":
            return await self._handle_webui_evolve(request, "pr")
        if got == "/api/webui/evolve/rollback":
            return await self._handle_webui_evolve(request, "rollback")
        if got == "/api/webui/evolve/enqueue":
            return await self._handle_webui_evolve(request, "enqueue")
        if got == "/api/webui/evolve/cancel":
            return await self._handle_webui_evolve(request, "cancel")
        if got == "/api/webui/evolve/daemon/start":
            return await self._handle_webui_evolve(request, "daemon_start")
        if got == "/api/webui/evolve/daemon/stop":
            return await self._handle_webui_evolve(request, "daemon_stop")
        if got == "/api/webui/evolve/docs":
            return await self._handle_webui_evolve(request, "docs")
        if got == "/api/webui/evolve/autorun":
            return await self._handle_webui_evolve(request, "autorun")
        if got == "/api/webui/git/status":
            return await self._handle_webui_git_status(request)
        if got == "/api/webui/git/clone":
            return await self._handle_webui_git_clone(request)
        if got == "/api/webui/diagnostics/workspace":
            return await self._handle_webui_workspace_diagnostics(request)
        if got == "/api/webui/diagnostics":
            return await self._handle_webui_diagnostics(request)
        if got == "/api/webui/project-audit":
            return await self._handle_webui_project_audit(request)
        if got == "/api/webui/plugins":
            return self._handle_webui_plugins(request)
        if got == "/api/webui/plugins/install":
            return await self._handle_webui_plugins_install(request)
        if got == "/api/webui/plugins/remove":
            return await self._handle_webui_plugins_remove(request)
        if got == "/api/webui/plugins/enable":
            return await self._handle_webui_plugins_set_enabled(request, True)
        if got == "/api/webui/plugins/disable":
            return await self._handle_webui_plugins_set_enabled(request, False)
        if got == "/api/webui/lsp-servers":
            return await self._handle_webui_lsp_servers(request)
        if got == "/api/webui/lsp-servers/search":
            return await self._handle_webui_lsp_servers_search(request)
        if got == "/api/webui/lsp-servers/install":
            return await self._handle_webui_lsp_servers_install(connection, request)
        if got == "/api/webui/lsp-servers/install-detected":
            return await self._handle_webui_lsp_servers_install_detected(connection, request)
        if got == "/api/webui/lsp-servers/uninstall":
            return await self._handle_webui_lsp_servers_uninstall(connection, request)
        if got == "/api/webui/sidebar-state":
            return self._handle_webui_sidebar_state(request)
        if got == "/api/webui/sidebar-state/update":
            return self._handle_webui_sidebar_state_update(request)
        if got == "/api/webui/onboarding":
            return self._handle_webui_onboarding(request)
        if got == "/api/webui/onboarding/update":
            return self._handle_webui_onboarding_update(request)
        if got == "/api/webui/exec-policy":
            return self._handle_webui_exec_policy(request)
        if got == "/api/webui/exec-policy/update":
            return await self._handle_webui_exec_policy_update(request)
        if got == "/api/webui/account":
            return await self._handle_webui_account(request)
        if got == "/api/webui/account/connect-url":
            return self._handle_webui_account_connect_url(request)
        if got == "/api/webui/account/activate":
            return await self._handle_webui_account_activate(request)
        if got == "/api/webui/account/logout":
            return await self._handle_webui_account_logout(request)
        if got == "/api/webui/openrouter/connect-url":
            return self._handle_webui_openrouter_connect_url(request)
        if got == "/api/webui/openrouter/status":
            return await self._handle_webui_openrouter_status(request)
        if got == "/api/webui/open-url":
            return self._handle_webui_open_url(request)
        if got == "/api/webui/runtime/health":
            return self._handle_webui_runtime_health(request)
        if got == "/api/webui/montage/status":
            return await self._handle_webui_montage_status(request)
        if got == "/api/webui/marketing-qa/readiness":
            return self._handle_webui_marketing_qa_readiness(request)
        if got == "/api/webui/marketing-qa/assets":
            return self._handle_webui_marketing_qa_assets(request)
        if got == "/api/webui/marketing-qa/reports":
            return self._handle_webui_marketing_qa_reports(request)
        if got == "/api/webui/marketing-qa/run":
            return await self._handle_webui_marketing_qa_run(request)
        marketing_qa_match = re.match(
            r"^/api/webui/marketing-qa/reports/([A-Za-z0-9._-]{1,160})(/override)?$",
            got,
        )
        if marketing_qa_match:
            report_id, action = marketing_qa_match.groups()
            if action:
                return self._handle_webui_marketing_qa_override(request, report_id)
            return self._handle_webui_marketing_qa_report(request, report_id)
        if got == "/api/webui/montage/assets":
            return self._handle_webui_montage_assets(request)
        if got == "/api/webui/montage/jobs":
            return self._handle_webui_montage_jobs(request)
        montage_job_match = re.match(
            r"^/api/webui/montage/jobs/([a-z0-9-]+)(/resume|/cancel)?$", got
        )
        if montage_job_match:
            if montage_job_match.group(2) == "/resume":
                return await self._handle_webui_montage_job_resume(
                    request, montage_job_match.group(1)
                )
            if montage_job_match.group(2) == "/cancel":
                return self._handle_webui_montage_job_cancel(
                    request, montage_job_match.group(1)
                )
            return self._handle_webui_montage_job(request, montage_job_match.group(1))
        if got == "/api/webui/montage/probe":
            return await self._handle_webui_montage_probe(request)
        if got == "/api/webui/montage/timelines":
            return self._handle_webui_montage_timelines(request)
        montage_timeline_match = re.match(
            r"^/api/webui/montage/timelines/([a-z0-9][a-z0-9_-]{0,63})"
            r"(?:/(put|delete|preview|render))?$",
            got,
        )
        if montage_timeline_match:
            name, action = montage_timeline_match.groups()
            if action == "put":
                return self._handle_webui_montage_timeline_put(request, name)
            if action == "delete":
                return self._handle_webui_montage_timeline_delete(request, name)
            if action == "preview":
                return await self._handle_webui_montage_timeline_preview(request, name)
            if action == "render":
                return await self._handle_webui_montage_timeline_render(request, name)
            return self._handle_webui_montage_timeline(request, name)
        if got == "/api/webui/montage/setup":
            return await self._handle_webui_montage_setup(connection, request)
        if got == "/api/document-templates":
            return await self._handle_document_templates(request)
        if got == "/api/media-templates":
            return await self._handle_media_templates(request)
        m = re.match(r"^/api/document-templates/([a-z]+)/([A-Za-z0-9][A-Za-z0-9_-]*)/(.+)$", got)
        if m:
            return self._handle_document_template_file(request, m.group(1), m.group(2), m.group(3))
        return None

    async def _handle_document_templates(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = await self.route_cache.get(
            "webui:document-templates",
            lambda: asyncio.to_thread(list_document_templates_payload),
            ttl=_TEMPLATES_CACHE_TTL_S,
        )
        return _http_json_response(payload)

    async def _handle_media_templates(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        studio = _query_first(query, "studio") or ""
        payload = await self.route_cache.get(
            f"webui:media-templates:{studio}",
            lambda: asyncio.to_thread(list_media_templates, studio or None),
            ttl=_TEMPLATES_CACHE_TTL_S,
        )
        return _http_json_response(payload)

    def _handle_document_template_file(
        self, request: WsRequest, category: str, name: str, relative: str
    ) -> Response:
        if not self.check_api_token(request) and not self._referer_token_valid(request):
            return _http_error(401, "Unauthorized")
        served = document_template_file(category, name, relative)
        if served is None:
            return _http_error(404, "template file not found")
        body, content_type = served
        return _http_response(
            body,
            status=200,
            content_type=content_type,
            extra_headers=[("Cache-Control", "private, max-age=300")],
        )

    def _handle_commands(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        module = _query_first(query, "module")
        return _http_json_response({"commands": builtin_command_palette(module)})

    def _handle_workspaces(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(
            self.workspaces.payload(
                controls_available=self.workspace_controls_available(connection)
            )
        )

    def _handle_webui_app_templates(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(list_app_templates_payload(self.skills_workspace_path))

    def _handle_webui_app_template_detail(self, request: WsRequest, slug: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        try:
            return _http_json_response(
                app_template_detail_payload(self.skills_workspace_path, slug)
            )
        except AppTemplateError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_app_templates_install(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        slug = _query_first(query, "slug") or ""
        dest = (_query_first(query, "dest") or "").strip()
        try:
            if dest:
                return _http_json_response(
                    create_app_template_payload(slug, dest, workspace=self.skills_workspace_path)
                )
            return _http_json_response(
                install_app_template_payload(self.skills_workspace_path, slug)
            )
        except AppTemplateError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_app_templates_create(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        slug = _query_first(query, "slug") or ""
        dest = _query_first(query, "dest") or ""
        try:
            return _http_json_response(
                create_app_template_payload(slug, dest, workspace=self.skills_workspace_path)
            )
        except AppTemplateError as exc:
            return _http_error(exc.status, exc.message)

    async def _handle_webui_skills(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = await self.route_cache.get(
            "webui:skills",
            lambda: self._gated(
                asyncio.to_thread(
                    webui_skills_payload,
                    self.skills_workspace_path,
                    disabled_skills=self.disabled_skills,
                )
            ),
            ttl=_SKILLS_CACHE_TTL_S,
        )
        return _http_json_response(payload)

    def _handle_webui_skills_create(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        description = _query_first(query, "description") or ""
        try:
            markdown = skill_body_from_headers(request.headers)
            payload = create_workspace_skill(
                self.skills_workspace_path,
                name=name,
                description=description,
                markdown=markdown,
                disabled_skills=self.disabled_skills,
            )
        except SkillsApiError as exc:
            return _http_error(exc.status, exc.message)
        self.route_cache.invalidate("webui:skills")
        return _http_json_response(payload)

    def _handle_webui_skills_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        try:
            markdown = skill_body_from_headers(request.headers)
            if not markdown:
                raise SkillsApiError("skill body is required")
            payload = update_workspace_skill(
                self.skills_workspace_path,
                name=name,
                markdown=markdown,
                disabled_skills=self.disabled_skills,
            )
        except SkillsApiError as exc:
            return _http_error(exc.status, exc.message)
        self.route_cache.invalidate("webui:skills")
        return _http_json_response(payload)

    def _handle_webui_skills_delete(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        try:
            payload = delete_workspace_skill(
                self.skills_workspace_path,
                name=name,
                disabled_skills=self.disabled_skills,
            )
        except SkillsApiError as exc:
            return _http_error(exc.status, exc.message)
        self.route_cache.invalidate("webui:skills")
        return _http_json_response(payload)

    def _handle_webui_skills_discover(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        try:
            scan_root = resolve_skill_scan_root(
                _query_first(query, "path"),
                self.skills_workspace_path,
            )
            apply_root = resolve_skill_apply_root(
                scope=_query_first(query, "scope"),
                workspace=scan_root,
                fallback=self.skills_workspace_path,
            )
            payload = discover_workspace_skills(
                scan_root,
                catalog_workspace=self.skills_workspace_path,
                apply_root=apply_root,
            )
        except SkillsApiError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    def _handle_webui_skills_import_workspace(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        raw_names = _query_first(query, "names") or ""
        names = [part.strip() for part in raw_names.split(",") if part.strip()]
        try:
            scan_root = resolve_skill_scan_root(
                _query_first(query, "path"),
                self.skills_workspace_path,
            )
            apply_root = resolve_skill_apply_root(
                scope=_query_first(query, "scope"),
                workspace=scan_root,
                fallback=self.skills_workspace_path,
            )
            payload = import_workspace_skills(
                self.skills_workspace_path,
                scan_root=scan_root,
                names=names or None,
                apply_root=apply_root,
                disabled_skills=self.disabled_skills,
            )
        except SkillsApiError as exc:
            return _http_error(exc.status, exc.message)
        self.route_cache.invalidate("webui:skills")
        return _http_json_response(payload)

    async def _handle_webui_skills_setup(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_controls_available(connection):
            return _http_error(403, "skill setup is localhost-only")
        from navin.webui.skills_setup import SkillsSetupError, run_skill_setup

        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        option = _query_first(query, "option") or None
        if not name or "/" in name or "\\" in name:
            return _http_error(400, "invalid skill name")
        try:
            # Package installs can take minutes; keep the event loop free.
            result = await asyncio.to_thread(
                run_skill_setup,
                self.skills_workspace_path,
                name,
                option_id=option,
                disabled_skills=self.disabled_skills,
            )
        except SkillsSetupError as exc:
            return _http_error(exc.status, exc.message)
        detail = webui_skill_detail_payload(
            self.skills_workspace_path,
            name,
            disabled_skills=self.disabled_skills,
        )
        return _http_json_response({**result, "skill": detail})

    def _handle_webui_skill_detail(self, request: WsRequest, raw_name: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from urllib.parse import unquote

        name = unquote(raw_name)
        if not name or "/" in name or "\\" in name:
            return _http_error(400, "invalid skill name")
        payload = webui_skill_detail_payload(
            self.skills_workspace_path,
            name,
            disabled_skills=self.disabled_skills,
        )
        if payload is None:
            return _http_error(404, "skill not found")
        return _http_json_response(payload)

    async def _handle_webui_migration(
        self, connection: Any, request: WsRequest, action: str
    ) -> Response:
        """Scan / import Cursor & VS Code assets (MCP servers, Copilot rules)."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.migration_api import (
            MigrationError,
            run_import,
            scan_migration_sources,
        )

        query = _parse_query(request.path)
        raw_key = (_query_first(query, "session") or "").strip()
        try:
            if raw_key:
                scope = self.workspaces.scope_for_session_key(raw_key)
            else:
                scope = self.workspaces.default_scope()
            project_root = scope.project_path
        except Exception:
            project_root = None

        try:
            if action == "scan":
                payload = await asyncio.to_thread(scan_migration_sources, project_root)
            else:
                if not self.workspace_controls_available(connection):
                    return _http_error(403, "migration import is localhost-only")
                payload = await asyncio.to_thread(run_import, project_root)
        except MigrationError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_webui_preview_proxy_start(
        self, connection: Any, request: WsRequest
    ) -> Response:
        """Start (or reuse) the telemetry injection proxy for a preview port."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_controls_available(connection):
            return _http_error(403, "preview proxy is localhost-only")
        from navin.webui.preview_proxy import PROXIES, PreviewProxyError

        query = _parse_query(request.path)
        raw_port = (_query_first(query, "port") or "").strip()
        try:
            port = int(raw_port)
        except ValueError:
            return _http_error(400, "port is required")
        try:
            proxy_port = await PROXIES.ensure(port)
        except PreviewProxyError as exc:
            return _http_error(400, str(exc))
        except OSError as exc:
            return _http_error(502, f"cannot start preview proxy: {exc}")
        return _http_json_response(
            {
                "targetPort": port,
                "proxyPort": proxy_port,
                "url": f"http://127.0.0.1:{proxy_port}/",
            }
        )

    async def _handle_webui_preview_screenshot(
        self, connection: Any, request: WsRequest
    ) -> Response:
        """Capture a PNG of a loopback preview URL via headless Chromium."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_controls_available(connection):
            return _http_error(403, "preview screenshot is localhost-only")
        from navin.webui.preview_screenshot import (
            PreviewScreenshotError,
            capture_preview_png,
        )

        query = _parse_query(request.path)
        url = (_query_first(query, "url") or "").strip()
        if not url:
            return _http_error(400, "url is required")

        def _dim(name: str, default: int) -> int:
            raw = (_query_first(query, name) or "").strip()
            if not raw:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        full = (_query_first(query, "full") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        try:
            png = await capture_preview_png(
                url,
                width=_dim("width", 1280),
                height=_dim("height", 800),
                full_page=full,
            )
        except PreviewScreenshotError as exc:
            return _http_error(400, str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("preview screenshot failed")
            return _http_error(502, f"screenshot failed: {exc}")

        import base64

        data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        return _http_json_response({"dataUrl": data_url, "bytes": len(png)})

    def _handle_webui_preview_logs(self, request: WsRequest) -> Response:
        """Console / network telemetry captured by the preview probe."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.preview_probe import TELEMETRY

        query = _parse_query(request.path)
        raw_port = (_query_first(query, "port") or "").strip()
        try:
            port = int(raw_port)
        except ValueError:
            return _http_error(400, "port is required")
        after = 0
        raw_after = (_query_first(query, "after") or "").strip()
        if raw_after:
            try:
                after = int(raw_after)
            except ValueError:
                return _http_error(400, "after must be an integer")
        return _http_json_response(
            {
                "port": port,
                "entries": TELEMETRY.entries(port, after_id=after),
                "counts": TELEMETRY.counts(port),
                "digest": TELEMETRY.digest(port),
            }
        )

    def _handle_webui_preview_logs_clear(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.preview_probe import TELEMETRY

        query = _parse_query(request.path)
        raw_port = (_query_first(query, "port") or "").strip()
        try:
            port = int(raw_port)
        except ValueError:
            return _http_error(400, "port is required")
        TELEMETRY.clear(port)
        return _http_json_response({"status": "ok", "port": port})

    async def _handle_webui_processes(self, request: WsRequest, action: str) -> Response:
        """Background process manager: agent exec sessions (list / kill)."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui import processes_api

        if action == "kill":
            query = _parse_query(request.path)
            session_id = (_query_first(query, "id") or "").strip()
            if not session_id:
                return _http_error(400, "id is required")
            killed = await processes_api.kill_process(session_id)
            if not killed:
                return _http_error(404, "unknown process")
            return _http_json_response({"status": "ok", "id": session_id})
        return _http_json_response({"processes": await processes_api.list_processes()})

    async def _handle_webui_preview_discover(self, request: WsRequest) -> Response:
        """Find a running local project app for Preview (server-side, no CORS).

        Optional ``url`` / ``preferred_port`` prefer a known address when it still
        answers. Never invents a default port and never starts a process.
        """
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.agent.tools.preview_server import (
            discover_running_project_url,
            http_reachable,
            url_looks_like_navin,
        )

        query = _parse_query(request.path)
        preferred_url = (_query_first(query, "url") or "").strip() or None
        preferred_port: int | None = None
        raw_port = (_query_first(query, "preferred_port") or "").strip()
        if raw_port:
            try:
                preferred_port = int(raw_port)
            except ValueError:
                return _http_error(400, "preferred_port must be an integer")

        if preferred_url:
            if not preferred_url.startswith(("http://", "https://")):
                preferred_url = f"http://{preferred_url}"
            try:
                from urllib.parse import urlparse

                parsed = urlparse(preferred_url)
                if parsed.port:
                    preferred_port = parsed.port
            except Exception:
                pass
            if await asyncio.to_thread(http_reachable, preferred_url):
                if await asyncio.to_thread(url_looks_like_navin, preferred_url):
                    preferred_url = None
                else:
                    return _http_json_response({"url": preferred_url, "source": "preferred"})

        url = await asyncio.to_thread(
            discover_running_project_url,
            preferred_port=preferred_port,
        )
        return _http_json_response({"url": url, "source": "discover" if url else None})

    def _handle_webui_fs_roots(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.fs_browse import fs_roots_payload

        try:
            default_path = str(self.workspaces.default_scope().project_path)
        except Exception:
            default_path = None
        return _http_json_response(fs_roots_payload(default_project_path=default_path))

    async def _handle_webui_fs_list(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.fs_browse import FsBrowseError, fs_list_payload

        query = _parse_query(request.path)
        raw_path = _query_first(query, "path") or ""
        show_hidden = _query_first(query, "hidden") == "1"
        try:
            payload = await asyncio.to_thread(fs_list_payload, raw_path, show_hidden=show_hidden)
        except FsBrowseError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_webui_fs_mkdir(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.fs_browse import FsBrowseError, fs_mkdir_payload

        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        parent = _query_first(query, "parent") or ""
        try:
            default_parent = str(self.workspaces.default_scope().project_path)
        except Exception:
            default_parent = None
        try:
            payload = await asyncio.to_thread(
                fs_mkdir_payload, name, parent, default_parent=default_parent
            )
        except FsBrowseError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_webui_evolve(self, request: WsRequest, action: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui import evolve_api

        query = _parse_query(request.path)
        raw_path = _query_first(query, "path") or ""
        promotion_id = _query_first(query, "id") or ""
        kind = _query_first(query, "kind") or ""
        try:
            if action == "overview":
                payload = await asyncio.to_thread(evolve_api.overview, raw_path)
            elif action == "status":
                payload = await asyncio.to_thread(evolve_api.daemon_status, raw_path)
            elif action == "verify":
                payload = await asyncio.to_thread(
                    evolve_api.verify_certificate, raw_path, promotion_id
                )
            elif action == "merge":
                payload = await asyncio.to_thread(
                    evolve_api.merge_promotion, raw_path, promotion_id
                )
            elif action == "pr":
                payload = await asyncio.to_thread(
                    evolve_api.publish_promotion, raw_path, promotion_id
                )
            elif action == "rollback":
                payload = await asyncio.to_thread(
                    evolve_api.rollback_promotion, raw_path, promotion_id
                )
            elif action == "enqueue":
                payload = await asyncio.to_thread(
                    evolve_api.enqueue_campaign, raw_path, kind, query
                )
            elif action == "cancel":
                payload = await asyncio.to_thread(
                    evolve_api.cancel_job, raw_path, _query_first(query, "job") or ""
                )
            elif action == "daemon_start":
                payload = await asyncio.to_thread(evolve_api.start_daemon, raw_path)
            elif action == "daemon_stop":
                payload = await asyncio.to_thread(evolve_api.stop_daemon, raw_path)
            elif action == "docs":
                lang = _query_first(query, "lang") or ""
                payload = await asyncio.to_thread(evolve_api.docs, lang)
            elif action == "autorun":
                enable = (_query_first(query, "enable") or "") == "true"
                payload = await asyncio.to_thread(evolve_api.set_autorun, raw_path, enable)
            else:
                return _http_error(404, "API route not found")
        except evolve_api.EvolveApiError as exc:
            return _http_error(exc.status, exc.message)
        if not isinstance(payload, dict):
            payload = {"result": payload}
        return _http_json_response(payload)

    async def _handle_webui_git_status(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.project_insights import ProjectInsightsError, git_status_payload

        query = _parse_query(request.path)
        raw_path = _query_first(query, "path") or ""
        try:
            # ``git status`` can take several seconds on cold WSL trees and used
            # to freeze every concurrent file-tree expand on the event loop.
            payload = await self.route_cache.get(
                f"git:status:{raw_path}",
                lambda: asyncio.to_thread(git_status_payload, raw_path),
                ttl=_GIT_STATUS_CACHE_TTL_S,
            )
        except ProjectInsightsError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_webui_git_clone(self, request: WsRequest) -> Response:
        """Clone a remote repo into Projects (or an explicit parent) and return the path."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.git_clone import GitCloneError, clone_git_project

        query = _parse_query(request.path)
        url = _query_first(query, "url") or ""
        parent = _query_first(query, "parent") or None
        name = _query_first(query, "name") or None
        try:
            default_parent = str(self.workspaces.default_scope().project_path)
        except Exception:
            default_parent = None
        try:
            payload = await asyncio.to_thread(
                clone_git_project,
                url,
                parent=parent,
                name=name,
                default_parent=default_parent,
            )
        except GitCloneError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            # Never return an empty 500: the picker only shows the body text.
            logger.exception("git clone failed for url={!r}", url)
            detail = str(exc).strip() or exc.__class__.__name__
            return _http_error(500, f"git clone failed: {detail[:400]}")
        return _http_json_response(payload)

    async def _handle_webui_diagnostics(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.project_insights import (
            ProjectInsightsError,
            file_diagnostics_payload,
        )

        query = _parse_query(request.path)
        raw_path = _query_first(query, "path") or ""
        raw_root = _query_first(query, "root") or ""
        try:
            # LSP round-trips spin on time.sleep while the server answers:
            # never on the event loop, or every chat stream freezes with it.
            payload = await asyncio.to_thread(
                file_diagnostics_payload, raw_path, raw_root=raw_root
            )
        except ProjectInsightsError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_webui_workspace_diagnostics(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.project_insights import (
            ProjectInsightsError,
            workspace_diagnostics_payload,
        )

        query = _parse_query(request.path)
        raw_path = _query_first(query, "path") or ""
        seeds_raw = _query_first(query, "seeds") or ""
        seeds = [part.strip() for part in seeds_raw.split(",") if part.strip()]
        try:
            # Blocked the gateway loop for up to 5.8 s per call (LSP waits on
            # time.sleep): every open chat stalled for that long.
            payload = await asyncio.to_thread(
                workspace_diagnostics_payload, raw_path, seed_paths=seeds or None
            )
        except ProjectInsightsError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_webui_project_audit(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.project_audit import ProjectAuditError, project_audit_payload

        query = _parse_query(request.path)
        raw_path = _query_first(query, "path") or ""
        try:
            # The scan walks the whole project tree; keep it off the event loop.
            payload = await asyncio.to_thread(project_audit_payload, raw_path)
        except ProjectAuditError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    def _handle_webui_plugins(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(webui_plugins_payload())

    def _plugins_mcp_reload(self):
        from navin.agent.tools.mcp import request_mcp_reload

        return lambda: request_mcp_reload(self.bus)

    async def _handle_webui_plugins_install(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        source = _query_first(query, "source") or ""
        location = _query_first(query, "location") or ""
        name = _query_first(query, "name") or None
        archive: bytes | None = None
        filename: str | None = None
        files: list[tuple[str, bytes]] | None = None
        if source == "upload":
            import base64

            try:
                raw = file_body_from_headers(request.headers) or "{}"
                body = json.loads(raw)
            except (WebUIFilePreviewError, json.JSONDecodeError, TypeError):
                body = {}
            if not isinstance(body, dict):
                body = {}
            name = str(body.get("name") or name or "").strip() or None
            filename = str(body.get("filename") or "pack.zip")
            archive_b64 = str(body.get("archiveBase64") or "")
            raw_files = body.get("files")
            if archive_b64:
                try:
                    archive = base64.b64decode(archive_b64, validate=True)
                except (ValueError, TypeError):
                    return _http_error(400, "invalid archive encoding")
            if isinstance(raw_files, list):
                files = []
                for item in raw_files:
                    if not isinstance(item, dict):
                        continue
                    rel = str(item.get("path") or "").strip()
                    encoded = str(item.get("contentBase64") or "")
                    if not rel or not encoded:
                        continue
                    try:
                        files.append((rel, base64.b64decode(encoded, validate=True)))
                    except (ValueError, TypeError):
                        return _http_error(400, "invalid file encoding")
        elif not location:
            return _http_error(400, "missing location")
        try:
            raw_apply = _query_first(query, "path")
            apply_workspace = None
            if raw_apply:
                apply_workspace = resolve_skill_scan_root(raw_apply, self.skills_workspace_path)
            payload = install_plugin(
                source=source,
                location=location,
                name=name,
                archive=archive,
                filename=filename,
                files=files,
                scope=_query_first(query, "scope") or "workspace",
                workspace_path=apply_workspace,
            )
        except SkillsApiError as exc:
            return _http_error(exc.status, exc.message)
        except PluginsApiError as exc:
            return _http_error(exc.status, exc.message)
        payload = await maybe_reload_mcp(payload, self._plugins_mcp_reload())
        return _http_json_response(payload)

    async def _handle_webui_plugins_remove(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        try:
            payload = remove_plugin(name)
        except PluginsApiError as exc:
            return _http_error(exc.status, exc.message)
        payload = await maybe_reload_mcp(payload, self._plugins_mcp_reload())
        return _http_json_response(payload)

    async def _handle_webui_plugins_set_enabled(
        self, request: WsRequest, enabled: bool
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        name = _query_first(query, "name") or ""
        try:
            payload = set_plugin_enabled(name, enabled)
        except PluginsApiError as exc:
            return _http_error(exc.status, exc.message)
        payload = await maybe_reload_mcp(payload, self._plugins_mcp_reload())
        return _http_json_response(payload)

    def _lsp_servers_root(self, query: dict[str, list[str]]) -> Path | None:
        """The project a language server is reported against, if we know it.

        Optional: which servers exist is machine-wide, only whether one can
        actually launch depends on the project.
        """
        raw = (_query_first(query, "key") or "").strip()
        if not raw:
            return None
        decoded = _decode_api_key(raw)
        if decoded is None or not _is_websocket_channel_session_key(decoded):
            return None
        with suppress(Exception):
            return Path(self.workspaces.scope_for_session_key(decoded).project_path)
        return None

    async def _handle_webui_lsp_servers(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.lsp_servers_api import lsp_servers_payload

        root = self._lsp_servers_root(_parse_query(request.path))
        # Reads the servers table and probes each launcher on disk.
        payload = await asyncio.to_thread(lsp_servers_payload, root)
        return _http_json_response(payload)

    async def _handle_webui_lsp_servers_search(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.lsp_servers_api import LspServersApiError, search_lsp_servers

        query = _parse_query(request.path)
        term = _query_first(query, "q") or ""
        try:
            # Calls out to the registry over the network.
            payload = await self.route_cache.get(
                f"lsp:search:{term}",
                lambda: asyncio.to_thread(search_lsp_servers, query=term),
                ttl=_LSP_SEARCH_CACHE_TTL_S,
            )
        except LspServersApiError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    async def _handle_webui_lsp_servers_install_detected(
        self, connection: Any, request: WsRequest
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_controls_available(connection):
            return _http_error(403, "installing language servers is localhost-only")
        from navin.webui.lsp_servers_api import (
            LspServersApiError,
            install_detected_lsp_server,
        )

        query = _parse_query(request.path)
        try:
            # Downloads the archive and then starts each server candidate to see
            # which one answers, so this is slow by nature.
            payload = await asyncio.to_thread(
                install_detected_lsp_server,
                extension=_query_first(query, "extension") or "",
                root=self._lsp_servers_root(query),
                version=_query_first(query, "version") or "",
                name=_query_first(query, "name") or "",
            )
        except LspServersApiError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            logger.exception("Installing a detected language server failed")
            return _http_error(500, str(exc))
        return _http_json_response(payload)

    async def _handle_webui_lsp_servers_install(
        self, connection: Any, request: WsRequest
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_controls_available(connection):
            return _http_error(403, "installing language servers is localhost-only")
        from navin.webui.lsp_servers_api import LspServersApiError, install_lsp_server

        query = _parse_query(request.path)
        try:
            # Downloads an archive from Open VSX; keep the event loop free.
            payload = await asyncio.to_thread(
                install_lsp_server,
                name=_query_first(query, "name") or "",
                root=self._lsp_servers_root(query),
                version=_query_first(query, "version") or "",
                extension=_query_first(query, "extension") or "",
                entrypoint=_query_first(query, "entrypoint") or "",
                suffixes=_query_first(query, "suffixes") or "",
                language_id=_query_first(query, "languageId") or "",
                native=_query_flag(query, "native"),
                platform_specific=_query_flag(query, "platformSpecific"),
            )
        except LspServersApiError as exc:
            return _http_error(exc.status, exc.message)
        except Exception as exc:
            logger.exception("Installing a language server failed")
            return _http_error(500, str(exc))
        return _http_json_response(payload)

    async def _handle_webui_lsp_servers_uninstall(
        self, connection: Any, request: WsRequest
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_controls_available(connection):
            return _http_error(403, "removing language servers is localhost-only")
        from navin.webui.lsp_servers_api import LspServersApiError, uninstall_lsp_server

        query = _parse_query(request.path)
        try:
            payload = await asyncio.to_thread(
                uninstall_lsp_server,
                name=_query_first(query, "name") or "",
                root=self._lsp_servers_root(query),
            )
        except LspServersApiError as exc:
            return _http_error(exc.status, exc.message)
        return _http_json_response(payload)

    def _handle_webui_sidebar_state(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(read_webui_sidebar_state())

    def _sidebar_or_onboarding_state_raw(self, request: WsRequest) -> str | None:
        """Prefer chunked body headers; keep the old ``?state=`` query as fallback."""
        try:
            raw = file_body_from_headers(request.headers)
        except WebUIFilePreviewError:
            raw = None
        if isinstance(raw, str) and raw.strip():
            return raw
        query = _parse_query(request.path)
        return _query_first(query, "state")

    def _handle_webui_sidebar_state_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        raw_state = self._sidebar_or_onboarding_state_raw(request)
        if raw_state is None:
            return _http_error(400, "missing state")
        try:
            decoded = json.loads(raw_state)
        except json.JSONDecodeError:
            return _http_error(400, "state must be JSON")
        if not isinstance(decoded, dict):
            return _http_error(400, "state must be an object")
        try:
            state = write_webui_sidebar_state(decoded)
        except ValueError as e:
            return _http_error(400, str(e))
        except OSError:
            self._log.exception("failed to write webui sidebar state")
            return _http_error(500, "failed to write sidebar state")
        return _http_json_response(state)

    def _handle_webui_onboarding(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(read_webui_onboarding())

    def _handle_webui_onboarding_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        raw_state = self._sidebar_or_onboarding_state_raw(request)
        if raw_state is None:
            return _http_error(400, "missing state")
        try:
            decoded = json.loads(raw_state)
        except json.JSONDecodeError:
            return _http_error(400, "state must be JSON")
        if not isinstance(decoded, dict):
            return _http_error(400, "state must be an object")
        try:
            state = write_webui_onboarding(decoded)
        except ValueError as e:
            return _http_error(400, str(e))
        except OSError:
            self._log.exception("failed to write webui onboarding")
            return _http_error(500, "failed to write onboarding")
        return _http_json_response(state)

    # -- navin.live account (Cursor-style sign-in) --------------------------

    async def _handle_webui_account(self, request: WsRequest) -> Response:
        if self.account is None:
            if not self.check_api_token(request):
                return _http_error(401, "Unauthorized")
            return _http_json_response(
                {
                    "connected": False,
                    "available": False,
                    "plan": "",
                    "plan_label": "",
                    "plan_price_usd": None,
                    "email": "",
                    "name": "",
                    "server_url": "",
                    "managed_key_active": False,
                    "org_id": None,
                    "org_role": None,
                    "seat_count": None,
                }
            )
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        refresh = _query_first(query, "refresh") == "1"
        # validate() does network I/O against navin.live; keep the loop free.
        # Never 500 here: the sidebar poller would otherwise toast a raw
        # route + HTTP code in front of the user.
        try:
            # Every window polls this. The service already caches the remote
            # validate for five minutes, but nothing stopped three windows from
            # stampeding the network the moment that cache expired.
            payload = await self.route_cache.get(
                "webui:account",
                lambda: asyncio.get_running_loop().run_in_executor(
                    _REMOTE_EXECUTOR,
                    partial(self.account.status_payload, refresh=refresh),
                ),
                ttl=_ACCOUNT_CACHE_TTL_S,
                force=refresh,
            )
        except Exception:
            logger.exception("webui account status failed")
            payload = {
                "connected": False,
                "plan": "",
                "plan_label": "",
                "plan_price_usd": None,
                "email": "",
                "name": "",
                "server_url": "",
                "managed_key_active": False,
                "org_id": None,
                "org_role": None,
                "seat_count": None,
            }
        return _http_json_response(payload)

    def _handle_webui_runtime_health(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.runtime_health import runtime_health_payload

        workspace = None
        try:
            workspace = str(getattr(self.config, "workspace", "") or "") or None
        except Exception:
            workspace = None
        return _http_json_response(runtime_health_payload(workspace=workspace))

    def _montage_workspace_root(self, request: WsRequest) -> str | None:
        """Resolve the project root for Montage status/assets (session or path)."""
        query = _parse_query(request.path)
        raw_path = (_query_first(query, "path") or "").strip()
        if raw_path:
            return raw_path
        session_key = (
            _query_first(query, "session_key") or _query_first(query, "sessionKey") or ""
        ).strip()
        if session_key:
            decoded = _decode_api_key(session_key) or session_key
            if _is_websocket_channel_session_key(decoded):
                scope = self.workspaces.scope_for_session_key(decoded)
                project = getattr(scope, "project_path", None)
                if project:
                    return str(project)
        try:
            return str(getattr(self.config, "workspace", "") or "") or None
        except Exception:
            return None

    def _handle_webui_marketing_qa_readiness(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.marketing_api import (
            MarketingQAApiError,
            marketing_qa_readiness,
        )

        try:
            return _http_json_response(
                marketing_qa_readiness(self._montage_workspace_root(request))
            )
        except MarketingQAApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_marketing_qa_assets(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.marketing_api import (
            MarketingQAApiError,
            list_marketing_qa_assets,
        )

        query = _parse_query(request.path)
        try:
            limit = int(_query_first(query, "limit") or 500)
            return _http_json_response(
                list_marketing_qa_assets(
                    self._montage_workspace_root(request), limit=limit
                )
            )
        except ValueError:
            return _http_error(400, "invalid limit")
        except MarketingQAApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_marketing_qa_reports(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.marketing_api import (
            MarketingQAApiError,
            list_marketing_qa_reports,
        )

        query = _parse_query(request.path)
        try:
            limit = int(_query_first(query, "limit") or 25)
            return _http_json_response(
                list_marketing_qa_reports(
                    self._montage_workspace_root(request), limit=limit
                )
            )
        except ValueError:
            return _http_error(400, "invalid limit")
        except MarketingQAApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_marketing_qa_report(
        self, request: WsRequest, report_id: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.marketing_api import (
            MarketingQAApiError,
            get_marketing_qa_report,
        )

        try:
            return _http_json_response(
                get_marketing_qa_report(
                    self._montage_workspace_root(request), report_id
                )
            )
        except MarketingQAApiError as exc:
            return _http_error(exc.status, exc.message)

    async def _handle_webui_marketing_qa_run(
        self, request: WsRequest
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.marketing_api import MarketingQAApiError, run_marketing_qa

        try:
            payload = json.loads(file_body_from_headers(request.headers))
            if not isinstance(payload, dict):
                raise ValueError
            references = payload.get("references")
            claims = payload.get("claims")
            requirements = payload.get("requirements")
            if references is not None and not isinstance(references, list):
                raise ValueError
            if claims is not None and not isinstance(claims, list):
                raise ValueError
            if requirements is not None and not isinstance(requirements, dict):
                raise ValueError
            report = await run_marketing_qa(
                self._montage_workspace_root(request),
                candidate=str(payload.get("candidate") or ""),
                references=[str(item) for item in references or []],
                claims=[str(item) for item in claims or []],
                requirements=requirements,
            )
            return _http_json_response(report)
        except (ValueError, WebUIFilePreviewError, json.JSONDecodeError):
            return _http_error(400, "invalid visual QA payload")
        except MarketingQAApiError as exc:
            return _http_error(exc.status, exc.message)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            return _http_error(422, str(exc))

    def _handle_webui_marketing_qa_override(
        self, request: WsRequest, report_id: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.marketing_api import (
            MarketingQAApiError,
            override_marketing_qa_report,
        )

        try:
            payload = json.loads(file_body_from_headers(request.headers))
            if not isinstance(payload, dict):
                raise ValueError
            return _http_json_response(
                override_marketing_qa_report(
                    self._montage_workspace_root(request),
                    report_id,
                    reason=str(payload.get("reason") or ""),
                    verdict=str(payload.get("verdict") or "PASS"),
                )
            )
        except (ValueError, WebUIFilePreviewError, json.JSONDecodeError):
            return _http_error(400, "invalid visual QA override payload")
        except MarketingQAApiError as exc:
            return _http_error(exc.status, exc.message)

    async def _handle_webui_montage_status(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.config.loader import load_config
        from navin.webui.montage_api import montage_status_payload

        workspace_root = self._montage_workspace_root(request)

        def _status() -> dict[str, Any]:
            # The toolchain doctor probes PATH (shutil.which over /mnt/c on
            # WSL) and spawns ffmpeg/chrome: seconds of blocking work.
            return montage_status_payload(load_config(), workspace_root=workspace_root)

        return _http_json_response(await asyncio.to_thread(_status))

    def _handle_webui_montage_assets(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, list_montage_assets

        root = self._montage_workspace_root(request)
        try:
            return _http_json_response(list_montage_assets(root))
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_montage_jobs(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, list_montage_jobs

        query = _parse_query(request.path)
        status = (_query_first(query, "status") or "").strip() or None
        try:
            limit = int(_query_first(query, "limit") or 100)
            return _http_json_response(
                list_montage_jobs(self._montage_workspace_root(request), status=status, limit=limit)
            )
        except ValueError:
            return _http_error(400, "invalid limit")
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_montage_job(self, request: WsRequest, job_id: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, get_montage_job

        try:
            return _http_json_response(
                get_montage_job(self._montage_workspace_root(request), job_id)
            )
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    def _montage_job_notifier(self, root: str | None) -> Any:
        """Live job updates for the WebUI (``montage_updated`` broadcasts)."""
        from navin.montage.notify import job_notifier

        return job_notifier(getattr(self, "bus", None), root)

    def _publish_montage_change(self, root: str | None, *, kind: str, name: str | None) -> None:
        from navin.montage.notify import publish_montage_update

        publish_montage_update(getattr(self, "bus", None), root, kind=kind, name=name)

    async def _handle_webui_montage_job_resume(self, request: WsRequest, job_id: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, resume_montage_job

        query = _parse_query(request.path)
        wait = (_query_first(query, "wait") or "1").strip().lower() not in {"0", "false", "no"}
        root = self._montage_workspace_root(request)
        try:
            return _http_json_response(
                await resume_montage_job(
                    root, job_id, wait=wait, notify=self._montage_job_notifier(root)
                )
            )
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_montage_job_cancel(self, request: WsRequest, job_id: str) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.montage.jobs import job_summary
        from navin.webui.montage_api import MontageApiError, cancel_montage_job

        root = self._montage_workspace_root(request)
        try:
            manifest = cancel_montage_job(root, job_id)
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)
        from navin.montage.notify import publish_montage_update

        publish_montage_update(
            getattr(self, "bus", None), root, kind="job", name=job_id, job=job_summary(manifest)
        )
        return _http_json_response(manifest)

    async def _handle_webui_montage_probe(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, probe_montage_media

        query = _parse_query(request.path)
        try:
            return _http_json_response(
                await probe_montage_media(
                    self._montage_workspace_root(request), _query_first(query, "file")
                )
            )
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_montage_timelines(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, list_montage_timelines

        try:
            return _http_json_response(
                list_montage_timelines(self._montage_workspace_root(request))
            )
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_montage_timeline(
        self, request: WsRequest, name: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, get_montage_timeline

        try:
            return _http_json_response(
                get_montage_timeline(self._montage_workspace_root(request), name)
            )
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    def _handle_webui_montage_timeline_put(
        self, request: WsRequest, name: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, put_montage_timeline

        root = self._montage_workspace_root(request)
        try:
            raw = file_body_from_headers(request.headers)
            document = json.loads(raw)
            if not isinstance(document, dict):
                raise ValueError
            saved = put_montage_timeline(root, name, document)
        except (ValueError, WebUIFilePreviewError):
            return _http_error(400, "invalid timeline JSON payload")
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)
        self._publish_montage_change(root, kind="timeline", name=name)
        return _http_json_response(saved)

    def _handle_webui_montage_timeline_delete(
        self, request: WsRequest, name: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, delete_montage_timeline

        root = self._montage_workspace_root(request)
        try:
            deleted = delete_montage_timeline(root, name)
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)
        self._publish_montage_change(root, kind="timeline", name=name)
        return _http_json_response(deleted)

    async def _handle_webui_montage_timeline_preview(
        self, request: WsRequest, name: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import MontageApiError, preview_montage_timeline

        try:
            return _http_json_response(
                await preview_montage_timeline(
                    self._montage_workspace_root(request), name
                )
            )
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    async def _handle_webui_montage_timeline_render(
        self, request: WsRequest, name: str
    ) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        from navin.webui.montage_api import (
            MontageApiError,
            render_montage_timeline,
            start_montage_timeline_render,
        )

        query = _parse_query(request.path)
        output = (_query_first(query, "output") or "").strip() or None
        # Default is a background job the UI follows (progress, cancel);
        # ``wait=1`` keeps the request open until ffmpeg finishes (scripts, tests).
        wait = (_query_first(query, "wait") or "").strip().lower() in {"1", "true", "yes"}
        root = self._montage_workspace_root(request)
        notify = self._montage_job_notifier(root)
        try:
            if wait:
                return _http_json_response(
                    await render_montage_timeline(root, name, output=output, notify=notify)
                )
            return _http_json_response(
                start_montage_timeline_render(root, name, output=output, notify=notify)
            )
        except MontageApiError as exc:
            return _http_error(exc.status, exc.message)

    async def _handle_webui_montage_setup(self, connection: Any, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if not self.workspace_controls_available(connection):
            return _http_error(403, "montage setup is localhost-only")
        import asyncio

        from navin.config.loader import load_config
        from navin.montage import MONTAGE_HOME
        from navin.montage.bootstrap import run_setup
        from navin.montage.setup_stream import MontageSetupStream
        from navin.webui.montage_api import montage_status_payload

        query = _parse_query(request.path)
        force_raw = (
            (_query_first(query, "force") or _query_first(query, "Force") or "").strip().lower()
        )
        force = force_raw in {"1", "true", "yes"}
        package = (
            (_query_first(query, "package") or _query_first(query, "Package") or "hyperframes")
            .strip()
            .lower()
        )
        stream_raw = (
            (_query_first(query, "stream") or _query_first(query, "Stream") or "").strip().lower()
        )
        want_stream = stream_raw in {"1", "true", "yes"}
        config = load_config()
        command = f"navin montage setup --package {package}"
        if force:
            command += " --force"
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        # Broadcast (`*`) so the install console works without `?chat=` / session_key.
        stream = MontageSetupStream(
            self.bus,
            "*",
            command=command,
            cwd=str(MONTAGE_HOME.expanduser()),
            loop=loop,
        )
        try:
            result = await run_setup(
                force=force,
                package=package,
                config=config,
                on_log=stream.log,
            )
        except Exception as exc:  # noqa: BLE001 - surface to console + JSON
            stream.log(f"ERROR: {exc}\n")
            stream.finish(1)
            if want_stream:
                return _http_response(
                    stream.ndjson_body(),
                    status=500,
                    content_type="application/x-ndjson; charset=utf-8",
                )
            return _http_error(500, str(exc))
        exit_code = 0 if result.get("ok") else 1
        if result.get("fix"):
            stream.log(f"Fix: {result['fix']}\n")
        if result.get("note"):
            stream.log(f"{result['note']}\n")
        stream.finish(exit_code)
        status = montage_status_payload(
            config,
            workspace_root=self._montage_workspace_root(request),
        )
        if want_stream:
            # Append a final status event for clients that only read the body.
            import json as _json

            body = stream.ndjson_body() + (
                _json.dumps(
                    {"type": "status", "setup": result, "status": status},
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            return _http_response(
                body,
                content_type="application/x-ndjson; charset=utf-8",
            )
        return _http_json_response({"setup": result, "status": status})

    def _handle_webui_account_connect_url(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        if self.account is None:
            return _http_error(404, "navin.live account is not available in this build")
        host = _safe_host_header(_case_insensitive_header(request.headers, "Host"))
        if not host:
            host = _host_for_url(self.config.host, self.config.port)
        # Legacy callback kept for API shape; connect_url no longer embeds it.
        callback = f"http://{host}/webui/account/callback"
        query = _parse_query(request.path)
        try:
            url = self.account.connect_url(
                callback=callback,
                locale=_query_first(query, "locale"),
            )
        except AccountApiError as e:
            return _http_error(e.status, e.message)
        # Desktop WebView: window.open is a no-op; open the OS browser from
        # the gateway when the UI asks (?open=1).
        opened = False
        if _query_first(query, "open") == "1":
            from navin.utils.open_browser import open_external_url

            opened = open_external_url(url)
        # Background claim: poll the site for the ticket - no 127.0.0.1 tab.
        state = self.account.connect_state_from_url(url)
        if state:
            self.account.start_background_claim(state)
        return _http_json_response({"url": url, "opened": opened})

    def _handle_webui_open_url(self, request: WsRequest) -> Response:
        """Open an http(s) URL in the OS browser (WebView ``window.open`` is unreliable)."""
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        raw = (_query_first(query, "url") or "").strip()
        from navin.utils.open_browser import open_external_url

        opened = open_external_url(raw)
        if not opened and not raw.startswith(("http://", "https://")):
            return _http_error(400, "invalid url")
        return _http_json_response({"opened": opened, "url": raw})

    async def _handle_webui_account_activate(self, request: WsRequest) -> Response:
        if self.account is None:
            return _http_error(404, "navin.live account is not available in this build")
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        key = _query_first(query, "licenseKey") or ""
        try:
            payload = await asyncio.to_thread(self.account.activate, key)
        except AccountApiError as e:
            return _http_error(e.status, e.message)
        return _http_json_response(payload)

    async def _handle_webui_account_logout(self, request: WsRequest) -> Response:
        if self.account is None:
            return _http_error(404, "navin.live account is not available in this build")
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = await asyncio.to_thread(self.account.disconnect)
        return _http_json_response(payload)

    async def _handle_webui_account_callback(self, connection: Any, request: WsRequest) -> Response:
        """Browser lands here after signing in on navin.live.

        No API token: the browser tab is not the WebUI. Two things gate it
        instead - the request must come from the local machine, and it must
        carry the one-time state nonce minted for this very connect flow.
        """

        def _page(ok: bool, status: int, detail: str = "") -> Response:
            return _http_response(
                account_callback_html(ok=ok, detail=detail).encode("utf-8"),
                status=status,
                content_type="text/html; charset=utf-8",
                extra_headers=[
                    ("Cache-Control", "no-store, no-cache, must-revalidate"),
                    ("Referrer-Policy", "no-referrer"),
                    ("Pragma", "no-cache"),
                ],
            )

        if self.account is None or account_callback_html is None:
            return _http_error(404, "navin.live account is not available in this build")
        if not _is_local_browser_request(connection, request.headers):
            return _http_error(403, "account callback is localhost-only")
        query = _parse_query(request.path)
        state = _query_first(query, "state") or ""
        ticket = _query_first(query, "ticket") or ""
        # Anciens liens avec licenseKey : refusés (clé ne doit plus circuler
        # dans l'URL / l'historique / les captures d'écran).
        legacy_key = _query_first(query, "licenseKey") or ""
        if legacy_key and not ticket:
            return _page(
                False,
                400,
                "This connect link is outdated. Update Navin, then connect again "
                "from Settings - Account.",
            )
        if not self.account.take_state(state):
            return _page(
                False,
                400,
                "This connect link expired or was already used. Start again from Navin settings.",
            )
        try:
            if ticket:
                await asyncio.to_thread(self.account.activate_ticket, ticket)
            else:
                return _page(
                    False,
                    400,
                    "Missing connect ticket. Start again from Navin settings.",
                )
        except AccountApiError as e:
            return _page(False, e.status, e.message)
        return _page(True, 200)

    # -- OpenRouter OAuth PKCE (Free onboarding path) ------------------------

    def _handle_webui_openrouter_connect_url(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        host = _safe_host_header(_case_insensitive_header(request.headers, "Host"))
        if not host:
            host = _host_for_url(self.config.host, self.config.port)
        callback = f"http://{host}/webui/openrouter/callback"
        query = _parse_query(request.path)
        url = self.openrouter_oauth.connect_url(
            callback=callback,
            lang=_query_first(query, "locale") or "",
        )
        opened = False
        if _query_first(query, "open") == "1":
            from navin.utils.open_browser import open_external_url

            opened = open_external_url(url)
        return _http_json_response({"url": url, "opened": opened})

    async def _handle_webui_openrouter_status(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = await asyncio.to_thread(self.openrouter_oauth.status_payload)
        return _http_json_response(payload)

    async def _handle_webui_openrouter_callback(
        self, connection: Any, request: WsRequest
    ) -> Response:
        """Browser lands here after authorizing Navin on openrouter.ai.

        No API token: the browser tab is not the WebUI. Two things gate it
        instead - the request must come from the local machine, and it must
        carry the one-time state nonce whose PKCE verifier only this gateway
        holds. The code alone is useless to anyone without that verifier.
        """

        query = _parse_query(request.path)
        lang = _query_first(query, "lang") or ""

        def _page(ok: bool, status: int, detail: str = "") -> Response:
            # Redirect to the navin.live confirmation page so the browser
            # never parks on a raw 127.0.0.1 URL. The local HTML stays as a
            # fallback when the site URL cannot be resolved.
            try:
                location = openrouter_finish_redirect_url(ok=ok, lang=lang)
            except Exception:
                location = ""
            if location:
                return _http_response(
                    b"",
                    status=302,
                    extra_headers=[
                        ("Location", location),
                        ("Cache-Control", "no-store, no-cache, must-revalidate"),
                        ("Referrer-Policy", "no-referrer"),
                    ],
                )
            return _http_response(
                openrouter_callback_html(ok=ok, detail=detail).encode("utf-8"),
                status=status,
                content_type="text/html; charset=utf-8",
                extra_headers=[
                    ("Cache-Control", "no-store, no-cache, must-revalidate"),
                    ("Referrer-Policy", "no-referrer"),
                    ("Pragma", "no-cache"),
                ],
            )

        if not _is_local_browser_request(connection, request.headers):
            return _http_error(403, "openrouter callback is localhost-only")
        state = _query_first(query, "state") or ""
        code = _query_first(query, "code") or ""
        verifier = self.openrouter_oauth.take_verifier(state)
        if verifier is None:
            return _page(
                False,
                400,
                "This connect link expired or was already used. Start again from Navin.",
            )
        if not code:
            return _page(
                False,
                400,
                "OpenRouter sent no connect code. Start again from Navin.",
            )
        try:
            await asyncio.to_thread(
                self.openrouter_oauth.exchange_and_store,
                code=code,
                verifier=verifier,
            )
        except OpenRouterOAuthError as e:
            return _page(False, e.status, e.message)
        return _page(True, 200)

    def _handle_webui_exec_policy(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        return _http_json_response(exec_policy_payload())

    async def _handle_webui_exec_policy_update(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        query = _parse_query(request.path)
        raw_state = _query_first(query, "state")
        if raw_state is None:
            return _http_error(400, "missing state")
        try:
            decoded = json.loads(raw_state)
        except json.JSONDecodeError:
            return _http_error(400, "state must be JSON")
        if not isinstance(decoded, dict):
            return _http_error(400, "state must be an object")
        try:
            payload = update_exec_policy(decoded)
        except ExecPolicyError as e:
            return _http_error(400, e.message)
        except OSError:
            self._log.exception("failed to save exec policy")
            return _http_error(500, "failed to save exec policy")
        try:
            payload["hot_reload"] = await request_exec_policy_reload(self.bus)
        except Exception:
            self._log.exception("exec policy hot reload request failed")
            payload["hot_reload"] = {"ok": False, "requires_restart": True}
        return _http_json_response(payload)

    # -- Static file serving ------------------------------------------------

    def _serve_static(self, request_path: str) -> Response | None:
        assert self.static_dist_path is not None
        rel = request_path.lstrip("/")
        if not rel:
            rel = "index.html"
        if ".." in rel.split("/") or rel.startswith("/"):
            return _http_error(403, "Forbidden")
        dist_root = self.static_dist_path.resolve()
        candidate = (dist_root / rel).resolve()
        try:
            candidate.relative_to(dist_root)
        except ValueError:
            return _http_error(403, "Forbidden")
        if not candidate.is_file():
            index = self.static_dist_path / "index.html"
            if index.is_file():
                candidate = index
            else:
                return None
        try:
            body = candidate.read_bytes()
        except OSError as e:
            self._log.warning("static: failed to read {}: {}", candidate, e)
            return _http_error(500, "Internal Server Error")
        if candidate.name.endswith(".webmanifest"):
            ctype = "application/manifest+json; charset=utf-8"
        else:
            ctype, _ = mimetypes.guess_type(candidate.name)
            if ctype is None:
                ctype = "application/octet-stream"
            if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
                ctype = f"{ctype}; charset=utf-8"
        # Only hashed assets are immutable; entry points / SW must revalidate.
        if candidate.name in {"index.html", "manifest.webmanifest", "sw.js"}:
            cache = "no-cache"
        else:
            cache = "public, max-age=31536000, immutable"
        return _http_response(
            body,
            status=200,
            content_type=ctype,
            extra_headers=[("Cache-Control", cache)],
        )


def _automation_values_from_request(request: WsRequest) -> dict[str, Any] | None:
    raw = _case_insensitive_header(request.headers, _AUTOMATION_VALUES_HEADER)
    if not raw:
        return {}
    try:
        values = json.loads(raw)
    except Exception:
        try:
            values = json.loads(unquote(raw))
        except Exception:
            return None
    return values if isinstance(values, dict) else None


def _parse_automation_update(
    values: dict[str, Any],
    *,
    current_job: CronJob | None = None,
) -> dict[str, Any] | str:
    update: dict[str, Any] = {}
    if "name" in values:
        raw_name = values.get("name")
        if not isinstance(raw_name, str):
            return "name must be a string"
        name = raw_name.strip()
        if not name:
            return "name cannot be empty"
        update["name"] = name
    if "message" in values:
        raw_message = values.get("message")
        if not isinstance(raw_message, str):
            return "message must be a string"
        message = raw_message.strip()
        if not message:
            return "message cannot be empty"
        update["message"] = message
    if "schedule" in values:
        raw_schedule = values.get("schedule")
        if not isinstance(raw_schedule, dict):
            return "schedule must be an object"
        parsed_schedule = _parse_automation_schedule(raw_schedule)
        if isinstance(parsed_schedule, str):
            return parsed_schedule
        if current_job is not None and _schedule_matches_job(parsed_schedule, current_job):
            return update
        schedule_error = _validate_automation_schedule(parsed_schedule)
        if schedule_error:
            return schedule_error
        update["schedule"] = parsed_schedule
        update["delete_after_run"] = parsed_schedule.kind == "at"
    if "limits" in values:
        parsed_limits = _parse_automation_limits(values.get("limits"))
        if isinstance(parsed_limits, str):
            return parsed_limits
        update["limits"] = parsed_limits
    return update


def _parse_automation_limits(raw: Any) -> CronLimits | str:
    if not isinstance(raw, dict):
        return "limits must be an object"
    budget = _non_negative_int(raw.get("daily_token_budget", 0))
    if budget is None:
        return "daily_token_budget must be a non-negative integer"
    failures = _non_negative_int(raw.get("max_consecutive_failures", 0))
    if failures is None:
        return "max_consecutive_failures must be a non-negative integer"
    if failures > 100:
        return "max_consecutive_failures must be 100 or less"
    return CronLimits(daily_token_budget=budget, max_consecutive_failures=failures)


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _parse_local_trigger_update(values: dict[str, Any]) -> dict[str, Any] | str:
    update: dict[str, Any] = {}
    if "name" in values:
        raw_name = values.get("name")
        if not isinstance(raw_name, str):
            return "name must be a string"
        name = raw_name.strip()
        if not name:
            return "name cannot be empty"
        update["name"] = name
    forbidden = [key for key in ("message", "schedule") if key in values]
    if forbidden:
        return "local trigger updates only support name"
    return update


def _parse_automation_schedule(values: dict[str, Any]) -> CronSchedule | str:
    raw_kind = values.get("kind")
    if not isinstance(raw_kind, str):
        return "schedule kind must be a string"
    kind = raw_kind.strip()
    if kind == "recurrence":
        # A preset the client picked: the server compiles it, so both agree on
        # what "every 8 hours" means.
        raw_tz = values.get("tz")
        if raw_tz is not None and not isinstance(raw_tz, str):
            return "schedule timezone must be a string"
        tz = raw_tz.strip() if isinstance(raw_tz, str) else ""
        try:
            recurrence = recurrence_from_payload(values.get("recurrence"), tz=tz or None)
        except RecurrenceError as exc:
            return str(exc)
        return recurrence.to_schedule()
    if kind == "every":
        every_ms = _positive_int(values.get("every_ms"))
        if every_ms is None:
            return "every schedule requires positive every_ms"
        return CronSchedule(kind="every", every_ms=every_ms)
    if kind == "cron":
        raw_expr = values.get("expr")
        if not isinstance(raw_expr, str):
            return "cron schedule requires expr"
        expr = raw_expr.strip()
        if not expr:
            return "cron schedule requires expr"
        raw_tz = values.get("tz")
        if raw_tz is not None and not isinstance(raw_tz, str):
            return "cron schedule timezone must be a string"
        tz = raw_tz.strip() if isinstance(raw_tz, str) else ""
        return CronSchedule(kind="cron", expr=expr, tz=tz or None)
    if kind == "at":
        at_ms = _positive_int(values.get("at_ms"))
        if at_ms is None:
            return "one-time schedule requires positive at_ms"
        return CronSchedule(kind="at", at_ms=at_ms)
    return "unknown schedule kind"


def _schedule_matches_job(schedule: CronSchedule, job: CronJob) -> bool:
    current = job.schedule
    if schedule.kind != current.kind:
        return False
    if schedule.kind == "at":
        return schedule.at_ms == current.at_ms
    if schedule.kind == "every":
        return schedule.every_ms == current.every_ms
    if schedule.kind == "cron":
        return (schedule.expr or "") == (current.expr or "") and (schedule.tz or None) == (
            current.tz or None
        )
    return False


def _validate_automation_schedule(schedule: CronSchedule) -> str | None:
    if schedule.kind == "at":
        if not schedule.at_ms or schedule.at_ms <= int(time.time() * 1000):
            return "one-time schedule must be in the future"
        return None
    if schedule.kind != "cron":
        return None

    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from croniter import croniter

        tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
        base = datetime.now(tz=tz)
        croniter(schedule.expr, base).get_next(datetime)
    except Exception:
        return "cron schedule is invalid"
    return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _is_websocket_channel_session_key(key: str) -> bool:
    return key.startswith("websocket:")
