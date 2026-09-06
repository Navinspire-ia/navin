"""Language server pool and normalized queries.

Routes a file to the right server from ``servers.json``, keeps one process per
(root, server) alive for reuse, and converts LSP payloads into the same flat
shapes the rest of navin uses - so callers never handle raw protocol dicts.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.lsp.client import LspClient, LspError, uri_to_path

_WORKSPACE_SEED_EXTENSIONS = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
}
_WORKSPACE_SEED_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".navin",
    ".tox",
    "vendor",
}
_MAX_WORKSPACE_SEEDS = 10

_TABLE_PATH = Path(__file__).with_name("servers.json")

# LSP SymbolKind (spec §Symbol Kind) → readable label.
SYMBOL_KINDS = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum-member", 23: "struct", 24: "event",
    25: "operator", 26: "type-parameter",
}

_SEVERITIES = {1: "error", 2: "warning", 3: "info", 4: "hint"}

# LSP CompletionItemKind (spec §CompletionItemKind) → CodeMirror-ish label.
COMPLETION_KINDS = {
    1: "text",
    2: "method",
    3: "function",
    4: "constructor",
    5: "field",
    6: "variable",
    7: "class",
    8: "interface",
    9: "module",
    10: "property",
    11: "unit",
    12: "value",
    13: "enum",
    14: "keyword",
    15: "snippet",
    16: "color",
    17: "file",
    18: "reference",
    19: "folder",
    20: "enum-member",
    21: "constant",
    22: "struct",
    23: "event",
    24: "operator",
    25: "type-parameter",
}


@dataclass(frozen=True, slots=True)
class LspLocation:
    path: str
    line: int
    col: int
    end_line: int
    end_col: int
    name: str = ""
    kind: str = ""
    detail: str = ""

    def render(self) -> str:
        head = f"{self.path}:{self.line}:{self.col}"
        if self.name:
            head += f" {self.name}"
        if self.kind:
            head += f" [{self.kind}]"
        if self.detail:
            head += f" - {self.detail}"
        return head

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "end_line": self.end_line,
            "end_col": self.end_col,
            "name": self.name,
            "kind": self.kind,
            "detail": self.detail,
        }


def _read_table() -> dict[str, dict[str, Any]]:
    from navin.quality.linters import _read_json

    packaged = _read_json(_TABLE_PATH).get("servers", {})
    merged: dict[str, dict[str, Any]] = dict(packaged) if isinstance(packaged, dict) else {}
    with suppress(Exception):
        from navin.config.loader import get_config_path

        user = _read_json(get_config_path().parent / "lsp_servers.json").get("servers")
        if isinstance(user, dict):
            merged.update(user)
    return merged


def servers_for(suffix: str) -> list[tuple[str, dict[str, Any]]]:
    """Servers handling ``suffix``, best priority first."""
    rows = [
        (name, spec)
        for name, spec in _read_table().items()
        if suffix.lower() in [str(e).lower() for e in spec.get("extensions", [])]
    ]
    rows.sort(key=lambda row: int(row[1].get("priority", 50)))
    return rows


def available_servers(root: Path) -> list[dict[str, Any]]:
    """Report which language servers are installed, and what they cover."""
    from navin.quality.linters import tool_argv

    out: list[dict[str, Any]] = []
    for name, spec in sorted(_read_table().items()):
        launch = tool_argv(spec, root)
        out.append(
            {
                "server": name,
                "languages": [str(lang) for lang in spec.get("languages", [])],
                "extensions": [str(e) for e in spec.get("extensions", [])],
                "available": launch is not None,
                "path": " ".join(launch) if launch else "",
                "reason": ""
                if launch
                else f"{(spec.get('binary') or {}).get('name', name)} not installed",
            }
        )
    return out


class LspManager:
    """Keeps one language server per (root, server name) and serves queries."""

    _instances: dict[str, LspManager] = {}
    _instances_lock = threading.Lock()

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        self._clients: dict[str, LspClient] = {}
        self._lock = threading.Lock()

    @classmethod
    def for_root(cls, root: Path | str) -> LspManager:
        resolved = Path(root).expanduser().resolve(strict=False)
        key = str(resolved)
        with cls._instances_lock:
            manager = cls._instances.get(key)
            if manager is None:
                manager = cls(resolved)
                cls._instances[key] = manager
            return manager

    def shutdown(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            with suppress(Exception):
                client.stop()

    @classmethod
    def drop_server(cls, name: str) -> None:
        """Stop every running instance of one server, everywhere.

        Uninstalling only rewrites the table, so without this the process
        started from the deleted files keeps running and answering.
        """
        with cls._instances_lock:
            managers = list(cls._instances.values())
        prefix = f"{name}:"
        for manager in managers:
            with manager._lock:
                keys = [key for key in manager._clients if key.startswith(prefix)]
                clients = [manager._clients.pop(key) for key in keys]
            for client in clients:
                with suppress(Exception):
                    client.stop()

    # -- routing -----------------------------------------------------------

    def _server_root(self, spec: dict[str, Any], target: Path) -> Path:
        markers = [str(m) for m in spec.get("root_markers", [])]
        start = target.parent if target.is_file() else target
        for candidate in [start, *start.parents]:
            if candidate == self.root.parent:
                break
            if any((candidate / marker).exists() for marker in markers):
                return candidate
        return self.root

    def installed_servers_for(self, rel_path: str) -> list[str]:
        """Names of installed servers that can handle this file, best first."""
        from navin.quality.linters import tool_argv

        suffix = (self.root / rel_path).suffix.lower()
        return [
            name
            for name, spec in servers_for(suffix)
            if tool_argv(spec, self.root) is not None
        ]

    def _client_for(
        self, rel_path: str, *, exclude: Iterable[str] = ()
    ) -> tuple[LspClient, str]:
        """Return a started client plus the LSP languageId for this file."""
        from navin.quality.linters import tool_argv

        target = (self.root / rel_path).resolve(strict=False)
        suffix = target.suffix.lower()
        candidates = servers_for(suffix)
        if not candidates:
            raise LspError(f"no language server configured for '{suffix}' files")

        skip = set(exclude)
        problems: list[str] = []
        for name, spec in candidates:
            if name in skip:
                continue
            launch = tool_argv(spec, self.root)
            if launch is None:
                wanted = (spec.get("binary") or {}).get("name", name)
                problems.append(f"{name} ({wanted} not installed)")
                continue
            language_id = str(
                (spec.get("language_ids") or {}).get(suffix, "plaintext")
            )
            server_root = self._server_root(spec, target)
            key = f"{name}:{server_root}"
            with self._lock:
                client = self._clients.get(key)
                if client is not None and not client.alive:
                    self._clients.pop(key, None)
                    client = None
                if client is None:
                    argv = [*launch, *(str(a) for a in spec.get("args", []))]
                    client = LspClient(
                        argv,
                        server_root,
                        name=name,
                        initialization_options=spec.get("settings") or {},
                    )
            if not client.alive:
                try:
                    client.start()
                except LspError as exc:
                    problems.append(f"{name} ({exc})")
                    continue
                with self._lock:
                    self._clients[key] = client
            return client, language_id

        # Every candidate refused to start, so navigation and diagnostics for
        # this language are quietly unavailable. Log only, never a user-facing
        # toast: LSP is ambient decoration for the editor, and a popup about a
        # missing optional binary reads as a product failure. The agent-side
        # lsp tool still gets the full LspError below and can react.
        logger.info(
            "No language server could start for {}: {}",
            suffix or "this file type",
            ", ".join(problems) or "no candidates",
        )
        raise LspError(
            "no usable language server for this file - tried: " + ", ".join(problems)
        )

    def _to_location(self, raw: dict[str, Any], *, name: str = "", kind: str = "") -> LspLocation:
        uri = str(raw.get("uri") or raw.get("targetUri") or "")
        rng = raw.get("range") or raw.get("targetSelectionRange") or raw.get("targetRange") or {}
        start = rng.get("start") or {}
        end = rng.get("end") or start
        absolute = Path(uri_to_path(uri))
        try:
            path = absolute.relative_to(self.root).as_posix()
        except ValueError:
            path = absolute.as_posix()
        return LspLocation(
            path=path,
            # LSP positions are zero-based; navin reports one-based everywhere.
            line=int(start.get("line", 0)) + 1,
            col=int(start.get("character", 0)) + 1,
            end_line=int(end.get("line", 0)) + 1,
            end_col=int(end.get("character", 0)) + 1,
            name=name,
            kind=kind,
        )

    @staticmethod
    def _position(line: int, col: int) -> dict[str, int]:
        return {"line": max(line - 1, 0), "character": max(col - 1, 0)}

    # -- queries -----------------------------------------------------------

    def hover(
        self, rel_path: str, line: int, col: int, *, text: str | None = None
    ) -> str:
        client, language_id = self._client_for(rel_path)
        uri = client.open_document(
            (self.root / rel_path).resolve(strict=False), language_id, text=text
        )
        result = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": self._position(line, col)},
        )
        if not isinstance(result, dict):
            return ""
        contents = result.get("contents")
        return _flatten_hover(contents)

    def completion(
        self,
        rel_path: str,
        line: int,
        col: int,
        *,
        text: str | None = None,
        trigger: str | None = None,
    ) -> list[dict[str, Any]]:
        """Completion items at a caret, flattened for the editor."""
        client, language_id = self._client_for(rel_path)
        uri = client.open_document(
            (self.root / rel_path).resolve(strict=False), language_id, text=text
        )
        params: dict[str, Any] = {
            "textDocument": {"uri": uri},
            "position": self._position(line, col),
        }
        if trigger:
            params["context"] = {
                "triggerKind": 2,
                "triggerCharacter": trigger,
            }
        result = client.request("textDocument/completion", params)
        rows = result.get("items") if isinstance(result, dict) else result
        out: list[dict[str, Any]] = []
        for row in _as_list(rows):
            item = _flatten_completion(row)
            if item:
                out.append(item)
        return out

    def signature_help(
        self,
        rel_path: str,
        line: int,
        col: int,
        *,
        text: str | None = None,
        trigger: str | None = None,
    ) -> dict[str, Any]:
        """Parameter hints at a caret (empty dict when the server has none)."""
        client, language_id = self._client_for(rel_path)
        uri = client.open_document(
            (self.root / rel_path).resolve(strict=False), language_id, text=text
        )
        params: dict[str, Any] = {
            "textDocument": {"uri": uri},
            "position": self._position(line, col),
        }
        if trigger:
            params["context"] = {
                "triggerKind": 2,
                "triggerCharacter": trigger,
                "isRetrigger": False,
            }
        result = client.request("textDocument/signatureHelp", params)
        return _flatten_signature_help(result)

    def code_actions(
        self,
        rel_path: str,
        line: int,
        col: int,
        *,
        end_line: int | None = None,
        end_col: int | None = None,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        """Quick-fixes / refactors covering a caret or a selection."""
        client, language_id = self._client_for(rel_path)
        uri = client.open_document(
            (self.root / rel_path).resolve(strict=False), language_id, text=text
        )
        start = self._position(line, col)
        finish = self._position(end_line or line, end_col or col)
        result = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {"start": start, "end": finish},
                "context": {"diagnostics": []},
            },
        )
        out: list[dict[str, Any]] = []
        for row in _as_list(result):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            if not title:
                continue
            edit = row.get("edit")
            out.append(
                {
                    "title": title,
                    "kind": str(row.get("kind") or ""),
                    "is_preferred": bool(row.get("isPreferred")),
                    "edits": self._workspace_edits(edit) if edit else {},
                    "command": str((row.get("command") or {}).get("command") or "")
                    if isinstance(row.get("command"), dict)
                    else str(row.get("command") or ""),
                }
            )
        return out

    def definition(
        self, rel_path: str, line: int, col: int, *, text: str | None = None
    ) -> list[LspLocation]:
        client, language_id = self._client_for(rel_path)
        uri = client.open_document(
            (self.root / rel_path).resolve(strict=False), language_id, text=text
        )
        result = client.request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": self._position(line, col)},
        )
        return [self._to_location(row) for row in _as_list(result)]

    def references(
        self,
        rel_path: str,
        line: int,
        col: int,
        *,
        include_declaration: bool = True,
        text: str | None = None,
    ) -> list[LspLocation]:
        client, language_id = self._client_for(rel_path)
        uri = client.open_document(
            (self.root / rel_path).resolve(strict=False), language_id, text=text
        )
        result = client.request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": self._position(line, col),
                "context": {"includeDeclaration": include_declaration},
            },
        )
        return [self._to_location(row) for row in _as_list(result)]

    def document_symbols(self, rel_path: str) -> list[LspLocation]:
        client, language_id = self._client_for(rel_path)
        uri = client.open_document((self.root / rel_path).resolve(strict=False), language_id)
        result = client.request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
        )
        out: list[LspLocation] = []

        def walk(rows: list[Any], prefix: str) -> None:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name", ""))
                kind = SYMBOL_KINDS.get(int(row.get("kind", 0) or 0), "")
                qualified = f"{prefix}{name}"
                if "location" in row:  # SymbolInformation
                    location = self._to_location(row["location"], name=qualified, kind=kind)
                else:  # DocumentSymbol
                    rng = row.get("selectionRange") or row.get("range") or {}
                    location = self._to_location(
                        {"uri": uri, "range": rng}, name=qualified, kind=kind
                    )
                out.append(location)
                children = row.get("children")
                if isinstance(children, list):
                    walk(children, f"{qualified}.")

        walk(_as_list(result), "")
        return out

    def workspace_symbols(self, query: str, rel_path_hint: str = "") -> list[LspLocation]:
        # A server must be running to answer; use the hint or any live client.
        client = None
        if rel_path_hint:
            client, _ = self._client_for(rel_path_hint)
        else:
            with self._lock:
                for candidate in self._clients.values():
                    if candidate.alive:
                        client = candidate
                        break
        if client is None:
            raise LspError(
                "no language server is running - pass a file path so the right "
                "server can be started"
            )
        result = client.request("workspace/symbol", {"query": query})
        out: list[LspLocation] = []
        for row in _as_list(result):
            if not isinstance(row, dict):
                continue
            kind = SYMBOL_KINDS.get(int(row.get("kind", 0) or 0), "")
            location = row.get("location")
            if isinstance(location, dict):
                out.append(
                    self._to_location(location, name=str(row.get("name", "")), kind=kind)
                )
        return out

    def rename(
        self,
        rel_path: str,
        line: int,
        col: int,
        new_name: str,
        *,
        exclude: Iterable[str] = (),
        text: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Compute a rename as ``{path: [edits]}`` without applying it."""
        client, language_id = self._client_for(rel_path, exclude=exclude)
        uri = client.open_document(
            (self.root / rel_path).resolve(strict=False), language_id, text=text
        )
        result = client.request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": self._position(line, col),
                "newName": new_name,
            },
        )
        return self._workspace_edits(result)

    def _workspace_edits(self, result: Any) -> dict[str, list[dict[str, Any]]]:
        """Turn a WorkspaceEdit into ``{path: [edits]}`` with one-based positions."""
        if not isinstance(result, dict):
            return {}
        edits: dict[str, list[dict[str, Any]]] = {}

        def add(target_uri: str, rows: list[Any]) -> None:
            if not target_uri:
                return
            absolute = Path(uri_to_path(target_uri))
            try:
                key = absolute.relative_to(self.root).as_posix()
            except ValueError:
                key = absolute.as_posix()
            bucket = edits.setdefault(key, [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rng = row.get("range") or {}
                start = rng.get("start") or {}
                end = rng.get("end") or {}
                bucket.append(
                    {
                        "line": int(start.get("line", 0)) + 1,
                        "col": int(start.get("character", 0)) + 1,
                        "end_line": int(end.get("line", 0)) + 1,
                        "end_col": int(end.get("character", 0)) + 1,
                        "new_text": str(row.get("newText", "")),
                    }
                )

        changes = result.get("changes")
        if isinstance(changes, dict):
            for target_uri, rows in changes.items():
                add(str(target_uri), _as_list(rows))
        document_changes = result.get("documentChanges")
        if isinstance(document_changes, list):
            for entry in document_changes:
                if not isinstance(entry, dict):
                    continue
                document = entry.get("textDocument") or {}
                add(str(document.get("uri", "")), _as_list(entry.get("edits")))
        return edits

    def diagnostics(self, rel_path: str, *, wait_s: float = 4.0) -> list[dict[str, Any]]:
        """Semantic diagnostics published by the server for one file."""
        client, language_id = self._client_for(rel_path)
        target = (self.root / rel_path).resolve(strict=False)
        uri = client.open_document(target, language_id)
        rows = client.diagnostics_for(uri, wait_s=wait_s)
        return self._normalize_diagnostic_rows(rows, rel_path=rel_path, tool=client.name)

    def default_workspace_seeds(self) -> list[str]:
        """A small set of source files used to wake workspace-mode servers."""
        found: list[str] = []
        root = self.root
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name
                for name in dirnames
                if name not in _WORKSPACE_SEED_SKIP_DIRS and not name.startswith(".")
            ]
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in _WORKSPACE_SEED_EXTENSIONS:
                    continue
                rel = (Path(dirpath) / name).relative_to(root).as_posix()
                found.append(rel)
                if len(found) >= _MAX_WORKSPACE_SEEDS:
                    return found
        return found

    def workspace_diagnostics(
        self,
        *,
        seed_rels: list[str] | None = None,
        wait_s: float = 2.5,
    ) -> list[dict[str, Any]]:
        """Collect diagnostics published for any file in this workspace.

        Opens a few seed files so workspace-mode servers (pyright
        ``diagnosticMode: workspace``) analyze the project, waits briefly for
        republish, then flattens every URI the live clients have reported.
        """
        seeds = [rel for rel in (seed_rels or self.default_workspace_seeds()) if rel]
        per_seed_wait = min(max(wait_s, 0.2), 1.5)
        for rel in seeds[:_MAX_WORKSPACE_SEEDS]:
            with suppress(Exception):
                self.diagnostics(rel, wait_s=per_seed_wait)
        # Give workspace-mode servers a beat to republish sibling files.
        if seeds:
            time.sleep(min(0.6, max(0.1, wait_s * 0.25)))

        with self._lock:
            clients = list(self._clients.items())
        out: list[dict[str, Any]] = []
        for _name, client in clients:
            for abs_path, rows in client.all_diagnostics().items():
                try:
                    rel = (
                        Path(abs_path)
                        .resolve(strict=False)
                        .relative_to(self.root)
                        .as_posix()
                    )
                except ValueError:
                    rel = Path(abs_path).name
                normalized = self._normalize_diagnostic_rows(
                    rows, rel_path=rel, tool=client.name
                )
                for row in normalized:
                    row["abs_path"] = str(Path(abs_path))
                    out.append(row)
        return out

    @staticmethod
    def _normalize_diagnostic_rows(
        rows: list[dict[str, Any]],
        *,
        rel_path: str,
        tool: str,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rng = row.get("range") or {}
            start = rng.get("start") or {}
            end = rng.get("end") or start
            out.append(
                {
                    "path": rel_path,
                    "line": int(start.get("line", 0)) + 1,
                    "col": int(start.get("character", 0)) + 1,
                    "end_line": int(end.get("line", 0)) + 1,
                    "end_col": int(end.get("character", 0)) + 1,
                    "severity": _SEVERITIES.get(int(row.get("severity", 1) or 1), "error"),
                    "code": str(row.get("code") or ""),
                    "message": str(row.get("message") or "").strip(),
                    "tool": tool,
                }
            )
        return out


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _flatten_completion(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    label = str(row.get("label") or "").strip()
    if not label:
        return None
    insert = row.get("insertText")
    if not isinstance(insert, str) or not insert:
        text_edit = row.get("textEdit")
        if isinstance(text_edit, dict):
            insert = str(text_edit.get("newText") or "")
    documentation = _flatten_hover(row.get("documentation"))
    kind_raw = row.get("kind")
    try:
        kind = COMPLETION_KINDS.get(int(kind_raw or 0), "text")
    except (TypeError, ValueError):
        kind = "text"
    return {
        "label": label,
        "kind": kind,
        "detail": str(row.get("detail") or ""),
        "insert_text": str(insert or label),
        "documentation": documentation,
    }


def _flatten_signature_help(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    signatures: list[dict[str, Any]] = []
    for row in _as_list(result.get("signatures")):
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        parameters: list[dict[str, str]] = []
        for param in _as_list(row.get("parameters")):
            if not isinstance(param, dict):
                continue
            raw_label = param.get("label")
            if isinstance(raw_label, list) and len(raw_label) >= 2:
                param_label = f"{raw_label[0]}:{raw_label[1]}"
            else:
                param_label = str(raw_label or "")
            parameters.append(
                {
                    "label": param_label,
                    "documentation": _flatten_hover(param.get("documentation")),
                }
            )
        signatures.append(
            {
                "label": label,
                "documentation": _flatten_hover(row.get("documentation")),
                "parameters": parameters,
            }
        )
    if not signatures:
        return {}
    try:
        active_signature = int(result.get("activeSignature") or 0)
    except (TypeError, ValueError):
        active_signature = 0
    try:
        active_parameter = int(result.get("activeParameter") or 0)
    except (TypeError, ValueError):
        active_parameter = 0
    return {
        "active_signature": max(0, min(active_signature, len(signatures) - 1)),
        "active_parameter": max(0, active_parameter),
        "signatures": signatures,
    }


def _flatten_hover(contents: Any) -> str:
    """Hover content comes in three shapes across LSP versions."""
    if contents is None:
        return ""
    if isinstance(contents, str):
        return contents.strip()
    if isinstance(contents, dict):
        return str(contents.get("value") or "").strip()
    if isinstance(contents, list):
        parts = [_flatten_hover(item) for item in contents]
        return "\n\n".join(part for part in parts if part)
    return ""
