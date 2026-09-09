# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""WebSocket server channel: navin acts as a WebSocket server and serves connected clients."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import ssl
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import Any, Self

import websockets.http11 as _ws_http11
from pydantic import Field, field_validator, model_validator
from websockets.asyncio.server import ServerConnection, serve, unix_serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request as WsRequest

from navin.agent.choice import CUSTOM_TEXT_MAX
from navin.bus.events import (
    INBOUND_META_MODEL_PRESET,
    INBOUND_META_RUNTIME_CONTROL,
    OUTBOUND_META_AGENT_UI,
    RUNTIME_CONTROL_APPROVAL_DECISION,
    RUNTIME_CONTROL_CHOICE_ANSWER,
    InboundMessage,
    OutboundMessage,
)
from navin.bus.outbound_events import (
    AgentBrowserEvent,
    AgentExecEvent,
    ApprovalClosedEvent,
    ApprovalRequestedEvent,
    ArtifactSelectEvent,
    ArtifactUpsertEvent,
    BoardUpdatedEvent,
    CheckpointSavedEvent,
    ChoiceClosedEvent,
    ChoiceRequestedEvent,
    ComposerModeRequestedEvent,
    ContextCompactedEvent,
    EditorOpenRequestedEvent,
    FilePreviewOpenRequestedEvent,
    GoalStateSyncEvent,
    GoalStatusEvent,
    MetagraphUpdatedEvent,
    MontageUpdatedEvent,
    NotificationEvent,
    PreviewOpenRequestedEvent,
    ProductModuleRequestedEvent,
    ProgressEvent,
    RetryWaitEvent,
    RuntimeModelUpdatedEvent,
    SessionUpdatedEvent,
    SubagentProgressEvent,
    TerminalOpenRequestedEvent,
    TurnEndEvent,
    outbound_event_from_message,
    outbound_message_for_event,
)
from navin.bus.queue import MessageBus
from navin.channels.base import BaseChannel
from navin.collab.acl import action_allowed, role_from_license
from navin.collab.presence import PresenceTracker
from navin.config.schema import Base
from navin.index.warmer import schedule_warm as schedule_index_warm
from navin.mobile.preview import PreviewError
from navin.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScope,
    WorkspaceScopeError,
)
from navin.session.goal_state import goal_state_ws_blob
from navin.session.webui_turns import websocket_turn_wall_started_at
from navin.utils.document_templates import normalize_document_template_mention
from navin.utils.file_mentions import (
    FILE_MENTION_METADATA_KEY,
    normalize_file_mentions,
)
from navin.utils.media_templates import (
    MEDIA_TEMPLATE_METADATA_KEY,
    MEDIA_TEMPLATES_METADATA_KEY,
    normalize_media_template_mentions,
)
from navin.webui.blocking_pool import blocking_pool_size, watch_pool_latency
from navin.webui.cli_apps_api import normalize_cli_app_mentions
from navin.webui.collab_tokens import (
    collab_identity_from_claims,
    verify_collab_token,
)
from navin.webui.forking import handle_webui_fork_chat
from navin.webui.gateway_services import GatewayServices
from navin.webui.http_utils import (
    normalize_config_path as _normalize_config_path,
)
from navin.webui.http_utils import (
    parse_request_path as _parse_request_path,
)
from navin.webui.http_utils import (
    query_first as _query_first,
)
from navin.webui.mcp_presets_api import normalize_mcp_preset_mentions
from navin.webui.mobile_preview_ws import MobilePreviewManager
from navin.webui.stall_watch import LoopLagMonitor
from navin.webui.terminal_ws import (
    TerminalError,
    TerminalManager,
    available_shells,
    decode_input,
    encode_output,
)
from navin.webui.transcription_ws import webui_transcription_event
from navin.webui.voice_session_ws import close_voice_sessions, webui_voice_session_events
from navin.webui.websocket_logging import websockets_server_logger

# The gateway's HTTP layer has no request bodies, so file-save, notes and
# assist payloads travel as chunked base64 request headers (one header per
# 6000-char chunk). websockets' default of 128 headers caps a save around
# 450 KB and answers 431 before navin ever sees the request. The constant is
# read at parse time, so raising the module global here (never lowering an
# explicit WEBSOCKETS_MAX_NUM_HEADERS override) lifts the cap for every
# listener this channel starts.
_ws_http11.MAX_NUM_HEADERS = max(_ws_http11.MAX_NUM_HEADERS, 640)

# Plain HTTP WebUI routes also run through websockets.process_request.
_WEBUI_HTTP_OPEN_TIMEOUT_S = 360.0
# Floor applied even when an older config.json still has ping_timeout_s=20.
# See WebSocketConfig.ping_timeout_s.
_MIN_WEBUI_PING_TIMEOUT_S = 120.0


def effective_ping_timeout_s(configured: float) -> float:
    """Never let a stale 20s ping timeout drop an IDE WebView mid-turn."""
    return max(float(configured), _MIN_WEBUI_PING_TIMEOUT_S)


class WebSocketConfig(Base):
    """WebSocket server channel configuration.

    Clients connect with URLs like ``ws://{host}:{port}{path}?client_id=...&token=...``.
    - ``client_id``: Used for ``allow_from`` authorization; if omitted, a value is generated and logged.
    - ``token``: If non-empty, the ``token`` query param may match this static secret; short-lived tokens
      from ``token_issue_path`` are also accepted.
    - ``token_issue_path``: If non-empty, **GET** (HTTP/1.1) to this path returns JSON
      ``{"token": "...", "expires_in": <seconds>}``; use ``?token=...`` when opening the WebSocket.
      Must differ from ``path`` (the WS upgrade path). If the client runs in the **same process** as
      navin and shares the asyncio loop, use a thread or async HTTP client for GET-do not call
      blocking ``urllib`` or synchronous ``httpx`` from inside a coroutine.
    - ``token_issue_secret``: If non-empty, token requests must send ``Authorization: Bearer <secret>`` or
      ``X-Navin-Auth: <secret>``.
    - ``websocket_requires_token``: If True, the handshake must include a valid token (static or issued and not expired).
    - Each connection has its own session: a unique ``chat_id`` maps to the agent session internally.
    - ``media`` field in outbound messages contains local filesystem paths; remote clients need a
      shared filesystem or an HTTP file server to access these files.
    """

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765
    unix_socket_path: str = ""
    path: str = "/"
    token: str = ""
    token_issue_path: str = ""
    token_issue_secret: str = ""
    token_ttl_s: int = Field(default=300, ge=30, le=86_400)
    websocket_requires_token: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    streaming: bool = True
    # Default 36 MB, upper 40 MB: supports up to 4 images at ~6 MB each after
    # client-side Worker normalization (see webui Composer). 4 × 6 MB × 1.37
    # (base64 overhead) + envelope framing stays under 36 MB; the 40 MB ceiling
    # leaves a small margin for sender slop without opening a DoS avenue.
    max_message_bytes: int = Field(default=37_748_736, ge=1024, le=41_943_040)
    ping_interval_s: float = Field(default=30.0, ge=5.0, le=300.0)
    # 20s was the websockets library default. The desktop WebView (Tauri /
    # WebView2 / WebKitGTK) often misses a pong while the UI thread paints
    # the first turn; Chromium in a normal browser answers instantly. That
    # is why the same gateway "works on the website" and "hangs in the IDE
    # until you switch models" (a switch reconnects the socket).
    ping_timeout_s: float = Field(default=120.0, ge=5.0, le=300.0)
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    @field_validator("unix_socket_path")
    @classmethod
    def unix_socket_path_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if "\x00" in value:
            raise ValueError("unix_socket_path must not contain NUL bytes")
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("unix_socket_path must be an absolute path")
        return str(path)

    @field_validator("path")
    @classmethod
    def path_must_start_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('path must start with "/"')
        return _normalize_config_path(value)

    @field_validator("token_issue_path")
    @classmethod
    def token_issue_path_format(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        if not value.startswith("/"):
            raise ValueError('token_issue_path must start with "/"')
        return _normalize_config_path(value)

    @model_validator(mode="after")
    def token_issue_path_differs_from_ws_path(self) -> Self:
        if not self.token_issue_path:
            return self
        if _normalize_config_path(self.token_issue_path) == _normalize_config_path(self.path):
            raise ValueError("token_issue_path must differ from path (the WebSocket upgrade path)")
        return self

    @model_validator(mode="after")
    def wildcard_host_requires_auth(self) -> Self:
        if self.host not in ("0.0.0.0", "::"):
            return self
        if self.token.strip() or self.token_issue_secret.strip():
            return self
        raise ValueError(
            "host is 0.0.0.0 (all interfaces) but neither token nor "
            "token_issue_secret is set - set one to prevent unauthenticated access"
        )


def publish_runtime_model_update(
    bus: MessageBus,
    model: str,
    model_preset: str | None,
) -> None:
    """Enqueue a runtime model snapshot for websocket subscribers (fan-out in-channel)."""
    bus.outbound.put_nowait(
        outbound_message_for_event(
            channel="websocket",
            chat_id="*",
            event=RuntimeModelUpdatedEvent(model=model, model_preset=model_preset),
        )
    )


def _parse_inbound_payload(raw: str) -> str | None:
    """Parse a client frame into text; return None for empty or unrecognized content."""
    text = raw.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(data, dict):
            for key in ("content", "text", "message"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return None
        return None
    return text


# Accept UUIDs and short scoped keys like "unified:default". Keeps the capability
# namespace small enough to rule out path traversal / quote injection tricks.
_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")


def _is_valid_chat_id(value: Any) -> bool:
    return isinstance(value, str) and _CHAT_ID_RE.match(value) is not None


def _mobile_native() -> bool:
    from navin.utils.native import native

    core = native()
    return core is not None and hasattr(core, "MobilePreviewSession")


def _terminal_cwd(requested: Any, scope: WorkspaceScope) -> str:
    """Starting directory for a shell: the requested folder, or the project.

    "Open in Integrated Terminal" asks for an explicit folder; the toolbar "+"
    button does not. Anything that is not a real directory falls back to the
    project rather than being an error the user has to read.

    A restricted scope keeps the shell inside the project. A full-access scope
    honours any existing directory: the editor may legitimately show a project
    that is not the scope the gateway resolved (no active chat yet, scope not
    persisted), and silently dropping the user into the default workspace is
    exactly the bug this guard would otherwise cause.
    """

    root = scope.project_path
    if not isinstance(requested, str) or not requested.strip():
        return str(root)
    try:
        candidate = Path(requested).resolve(strict=True)
    except OSError:
        return str(root)
    if scope.restrict_to_workspace:
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return str(root)
    return str(candidate if candidate.is_dir() else candidate.parent)


def _parse_envelope(raw: str) -> dict[str, Any] | None:
    """Return a typed envelope dict if the frame is a new-style JSON envelope, else None.

    A frame qualifies when it parses as a JSON object with a string ``type`` field.
    Legacy frames (plain text, or ``{"content": ...}`` without ``type``) return None;
    callers should fall back to :func:`_parse_inbound_payload` for those.
    """
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    t = data.get("type")
    if not isinstance(t, str):
        return None
    return data


def _is_websocket_upgrade(request: WsRequest) -> bool:
    """Detect an actual WS upgrade; plain HTTP GETs to the same path should fall through."""
    upgrade = request.headers.get("Upgrade") or request.headers.get("upgrade")
    connection = request.headers.get("Connection") or request.headers.get("connection")
    if not upgrade or "websocket" not in upgrade.lower():
        return False
    if not connection or "upgrade" not in connection.lower():
        return False
    return True


class WebSocketChannel(BaseChannel):
    """Run a local WebSocket server; forward text/JSON messages to the message bus."""

    name = "websocket"
    display_name = "WebSocket"

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        gateway: GatewayServices,
    ):
        if isinstance(config, dict):
            config = WebSocketConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: WebSocketConfig = config
        # chat_id -> connections subscribed to it (fan-out target).
        self._subs: dict[str, set[Any]] = {}
        # connection -> chat_ids it is subscribed to (O(1) cleanup on disconnect).
        self._conn_chats: dict[Any, set[str]] = {}
        # connection -> default chat_id for legacy frames that omit routing.
        self._conn_default: dict[Any, str] = {}
        self._stop_event: asyncio.Event | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._blocking_pool: ThreadPoolExecutor | None = None
        self._pool_watch_task: asyncio.Task[None] | None = None
        self._loop_lag: LoopLagMonitor | None = None

        self.gateway = gateway
        self._http_router = gateway.http
        self._tokens = gateway.tokens
        self._media = gateway.media
        self._ingress = gateway.ingress
        self._transcripts = gateway.transcripts
        self._workspaces = gateway.workspaces
        self._terminals = TerminalManager()
        # Workspace file watcher: attached chats get coalesced fs_changed
        # events so the explorer and Git panel follow external edits live.
        from navin.webui.fs_watch import WorkspaceWatcherService

        self._fs_watcher = WorkspaceWatcherService(self._notify_fs_changed)
        # Inline Tab completion streams: connection -> request_id -> Task.
        self._assist_tasks: dict[Any, dict[str, asyncio.Task[Any]]] = {}
        # Live voice frames (STT, prompt rewrite, TTS synthesis) run off the
        # receive loop: a 10 s synthesis must not hold back the barge-in or the
        # next utterance queued behind it.
        self._voice_tasks: dict[Any, set[asyncio.Task[Any]]] = {}
        self._mobile_previews = MobilePreviewManager()
        self._presence = PresenceTracker()
        # connection -> collab identity {member_id, display_name, role, org_id}
        self._conn_identity: dict[Any, dict[str, str]] = {}
        # Stable id per connection for presence bookkeeping.
        self._conn_ids: dict[Any, str] = {}

        self._stream_text_buffers: dict[tuple[str, str], list[str]] = {}

        # navin.live sign-in / plan changes complete in gateway threads
        # (background handoff, validate refresh): push the news to every
        # WebUI instead of leaving them to poll until a manual reload.
        account = getattr(self._http_router, "account", None)
        if account is not None:
            account.on_changed = self._notify_account_updated_threadsafe

    def _notify_account_updated_threadsafe(self) -> None:
        """Schedule an ``account_updated`` broadcast; safe from any thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run_coroutine_threadsafe(self._broadcast_account_updated(), loop)
            return
        loop.create_task(self._broadcast_account_updated())

    async def _broadcast_account_updated(self) -> None:
        raw = json.dumps({"event": "account_updated"}, ensure_ascii=False)
        for connection in list(self._conn_chats.keys()):
            with suppress(Exception):
                await self._safe_send_to(connection, raw, label=" account_updated ")

    # -- Subscription bookkeeping -------------------------------------------

    def _workspace_controls_available(self, connection: Any) -> bool:
        return self._http_router.workspace_controls_available(connection)

    def _connection_id(self, connection: Any) -> str:
        cid = self._conn_ids.get(connection)
        if cid:
            return cid
        cid = f"c-{uuid.uuid4().hex[:12]}"
        self._conn_ids[connection] = cid
        return cid

    def _identity_for(self, connection: Any) -> dict[str, str]:
        cached = self._conn_identity.get(connection)
        if cached:
            return cached
        # Solo host fallback from the local license profile.
        try:
            from navin.config.loader import load_config

            lic = load_config().license
            role = role_from_license(lic) or "host"
            email = (lic.account_email or "").strip()
            name = (lic.account_name or (email.split("@")[0] if email else "") or "Host").strip()
            identity = {
                "member_id": email or self._connection_id(connection),
                "display_name": name or "Host",
                "role": role,
                "org_id": (lic.org_id or "").strip(),
            }
        except Exception:
            identity = {
                "member_id": self._connection_id(connection),
                "display_name": "Host",
                "role": "host",
                "org_id": "",
            }
        self._conn_identity[connection] = identity
        return identity

    def _role_for(self, connection: Any) -> str | None:
        role = (self._identity_for(connection).get("role") or "").strip()
        if role and role != "host":
            return role
        try:
            from navin.config.loader import load_config

            return role_from_license(load_config())
        except Exception:
            return None

    async def _broadcast_presence(self, chat_id: str, event: str, **fields: Any) -> None:
        payload: dict[str, Any] = {"event": event, "chat_id": chat_id}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        for peer in list(self._subs.get(chat_id, ())):
            await self._safe_send_to(peer, raw, label=f" {event} ")

    async def _presence_join(self, connection: Any, chat_id: str) -> None:
        identity = self._identity_for(connection)
        member = self._presence.join(
            chat_id,
            connection_id=self._connection_id(connection),
            member_id=identity["member_id"],
            display_name=identity["display_name"],
            role=identity.get("role") or "member",
        )
        await self._send_event(
            connection,
            "presence_sync",
            **self._presence.sync_payload(chat_id),
        )
        payload = json.dumps(
            {
                "event": "presence_join",
                "chat_id": chat_id,
                "member": member.payload(),
            },
            ensure_ascii=False,
        )
        for peer in list(self._subs.get(chat_id, ())):
            if peer is connection:
                continue
            await self._safe_send_to(peer, payload, label=" presence_join ")

    def _attach(self, connection: Any, chat_id: str) -> None:
        """Idempotently subscribe *connection* to *chat_id*."""
        self._subs.setdefault(chat_id, set()).add(connection)
        self._conn_chats.setdefault(connection, set()).add(chat_id)
        with suppress(Exception):
            self._fs_watcher.watch(
                chat_id,
                self._workspaces.scope_for_session_key(
                    f"websocket:{chat_id}"
                ).project_path,
            )

    def _invalidate_file_tree_cache(self, project_path: str | Path | None = None) -> None:
        """Drop cached explorer listings so the next expand sees agent writes."""
        cache = getattr(self._http_router, "route_cache", None)
        invalidate = getattr(cache, "invalidate_prefix", None)
        if not callable(invalidate):
            return
        raw = str(project_path).strip() if project_path is not None else ""
        prefix = f"file-tree:{raw}" if raw else "file-tree:"
        invalidate(prefix)

    async def _notify_fs_changed(self, root: str, chat_ids: list[str]) -> None:
        """Tell every subscriber of *chat_ids* that files changed under *root*."""
        self._invalidate_file_tree_cache(root)
        for chat_id in chat_ids:
            conns = list(self._subs.get(chat_id, ()))
            if not conns:
                continue
            raw = json.dumps(
                {"event": "fs_changed", "chat_id": chat_id, "root": root},
                ensure_ascii=False,
            )
            for connection in conns:
                await self._safe_send_to(connection, raw, label=" fs_changed ")

    def _cleanup_connection(self, connection: Any) -> None:
        """Remove *connection* from every subscription set; safe to call multiple times."""
        self._terminals.cleanup_connection(connection)
        for task in self._assist_tasks.pop(connection, {}).values():
            task.cancel()
        for task in self._voice_tasks.pop(connection, set()):
            task.cancel()
        close_voice_sessions(connection)
        self._mobile_previews.cleanup_connection(connection)
        conn_id = self._conn_ids.get(connection)
        if conn_id:
            left = self._presence.leave_connection(conn_id)
            if left is not None:
                chat_id, member = left
                payload = json.dumps(
                    {
                        "event": "presence_leave",
                        "chat_id": chat_id,
                        "member": member.payload(),
                    },
                    ensure_ascii=False,
                )
                for peer in list(self._subs.get(chat_id, ())):
                    if peer is connection:
                        continue
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        break
                    loop.create_task(
                        self._safe_send_to(peer, payload, label=" presence_leave ")
                    )
        chat_ids = self._conn_chats.pop(connection, set())
        for cid in chat_ids:
            subs = self._subs.get(cid)
            if subs is None:
                continue
            subs.discard(connection)
            if not subs:
                self._subs.pop(cid, None)
                self._fs_watcher.unwatch_chat(cid)
        self._conn_default.pop(connection, None)
        self._conn_identity.pop(connection, None)
        self._conn_ids.pop(connection, None)

    async def _maybe_push_active_goal_state(self, chat_id: str) -> None:
        """Replay an active sustained goal from session metadata after *chat_id* is subscribed.

        Goal metadata lives on the session JSONL and survives gateway restarts, but
        connected clients normally see it via ``goal_state`` / ``turn_end`` frames.
        Pushing here makes refresh + reconnect restore the strip without a new model turn.
        """
        if self.gateway.session_manager is None:
            return
        row = self.gateway.session_manager.read_session_file(f"websocket:{chat_id}")
        meta = row.get("metadata", {}) if isinstance(row, dict) else {}
        if not isinstance(meta, dict):
            meta = {}
        blob = goal_state_ws_blob(meta)
        if not blob.get("active"):
            return
        await self.send_goal_state(chat_id, blob)

    async def _maybe_push_turn_run_wall_clock(self, chat_id: str) -> None:
        """Replay ``goal_status: running`` when a turn is still active.

        Leaving the chat must not look like a crash. Same-process reconnect
        restores the live strip. A finished reply waiting in history stays
        quiet. Only a dead process mid-turn raises the interrupted banner.
        """
        t0 = websocket_turn_wall_started_at(chat_id)
        if t0 is not None:
            await self.send_goal_status(chat_id, "running", started_at=t0)
            return
        sessions = self.gateway.session_manager
        if sessions is None:
            return
        from navin.session.webui_turns import reconnect_turn_action

        action, started_at = reconnect_turn_action(
            sessions, f"websocket:{chat_id}", chat_id
        )
        if action == "running" and started_at is not None:
            await self.send_goal_status(chat_id, "running", started_at=started_at)
            return
        if action != "interrupted":
            return
        await self.send_goal_status(chat_id, "idle")
        await self.send_notification(
            chat_id,
            NotificationEvent(
                title="Turn interrupted",
                level="warning",
                detail=(
                    "The gateway restarted while a turn was running. The last "
                    "reply may be incomplete - send a new message to continue."
                ),
                key=f"turn-interrupted:{chat_id}",
                source="agent",
            ),
        )

    def _chat_exists(self, chat_id: str) -> bool:
        """True when this chat id already has a stored session."""
        sessions = self.gateway.session_manager
        if sessions is None:
            return False
        try:
            return sessions.peek(f"websocket:{chat_id}") is not None
        except Exception:
            return False

    async def _hydrate_after_subscribe(self, chat_id: str) -> None:
        """Replay goal/run strip state after subscribe (same-process refresh)."""
        if not self._chat_exists(chat_id):
            # A connection default id has no history to replay. Hydrating it
            # would cost five bus round-trips per reconnect and materialise an
            # empty chat, which is how a reconnect storm used to fill the
            # sidebar with hundreds of "New chat" rows.
            return
        await self._maybe_push_active_goal_state(chat_id)
        await self._maybe_push_turn_run_wall_clock(chat_id)
        await self._maybe_push_open_approvals(chat_id)
        await self._maybe_push_open_choices(chat_id)
        await self._maybe_push_running_subagents(chat_id)
        await self._maybe_push_artifacts(chat_id)

    async def _maybe_push_running_subagents(self, chat_id: str) -> None:
        """Re-send the background subagents this chat still has in flight.

        Their cards live in browser memory, so a refresh or a detour through
        another chat used to leave a subagent working with nothing on screen
        saying so - the request looked ignored until its result turned up much
        later, or not at all.
        """
        from navin.agent.subagent import request_running_subagents

        try:
            # Shorter than the approvals replay on purpose: this also runs on
            # the send path, where nothing is suspended on the answer. A missing
            # card is worth far less than a message that waits for it.
            running = await request_running_subagents(
                self.bus, f"websocket:{chat_id}", timeout=0.75
            )
        except Exception as exc:  # pragma: no cover - transport-level
            self.logger.debug("could not reload running subagents: {}", exc)
            return
        for payload in running:
            task_id = str(payload.get("task_id") or "")
            if not task_id:
                continue
            started = payload.get("started_ms_ago")
            await self.send_subagent_progress(
                chat_id,
                SubagentProgressEvent(
                    task_id=task_id,
                    label=str(payload.get("label") or task_id),
                    phase=str(payload.get("phase") or "initializing"),
                    status_line=str(payload.get("status_line") or ""),
                    model=payload.get("model") or None,
                    iteration=int(payload.get("iteration") or 0),
                    done=False,
                    task_description=payload.get("task_description") or None,
                    started_ms_ago=int(started) if isinstance(started, int) else None,
                ),
            )

    async def _maybe_push_artifacts(self, chat_id: str) -> None:
        """Re-send persisted artifacts so a reopened chat restores the canvas."""
        try:
            from navin.artifacts.store import ArtifactStore

            store = ArtifactStore(chat_id)
            for meta in store.list():
                artifact = store.get(str(meta["id"]), include_content=True)
                if artifact is None:
                    continue
                await self.send_artifact_upsert(
                    chat_id, ArtifactUpsertEvent(artifact=artifact), restored=True
                )
        except Exception as exc:  # pragma: no cover - hydrate is best-effort
            self.logger.debug("could not reload artifacts: {}", exc)

    async def _maybe_push_open_approvals(self, chat_id: str) -> None:
        """Re-send the questions this chat is still suspended on.

        A refresh loses the cards but not the tool calls behind them, so without
        this the agent waits out its timeout with nothing on screen to answer.
        """
        from navin.agent.approval import request_open_approvals

        try:
            open_requests = await request_open_approvals(self.bus, f"websocket:{chat_id}")
        except Exception as exc:  # pragma: no cover - transport-level
            self.logger.debug("could not reload open approvals: {}", exc)
            return
        for payload in open_requests:
            await self.send_approval_request(
                chat_id,
                ApprovalRequestedEvent(
                    request_id=str(payload.get("request_id") or ""),
                    tool=str(payload.get("tool") or ""),
                    action=str(payload.get("action") or ""),
                    reason=str(payload.get("reason") or ""),
                    detail=str(payload.get("detail") or ""),
                    consequence=str(payload.get("consequence") or ""),
                    scope=str(payload.get("scope") or ""),
                    expires_at_ms=payload.get("expires_at_ms"),
                    remember_offered=bool(payload.get("remember_offered")),
                ),
            )

    async def _maybe_push_open_choices(self, chat_id: str) -> None:
        """Re-send the choice cards this chat is still suspended on."""
        from navin.agent.choice import request_open_choices

        try:
            open_requests = await request_open_choices(self.bus, f"websocket:{chat_id}")
        except Exception as exc:  # pragma: no cover - transport-level
            self.logger.debug("could not reload open choices: {}", exc)
            return
        for payload in open_requests:
            options = payload.get("options")
            await self.send_choice_request(
                chat_id,
                ChoiceRequestedEvent(
                    request_id=str(payload.get("request_id") or ""),
                    question=str(payload.get("question") or ""),
                    options=options if isinstance(options, list) else [],
                    allow_skip=bool(payload.get("allow_skip", True)),
                    recommended_id=str(payload.get("recommended_id") or ""),
                    expires_at_ms=payload.get("expires_at_ms"),
                ),
            )

    async def _send_event(self, connection: Any, event: str, **fields: Any) -> None:
        """Send a control event (attached, error, ...) to a single connection."""
        payload: dict[str, Any] = {"event": event}
        payload.update(fields)
        raw = json.dumps(payload, ensure_ascii=False)
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
        except Exception as e:
            self.logger.warning("failed to send {} event: {}", event, e)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return WebSocketConfig().model_dump(by_alias=True)

    def _expected_path(self) -> str:
        return _normalize_config_path(self.config.path)

    def _build_ssl_context(self) -> ssl.SSLContext | None:
        cert = self.config.ssl_certfile.strip()
        key = self.config.ssl_keyfile.strip()
        if not cert and not key:
            return None
        if not cert or not key:
            raise ValueError(
                "ssl_certfile and ssl_keyfile must both be set for WSS, or both left empty"
            )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        return ctx

    # -- HTTP dispatch ------------------------------------------------------

    async def _dispatch_http(self, connection: Any, request: WsRequest) -> Any:
        """Route an inbound HTTP request to the HTTP handler or WS upgrade."""
        got, query = _parse_request_path(request.path)

        # WebSocket upgrade - channel handles this itself
        expected_ws = self._expected_path()
        if got == expected_ws and _is_websocket_upgrade(request):
            client_id = _query_first(query, "client_id") or ""
            if len(client_id) > 128:
                client_id = client_id[:128]
            if not self.is_allowed(client_id):
                return connection.respond(403, "Forbidden")
            return self._authorize_websocket_handshake(connection, query)

        # Everything else goes to the HTTP handler
        return await self._http_router.dispatch(connection, request)

    def _authorize_websocket_handshake(self, connection: Any, query: dict[str, list[str]]) -> Any:
        supplied = _query_first(query, "token")
        collab_raw = _query_first(query, "collab_token")
        static_token = self.config.token.strip()

        # Org collab tokens may authorize the upgrade on their own (hybrid join).
        if collab_raw and verify_collab_token(collab_raw):
            return None
        if supplied and verify_collab_token(supplied):
            return None

        if static_token:
            if supplied and hmac.compare_digest(supplied, static_token):
                return None
            if supplied and self._tokens.take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if self.config.websocket_requires_token:
            if supplied and self._tokens.take_issued_token_if_valid(supplied):
                return None
            return connection.respond(401, "Unauthorized")

        if supplied:
            self._tokens.take_issued_token_if_valid(supplied)
        return None

    # -- Server lifecycle and connection ingress ---------------------------

    async def start(self) -> None:
        from navin.utils.logging_bridge import redirect_lib_logging

        redirect_lib_logging("websockets", level="WARNING")
        ws_logger = websockets_server_logger()

        self._running = True
        self._stop_event = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        self._install_blocking_pool()
        self._fs_watcher.start()

        ssl_context = self._build_ssl_context()
        scheme = "wss" if ssl_context else "ws"

        async def process_request(
            connection: ServerConnection,
            request: WsRequest,
        ) -> Any:
            return await self._dispatch_http(connection, request)

        async def handler(connection: ServerConnection) -> None:
            await self._connection_loop(connection)

        self.logger.info(
            "WebSocket server listening on {}",
            (
                f"unix:{self.config.unix_socket_path}{self.config.path}"
                if self.config.unix_socket_path
                else f"{scheme}://{self.config.host}:{self.config.port}{self.config.path}"
            ),
        )
        if self.config.token_issue_path:
            self.logger.info(
                "WebSocket token issue route: {}",
                (
                    f"unix:{self.config.unix_socket_path}{_normalize_config_path(self.config.token_issue_path)}"
                    if self.config.unix_socket_path
                    else (
                        f"{scheme}://{self.config.host}:{self.config.port}"
                        f"{_normalize_config_path(self.config.token_issue_path)}"
                    )
                ),
            )

        async def runner() -> None:
            socket_path = self.config.unix_socket_path
            if socket_path:
                path_obj = Path(socket_path)
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                with suppress(FileNotFoundError):
                    path_obj.unlink()
                server = await unix_serve(
                    handler,
                    socket_path,
                    process_request=process_request,
                    open_timeout=_WEBUI_HTTP_OPEN_TIMEOUT_S,
                    max_size=self.config.max_message_bytes,
                    ping_interval=self.config.ping_interval_s,
                    ping_timeout=effective_ping_timeout_s(self.config.ping_timeout_s),
                    logger=ws_logger,
                )
                with suppress(OSError):
                    path_obj.chmod(0o600)
            else:
                server = await serve(
                    handler,
                    self.config.host,
                    self.config.port,
                    process_request=process_request,
                    open_timeout=_WEBUI_HTTP_OPEN_TIMEOUT_S,
                    max_size=self.config.max_message_bytes,
                    ping_interval=self.config.ping_interval_s,
                    ping_timeout=effective_ping_timeout_s(self.config.ping_timeout_s),
                    ssl=ssl_context,
                    logger=ws_logger,
                )
            try:
                assert self._stop_event is not None
                await self._stop_event.wait()
            finally:
                server.close()
                await server.wait_closed()
                if socket_path:
                    with suppress(FileNotFoundError):
                        Path(socket_path).unlink()

        self._server_task = asyncio.create_task(runner())
        await self._server_task

    async def _connection_loop(self, connection: Any) -> None:
        request = connection.request
        path_part = request.path if request else "/"
        _, query = _parse_request_path(path_part)
        client_id_raw = _query_first(query, "client_id")
        client_id = client_id_raw.strip() if client_id_raw else ""
        if not client_id:
            client_id = f"anon-{uuid.uuid4().hex[:12]}"
        elif len(client_id) > 128:
            self.logger.warning("client_id too long ({} chars), truncating", len(client_id))
            client_id = client_id[:128]

        collab_raw = (_query_first(query, "collab_token") or _query_first(query, "token") or "").strip()
        claims = verify_collab_token(collab_raw) if collab_raw else None
        if claims:
            self._conn_identity[connection] = collab_identity_from_claims(claims)

        default_chat_id = str(uuid.uuid4())
        if claims and claims.get("chat_id"):
            default_chat_id = str(claims["chat_id"])

        try:
            identity = self._identity_for(connection)
            await connection.send(
                json.dumps(
                    {
                        "event": "ready",
                        "chat_id": default_chat_id,
                        "client_id": client_id,
                        "org_role": identity.get("role"),
                        "display_name": identity.get("display_name"),
                    },
                    ensure_ascii=False,
                )
            )
            # Register only after ready is successfully sent to avoid out-of-order sends
            self._conn_default[connection] = default_chat_id
            self._attach(connection, default_chat_id)
            await self._presence_join(connection, default_chat_id)
            await self._hydrate_after_subscribe(default_chat_id)

            async for raw in connection:
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        self.logger.warning("ignoring non-utf8 binary frame")
                        continue

                envelope = _parse_envelope(raw)
                if envelope is not None:
                    await self._dispatch_envelope(connection, client_id, envelope)
                    continue

                content = _parse_inbound_payload(raw)
                if content is None:
                    continue
                # WebSocket already authenticates at handshake time (token),
                # so pairing is not applicable. Treat as non-DM to avoid
                # sending pairing codes to an already-authenticated client.
                await self._handle_message(
                    sender_id=client_id,
                    chat_id=default_chat_id,
                    content=content,
                    metadata={"remote": getattr(connection, "remote_address", None)},
                    is_dm=False,
                )
        except Exception as e:
            self.logger.debug("connection ended: {}", e)
        finally:
            self._cleanup_connection(connection)

    # -- Inbound WebSocket envelopes ---------------------------------------

    async def _dispatch_envelope(
        self,
        connection: Any,
        client_id: str,
        envelope: dict[str, Any],
    ) -> None:
        """Route one typed inbound envelope (``new_chat`` / ``attach`` / ``message``)."""
        t = envelope.get("type")
        if t == "new_chat":
            if not action_allowed(self._role_for(connection), "message"):
                await self._send_event(
                    connection,
                    "error",
                    detail="forbidden_role",
                    reason="viewer_read_only",
                )
                return
            new_id = str(uuid.uuid4())
            scope = await self._workspace_scope_or_error(
                connection,
                lambda: self._workspaces.scope_for_new_chat(
                    envelope,
                    controls_available=self._workspace_controls_available(connection),
                ),
            )
            if scope is None:
                return
            self._workspaces.persist_scope(new_id, scope)
            schedule_index_warm(scope.project_path)
            self._attach(connection, new_id)
            await self._presence_join(connection, new_id)
            await self._send_event(connection, "attached", chat_id=new_id)
            await self._send_event(
                connection,
                "session_updated",
                chat_id=new_id,
                scope="metadata",
                workspace_scope=scope.payload(),
            )
            await self._hydrate_after_subscribe(new_id)
            return
        if t == "fork_chat":
            if not action_allowed(self._role_for(connection), "message"):
                await self._send_event(
                    connection,
                    "error",
                    detail="forbidden_role",
                    reason="viewer_read_only",
                )
                return
            await handle_webui_fork_chat(self, connection, envelope)
            return
        if t == "attach":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            self._attach(connection, cid)
            await self._presence_join(connection, cid)
            with suppress(Exception):
                # Reopening a chat is the "open workspace" moment: start the
                # index build now so the first code tool of the next turn is warm.
                schedule_index_warm(
                    self._workspaces.scope_for_session_key(f"websocket:{cid}").project_path
                )
            await self._send_event(connection, "attached", chat_id=cid)
            await self._hydrate_after_subscribe(cid)
            return
        if t == "member_cursor":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                return
            identity = self._identity_for(connection)
            cursor = envelope.get("cursor")
            await self._broadcast_presence(
                cid,
                "member_cursor",
                member_id=identity.get("member_id"),
                display_name=identity.get("display_name"),
                cursor=cursor if isinstance(cursor, dict) else {},
            )
            return
        if t == "presence_sync":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            await self._send_event(
                connection,
                "presence_sync",
                **self._presence.sync_payload(cid),
            )
            return
        if t == "set_workspace_scope":
            cid = envelope.get("chat_id")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            scope = await self._workspace_scope_or_error(
                connection,
                lambda: self._workspaces.scope_for_set_request(
                    envelope,
                    chat_id=cid,
                    chat_running=websocket_turn_wall_started_at(cid) is not None,
                    controls_available=self._workspace_controls_available(connection),
                ),
                chat_id=cid,
            )
            if scope is None:
                return
            self._workspaces.persist_scope(cid, scope)
            schedule_index_warm(scope.project_path)
            await self._send_event(
                connection,
                "session_updated",
                chat_id=cid,
                scope="metadata",
                workspace_scope=scope.payload(),
            )
            return
        if t == "transcribe_audio":
            event, payload = await webui_transcription_event(envelope)
            await self._send_event(connection, event, **payload)
            return
        if t in {"assist_complete", "assist_edit", "assist_cancel"}:
            await self._dispatch_assist_envelope(connection, t, envelope)
            return
        if t in {"voice_session_start", "voice_audio_chunk", "voice_session_end", "voice_prompt"}:
            if t == "voice_session_start" and not action_allowed(
                self._role_for(connection), "message"
            ):
                await self._send_event(
                    connection,
                    "error",
                    detail="forbidden_role",
                    reason="viewer_read_only",
                )
                return
            if t in {"voice_audio_chunk", "voice_prompt"}:
                self._spawn_voice_task(connection, envelope)
                return
            for event, payload in await webui_voice_session_events(envelope, owner=connection):
                await self._send_event(connection, event, **payload)
            return
        if t == "approval_decision":
            if not action_allowed(self._role_for(connection), "approval_decision"):
                await self._send_event(
                    connection,
                    "error",
                    detail="forbidden_role",
                    reason="viewer_cannot_approve",
                )
                return
            await self._dispatch_approval_decision(connection, envelope)
            return
        if t == "choice_answer":
            if not action_allowed(self._role_for(connection), "choice_answer"):
                await self._send_event(
                    connection,
                    "error",
                    detail="forbidden_role",
                    reason="viewer_cannot_approve",
                )
                return
            await self._dispatch_choice_answer(connection, envelope)
            return
        if isinstance(t, str) and t.startswith("terminal_"):
            if t in {"terminal_open", "terminal_input"} and not action_allowed(
                self._role_for(connection), t
            ):
                await self._send_event(
                    connection,
                    "terminal_error",
                    detail="forbidden_role",
                )
                return
            await self._dispatch_terminal_envelope(connection, t, envelope)
            return
        if isinstance(t, str) and t.startswith("mobile_preview_"):
            # Map concrete WS types onto ACL action names (open vs input).
            if t in {"mobile_preview_open", "mobile_preview_start_avd"}:
                acl_action = "mobile_preview_open"
            elif t in {
                "mobile_preview_tap",
                "mobile_preview_swipe",
                "mobile_preview_key",
                "mobile_preview_text",
                "mobile_preview_ui_dump",
            }:
                acl_action = "mobile_preview_input"
            else:
                acl_action = None
            if acl_action is not None and not action_allowed(
                self._role_for(connection), acl_action
            ):
                await self._send_event(
                    connection,
                    "mobile_preview_error",
                    detail="forbidden_role",
                )
                return
            await self._dispatch_mobile_preview_envelope(connection, t, envelope)
            return
        if t in {"agent_browser_input", "agent_browser_close"}:
            if not action_allowed(self._role_for(connection), "agent_browser_input"):
                await self._send_event(
                    connection, "agent_browser_error", detail="forbidden_role",
                    **{key: envelope.get(key) for key in ("chat_id", "id", "request_id")},
                )
                return
            if t == "agent_browser_close":
                await self._dispatch_agent_browser_close(connection, envelope)
            else:
                await self._dispatch_agent_browser_input(connection, envelope)
            return
        if t == "message":
            if not action_allowed(self._role_for(connection), "message"):
                await self._send_event(
                    connection,
                    "error",
                    chat_id=envelope.get("chat_id") if isinstance(envelope.get("chat_id"), str) else None,
                    detail="forbidden_role",
                    reason="viewer_read_only",
                )
                return
            cid = envelope.get("chat_id")
            content = envelope.get("content")
            if not _is_valid_chat_id(cid):
                await self._send_event(connection, "error", detail="invalid chat_id")
                return
            if not isinstance(content, str):
                await self._send_event(connection, "error", detail="missing content")
                return
            message_rejection = self._ingress.validate_text(content)
            if message_rejection is not None:
                await self._send_event(
                    connection,
                    "error",
                    chat_id=cid,
                    detail="message_rejected",
                    reason=message_rejection,
                )
                return

            raw_media = envelope.get("media")
            media_paths: list[str] = []
            if raw_media is not None:
                if not isinstance(raw_media, list):
                    await self._send_event(
                        connection,
                        "error",
                        detail="attachment_rejected",
                        reason="malformed",
                    )
                    return
                media_paths, reason = self._media.store_inbound_attachments(raw_media)
                if reason is not None:
                    await self._send_event(
                        connection,
                        "error",
                        detail="attachment_rejected",
                        reason=reason,
                    )
                    return

            # Allow media-only turns (content may be empty when attachments are present).
            if not content.strip() and not media_paths:
                await self._send_event(connection, "error", detail="missing content")
                return
            # Auto-attach on first use so clients can one-shot without a separate attach.
            self._attach(connection, cid)
            await self._hydrate_after_subscribe(cid)

            # Resolve after hydration so a concurrent downgrade cannot be overwritten.
            scope = await self._workspace_scope_or_error(
                connection,
                lambda: self._workspaces.scope_for_message(
                    envelope,
                    chat_id=cid,
                    chat_running=websocket_turn_wall_started_at(cid) is not None,
                    controls_available=self._workspace_controls_available(connection),
                ),
                chat_id=cid,
            )
            if scope is None:
                return

            metadata: dict[str, Any] = {"remote": getattr(connection, "remote_address", None)}
            if envelope.get("webui") is True:
                metadata["webui"] = True
                metadata.update(self._transcripts.client_turn_metadata(envelope.get("turn_id")))
            cli_apps = normalize_cli_app_mentions(envelope.get("cli_apps"))
            if cli_apps:
                metadata["cli_apps"] = cli_apps
            mcp_presets = normalize_mcp_preset_mentions(envelope.get("mcp_presets"))
            if mcp_presets:
                metadata["mcp_presets"] = mcp_presets
            document_template = normalize_document_template_mention(
                envelope.get("document_template")
            )
            if document_template:
                metadata["document_template"] = document_template
            # Normalization may fetch the remote catalog on a cache miss
            # (blocking urllib, ~1.5s timeout): keep it off the event loop.
            media_templates = await asyncio.to_thread(
                normalize_media_template_mentions,
                envelope.get("media_templates")
                if envelope.get("media_templates") is not None
                else envelope.get("media_template"),
            )
            media_template = media_templates[0] if media_templates else None
            if media_templates:
                metadata[MEDIA_TEMPLATES_METADATA_KEY] = media_templates
                metadata[MEDIA_TEMPLATE_METADATA_KEY] = media_templates[0]
            file_mentions = normalize_file_mentions(envelope.get("file_mentions"))
            if file_mentions:
                metadata[FILE_MENTION_METADATA_KEY] = file_mentions
            from navin.agent.context_pack import OPEN_FILES_METADATA_KEY

            raw_open = envelope.get("open_files")
            if isinstance(raw_open, list):
                open_files = [
                    str(item).strip().replace("\\", "/")
                    for item in raw_open
                    if isinstance(item, str) and item.strip()
                ][:12]
                if open_files:
                    metadata[OPEN_FILES_METADATA_KEY] = open_files
            model_preset = envelope.get("model_preset")
            if isinstance(model_preset, str) and model_preset.strip():
                metadata[INBOUND_META_MODEL_PRESET] = model_preset.strip()
            from navin.command.modules import (
                PRODUCT_MODULE_METADATA_KEY,
                normalize_product_module,
            )

            product_module = normalize_product_module(envelope.get("product_module"))
            if product_module is not None:
                metadata[PRODUCT_MODULE_METADATA_KEY] = product_module
            # Live voice conversation: the WebUI reads the reply aloud, so the
            # agent gets the "colleague on a call" brief for this turn.
            if envelope.get("voice_mode") is True:
                from navin.agent.voice_mode import VOICE_MODE_METADATA_KEY

                metadata[VOICE_MODE_METADATA_KEY] = True
            metadata[WORKSPACE_SCOPE_METADATA_KEY] = scope.metadata()
            self._workspaces.persist_scope(cid, scope)
            if metadata.get("webui") is True and self.is_allowed(client_id):
                self._transcripts.append_user_message(
                    cid,
                    content,
                    metadata=metadata,
                    media_paths=media_paths or None,
                    cli_apps=cli_apps or None,
                    mcp_presets=mcp_presets or None,
                    document_template=document_template,
                    media_template=media_template,
                    media_templates=media_templates or None,
                )
            await self._handle_message(
                sender_id=client_id,
                chat_id=cid,
                content=content,
                media=media_paths or None,
                metadata=metadata,
                is_dm=False,
            )
            return
        await self._send_event(connection, "error", detail=f"unknown type: {t!r}")

    # -- Approvals -----------------------------------------------------------

    async def _dispatch_approval_decision(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> None:
        """Carry an allow or refuse back to the tool call that is waiting.

        This cannot go through the normal message path: a user message sent
        during a turn is parked for mid-turn injection and only drained between
        runner iterations, which never happens while a tool is suspended. The
        runtime-control route is handled inline by the loop, so it arrives.
        """
        request_id = envelope.get("request_id")
        allowed = envelope.get("allowed")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 80:
            await self._send_event(connection, "error", detail="invalid request_id")
            return
        if not isinstance(allowed, bool):
            await self._send_event(connection, "error", detail="missing allowed")
            return
        await self.bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="webui-approval",
                chat_id="runtime",
                content=RUNTIME_CONTROL_APPROVAL_DECISION,
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_APPROVAL_DECISION,
                    "request_id": request_id,
                    "allowed": allowed,
                    "remember": bool(envelope.get("remember")),
                    # The broker checks that the answering connection actually
                    # saw the card: a request belongs to one chat, and only
                    # that chat's subscribers were shown it.
                    "answerer_chats": sorted(self._conn_chats.get(connection, set())),
                },
            )
        )

    async def _dispatch_choice_answer(
        self,
        connection: Any,
        envelope: dict[str, Any],
    ) -> None:
        """Carry a picked option back to the ask_user call that is waiting."""
        request_id = envelope.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 80:
            await self._send_event(connection, "error", detail="invalid request_id")
            return
        option_id = envelope.get("option_id")
        skipped = bool(envelope.get("skipped"))
        custom_raw = envelope.get("custom_text")
        custom_text = (
            custom_raw.strip()[:CUSTOM_TEXT_MAX]
            if isinstance(custom_raw, str)
            else ""
        )
        if (
            not skipped
            and not custom_text
            and (not isinstance(option_id, str) or not option_id)
        ):
            await self._send_event(connection, "error", detail="missing option_id")
            return
        await self.bus.publish_inbound(
            InboundMessage(
                channel="system",
                sender_id="webui-choice",
                chat_id="runtime",
                content=RUNTIME_CONTROL_CHOICE_ANSWER,
                metadata={
                    INBOUND_META_RUNTIME_CONTROL: RUNTIME_CONTROL_CHOICE_ANSWER,
                    "request_id": request_id,
                    "option_id": option_id if isinstance(option_id, str) else "",
                    "skipped": skipped,
                    "custom_text": custom_text,
                },
            )
        )

    # -- Inline Tab completion stream (editor ghost text) --------------------

    async def _dispatch_assist_envelope(
        self,
        connection: Any,
        t: str,
        envelope: dict[str, Any],
    ) -> None:
        request_id = envelope.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 80:
            await self._send_event(
                connection, "assist_error", detail="invalid request_id"
            )
            return

        bucket = self._assist_tasks.setdefault(connection, {})

        if t == "assist_cancel":
            task = bucket.pop(request_id, None)
            if task is not None:
                task.cancel()
            await self._send_event(
                connection, "assist_done", request_id=request_id, completion="", cancelled=True
            )
            return

        # Cancel any previous stream for the same request id.
        previous = bucket.pop(request_id, None)
        if previous is not None:
            previous.cancel()

        path = str(envelope.get("path") or "")
        prefix = str(envelope.get("prefix") or "")
        suffix = str(envelope.get("suffix") or "")
        language = str(envelope.get("language") or "")
        selection = str(envelope.get("selection") or "")
        instruction = str(envelope.get("instruction") or "")
        recent_edits_raw = envelope.get("recent_edits")
        is_edit = t == "assist_edit"
        chat_id = envelope.get("chat_id")
        if not isinstance(chat_id, str) or not chat_id:
            chat_id = self._conn_default.get(connection) or ""
        project_root = None
        if chat_id:
            try:
                project_root = self._workspaces.scope_for_session_key(
                    f"websocket:{chat_id}"
                ).project_path
            except Exception:
                project_root = None

        async def _run() -> None:
            from navin.webui.assist_api import AssistError, stream_completion, stream_edit

            async def on_partial(text: str) -> None:
                await self._send_event(
                    connection,
                    "assist_delta",
                    request_id=request_id,
                    text=text,
                )

            try:
                if is_edit:
                    payload = await stream_edit(
                        path=path,
                        selection=selection,
                        instruction=instruction,
                        prefix=prefix,
                        suffix=suffix,
                        language=language,
                        on_partial=on_partial,
                    )
                    await self._send_event(
                        connection,
                        "assist_done",
                        request_id=request_id,
                        replacement=payload.get("replacement") or "",
                        model=payload.get("model") or "",
                        route=payload.get("route") or "",
                        latency_ms=payload.get("latency_ms"),
                        ttft_ms=payload.get("ttft_ms"),
                    )
                else:
                    from navin.webui.assist_api import sanitize_recent_edits

                    payload = await stream_completion(
                        path=path,
                        prefix=prefix,
                        suffix=suffix,
                        language=language,
                        project_root=project_root,
                        recent_edits=sanitize_recent_edits(recent_edits_raw),
                        on_partial=on_partial,
                    )
                    await self._send_event(
                        connection,
                        "assist_done",
                        request_id=request_id,
                        completion=payload.get("completion") or "",
                        model=payload.get("model") or "",
                        reason=payload.get("reason") or "",
                        route=payload.get("route") or "",
                        latency_ms=payload.get("latency_ms"),
                        ttft_ms=payload.get("ttft_ms"),
                    )
            except asyncio.CancelledError:
                await self._send_event(
                    connection,
                    "assist_done",
                    request_id=request_id,
                    completion="",
                    replacement="",
                    cancelled=True,
                )
                raise
            except AssistError as exc:
                await self._send_event(
                    connection,
                    "assist_error",
                    request_id=request_id,
                    detail=exc.message,
                    status=exc.status,
                )
            except Exception as exc:
                await self._send_event(
                    connection,
                    "assist_error",
                    request_id=request_id,
                    detail=str(exc) or "assist failed",
                )
            finally:
                current = bucket.get(request_id)
                if current is asyncio.current_task():
                    bucket.pop(request_id, None)

        bucket[request_id] = asyncio.create_task(_run())

    # -- Live voice (WebUI) --------------------------------------------------

    def _spawn_voice_task(self, connection: Any, envelope: dict[str, Any]) -> None:
        """Run one STT / rewrite / TTS frame concurrently with the receive loop.

        The client orders TTS chunks by request id and sends one utterance at a
        time, so results may arrive in any order; what matters is that a slow
        synthesis never delays the barge-in or the next utterance behind it.
        """
        bucket = self._voice_tasks.setdefault(connection, set())

        async def _run() -> None:
            try:
                for event, payload in await webui_voice_session_events(envelope, owner=connection):
                    await self._send_event(connection, event, **payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("voice frame failed: {}", exc)
                with suppress(Exception):
                    await self._send_event(
                        connection,
                        "voice_session_error",
                        session_id=str(envelope.get("session_id") or ""),
                        request_id=envelope.get("request_id"),
                        detail="voice_failed",
                    )
            finally:
                bucket.discard(asyncio.current_task())  # type: ignore[arg-type]

        bucket.add(asyncio.create_task(_run()))

    # -- Interactive terminals (WebUI Dev mode) ------------------------------

    async def _dispatch_terminal_envelope(
        self,
        connection: Any,
        t: str,
        envelope: dict[str, Any],
    ) -> None:
        terminal_id = envelope.get("terminal_id")
        if t == "terminal_shells":
            # PATH probing over /mnt/c can take seconds: never on the loop.
            shells = await asyncio.to_thread(available_shells)
            await self._send_event(connection, "terminal_shells", shells=shells)
            return
        if not isinstance(terminal_id, str) or not terminal_id or len(terminal_id) > 64:
            await self._send_event(connection, "terminal_error", detail="invalid terminal_id")
            return

        if t == "terminal_open":
            if not self._workspace_controls_available(connection):
                await self._send_event(
                    connection,
                    "terminal_error",
                    terminal_id=terminal_id,
                    detail="terminals are localhost-only",
                )
                return
            cid = envelope.get("chat_id")
            if isinstance(cid, str) and _is_valid_chat_id(cid):
                scope = self._workspaces.scope_for_session_key(f"websocket:{cid}")
            else:
                scope = self._workspaces.default_scope()
            cwd = _terminal_cwd(envelope.get("cwd"), scope)

            # Re-attach: the WebUI terminal panel unmounts its xterm when
            # hidden and re-sends terminal_open when shown again. The shell
            # is still running, so resize it and replay the scrollback
            # instead of failing with "terminal already open".
            existing = self._terminals.get(connection, terminal_id)
            if existing is not None:
                try:
                    existing.resize(
                        int(envelope.get("cols") or 80),
                        int(envelope.get("rows") or 24),
                    )
                except (TypeError, ValueError):
                    pass
                await self._send_event(
                    connection,
                    "terminal_opened",
                    terminal_id=terminal_id,
                    shell=existing.shell_name,
                    cwd=cwd,
                    sandbox=bool(getattr(existing, "sandboxed", False)),
                )
                replay = self._terminals.scrollback(connection, terminal_id)
                if replay:
                    await self._send_event(
                        connection,
                        "terminal_output",
                        terminal_id=terminal_id,
                        data=encode_output(replay),
                    )
                return

            async def on_output(tid: str, data: bytes) -> None:
                await self._send_event(
                    connection,
                    "terminal_output",
                    terminal_id=tid,
                    data=encode_output(data),
                )

            async def on_exit(tid: str, exit_code: int | None) -> None:
                # close(), not discard(): the shell reporting its own exit is
                # not the same as its process tree being gone. Dropping the
                # session without closing it leaked a shell - and on Windows the
                # conhost beside it - for every terminal that ever ended.
                self._terminals.close(connection, tid)
                await self._send_event(
                    connection,
                    "terminal_exit",
                    terminal_id=tid,
                    exit_code=exit_code,
                )

            try:
                session = self._terminals.open(
                    connection,
                    terminal_id=terminal_id,
                    shell=envelope.get("shell") if isinstance(envelope.get("shell"), str) else None,
                    cwd=cwd,
                    cols=int(envelope.get("cols") or 80),
                    rows=int(envelope.get("rows") or 24),
                    on_output=on_output,
                    on_exit=on_exit,
                    # Isolated terminal: the shell runs inside the same OS
                    # sandbox as agent commands, confined to this project.
                    sandbox=envelope.get("sandbox") is True,
                    workspace=str(scope.project_path),
                )
            except (TerminalError, ValueError) as exc:
                await self._send_event(
                    connection,
                    "terminal_error",
                    terminal_id=terminal_id,
                    detail=str(exc),
                )
                return
            await self._send_event(
                connection,
                "terminal_opened",
                terminal_id=terminal_id,
                shell=session.shell_name,
                cwd=cwd,
                sandbox=bool(getattr(session, "sandboxed", False)),
            )
            return

        session = self._terminals.get(connection, terminal_id)
        if t == "terminal_close":
            self._terminals.close(connection, terminal_id)
            return
        if session is None:
            await self._send_event(
                connection,
                "terminal_error",
                terminal_id=terminal_id,
                detail="terminal not found",
            )
            return
        if t == "terminal_input":
            data = envelope.get("data")
            if not isinstance(data, str):
                return
            try:
                session.write(decode_input(data))
            except TerminalError as exc:
                await self._send_event(
                    connection,
                    "terminal_error",
                    terminal_id=terminal_id,
                    detail=str(exc),
                )
            return
        if t == "terminal_resize":
            try:
                session.resize(int(envelope.get("cols") or 80), int(envelope.get("rows") or 24))
            except (TypeError, ValueError):
                pass
            return
        await self._send_event(
            connection,
            "terminal_error",
            terminal_id=terminal_id,
            detail=f"unknown terminal action: {t}",
        )

    # -- Mobile preview (Android screencap / input) --------------------------

    async def _dispatch_agent_browser_close(
        self, connection: Any, envelope: dict[str, Any]
    ) -> None:
        """Close exactly the live session visible to the user."""
        reply = {key: envelope.get(key) for key in ("chat_id", "id", "request_id")}
        if not self._workspace_controls_available(connection):
            await self._send_event(
                connection, "agent_browser_error", **reply, detail="localhost_only"
            )
            return
        chat_id = envelope.get("chat_id")
        if not isinstance(chat_id, str) or not chat_id:
            await self._send_event(
                connection, "agent_browser_error", **reply, detail="invalid chat_id"
            )
            return

        from navin.webui.live_control import close_session

        try:
            closed = await close_session(f"websocket:{chat_id}", live_id=envelope.get("id"))
        except ValueError as exc:
            await self._send_event(connection, "agent_browser_error", **reply, detail=str(exc))
            return
        await self._send_event(
            connection, "agent_browser_closed", **reply, closed=closed
        )

    async def _dispatch_agent_browser_input(
        self, connection: Any, envelope: dict[str, Any]
    ) -> None:
        """Hand a click or a keystroke from the live view to the agent's browser.

        Kept to localhost like the other controls that drive something on this
        machine: the mirror is shareable, but driving the browser it mirrors is
        not something a remote viewer should be able to do.
        """
        reply = {key: envelope.get(key) for key in ("chat_id", "id", "request_id")}
        if not self._workspace_controls_available(connection):
            await self._send_event(
                connection, "agent_browser_error", **reply, detail="localhost_only"
            )
            return
        chat_id = envelope.get("chat_id")
        action = envelope.get("action")
        if not isinstance(chat_id, str) or not chat_id:
            await self._send_event(
                connection, "agent_browser_error", **reply, detail="invalid chat_id"
            )
            return
        if not isinstance(action, str) or not action:
            await self._send_event(
                connection, "agent_browser_error", **reply, detail="invalid action"
            )
            return

        from navin.webui.live_control import dispatch_input

        payload = {
            key: envelope.get(key)
            for key in ("x", "y", "width", "height", "dx", "dy", "text", "key", "count", "button")
        }
        try:
            result = await dispatch_input(
                f"websocket:{chat_id}", action, payload, live_id=envelope.get("id")
            )
        except ValueError as exc:
            await self._send_event(connection, "agent_browser_error", **reply, detail=str(exc))
            return
        except Exception as exc:
            self.logger.debug("agent browser input failed: {}", exc)
            await self._send_event(
                connection, "agent_browser_error", **reply, detail="the live session did not accept it"
            )
            return
        await self._send_event(
            connection, "agent_browser_input_done", **reply, url=result.get("url"),
            user_control=bool(result.get("user_control")),
        )

    async def _dispatch_mobile_preview_envelope(
        self,
        connection: Any,
        t: str,
        envelope: dict[str, Any],
    ) -> None:
        if t == "mobile_preview_status":
            if not self._workspace_controls_available(connection):
                await self._send_event(
                    connection,
                    "mobile_preview_status",
                    ready=False,
                    error="localhost_only",
                    help="Mobile preview status is localhost-only.",
                    fixes=[],
                    devices=[],
                    avds=[],
                    platform="unknown",
                    can_auto_install=False,
                )
                return
            from navin.mobile.adb import preview_readiness

            # install=true: attempt non-interactive package install when rights allow.
            auto_install = envelope.get("install") is True
            status = await asyncio.to_thread(
                preview_readiness, auto_install=auto_install
            )
            await self._send_event(connection, "mobile_preview_status", **status)
            return

        if t == "mobile_preview_start_avd":
            if not self._workspace_controls_available(connection):
                await self._send_event(
                    connection,
                    "mobile_preview_avd_started",
                    ok=False,
                    detail="mobile preview is localhost-only",
                )
                return
            avd = envelope.get("avd")
            if not isinstance(avd, str) or not avd.strip() or len(avd) > 64:
                await self._send_event(
                    connection,
                    "mobile_preview_avd_started",
                    ok=False,
                    detail="invalid avd name",
                )
                return
            from navin.mobile.adb import start_avd

            result = await asyncio.to_thread(start_avd, avd.strip())
            await self._send_event(
                connection,
                "mobile_preview_avd_started",
                ok=bool(result.get("ok")),
                detail=result.get("detail"),
            )
            return

        preview_id = envelope.get("preview_id")
        if not isinstance(preview_id, str) or not preview_id or len(preview_id) > 64:
            await self._send_event(
                connection, "mobile_preview_error", detail="invalid preview_id"
            )
            return

        if t == "mobile_preview_open":
            if not self._workspace_controls_available(connection):
                await self._send_event(
                    connection,
                    "mobile_preview_error",
                    preview_id=preview_id,
                    detail="mobile preview is localhost-only",
                )
                return
            existing = self._mobile_previews.get(connection, preview_id)
            if existing is not None:
                await self._send_event(
                    connection,
                    "mobile_preview_opened",
                    preview_id=preview_id,
                    serial=existing.device_serial(),
                    backend="native" if _mobile_native() else "python",
                )
                return

            async def on_frame(pid: str, frame: dict[str, Any]) -> None:
                await self._send_event(
                    connection, "mobile_preview_frame", preview_id=pid, **frame
                )

            async def on_logs(pid: str, lines: list[str]) -> None:
                await self._send_event(
                    connection, "mobile_preview_logs", preview_id=pid, lines=lines
                )

            async def on_error(pid: str, detail: str) -> None:
                await self._send_event(
                    connection,
                    "mobile_preview_error",
                    preview_id=pid,
                    detail=detail,
                )

            async def on_exit(pid: str) -> None:
                self._mobile_previews.close(connection, pid)
                await self._send_event(
                    connection, "mobile_preview_exit", preview_id=pid
                )

            try:
                fps = float(envelope.get("fps") or 4)
            except (TypeError, ValueError):
                fps = 4.0
            serial = envelope.get("serial")
            serial_s = serial.strip() if isinstance(serial, str) else None
            try:
                session = self._mobile_previews.open(
                    connection,
                    preview_id=preview_id,
                    serial=serial_s,
                    fps=fps,
                    on_frame=on_frame,
                    on_logs=on_logs,
                    on_error=on_error,
                    on_exit=on_exit,
                )
            except PreviewError as exc:
                await self._send_event(
                    connection,
                    "mobile_preview_error",
                    preview_id=preview_id,
                    detail=str(exc),
                )
                return
            await self._send_event(
                connection,
                "mobile_preview_opened",
                preview_id=preview_id,
                serial=session.device_serial(),
                backend="native" if _mobile_native() else "python",
            )
            return

        session = self._mobile_previews.get(connection, preview_id)
        if t == "mobile_preview_close":
            self._mobile_previews.close(connection, preview_id)
            return
        if session is None:
            await self._send_event(
                connection,
                "mobile_preview_error",
                preview_id=preview_id,
                detail="preview not found",
            )
            return

        try:
            if t == "mobile_preview_tap":
                await asyncio.to_thread(
                    session.tap, int(envelope["x"]), int(envelope["y"])
                )
            elif t == "mobile_preview_swipe":
                await asyncio.to_thread(
                    session.swipe,
                    int(envelope["x1"]),
                    int(envelope["y1"]),
                    int(envelope["x2"]),
                    int(envelope["y2"]),
                    int(envelope.get("duration_ms") or 300),
                )
            elif t == "mobile_preview_key":
                keycode = envelope.get("keycode")
                if not isinstance(keycode, str):
                    raise PreviewError("keycode required")
                await asyncio.to_thread(session.key, keycode)
            elif t == "mobile_preview_text":
                value = envelope.get("text")
                if not isinstance(value, str):
                    raise PreviewError("text required")
                await asyncio.to_thread(session.text, value)
            elif t == "mobile_preview_ui_dump":
                xml = await asyncio.to_thread(session.ui_dump)
                await self._send_event(
                    connection,
                    "mobile_preview_ui_dump",
                    preview_id=preview_id,
                    xml=xml[:200_000],
                )
            else:
                await self._send_event(
                    connection,
                    "mobile_preview_error",
                    preview_id=preview_id,
                    detail=f"unknown mobile preview action: {t}",
                )
        except (PreviewError, KeyError, TypeError, ValueError, RuntimeError) as exc:
            await self._send_event(
                connection,
                "mobile_preview_error",
                preview_id=preview_id,
                detail=str(exc),
            )

    async def _workspace_scope_or_error(
        self,
        connection: Any,
        resolver: Callable[[], Any],
        *,
        chat_id: str | None = None,
    ) -> Any | None:
        try:
            return resolver()
        except WorkspaceScopeError as exc:
            await self._send_event(
                connection,
                "error",
                detail="workspace_scope_rejected",
                reason=exc.message,
                **({"chat_id": chat_id} if chat_id else {}),
            )
            return None

    # -- Outbound WebSocket events -----------------------------------------

    def _install_blocking_pool(self) -> None:
        """Give ``asyncio.to_thread`` a pool sized for waiting, not computing.

        Nearly everything the gateway pushes off the loop is waiting on
        something remote: navin.live for the plan, `gh` for CI, the registry
        for LSP servers, git for status. Python's default executor is sized
        ``min(32, cpu_count + 4)`` - ten threads on six cores - which suits CPU
        work and badly under-serves this. Once those ten are parked on network
        calls, every later ``to_thread`` queues behind them, including the ones
        a chat turn needs, and a one-word prompt waits minutes for its first
        token while the log fills with unrelated routes all going slow at the
        same second.
        """
        pool = ThreadPoolExecutor(
            max_workers=blocking_pool_size(),
            thread_name_prefix="navin-blocking",
        )
        self._blocking_pool = pool
        if self._loop is not None:
            self._loop.set_default_executor(pool)
        # Any size we pick can be outgrown. Say so in the log rather than let
        # it resurface as "everything is slow" with no cause attached.
        self._pool_watch_task = asyncio.create_task(watch_pool_latency(self.logger))
        # Same idea for the loop itself: a synchronous call that holds it for
        # seconds stalls every route at once, and only a thread can see it.
        self._loop_lag = LoopLagMonitor(self.logger)
        self._loop_lag.start()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self._fs_watcher.stop()
        if self._stop_event:
            self._stop_event.set()
        if self._server_task:
            try:
                await self._server_task
            except asyncio.CancelledError:
                if asyncio.current_task() and asyncio.current_task().cancelling():
                    raise
                self.logger.debug("server task was already cancelled during shutdown")
            except Exception as e:
                self.logger.warning("server task error during shutdown: {}", e)
            self._server_task = None
        self._subs.clear()
        self._conn_chats.clear()
        self._conn_default.clear()
        self._conn_identity.clear()
        self._conn_ids.clear()
        self._presence.clear()
        self._tokens.clear()
        watch = self._pool_watch_task
        self._pool_watch_task = None
        if watch is not None:
            watch.cancel()
            with suppress(asyncio.CancelledError):
                await watch
        lag = self._loop_lag
        self._loop_lag = None
        if lag is not None:
            await lag.stop()
        pool = self._blocking_pool
        self._blocking_pool = None
        if pool is not None:
            # Not waiting: a shutdown must not hang behind a `gh` call that
            # still has most of its two-minute timeout left to burn.
            pool.shutdown(wait=False, cancel_futures=True)

    async def _safe_send_to(self, connection: Any, raw: str, *, label: str = "") -> None:
        """Send a raw frame to one connection, cleaning up on ConnectionClosed."""
        try:
            await connection.send(raw)
        except ConnectionClosed:
            self._cleanup_connection(connection)
            self.logger.warning("connection gone{}", label)
        except Exception:
            self.logger.exception("send failed{}", label)
            raise

    async def send(self, msg: OutboundMessage) -> None:
        event = outbound_event_from_message(msg)
        progress_event = event if isinstance(event, ProgressEvent) else None
        if isinstance(event, RuntimeModelUpdatedEvent):
            await self.send_runtime_model_updated(
                model_name=event.model,
                chat_id=msg.chat_id,
                model_preset=event.model_preset,
                reason=event.reason,
                previous_model=event.previous_model,
                used_percent=event.used_percent,
            )
            return
        if isinstance(event, BoardUpdatedEvent):
            await self.send_board_updated(project_path=event.project_path)
            return
        if isinstance(event, MontageUpdatedEvent):
            await self.send_montage_updated(event)
            return
        if isinstance(event, MetagraphUpdatedEvent):
            await self.send_metagraph_updated(
                project_path=event.project_path,
                generation=event.generation,
                diff=event.diff,
                view=event.view,
            )
            return
        if isinstance(event, TerminalOpenRequestedEvent):
            await self.send_terminal_open_request(msg.chat_id, event)
            return
        if isinstance(event, AgentExecEvent):
            await self.send_agent_exec(msg.chat_id, event)
            return
        if isinstance(event, AgentBrowserEvent):
            await self.send_agent_browser(msg.chat_id, event)
            return
        if isinstance(event, EditorOpenRequestedEvent):
            await self.send_editor_open_request(msg.chat_id, event)
            return
        if isinstance(event, ComposerModeRequestedEvent):
            await self.send_composer_mode_request(msg.chat_id, event)
            return
        if isinstance(event, ProductModuleRequestedEvent):
            await self.send_product_module_request(msg.chat_id, event)
            return
        if isinstance(event, SubagentProgressEvent):
            await self.send_subagent_progress(msg.chat_id, event)
            return
        if isinstance(event, PreviewOpenRequestedEvent):
            await self.send_preview_open_request(msg.chat_id, event)
            return
        if isinstance(event, FilePreviewOpenRequestedEvent):
            await self.send_file_preview_open_request(msg.chat_id, event)
            return
        if isinstance(event, ArtifactUpsertEvent):
            await self.send_artifact_upsert(msg.chat_id, event)
            return
        if isinstance(event, ArtifactSelectEvent):
            await self.send_artifact_select(msg.chat_id, event)
            return
        if isinstance(event, ContextCompactedEvent):
            await self.send_context_compacted(msg.chat_id, event)
            return
        if isinstance(event, CheckpointSavedEvent):
            await self.send_checkpoint_saved(msg.chat_id, event)
            return
        if isinstance(event, NotificationEvent):
            await self.send_notification(msg.chat_id, event)
            return
        if isinstance(event, ApprovalRequestedEvent):
            await self.send_approval_request(msg.chat_id, event)
            return
        if isinstance(event, ApprovalClosedEvent):
            await self.send_approval_closed(msg.chat_id, event)
            return
        if isinstance(event, ChoiceRequestedEvent):
            await self.send_choice_request(msg.chat_id, event)
            return
        if isinstance(event, ChoiceClosedEvent):
            await self.send_choice_closed(msg.chat_id, event)
            return
        if isinstance(event, RetryWaitEvent):
            # A provider backing off is a status, not something the assistant
            # said. It used to fall through to the generic text path and land in
            # the transcript, once per countdown tick; the shared key folds the
            # whole wait into one entry.
            await self.send_notification(
                msg.chat_id,
                NotificationEvent(
                    title="Model request retrying",
                    level="warning",
                    detail=event.content or None,
                    key=f"retry:{msg.chat_id}",
                ),
            )
            return

        # Snapshot the subscriber set so ConnectionClosed cleanups mid-iteration are safe.
        conns = list(self._subs.get(msg.chat_id, ()))
        if not conns:
            if isinstance(
                event,
                ProgressEvent
                | TurnEndEvent
                | SessionUpdatedEvent
                | GoalStatusEvent
                | GoalStateSyncEvent,
            ):
                self.logger.debug("no active subscribers for chat_id={}", msg.chat_id)
            else:
                self.logger.warning("no active subscribers for chat_id={}", msg.chat_id)
        if isinstance(event, GoalStateSyncEvent):
            if conns:
                await self.send_goal_state(msg.chat_id, event.goal_state or {"active": False})
            return
        if isinstance(event, GoalStatusEvent):
            if conns:
                if event.status in ("running", "idle"):
                    await self.send_goal_status(
                        msg.chat_id,
                        event.status,
                        started_at=event.started_at,
                    )
            return
        # Signal that the agent has fully finished processing the current turn.
        if isinstance(event, TurnEndEvent):
            await self.send_turn_end(
                msg.chat_id,
                latency_ms=event.latency_ms,
                goal_state=event.goal_state,
                metadata=msg.metadata,
                phase_timings_ms=event.phase_timings_ms,
            )
            await self.send_session_updated(msg.chat_id, scope="thread")
            return
        if isinstance(event, SessionUpdatedEvent):
            if conns:
                await self.send_session_updated(
                    msg.chat_id,
                    scope=event.scope,
                )
            return
        if progress_event and progress_event.file_edit_events:
            await self.send_file_edit_events(
                msg.chat_id,
                progress_event.file_edit_events,
                msg.metadata,
            )
            return
        text = msg.content
        wire_text = self._media.rewrite_local_markdown_images(text)
        payload: dict[str, Any] = {
            "event": "message",
            "chat_id": msg.chat_id,
            "text": wire_text,
        }
        if msg.media:
            payload["media"] = msg.media
            urls: list[dict[str, str]] = []
            for entry in msg.media:
                signed = self._media.sign_or_stage_media_path(Path(entry))
                if signed is not None:
                    urls.append(signed)
            if urls:
                payload["media_urls"] = urls
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to
        lat = msg.metadata.get("latency_ms")
        if isinstance(lat, (int, float)):
            payload["latency_ms"] = int(lat)
        model_name = msg.metadata.get("model_name") or msg.metadata.get("model")
        if isinstance(model_name, str) and model_name.strip():
            payload["model_name"] = model_name.strip()
        model_label = msg.metadata.get("model_label")
        if isinstance(model_label, str) and model_label.strip():
            payload["model_label"] = model_label.strip()
        model_preset = msg.metadata.get("model_preset")
        if isinstance(model_preset, str) and model_preset.strip():
            payload["model_preset"] = model_preset.strip()
        task_role = msg.metadata.get("task_role") or msg.metadata.get("model_route_role")
        if isinstance(task_role, str) and task_role.strip():
            payload["task_role"] = task_role.strip()
        if progress_event and progress_event.tool_events:
            payload["tool_events"] = progress_event.tool_events
        agent_ui = msg.metadata.get(OUTBOUND_META_AGENT_UI)
        if agent_ui is not None:
            payload["agent_ui"] = agent_ui
        # Mark intermediate agent breadcrumbs (tool-call hints, generic
        # progress strings) so WS clients can render them as subordinate
        # trace rows rather than conversational replies.
        if progress_event and progress_event.tool_hint:
            payload["kind"] = "tool_hint"
        elif progress_event:
            payload["kind"] = "progress"
        phase = "activity" if payload.get("kind") in ("tool_hint", "progress") else "answer"
        # Live tool-output frames (phase "output") are transient UI updates
        # emitted every ~500ms while a command runs; the final "end" event
        # already carries the full result, so keep them out of the transcript.
        ephemeral_tool_output = (
            not text.strip()
            and progress_event is not None
            and bool(progress_event.tool_events)
            and all(
                isinstance(event, dict) and event.get("phase") == "output"
                for event in progress_event.tool_events
            )
        )
        if ephemeral_tool_output:
            self._transcripts.prepare_event(
                msg.chat_id,
                payload,
                metadata=msg.metadata,
                phase=phase,
                include_source=True,
            )
        else:
            self._transcripts.prepare_and_append(
                msg.chat_id,
                payload,
                metadata=msg.metadata,
                phase=phase,
                include_source=True,
                transcript_overrides={"text": text},
            )
        raw = json.dumps(payload, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" ")

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
    ) -> None:
        """Push one chunk of model reasoning. Mirrors ``send_delta`` shape so
        clients receive a stream that opens, updates in place, and closes -
        rendered above the active assistant bubble with a shimmer header
        until the matching ``reasoning_end`` arrives.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not delta:
            return
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_delta",
            "chat_id": chat_id,
            "text": delta,
        }
        if stream_id is not None:
            body["stream_id"] = stream_id
        model_name = meta.get("model_name") or meta.get("model")
        if isinstance(model_name, str) and model_name.strip():
            body["model_name"] = model_name.strip()
        model_label = meta.get("model_label")
        if isinstance(model_label, str) and model_label.strip():
            body["model_label"] = model_label.strip()
        task_role = meta.get("task_role") or meta.get("model_route_role")
        if isinstance(task_role, str) and task_role.strip():
            body["task_role"] = task_role.strip()
        self._transcripts.prepare_and_append(
            chat_id,
            body,
            metadata=meta,
            phase="reasoning",
        )
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" reasoning ")

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
    ) -> None:
        """Close the current reasoning stream segment for in-place renderers."""
        conns = list(self._subs.get(chat_id, ()))
        meta = metadata or {}
        body: dict[str, Any] = {
            "event": "reasoning_end",
            "chat_id": chat_id,
        }
        if stream_id is not None:
            body["stream_id"] = stream_id
        self._transcripts.prepare_and_append(
            chat_id,
            body,
            metadata=meta,
            phase="reasoning",
        )
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" reasoning_end ")

    async def send_file_edit_events(
        self,
        chat_id: str,
        edits: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        payload: dict[str, Any] = {
            "event": "file_edit",
            "chat_id": chat_id,
            "edits": edits,
        }
        try:
            root = self._workspaces.scope_for_session_key(
                f"websocket:{chat_id}"
            ).project_path
        except Exception:
            root = None
        self._invalidate_file_tree_cache(root)
        self._transcripts.prepare_and_append(
            chat_id,
            payload,
            metadata=metadata,
            phase="activity",
        )
        raw = json.dumps(payload, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" file_edit ")

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
        stream_end: bool = False,
        resuming: bool = False,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        meta = metadata or {}
        stream_key = (chat_id, str(stream_id or ""))
        if stream_end:
            body: dict[str, Any] = {"event": "stream_end", "chat_id": chat_id}
            buffered = self._stream_text_buffers.pop(stream_key, [])
            if delta:
                buffered.append(delta)
            full_text = "".join(buffered)
            rewritten = self._media.rewrite_local_markdown_images(full_text)
            if delta or rewritten != full_text:
                body["text"] = rewritten
        else:
            body = {
                "event": "delta",
                "chat_id": chat_id,
                "text": delta,
            }
            self._stream_text_buffers.setdefault(stream_key, []).append(delta)
        if stream_id is not None:
            body["stream_id"] = stream_id
        self._transcripts.prepare_and_append(
            chat_id,
            body,
            metadata=meta,
            phase="answer",
        )
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" stream ")

    async def send_turn_end(
        self,
        chat_id: str,
        latency_ms: int | None = None,
        *,
        goal_state: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        phase_timings_ms: dict[str, int] | None = None,
    ) -> None:
        """Signal that the agent has fully finished processing the current turn."""
        conns = list(self._subs.get(chat_id, ()))
        body: dict[str, Any] = {"event": "turn_end", "chat_id": chat_id}
        if latency_ms is not None:
            body["latency_ms"] = int(latency_ms)
        if goal_state is not None:
            body["goal_state"] = goal_state
        if phase_timings_ms:
            body["phase_timings_ms"] = phase_timings_ms
        if isinstance(metadata, dict):
            model_name = metadata.get("model_name") or metadata.get("model")
            if isinstance(model_name, str) and model_name.strip():
                body["model_name"] = model_name.strip()
            model_label = metadata.get("model_label")
            if isinstance(model_label, str) and model_label.strip():
                body["model_label"] = model_label.strip()
            model_preset = metadata.get("model_preset")
            if isinstance(model_preset, str) and model_preset.strip():
                body["model_preset"] = model_preset.strip()
            task_role = metadata.get("task_role") or metadata.get("model_route_role")
            if isinstance(task_role, str) and task_role.strip():
                body["task_role"] = task_role.strip()
        self._transcripts.prepare_and_append(
            chat_id,
            body,
            metadata=metadata,
            phase="complete",
        )
        raw = json.dumps(body, ensure_ascii=False)
        if not conns:
            return
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" turn_end ")

    async def send_goal_state(self, chat_id: str, blob: dict[str, Any]) -> None:
        """Push persisted goal-state snapshot for *chat_id* (multi-chat isolation)."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body = {"event": "goal_state", "chat_id": chat_id, "goal_state": blob}
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" goal_state ")

    async def send_goal_status(
        self,
        chat_id: str,
        status: str,
        *,
        started_at: float | None = None,
    ) -> None:
        """Notify subscribed clients that a turn started or finished (wall-clock hint)."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "goal_status",
            "chat_id": chat_id,
            "status": status,
        }
        if status == "running" and started_at is not None:
            body["started_at"] = started_at
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" goal_status ")

    async def send_session_updated(self, chat_id: str, *, scope: str | None = None) -> None:
        """Notify WebUI clients that a session row should refresh."""
        conns = list(self._conn_chats)
        if not conns:
            return
        body: dict[str, Any] = {"event": "session_updated", "chat_id": chat_id}
        if scope:
            body["scope"] = scope
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" session_updated ")

    async def send_context_compacted(
        self,
        chat_id: str,
        event: ContextCompactedEvent,
    ) -> None:
        """Tell WebUI clients that part of a chat's history was summarized."""
        if chat_id == "*":
            conns = list(self._conn_chats)
        else:
            conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "context_compacted",
            "chat_id": chat_id,
            "kind": event.kind,
            "messages_archived": event.messages_archived,
        }
        if isinstance(event.tokens_before, int):
            body["tokens_before"] = event.tokens_before
        if isinstance(event.tokens_after, int):
            body["tokens_after"] = event.tokens_after
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" context_compacted ")

    async def send_checkpoint_saved(
        self,
        chat_id: str,
        event: CheckpointSavedEvent,
    ) -> None:
        """Tell WebUI clients a restore point exists for this turn."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "checkpoint_saved",
            "chat_id": chat_id,
            "name": event.name,
            "auto": event.auto,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" checkpoint_saved ")

    async def send_board_updated(self, *, project_path: str | None) -> None:
        """Broadcast a board change to every open connection.

        The board is project-scoped, not chat-scoped: clients compare
        ``project_path`` with their active workspace and refetch on match.
        """
        conns = list(self._conn_chats)
        if not conns:
            return
        body: dict[str, Any] = {"event": "board_updated"}
        if isinstance(project_path, str) and project_path:
            body["project_path"] = project_path
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" board_updated ")

    async def send_montage_updated(self, event: MontageUpdatedEvent) -> None:
        """Broadcast a Montage change (timeline, render job, assets) to every client."""
        conns = list(self._conn_chats)
        if not conns:
            return
        body: dict[str, Any] = {"event": "montage_updated", "kind": event.kind}
        if isinstance(event.project_path, str) and event.project_path:
            body["project_path"] = event.project_path
        if event.name:
            body["name"] = event.name
        if isinstance(event.job, dict):
            body["job"] = event.job
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" montage_updated ")

    async def send_metagraph_updated(
        self,
        *,
        project_path: str | None,
        generation: int = 0,
        diff: dict[str, Any] | None = None,
        view: str = "files",
    ) -> None:
        """Broadcast a metagraph change to every open connection."""
        conns = list(self._conn_chats)
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "metagraph_updated",
            "generation": int(generation or 0),
            "view": view or "files",
        }
        if isinstance(project_path, str) and project_path:
            body["project_path"] = project_path
        if isinstance(diff, dict):
            body["diff"] = diff
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" metagraph_updated ")

    async def send_terminal_open_request(
        self,
        chat_id: str,
        event: TerminalOpenRequestedEvent,
    ) -> None:
        """Ask the clients of *chat_id* to open their integrated terminal panel.

        The client answers with a normal ``terminal_open`` envelope, so the PTY
        session is created through the standard flow and dies with the
        connection - the agent never owns the shell.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {"event": "terminal_open_request", "chat_id": chat_id}
        if event.shell:
            body["shell"] = event.shell
        if event.cwd:
            body["cwd"] = event.cwd
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" terminal_open_request ")

    async def send_agent_exec(self, chat_id: str, event: AgentExecEvent) -> None:
        """Stream one agent exec command to the editor's read-only terminal tabs.

        Cursor-style: a ``start`` frame opens the tab, ``output`` frames carry
        live deltas, ``exit`` closes the story with the return code. Frames
        are ephemeral - the transcript already records the tool result.

        ``chat_id="*"`` broadcasts to every open WebUI connection (used by
        Montage toolchain installs that are not tied to one chat).
        """
        if chat_id == "*":
            conns = list(self._conn_chats)
        else:
            conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "agent_exec",
            "chat_id": chat_id,
            "id": event.exec_id,
            "phase": event.phase,
            "background": event.background,
        }
        if event.command:
            body["command"] = event.command
        if event.cwd:
            body["cwd"] = event.cwd
        if event.data:
            body["data"] = event.data
        if event.exit_code is not None:
            body["exit_code"] = event.exit_code
        if event.sandbox:
            body["sandbox"] = event.sandbox
        if event.sandbox_lifted:
            body["sandbox_lifted"] = True
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" agent_exec ")

    async def send_agent_browser(self, chat_id: str, event: AgentBrowserEvent) -> None:
        """Stream the agent's live browser mirror to the Dev workbench.

        ``start`` opens the tab, ``frame`` carries one base64 JPEG screencast
        frame, ``action`` feeds the activity line, ``exit`` marks the session
        closed. Frames are ephemeral: nothing is persisted, exactly like the
        agent exec terminal feed.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "agent_browser",
            "chat_id": chat_id,
            "id": event.browser_id,
            "phase": event.phase,
        }
        if event.url:
            body["url"] = event.url
        if event.title:
            body["title"] = event.title
        if event.action:
            body["action"] = event.action
        if event.data:
            body["data"] = event.data
        if event.width:
            body["width"] = event.width
        if event.height:
            body["height"] = event.height
        if event.user_control is not None:
            body["user_control"] = event.user_control
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" agent_browser ")

    async def send_editor_open_request(
        self,
        chat_id: str,
        event: EditorOpenRequestedEvent,
    ) -> None:
        """Ask the clients of *chat_id* to open a file tab or reveal a folder.

        The client goes through its normal Dev workbench flow, so the result
        is exactly what a click in the explorer produces.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "editor_open_request",
            "chat_id": chat_id,
            "path": event.path,
            "kind": event.kind,
        }
        if event.line:
            body["line"] = event.line
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" editor_open_request ")

    async def send_composer_mode_request(
        self,
        chat_id: str,
        event: ComposerModeRequestedEvent,
    ) -> None:
        """Ask the clients of *chat_id* to switch composer turn mode + accent."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        mode = (event.mode or "").strip().lower()
        # "montage" was missing here, so the /montage brief's set_composer_mode
        # was dropped in transit and the composer never showed Montage. "ask" is
        # a real mode the agent can hand back to as well.
        if mode not in {"ask", "plan", "agent", "review", "security", "debug", "montage"}:
            return
        body: dict[str, Any] = {
            "event": "composer_mode_request",
            "chat_id": chat_id,
            "mode": mode,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" composer_mode_request ")

    async def send_product_module_request(
        self,
        chat_id: str,
        event: ProductModuleRequestedEvent,
    ) -> None:
        """Ask the clients of *chat_id* to open a product module (Code, …)."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        from navin.command.modules import normalize_product_module

        module = normalize_product_module(event.module)
        if module is None:
            return
        body: dict[str, Any] = {
            "event": "product_module_request",
            "chat_id": chat_id,
            "module": module,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" product_module_request ")

    async def send_subagent_progress(
        self,
        chat_id: str,
        event: SubagentProgressEvent,
    ) -> None:
        """Push a live parallel-subagent card update to *chat_id* clients."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        task_id = (event.task_id or "").strip()
        if not task_id:
            return
        body: dict[str, Any] = {
            "event": "subagent_progress",
            "chat_id": chat_id,
            "task_id": task_id,
            "label": (event.label or task_id).strip() or task_id,
            "phase": (event.phase or "initializing").strip() or "initializing",
            "status_line": (event.status_line or "").strip() or "Working…",
            "iteration": int(event.iteration or 0),
            "done": bool(event.done),
        }
        if event.model:
            body["model"] = event.model
        if event.error:
            body["error"] = event.error
        if event.task_description:
            body["task_description"] = event.task_description
        if event.started_ms_ago is not None:
            body["started_ms_ago"] = int(event.started_ms_ago)
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" subagent_progress ")

    async def send_file_preview_open_request(
        self,
        chat_id: str,
        event: FilePreviewOpenRequestedEvent,
    ) -> None:
        """Ask the clients of *chat_id* to open a file in File Preview."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        path = (event.path or "").strip()
        if not path:
            return
        body: dict[str, Any] = {
            "event": "file_preview_open_request",
            "chat_id": chat_id,
            "path": path,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" file_preview_open_request ")

    async def send_artifact_upsert(
        self,
        chat_id: str,
        event: ArtifactUpsertEvent,
        *,
        restored: bool = False,
    ) -> None:
        """Push an artifact create/update to clients of *chat_id*.

        *restored* marks a replay of something already on disk rather than
        something the agent just produced. The client uses it to rebuild the
        canvas without popping it open: reattaching to a chat is not a reason to
        cover the conversation with a panel the user had closed.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        artifact = event.artifact if isinstance(event.artifact, dict) else None
        if not artifact or not artifact.get("id"):
            return
        body: dict[str, Any] = {
            "event": "artifact_upsert",
            "chat_id": chat_id,
            "artifact": artifact,
            "restored": restored,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" artifact_upsert ")

    async def send_artifact_select(
        self,
        chat_id: str,
        event: ArtifactSelectEvent,
    ) -> None:
        """Ask clients of *chat_id* to focus one artifact in the canvas."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        artifact_id = (event.artifact_id or "").strip()
        if not artifact_id:
            return
        body: dict[str, Any] = {
            "event": "artifact_select",
            "chat_id": chat_id,
            "artifact_id": artifact_id,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" artifact_select ")

    async def send_preview_open_request(
        self,
        chat_id: str,
        event: PreviewOpenRequestedEvent,
    ) -> None:
        """Ask the clients of *chat_id* to open Preview (web) or Mobile.

        The client loads the iframe / starts the Mobile mirror through its
        normal Dev workbench flow.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        kind = "mobile" if event.kind == "mobile" else "web"
        body: dict[str, Any] = {
            "event": "preview_open_request",
            "chat_id": chat_id,
            "kind": kind,
        }
        if kind == "web" and event.url:
            body["url"] = event.url
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" preview_open_request ")

    async def send_notification(
        self,
        chat_id: str,
        event: NotificationEvent,
    ) -> None:
        """Push one entry to the WebUI notification centre.

        Chat-scoped when a chat owns the condition, broadcast when ``chat_id``
        is ``*`` - a stalled provider or a failed scheduled job is not something
        only the currently open conversation should learn about.
        """
        title = event.title.strip()
        if not title:
            return
        if chat_id == "*":
            conns = list(self._conn_chats)
        else:
            conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "notification",
            "chat_id": chat_id,
            "title": title,
            "level": event.level,
            "source": event.source,
        }
        if event.detail:
            body["detail"] = event.detail
        if event.key:
            body["key"] = event.key
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" notification ")

    async def send_approval_request(
        self,
        chat_id: str,
        event: ApprovalRequestedEvent,
    ) -> None:
        """Ask this chat's clients to allow or refuse, with a tool call waiting.

        Sent to every connection subscribed to the chat, not just the one that
        started the turn: the user may have the conversation open in a second
        window, and the answer is matched by ``request_id`` either way. If no one
        is subscribed there is nothing to do; the request will time out and the
        tool will report the refusal.
        """
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "approval_request",
            "chat_id": chat_id,
            "request_id": event.request_id,
            "tool": event.tool,
            "action": event.action,
            "reason": event.reason,
            "remember_offered": event.remember_offered,
        }
        if event.detail:
            body["detail"] = event.detail
        if event.consequence:
            body["consequence"] = event.consequence
        if event.scope:
            body["scope"] = event.scope
        if event.expires_at_ms is not None:
            body["expires_at_ms"] = event.expires_at_ms
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" approval_request ")

    async def send_approval_closed(
        self,
        chat_id: str,
        event: ApprovalClosedEvent,
    ) -> None:
        """Tell the clients a pending request is settled, however it ended."""
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "approval_closed",
            "chat_id": chat_id,
            "request_id": event.request_id,
            "allowed": event.allowed,
        }
        if event.reason:
            body["reason"] = event.reason
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" approval_closed ")

    async def send_choice_request(
        self,
        chat_id: str,
        event: ChoiceRequestedEvent,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "choice_request",
            "chat_id": chat_id,
            "request_id": event.request_id,
            "question": event.question,
            "options": event.options,
            "allow_skip": event.allow_skip,
            "recommended_id": event.recommended_id,
        }
        if event.expires_at_ms is not None:
            body["expires_at_ms"] = event.expires_at_ms
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" choice_request ")

    async def send_choice_closed(
        self,
        chat_id: str,
        event: ChoiceClosedEvent,
    ) -> None:
        conns = list(self._subs.get(chat_id, ()))
        if not conns:
            return
        body: dict[str, Any] = {
            "event": "choice_closed",
            "chat_id": chat_id,
            "request_id": event.request_id,
            "option_id": event.option_id,
            "skipped": event.skipped,
        }
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" choice_closed ")

    async def send_runtime_model_updated(
        self,
        *,
        model_name: Any,
        chat_id: Any = None,
        model_preset: Any = None,
        reason: Any = None,
        previous_model: Any = None,
        used_percent: Any = None,
    ) -> None:
        """Broadcast runtime model changes to every open websocket connection.

        ``chat_id`` names the chat whose turn picked the model. Every client
        still receives the frame (a second window may show that same chat),
        but without the tag they all repainted their badge with whichever
        session spoke last, and a chat could adopt another chat's model.
        """
        conns = list(self._conn_chats)
        if not conns or not isinstance(model_name, str) or not model_name.strip():
            return
        body: dict[str, Any] = {
            "event": "runtime_model_updated",
            "model_name": model_name.strip(),
        }
        if isinstance(chat_id, str) and chat_id.strip():
            body["chat_id"] = chat_id.strip()
        if isinstance(model_preset, str) and model_preset.strip():
            body["model_preset"] = model_preset.strip()
        if isinstance(reason, str) and reason.strip():
            body["reason"] = reason.strip()
        if isinstance(previous_model, str) and previous_model.strip():
            body["previous_model"] = previous_model.strip()
        if isinstance(used_percent, int) and used_percent >= 0:
            body["used_percent"] = used_percent
        raw = json.dumps(body, ensure_ascii=False)
        for connection in conns:
            await self._safe_send_to(connection, raw, label=" runtime_model_updated ")
