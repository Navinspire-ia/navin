# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Minimal synchronous LSP client over stdio.

Implements just enough of the Language Server Protocol to answer the questions
that matter for editing: hover, definition, references, completion, signature
help, code actions, document and workspace symbols, rename edits, and
published diagnostics.

Design constraints that shaped this:

- **Synchronous, thread-confined.** Callers run it in a worker thread
  (``asyncio.to_thread``). A background reader thread demultiplexes responses
  and notifications so request ordering never deadlocks.
- **Fail soft.** A missing or crashed server degrades to an error string; it
  never raises into the agent loop or the web request handler.
- **Explicit document sync.** Files are opened on demand and closed with the
  server, because servers answer position queries only for open documents.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from navin.utils import wsl
from navin.utils.proc import no_window_kwargs

_DEFAULT_TIMEOUT_S = 20.0
_INIT_TIMEOUT_S = 45.0
_SHUTDOWN_TIMEOUT_S = 5.0
_READ_CHUNK = 65536


class LspError(RuntimeError):
    """A language server could not be started or did not answer."""


def path_to_uri(path: Path) -> str:
    """Absolute path -> ``file:`` URI.

    Uses ``as_uri`` rather than quoting the raw string so a Windows path becomes
    ``file:///C:/dir/f.py``. Percent-encoding the native separators instead
    yielded ``file://C%3A%5Cdir%5Cf.py``, which no language server accepts.
    """
    return path.resolve(strict=False).as_uri()


def uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme and parsed.scheme != "file":
        return uri
    if not parsed.scheme:
        return unquote(parsed.path or uri)
    # url2pathname turns "/C:/dir/f.py" back into "C:\dir\f.py" on Windows and
    # leaves POSIX paths alone.
    return url2pathname(parsed.path)


def diagnostics_key(uri: str) -> str:
    """A URI reduced to something both sides spell the same way.

    Diagnostics arrive under whatever URI the server chose to republish, and
    servers built on ``vscode-languageserver`` lowercase the drive letter and
    percent-encode its colon. Comparing the raw strings meant a Windows file
    always looked clean, however many errors it had.
    """
    return os.path.normcase(uri_to_path(uri))


class PathMapping:
    """How the server spells the files it is given.

    The default is the identity: the server runs on this machine and reads the
    same paths navin does. :class:`WslPathMapping` changes the spelling when
    the server lives inside a WSL distribution.
    """

    def to_server_path(self, path: PurePath | str) -> str:
        return str(Path(path).resolve(strict=False))

    def to_server_uri(self, path: PurePath | str) -> str:
        return path_to_uri(Path(path))

    def to_local_path(self, uri: str) -> str:
        return uri_to_path(uri)


class WslPathMapping(PathMapping):
    """Paths for a language server running inside a WSL distribution.

    A project under ``\\\\wsl.localhost\\<distro>`` reaches Navin through the 9P
    redirector, where a stat costs a millisecond and a tree walk minutes; a
    server started on the Windows side paid that on every file it indexed. The
    server is started inside the distribution instead (see
    :mod:`navin.quality.wsl_route`), so it is spoken to in Linux paths: the
    project's UNC paths become ``/home/...`` on the way out, Windows drive paths
    become ``/mnt/<drive>/...``, and what comes back is turned into the path
    Navin can open.
    """

    _MOUNT = re.compile(r"^/mnt/([a-zA-Z])(/.*)?$")

    def __init__(self, distro: str) -> None:
        self.distro = distro

    def to_server_path(self, path: PurePath | str) -> str:
        text = str(path)
        inside = wsl.parse_unc(text)
        if inside is not None:
            return inside.posix
        mounted = wsl.drive_to_mount(text)
        if mounted is not None:
            return mounted
        return text.replace("\\", "/")

    def to_server_uri(self, path: PurePath | str) -> str:
        server = self.to_server_path(path)
        if not server.startswith("/"):
            server = "/" + server
        return PurePosixPath(server).as_uri()

    def to_local_path(self, uri: str) -> str:
        parsed = urlparse(uri)
        if parsed.scheme and parsed.scheme != "file":
            return uri
        posix = unquote(parsed.path or uri)
        mounted = self._MOUNT.match(posix)
        if mounted:
            tail = (mounted.group(2) or "/").replace("/", "\\")
            return f"{mounted.group(1).upper()}:{tail}"
        return wsl.to_unc(self.distro, posix)


@dataclass(slots=True)
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: dict[str, Any] | None = None


class LspClient:
    """One language server process, driven over stdio."""

    def __init__(
        self,
        argv: list[str],
        root: Path,
        *,
        name: str = "lsp",
        initialization_options: dict[str, Any] | None = None,
        mapping: PathMapping | None = None,
        launch_cwd: str | None = None,
    ) -> None:
        self.name = name
        self.root = root.resolve(strict=False)
        self._argv = argv
        self._init_options = initialization_options or {}
        # ``mapping`` spells paths the way the server sees them; ``launch_cwd``
        # is where the process itself starts when the root is not a folder a
        # process can start in (a UNC path for wsl.exe).
        self._mapping = mapping or PathMapping()
        self._launch_cwd = launch_cwd
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._open_docs: dict[str, int] = {}
        self._capabilities: dict[str, Any] = {}
        self._alive = False
        self._stderr_tail: list[str] = []

    # -- lifecycle ---------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._alive and self._process is not None and self._process.poll() is None

    @property
    def returncode(self) -> int | None:
        """The server's exit code once it has ended, else None."""
        process = self._process
        return process.poll() if process is not None else None

    def local_path(self, uri: str) -> str:
        """The path Navin can open for a URI the server sent."""
        return self._mapping.to_local_path(uri)

    def _key(self, uri: str) -> str:
        return os.path.normcase(self._mapping.to_local_path(uri))

    def start(self) -> None:
        if self.alive:
            return
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        try:
            self._process = subprocess.Popen(  # noqa: S603
                self._argv,
                cwd=self._launch_cwd or str(self.root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LspError(f"cannot start {self.name}: {exc}") from exc

        self._alive = True
        self._reader = threading.Thread(
            target=self._read_loop, name=f"lsp-{self.name}", daemon=True
        )
        self._reader.start()
        threading.Thread(
            target=self._drain_stderr, name=f"lsp-{self.name}-err", daemon=True
        ).start()
        self._initialize()

    def _initialize(self) -> None:
        root_uri = self._mapping.to_server_uri(self.root)
        params: dict[str, Any] = {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "rootPath": self._mapping.to_server_path(self.root),
            "workspaceFolders": [{"uri": root_uri, "name": self.root.name}],
            "initializationOptions": self._init_options,
            "capabilities": {
                "textDocument": {
                    "synchronization": {"didSave": True, "dynamicRegistration": False},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "completion": {
                        "completionItem": {
                            "snippetSupport": False,
                            "documentationFormat": ["markdown", "plaintext"],
                        }
                    },
                    "signatureHelp": {
                        "signatureInformation": {
                            "documentationFormat": ["markdown", "plaintext"],
                            "parameterInformation": {"labelOffsetSupport": True},
                        }
                    },
                    "codeAction": {
                        "codeActionLiteralSupport": {
                            "codeActionKind": {
                                "valueSet": [
                                    "",
                                    "quickfix",
                                    "refactor",
                                    "refactor.extract",
                                    "refactor.inline",
                                    "refactor.rewrite",
                                    "source",
                                    "source.organizeImports",
                                ]
                            }
                        },
                        "resolveSupport": {"properties": ["edit"]},
                    },
                    "definition": {"linkSupport": True},
                    "references": {"dynamicRegistration": False},
                    "documentSymbol": {
                        "hierarchicalDocumentSymbolSupport": True,
                        "symbolKind": {"valueSet": list(range(1, 27))},
                    },
                    "rename": {"prepareSupport": False},
                    "publishDiagnostics": {"relatedInformation": False},
                },
                "workspace": {
                    "symbol": {"symbolKind": {"valueSet": list(range(1, 27))}},
                    "workspaceFolders": True,
                    "configuration": True,
                },
            },
        }
        result = self.request("initialize", params, timeout=_INIT_TIMEOUT_S)
        if isinstance(result, dict):
            caps = result.get("capabilities")
            if isinstance(caps, dict):
                self._capabilities = caps
        self.notify("initialized", {})

    def stop(self) -> None:
        if self._process is None:
            return
        self._alive = False
        try:
            self.request("shutdown", None, timeout=_SHUTDOWN_TIMEOUT_S)
            self.notify("exit", None)
        except Exception:
            pass
        try:
            self._process.terminate()
            self._process.wait(timeout=_SHUTDOWN_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            with_suppress_kill(self._process)
        self._process = None
        self._open_docs.clear()

    def kill(self) -> None:
        """End the server process now, without the protocol handshake.

        For process exit: a graceful ``stop`` waits up to twice the shutdown
        timeout per server, and a server that no longer reads its stdin would
        hold the gateway's exit for that long.
        """
        process = self._process
        self._process = None
        self._alive = False
        if process is None or process.poll() is not None:
            return
        with suppress(OSError, subprocess.SubprocessError):
            process.terminate()
        try:
            process.wait(timeout=1.0)
        except (OSError, subprocess.SubprocessError):
            with_suppress_kill(process)

    # -- transport ---------------------------------------------------------

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise LspError(f"{self.name} is not running")
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        try:
            process.stdin.write(header + body)
            process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._alive = False
            raise LspError(f"{self.name} stdin closed: {exc}") from exc

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        stream = process.stdout
        buffer = b""
        while True:
            if process.poll() is not None and not buffer:
                break
            try:
                chunk = stream.read1(_READ_CHUNK) if hasattr(stream, "read1") else stream.read(1)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buffer += chunk
            while True:
                header_end = buffer.find(b"\r\n\r\n")
                if header_end < 0:
                    break
                header_text = buffer[:header_end].decode("utf-8", errors="replace")
                length = 0
                for line in header_text.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        try:
                            length = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            length = 0
                body_start = header_end + 4
                if len(buffer) < body_start + length:
                    break
                body = buffer[body_start : body_start + length]
                buffer = buffer[body_start + length :]
                try:
                    message = json.loads(body.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                self._dispatch(message)

        self._alive = False
        # Unblock every waiter so callers get an error instead of hanging.
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            entry.error = {"message": f"{self.name} exited"}
            entry.event.set()

    def _dispatch(self, message: dict[str, Any]) -> None:
        message_id = message.get("id")
        if message_id is not None and ("result" in message or "error" in message):
            with self._lock:
                entry = self._pending.pop(int(message_id), None)
            if entry is not None:
                entry.result = message.get("result")
                entry.error = message.get("error")
                entry.event.set()
            return

        method = message.get("method")
        if method == "textDocument/publishDiagnostics":
            params = message.get("params") or {}
            uri = str(params.get("uri", ""))
            rows = params.get("diagnostics")
            self._diagnostics[self._key(uri)] = rows if isinstance(rows, list) else []
            return
        if message_id is not None:
            # Server-to-client request: answer minimally so it does not stall.
            reply: Any = None
            if method == "workspace/configuration":
                items = (message.get("params") or {}).get("items") or []
                reply = [self._configuration_for(item) for item in items]
            elif method in {"client/registerCapability", "client/unregisterCapability"}:
                reply = None
            elif method == "workspace/workspaceFolders":
                reply = [{"uri": self._mapping.to_server_uri(self.root), "name": self.root.name}]
            try:
                self._write({"jsonrpc": "2.0", "id": message_id, "result": reply})
            except LspError:
                pass

    def _configuration_for(self, item: Any) -> Any:
        """The declared settings for one requested configuration section.

        Servers pull their settings instead of reading initializationOptions, and
        answering ``{}`` leaves them on their defaults. For pyright that default is
        ``openFilesOnly``, which makes it answer a rename with only the file it has
        open - silently omitting the callers it never looked at.
        """
        section = (item or {}).get("section") if isinstance(item, dict) else None
        if not isinstance(section, str) or not section:
            return self._init_options
        value: Any = self._init_options
        for key in section.split("."):
            if not isinstance(value, dict) or key not in value:
                return {}
            value = value[key]
        return value

    def request(
        self, method: str, params: Any, *, timeout: float = _DEFAULT_TIMEOUT_S
    ) -> Any:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            entry = _Pending()
            self._pending[request_id] = entry
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)
        if not entry.event.wait(timeout):
            with self._lock:
                self._pending.pop(request_id, None)
            raise LspError(f"{self.name} timed out on {method} after {timeout:g}s")
        if entry.error:
            raise LspError(f"{self.name} error on {method}: {entry.error.get('message')}")
        return entry.result

    def notify(self, method: str, params: Any) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw in iter(process.stderr.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                self._stderr_tail.append(text)
                del self._stderr_tail[:-40]

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-10:])

    # -- documents ---------------------------------------------------------

    def open_document(
        self,
        path: Path,
        language_id: str,
        *,
        text: str | None = None,
    ) -> str:
        """Open or refresh a document and return its URI.

        ``text`` is the editor's current buffer when supplied. This keeps LSP
        navigation truthful before Ctrl+S instead of silently replacing the
        buffer with the older file on disk.
        """
        uri = self._mapping.to_server_uri(path)
        if text is None:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise LspError(f"cannot read {path}: {exc}") from exc
        # Drop the previous batch so a later read waits for diagnostics that
        # describe this content, not the version we just replaced.
        self._diagnostics.pop(self._key(uri), None)
        version = self._open_docs.get(uri)
        if version is None:
            self._open_docs[uri] = 1
            self.notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": language_id,
                        "version": 1,
                        "text": text,
                    }
                },
            )
        else:
            self._open_docs[uri] = version + 1
            self.notify(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version + 1},
                    "contentChanges": [{"text": text}],
                },
            )
        return uri

    def diagnostics_for(self, uri: str, *, wait_s: float = 3.0) -> list[dict[str, Any]]:
        """Diagnostics published for ``uri``, waiting briefly for the first batch."""
        key = self._key(uri)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if key in self._diagnostics:
                break
            time.sleep(0.05)
        return self._diagnostics.get(key, [])

    def all_diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        """Snapshot of every path the server has published diagnostics for.

        Keys are filesystem paths (see :func:`diagnostics_key`). Workspace-mode
        servers such as pyright republish errors for files the editor never
        opened; the Problems panel reads this map to surface them.
        """
        return {key: list(rows) for key, rows in self._diagnostics.items()}

    def capability(self, name: str) -> Any:
        return self._capabilities.get(name)


def with_suppress_kill(process: subprocess.Popen[bytes]) -> None:
    try:
        process.kill()
    except (OSError, subprocess.SubprocessError):
        pass
