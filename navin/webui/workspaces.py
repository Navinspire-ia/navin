# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Persisted WebUI project workspace state."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

from loguru import logger

from navin.config.paths import get_webui_dir
from navin.security.workspace_access import (
    WORKSPACE_SCOPE_METADATA_KEY,
    WorkspaceScope,
    WorkspaceScopeError,
    build_workspace_scope,
    default_workspace_scope,
    validate_workspace_scope_payload,
)

WEBUI_WORKSPACE_STATE_SCHEMA_VERSION = 1
_MAX_STATE_FILE_BYTES = 128 * 1024
_DEFAULT_ACCESS_MODES = {"default", "full"}
_LEGACY_RESTRICTED_DEFAULT_ACCESS_MODE = "restricted"
_WEBUI_SCOPE_CHANNEL = "websocket"
# A session scope is served from memory this long, then re-checked in the
# background while the last answer keeps serving (see _revalidate_scope).
_SCOPE_FRESH_S = 5.0
_SCOPE_CACHE_MAX = 512


def _scope_change_is_non_escalating(current: WorkspaceScope, requested: WorkspaceScope) -> bool:
    """Allow a remote request only when it keeps the project and does not add access."""
    return (
        requested.project_path == current.project_path
        and (not current.restrict_to_workspace or requested.restrict_to_workspace)
    )


# Words that say "make me something" rather than what the project is.
_PROJECT_NAME_FILLER = frozenset(
    """
    a an the and or of for to in on with by from into using use my me i you we it this that
    please can could would will want need like make build create write code generate set up
    new let lets start user users wants wanted develop developing
    je tu il nous vous moi me te mon ma mes ton ta tes notre votre un une des le la les l d de du
    au aux et ou en dans sur pour par avec sans qui que quoi est c ce cet cette ces
    cree creer creez crees creons fais faire fait fait faites genere generer code coder
    developpe developper construis construire ecris ecrire veux voudrais aimerais peux pourrais
    besoin stp svp merci nouveau nouvelle nouveaux nouvelles petit petite simple
    """.split()
)
# Kinds of project: dropped when the request says more than that.
_PROJECT_NAME_GENERIC = frozenset(
    "app apps application applications appli web site website page projet project "
    "programme program script outil tool".split()
)
_PROJECT_NAME_MAX_WORDS = 2
_PROJECT_NAME_MAX_CHARS = 40
_PROJECT_NAME_FALLBACK = "projet"


def project_folder_name(request: str) -> str:
    """Folder name for the project a Code request asks for.

    ``"crée moi une app de todo list en react"`` -> ``todo-list``.
    Accents fold to ASCII; a request with no usable word gets ``projet``.
    """
    text = re.sub(r"https?://\S+|[`*_#>\[\]()]", " ", request or "")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", ascii_text) if w not in _PROJECT_NAME_FILLER]
    words = [w for w in words if w not in _PROJECT_NAME_GENERIC] or words
    name = "-".join(words[:_PROJECT_NAME_MAX_WORDS])[:_PROJECT_NAME_MAX_CHARS].strip("-")
    return name or _PROJECT_NAME_FALLBACK


def _free_folder(parent: Path, name: str) -> Path:
    candidate = parent / name
    index = 2
    while candidate.exists():
        candidate = parent / f"{name}-{index}"
        index += 1
    return candidate


def webui_workspace_state_path() -> Path:
    return get_webui_dir() / "workspace-state.json"


def default_webui_workspace_state() -> dict[str, Any]:
    return {
        "schema_version": WEBUI_WORKSPACE_STATE_SCHEMA_VERSION,
        "default_access_mode": "default",
        "updated_at": None,
    }


def normalize_webui_workspace_state(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    state = default_webui_workspace_state()
    updated_at = raw.get("updated_at")
    state["updated_at"] = updated_at if isinstance(updated_at, str) else None
    default_access_mode = raw.get("default_access_mode")
    if default_access_mode in _DEFAULT_ACCESS_MODES:
        state["default_access_mode"] = default_access_mode
    return state


def read_webui_workspace_state() -> dict[str, Any]:
    path = webui_workspace_state_path()
    if not path.is_file():
        return default_webui_workspace_state()
    try:
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            logger.warning("webui workspace state too large, ignoring: {}", path)
            return default_webui_workspace_state()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read webui workspace state failed {}: {}", path, e)
        return default_webui_workspace_state()
    return normalize_webui_workspace_state(raw)


def write_webui_workspace_state(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_webui_workspace_state(raw)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise ValueError("workspace state is too large")

    path = webui_workspace_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return state
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return state


def read_webui_default_access_mode() -> str:
    state = read_webui_workspace_state()
    mode = state.get("default_access_mode")
    return mode if mode in _DEFAULT_ACCESS_MODES else "default"


def write_webui_default_access_mode(mode: str) -> bool:
    if mode == _LEGACY_RESTRICTED_DEFAULT_ACCESS_MODE:
        mode = "default"
    if mode not in _DEFAULT_ACCESS_MODES:
        raise ValueError("default access mode must be default or full")
    state = read_webui_workspace_state()
    changed = state.get("default_access_mode") != mode
    if changed:
        state["default_access_mode"] = mode
        write_webui_workspace_state(state)
    return changed


def default_scope_for_webui(
    default_workspace: Path,
    default_restrict_to_workspace: bool,
) -> WorkspaceScope:
    mode = read_webui_default_access_mode()
    if mode == "default":
        return default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )
    return build_workspace_scope(default_workspace, mode, source_channel=_WEBUI_SCOPE_CHANNEL)


def workspaces_payload(
    *,
    default_workspace: Path,
    default_restrict_to_workspace: bool,
    controls_available: bool,
) -> dict[str, Any]:
    default_access_mode = read_webui_default_access_mode()
    default_scope = (
        default_workspace_scope(
            default_workspace,
            default_restrict_to_workspace,
            source_channel=_WEBUI_SCOPE_CHANNEL,
        )
        if default_access_mode == "default"
        else build_workspace_scope(default_workspace, default_access_mode, source_channel=_WEBUI_SCOPE_CHANNEL)
    )
    return {
        "schema_version": WEBUI_WORKSPACE_STATE_SCHEMA_VERSION,
        "default_access_mode": default_access_mode,
        "default_scope": default_scope.payload(),
        "controls": {
            "can_change_project": controls_available,
            "can_use_full_access": controls_available,
        },
    }


class WebUIWorkspaceController:
    """Own WebUI project scope persistence and validation."""

    def __init__(
        self,
        *,
        session_manager: Any | None,
        default_workspace: Path,
        default_restrict_to_workspace: bool,
    ) -> None:
        self._sessions = session_manager
        self._default_workspace = default_workspace
        self._default_restrict_to_workspace = default_restrict_to_workspace
        self._scope_cache: dict[str, tuple[float, tuple[str, bool], WorkspaceScope]] = {}
        self._scope_refreshing: set[str] = set()
        self._scope_refresh_lock = threading.Lock()

    def _live_restrict_to_workspace(self) -> bool:
        """Settings > restrict to workspace, re-read only when config.json changes.

        Every WebUI route resolves a scope on the event loop, and each one used
        to load and decrypt the whole config for this one flag: a burst of
        explorer/diagnostics requests stalled the loop under disk contention.
        """
        try:
            from navin.config.loader import get_config_path, load_config

            stamp = get_config_path().stat().st_mtime_ns
            cached = getattr(self, "_restrict_cache", None)
            if cached is not None and cached[0] == stamp:
                return cached[1]
            value = bool(load_config().tools.restrict_to_workspace)
            self._restrict_cache = (stamp, value)
            return value
        except Exception:
            return self._default_restrict_to_workspace

    def default_scope(self) -> WorkspaceScope:
        return default_scope_for_webui(
            self._default_workspace,
            self._live_restrict_to_workspace(),
        )

    def scope_from_stored_payload(self, raw: Any) -> WorkspaceScope:
        """Resolve a stored scope without opening the session file again."""
        if not isinstance(raw, dict):
            return self.default_scope()
        try:
            return validate_workspace_scope_payload(
                raw,
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=self._live_restrict_to_workspace(),
                source_channel=_WEBUI_SCOPE_CHANNEL,
            )
        except WorkspaceScopeError:
            return self.default_scope()

    def list_scope_payload(
        self,
        raw: Any,
        *,
        default: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sidebar scope payload without ``is_dir`` on every row."""
        fallback = default if default is not None else self.default_scope().payload()
        if not isinstance(raw, dict):
            return fallback
        path = raw.get("project_path") or raw.get("path")
        if not isinstance(path, str) or not path.strip():
            return fallback
        mode = raw.get("access_mode")
        if not isinstance(mode, str) or not mode:
            mode = fallback.get("access_mode") or "default"
        return {
            **fallback,
            "project_path": path,
            "access_mode": mode,
            "project_name": Path(path).name or path,
        }

    def scope_for_session_key(self, session_key: str) -> WorkspaceScope:
        if self._sessions is None:
            return self.default_scope()
        metadata_reader = getattr(self._sessions, "read_session_metadata", None)
        if callable(metadata_reader):
            data = metadata_reader(session_key)
        else:
            data = self._sessions.read_session_file(session_key)
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        if not isinstance(metadata, dict) or WORKSPACE_SCOPE_METADATA_KEY not in metadata:
            return self.default_scope()
        raw = metadata.get(WORKSPACE_SCOPE_METADATA_KEY)
        restrict = self._live_restrict_to_workspace()
        signature = (repr(raw), restrict)
        now = time.monotonic()
        hit = self._scope_cache.get(session_key)
        if hit is not None and hit[1] == signature:
            if now - hit[0] >= _SCOPE_FRESH_S:
                self._revalidate_scope(session_key, raw, restrict, signature)
            return hit[2]
        scope = self._validate_scope(raw, restrict)
        self._remember_scope(session_key, signature, scope)
        return scope

    def _validate_scope(self, raw: Any, restrict: bool) -> WorkspaceScope:
        try:
            return validate_workspace_scope_payload(
                raw,
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=restrict,
                source_channel=_WEBUI_SCOPE_CHANNEL,
            )
        except WorkspaceScopeError:
            return self.default_scope()

    def _remember_scope(
        self, session_key: str, signature: tuple[str, bool], scope: WorkspaceScope
    ) -> None:
        if len(self._scope_cache) >= _SCOPE_CACHE_MAX:
            self._scope_cache.clear()
        self._scope_cache[session_key] = (time.monotonic(), signature, scope)

    def _revalidate_scope(
        self, session_key: str, raw: Any, restrict: bool, signature: tuple[str, bool]
    ) -> None:
        """Re-check an expired scope in a thread; the caller keeps the last one.

        Validating resolves the project folder and checks it exists. On a
        ``\\\\wsl.localhost`` project seen from Windows that is a 9P round trip
        per path part, seconds when the bridge is busy, and every HTTP route
        did it on the event loop once the 5 s cache had expired (desktop log of
        v2.3.3: 25 loop blocks, 48 s, mostly the 3 s review poll). The stored
        payload has not changed (same signature), so the answer is the same
        project with the same access; a deleted folder is noticed by the next
        request instead of this one.
        """
        with self._scope_refresh_lock:
            if session_key in self._scope_refreshing:
                return
            self._scope_refreshing.add(session_key)

        def refresh() -> None:
            try:
                self._remember_scope(session_key, signature, self._validate_scope(raw, restrict))
            except Exception:  # noqa: BLE001 - keep serving the last scope
                logger.opt(exception=True).debug("scope revalidation failed for {}", session_key)
            finally:
                with self._scope_refresh_lock:
                    self._scope_refreshing.discard(session_key)

        threading.Thread(target=refresh, name="webui-scope-refresh", daemon=True).start()

    def payload(self, *, controls_available: bool) -> dict[str, Any]:
        return workspaces_payload(
            default_workspace=self._default_workspace,
            default_restrict_to_workspace=self._live_restrict_to_workspace(),
            controls_available=controls_available,
        )

    def scope_from_envelope(
        self,
        envelope: dict[str, Any],
        *,
        session_key: str | None,
        controls_available: bool,
    ) -> WorkspaceScope:
        current = self.scope_for_session_key(session_key) if session_key else self.default_scope()
        raw = envelope.get(WORKSPACE_SCOPE_METADATA_KEY)
        if raw is None:
            scope = current
        else:
            scope = validate_workspace_scope_payload(
                raw,
                default_workspace=self._default_workspace,
                default_restrict_to_workspace=self._live_restrict_to_workspace(),
                source_channel=_WEBUI_SCOPE_CHANNEL,
            )
        if not controls_available and not _scope_change_is_non_escalating(current, scope):
            raise WorkspaceScopeError("workspace controls are localhost-only", status=403)
        return scope

    def scope_for_new_chat(
        self,
        envelope: dict[str, Any],
        *,
        controls_available: bool,
    ) -> WorkspaceScope:
        return self.scope_from_envelope(
            envelope,
            session_key=None,
            controls_available=controls_available,
        )

    def scope_for_set_request(
        self,
        envelope: dict[str, Any],
        *,
        chat_id: str,
        chat_running: bool,
        controls_available: bool,
    ) -> WorkspaceScope:
        if chat_running:
            raise WorkspaceScopeError("chat_running", status=409)
        return self.scope_from_envelope(
            envelope,
            session_key=f"websocket:{chat_id}",
            controls_available=controls_available,
        )

    def scope_for_message(
        self,
        envelope: dict[str, Any],
        *,
        chat_id: str,
        chat_running: bool,
        controls_available: bool,
    ) -> WorkspaceScope:
        scope = self.scope_from_envelope(
            envelope,
            session_key=f"websocket:{chat_id}",
            controls_available=controls_available,
        )
        if (
            WORKSPACE_SCOPE_METADATA_KEY in envelope
            and chat_running
            and scope.metadata() != self.scope_for_session_key(f"websocket:{chat_id}").metadata()
        ):
            raise WorkspaceScopeError("chat_running", status=409)
        return scope

    def project_for_code_chat(
        self,
        chat_id: str,
        scope: WorkspaceScope,
        request: str,
    ) -> WorkspaceScope | None:
        """A Code chat started without a project gets its own folder.

        The folder sits under the Projects root, named after the request, and
        the chat moves into it before the agent runs. None when the chat is
        already in a project or has already started.
        """
        root = Path(self._default_workspace).expanduser().resolve(strict=False)
        if scope.project_path.resolve(strict=False) != root:
            return None
        # The default workspace is itself a real project (a git repo): work in
        # it, never scatter generated sub-folders inside the user's code.
        if (root / ".git").exists():
            return None
        if self._sessions is not None and self._sessions.get_or_create(f"websocket:{chat_id}").messages:
            return None
        folder = _free_folder(root, project_folder_name(request))
        try:
            folder.mkdir(parents=True)
        except OSError:
            logger.exception("cannot create the project folder {}", folder)
            return None
        return dataclasses.replace(scope, project_path=folder)

    def persist_scope(self, chat_id: str, scope: WorkspaceScope) -> None:
        session = self._scope_session_to_save(chat_id, scope)
        if session is not None:
            self._sessions.save(session)
        if not self._scaffold_is_fresh(scope.project_path):
            self._ensure_scaffold(scope.project_path)

    async def persist_scope_async(self, chat_id: str, scope: WorkspaceScope) -> None:
        """``persist_scope`` for the event loop: disk work runs on worker threads.

        Called for every chat message, so the common case (same scope, project
        already scaffolded) touches neither the session file nor the template
        tree; the rest is awaited off the loop, so the scaffold still exists
        before the turn that needs it starts.
        """
        import asyncio

        session = self._scope_session_to_save(chat_id, scope)
        if session is not None:
            save_async = getattr(self._sessions, "save_async", None)
            if save_async is not None:
                await save_async(session)
            else:
                self._sessions.save(session)
        if self._scaffold_is_fresh(scope.project_path):
            return
        key = self._scaffold_key(scope.project_path)
        locks = self.__dict__.setdefault("_scaffold_locks", {})
        lock = locks.get(key)
        if lock is None:
            lock = locks[key] = asyncio.Lock()
        try:
            async with lock:
                # A concurrent message may have scaffolded while we waited.
                if not self._scaffold_is_fresh(scope.project_path):
                    await asyncio.to_thread(self._ensure_scaffold, scope.project_path)
        finally:
            if not lock.locked() and locks.get(key) is lock:
                del locks[key]

    def _scope_session_to_save(self, chat_id: str, scope: WorkspaceScope) -> Any | None:
        """Stamp *scope* on the chat's session; None when nothing changed."""
        if self._sessions is None:
            return None
        session = self._sessions.get_or_create(f"websocket:{chat_id}")
        scope_meta = scope.metadata()
        if (
            session.metadata.get("webui") is True
            and session.metadata.get(WORKSPACE_SCOPE_METADATA_KEY) == scope_meta
        ):
            return None
        session.metadata["webui"] = True
        session.metadata[WORKSPACE_SCOPE_METADATA_KEY] = scope_meta
        return session

    # Re-run the (idempotent) scaffold at least this often even when the
    # cheap fingerprint below did not change, so a deleted deep file returns.
    _SCAFFOLD_RECHECK_S = 300.0

    @staticmethod
    def _scaffold_key(project_path: Path | str) -> str:
        return os.path.abspath(os.path.expanduser(str(project_path)))

    @staticmethod
    def _scaffold_fingerprint(key: str) -> tuple[int | None, ...]:
        """mtimes of the project root, its ``.navin`` and ``.navin/memory`` dirs.

        Creating or deleting a scaffold entry there changes them (the root
        too: a bare folder that gains files becomes a project); a missing
        directory reads as None, so a removed ``.navin`` re-scaffolds.
        """
        stamps: list[int | None] = []
        for rel in ("", ".navin", os.path.join(".navin", "memory")):
            try:
                stamps.append(os.stat(os.path.join(key, rel)).st_mtime_ns)
            except OSError:
                stamps.append(None)
        return tuple(stamps)

    def _scaffold_is_fresh(self, project_path: Path | str) -> bool:
        cache: dict[str, tuple[tuple[int | None, ...], float]] = self.__dict__.get(
            "_scaffold_cache", {}
        )
        key = self._scaffold_key(project_path)
        hit = cache.get(key)
        if hit is None:
            return False
        fingerprint, at = hit
        if time.monotonic() - at > self._SCAFFOLD_RECHECK_S:
            return False
        return fingerprint == self._scaffold_fingerprint(key)

    def _ensure_scaffold(self, project_path: Path | str) -> None:
        # Opening / binding a project must create the durable scaffold once so
        # Code and every studio share the same brain (SOUL / USER / MEMORY),
        # board, and metadata - not only the default ~/.navin workspace.
        try:
            from navin.utils.helpers import ensure_project_scaffold

            ensure_project_scaffold(project_path, silent=True)
        except Exception:
            logger.exception("project scaffold failed for {}", project_path)
            return
        key = self._scaffold_key(project_path)
        cache = self.__dict__.setdefault("_scaffold_cache", {})
        cache[key] = (self._scaffold_fingerprint(key), time.monotonic())
