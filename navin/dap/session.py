"""One active debug session (Python/debugpy, Node/js-debug, Go/delve, LLDB)."""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from navin.dap.client import DapClient, DapError

_NODE_EXTENSIONS = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"}
_PYTHON_EXTENSIONS = {".py", ".pyw"}
_GO_EXTENSIONS = {".go"}
_LLDB_EXTENSIONS = {
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".hxx",
    ".hh",
    ".m",
    ".mm",
}
_SUPPORTED_RUNTIMES = frozenset({"python", "node", "go", "lldb"})


class DebugSessionError(ValueError):
    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def detect_runtime(
    program: str | None = None,
    *,
    runtime: str | None = None,
    pytest_node: str | None = None,
) -> str:
    """Resolve a debug runtime from an explicit value or file extension."""
    explicit = (runtime or "").strip().lower()
    if explicit in _SUPPORTED_RUNTIMES:
        return explicit
    if explicit:
        raise DebugSessionError(
            f"unsupported debug runtime: {explicit} "
            "(use python, node, go, or lldb)",
            status=400,
        )
    if pytest_node:
        return "python"
    suffix = Path((program or "").strip()).suffix.lower()
    if suffix in _NODE_EXTENSIONS:
        return "node"
    if suffix in _GO_EXTENSIONS:
        return "go"
    if suffix in _LLDB_EXTENSIONS:
        return "lldb"
    if suffix in _PYTHON_EXTENSIONS or not suffix:
        return "python"
    raise DebugSessionError(
        f"cannot detect debug runtime for {program!r} - "
        "pass runtime=python|node|go|lldb",
        status=400,
    )


def runtime_for_path(path: str | None) -> str | None:
    """Return the debug runtime for ``path``, or ``None`` if unsupported."""
    suffix = Path((path or "").strip()).suffix.lower()
    if not suffix:
        return None
    if suffix in _PYTHON_EXTENSIONS:
        return "python"
    if suffix in _NODE_EXTENSIONS:
        return "node"
    if suffix in _GO_EXTENSIONS:
        return "go"
    if suffix in _LLDB_EXTENSIONS:
        return "lldb"
    return None


def resolve_adapter_argv(
    *,
    fake_adapter: str | None = None,
    runtime: str = "python",
) -> list[str]:
    """Argv for the DAP adapter process.

    - Python: ``python -m debugpy.adapter``
    - Node: ``NAVIN_JS_DEBUG_ADAPTER`` / ``@vscode/js-debug`` dapDebugServer.js
    - Go: ``dlv dap`` (Delve)
    - Rust/C/C++: ``lldb-dap`` or ``codelldb`` (``NAVIN_LLDB_DAP`` override)

    Tests may inject a fake adapter script via ``fake_adapter``.
    """
    if fake_adapter:
        return [sys.executable, fake_adapter]

    kind = (runtime or "python").strip().lower() or "python"
    if kind == "node":
        return _resolve_node_adapter_argv()
    if kind == "go":
        return _resolve_go_adapter_argv()
    if kind == "lldb":
        return _resolve_lldb_adapter_argv()
    if kind != "python":
        raise DebugSessionError(
            f"unsupported debug runtime: {kind}",
            status=400,
        )

    # Probe import without starting the adapter.
    import subprocess

    from navin.utils.proc import no_window_kwargs

    probe = shutil.which(sys.executable) and sys.executable
    if not probe:
        raise DebugSessionError("python interpreter unavailable", status=503)
    check = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import debugpy"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        **no_window_kwargs(),
    )
    if check.returncode != 0:
        raise DebugSessionError(
            "debugpy is not installed - pip install debugpy to enable Python debugging",
            status=503,
        )
    return [sys.executable, "-m", "debugpy.adapter"]


def _candidate_js_debug_servers() -> list[Path]:
    """Prod discovery order for js-debug's dapDebugServer.js."""
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        found.append(resolved)

    env = (os.environ.get("NAVIN_JS_DEBUG_ADAPTER") or "").strip()
    if env:
        _add(Path(env))

    # Local / global npm installs of @vscode/js-debug.
    for base in (
        Path.cwd() / "node_modules" / "@vscode" / "js-debug",
        Path.home() / ".navin" / "js-debug",
    ):
        _add(base / "src" / "dapDebugServer.js")
        _add(base / "dist" / "src" / "dapDebugServer.js")

    try:
        import subprocess

        from navin.utils.proc import no_window_kwargs

        npm = shutil.which("npm")
        if npm:
            probe = subprocess.run(  # noqa: S603
                [npm, "root", "-g"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                **no_window_kwargs(),
            )
            if probe.returncode == 0:
                root = Path((probe.stdout or "").strip())
                if root.is_dir():
                    _add(root / "@vscode" / "js-debug" / "src" / "dapDebugServer.js")
                    _add(
                        root
                        / "@vscode"
                        / "js-debug"
                        / "dist"
                        / "src"
                        / "dapDebugServer.js"
                    )
    except Exception:
        pass

    # Common VS Code / Cursor extension layouts (js-debug bundled).
    for parent in (
        Path.home() / ".vscode" / "extensions",
        Path.home() / ".cursor" / "extensions",
        Path.home() / ".vscode-server" / "extensions",
    ):
        if not parent.is_dir():
            continue
        try:
            for child in sorted(parent.glob("ms-vscode.js-debug-*")):
                _add(child / "src" / "dapDebugServer.js")
                _add(child / "dist" / "src" / "dapDebugServer.js")
        except OSError:
            continue

    return found


def _resolve_node_adapter_argv() -> list[str]:
    node = shutil.which("node")
    if not node:
        raise DebugSessionError(
            "node is not installed - install Node.js to debug .js/.ts files",
            status=503,
        )
    for candidate in _candidate_js_debug_servers():
        if candidate.is_file():
            return [node, str(candidate)]
    env = (os.environ.get("NAVIN_JS_DEBUG_ADAPTER") or "").strip()
    if env:
        raise DebugSessionError(
            f"NAVIN_JS_DEBUG_ADAPTER is set but not a file: {env}",
            status=503,
        )
    raise DebugSessionError(
        "Node debug adapter not found. Install @vscode/js-debug "
        "(npm i -g @vscode/js-debug) or set NAVIN_JS_DEBUG_ADAPTER to "
        "dapDebugServer.js",
        status=503,
    )


def _resolve_go_adapter_argv() -> list[str]:
    """Delve DAP mode: ``dlv dap`` speaks the Debug Adapter Protocol on stdio."""
    env = (os.environ.get("NAVIN_DELVE") or "").strip()
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return [str(path.resolve(strict=False)), "dap"]
        raise DebugSessionError(
            f"NAVIN_DELVE is set but not a file: {env}",
            status=503,
        )
    dlv = shutil.which("dlv")
    if not dlv:
        raise DebugSessionError(
            "delve (dlv) is not installed - install Delve "
            "(go install github.com/go-delve/delve/cmd/dlv@latest) to debug Go",
            status=503,
        )
    return [dlv, "dap"]


def _candidate_lldb_dap() -> list[Path]:
    """Discovery order for lldb-dap / CodeLLDB adapter binaries."""
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        found.append(resolved)

    env = (os.environ.get("NAVIN_LLDB_DAP") or "").strip()
    if env:
        _add(Path(env))

    for name in ("lldb-dap", "lldb-vscode", "codelldb"):
        which = shutil.which(name)
        if which:
            _add(Path(which))

    # CodeLLDB shipped inside VS Code / Cursor extensions.
    for parent in (
        Path.home() / ".vscode" / "extensions",
        Path.home() / ".cursor" / "extensions",
        Path.home() / ".vscode-server" / "extensions",
    ):
        if not parent.is_dir():
            continue
        try:
            for child in sorted(parent.glob("vadimcn.vscode-lldb-*")):
                _add(child / "adapter" / "codelldb")
                _add(child / "lldb" / "bin" / "lldb-dap")
        except OSError:
            continue
    return found


def _resolve_lldb_adapter_argv() -> list[str]:
    for candidate in _candidate_lldb_dap():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
        if candidate.is_file():
            # Still usable if the platform marks it executable via shebang/node.
            return [str(candidate)]
    env = (os.environ.get("NAVIN_LLDB_DAP") or "").strip()
    if env:
        raise DebugSessionError(
            f"NAVIN_LLDB_DAP is set but not a usable file: {env}",
            status=503,
        )
    raise DebugSessionError(
        "LLDB debug adapter not found. Install lldb-dap (LLVM) or CodeLLDB, "
        "or set NAVIN_LLDB_DAP to the adapter binary",
        status=503,
    )


class DebugSession:
    """Workbench-facing session: breakpoints, launch, step, stack, variables."""

    def __init__(self, root: Path, *, fake_adapter: str | None = None) -> None:
        self.root = root.resolve(strict=False)
        self._fake_adapter = fake_adapter
        self._client: DapClient | None = None
        self._lock = threading.RLock()
        self._breakpoints: dict[str, list[int]] = {}
        self._state = "idle"
        self._thread_id: int | None = None
        self._stopped_reason: str | None = None
        self._stopped_frame: dict[str, Any] | None = None
        self._console: list[str] = []
        self._program: str | None = None
        self._seq_events = 0

    # -- public state ------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "program": self._program,
                "thread_id": self._thread_id,
                "stopped_reason": self._stopped_reason,
                "stopped_frame": self._stopped_frame,
                "breakpoints": [
                    {"path": path, "lines": sorted(lines)}
                    for path, lines in sorted(self._breakpoints.items())
                    if lines
                ],
                "console": list(self._console[-80:]),
                "alive": bool(self._client and self._client.alive),
            }

    def set_breakpoints(self, path: str, lines: list[int]) -> dict[str, Any]:
        rel = self._rel(path)
        cleaned = sorted({int(line) for line in lines if int(line) > 0})
        with self._lock:
            if cleaned:
                self._breakpoints[rel] = cleaned
            else:
                self._breakpoints.pop(rel, None)
            client = self._client
            state = self._state
        if client and client.alive and state not in {"idle", "terminated"}:
            self._send_breakpoints(client, rel, cleaned)
        return {"path": rel, "lines": cleaned}

    def start(
        self,
        *,
        program: str | None = None,
        pytest_node: str | None = None,
        args: list[str] | None = None,
        runtime: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._client and self._client.alive and self._state not in {
                "idle",
                "terminated",
            }:
                raise DebugSessionError("a debug session is already running", status=409)

        kind = detect_runtime(program, runtime=runtime, pytest_node=pytest_node)
        if pytest_node:
            if kind != "python":
                raise DebugSessionError(
                    "pytest debug requires runtime=python",
                    status=400,
                )
            program_rel = None
            launch_args = self._pytest_launch(pytest_node)
            label = f"pytest {pytest_node}"
            adapter_id = "python"
            client_name = "debugpy"
        else:
            if not program:
                raise DebugSessionError("program or pytest node is required", status=400)
            program_rel = self._rel(program)
            abs_program = (self.root / program_rel).resolve(strict=False)
            if not abs_program.is_file():
                raise DebugSessionError(f"program not found: {program_rel}", status=404)
            if kind == "node":
                launch_args = {
                    "name": "Node: Current File",
                    "type": "pwa-node",
                    "request": "launch",
                    "program": str(abs_program),
                    "cwd": str(self.root),
                    "console": "internalConsole",
                    "args": [str(a) for a in (args or [])],
                }
                adapter_id = "pwa-node"
                client_name = "js-debug"
            elif kind == "go":
                launch_args = {
                    "name": "Go: Current File",
                    "type": "go",
                    "request": "launch",
                    "mode": "debug",
                    "program": str(abs_program),
                    "cwd": str(self.root),
                    "args": [str(a) for a in (args or [])],
                }
                adapter_id = "go"
                client_name = "delve"
            elif kind == "lldb":
                launch_args = {
                    "name": "LLDB: Current File",
                    "type": "lldb",
                    "request": "launch",
                    "program": str(abs_program),
                    "cwd": str(self.root),
                    "args": [str(a) for a in (args or [])],
                }
                adapter_id = "lldb"
                client_name = "lldb-dap"
            else:
                launch_args = {
                    "name": "Python: Current File",
                    "type": "python",
                    "request": "launch",
                    "program": str(abs_program),
                    "cwd": str(self.root),
                    "console": "internalConsole",
                    "justMyCode": True,
                    "args": [str(a) for a in (args or [])],
                }
                adapter_id = "python"
                client_name = "debugpy"
            label = program_rel

        argv = resolve_adapter_argv(
            fake_adapter=self._fake_adapter,
            runtime=kind,
        )
        client = DapClient(argv, self.root, name=client_name, on_event=self._on_event)
        try:
            client.start()
            client.request(
                "initialize",
                {
                    "clientID": "navin",
                    "clientName": "Navin Code",
                    "adapterID": adapter_id,
                    "pathFormat": "path",
                    "linesStartAt1": True,
                    "columnsStartAt1": True,
                    "supportsVariableType": True,
                    "supportsVariablePaging": False,
                    "supportsRunInTerminalRequest": False,
                },
                timeout=30,
            )
            # Adapter sends initialized event; some fakes reply immediately.
            time.sleep(0.05)
            with self._lock:
                self._client = client
                self._state = "starting"
                self._program = label
                self._stopped_frame = None
                self._stopped_reason = None
                self._console.clear()
                bps = dict(self._breakpoints)

            for rel, lines in bps.items():
                self._send_breakpoints(client, rel, lines)

            # VS Code order: launch prepares the debuggee; configurationDone
            # releases it to run (and hit breakpoints).
            client.request("launch", launch_args, timeout=30)
            try:
                client.request("configurationDone", {})
            except DapError:
                # Some adapters treat configurationDone as optional.
                pass
            with self._lock:
                if self._state == "starting":
                    self._state = "running"
        except DapError as exc:
            detail = client.stderr_tail()
            client.stop()
            with self._lock:
                self._client = None
                self._state = "idle"
            raise DebugSessionError(
                f"debug start failed: {exc}" + (f" ({detail})" if detail else ""),
                status=409,
            ) from exc

        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            client = self._client
            self._client = None
            self._state = "terminated"
            self._thread_id = None
        if client is not None:
            try:
                if client.alive:
                    client.request("disconnect", {"terminateDebuggee": True}, timeout=5)
            except Exception:
                pass
            client.stop()
        with self._lock:
            self._state = "idle"
            self._stopped_frame = None
            self._stopped_reason = None
        return self.snapshot()

    def continue_(self) -> dict[str, Any]:
        return self._thread_command("continue")

    def step(self, kind: str = "over") -> dict[str, Any]:
        mapping = {
            "over": "next",
            "into": "stepIn",
            "out": "stepOut",
            "next": "next",
            "stepIn": "stepIn",
            "stepOut": "stepOut",
        }
        command = mapping.get((kind or "over").strip(), "next")
        return self._thread_command(command)

    def stack(self) -> dict[str, Any]:
        client, thread_id = self._require_stopped()
        body = client.request(
            "stackTrace",
            {"threadId": thread_id, "startFrame": 0, "levels": 40},
        ) or {}
        frames = []
        for frame in body.get("stackFrames") or []:
            if not isinstance(frame, dict):
                continue
            source = frame.get("source") if isinstance(frame.get("source"), dict) else {}
            path = str(source.get("path") or "")
            try:
                rel = Path(path).resolve(strict=False).relative_to(self.root).as_posix()
            except Exception:
                rel = path
            frames.append(
                {
                    "id": frame.get("id"),
                    "name": frame.get("name") or "?",
                    "path": rel,
                    "line": frame.get("line") or 0,
                    "column": frame.get("column") or 0,
                }
            )
        if frames:
            with self._lock:
                self._stopped_frame = frames[0]
        return {"frames": frames, **self.snapshot()}

    def scopes(self, frame_id: int) -> dict[str, Any]:
        client, _thread_id = self._require_stopped()
        body = client.request("scopes", {"frameId": int(frame_id)}) or {}
        scopes = []
        for scope in body.get("scopes") or []:
            if isinstance(scope, dict):
                scopes.append(
                    {
                        "name": scope.get("name") or "scope",
                        "variablesReference": scope.get("variablesReference") or 0,
                        "expensive": bool(scope.get("expensive")),
                    }
                )
        return {"scopes": scopes}

    def variables(self, variables_reference: int) -> dict[str, Any]:
        client, _thread_id = self._require_stopped()
        body = client.request(
            "variables",
            {"variablesReference": int(variables_reference)},
        ) or {}
        rows = []
        for var in body.get("variables") or []:
            if isinstance(var, dict):
                rows.append(
                    {
                        "name": var.get("name") or "",
                        "value": str(var.get("value") or ""),
                        "type": var.get("type"),
                        "variablesReference": var.get("variablesReference") or 0,
                    }
                )
        return {"variables": rows}

    def evaluate(self, expression: str, *, frame_id: int | None = None) -> dict[str, Any]:
        client, _thread_id = self._require_stopped()
        expr = (expression or "").strip()
        if not expr:
            raise DebugSessionError("expression is required", status=400)
        args: dict[str, Any] = {
            "expression": expr,
            "context": "repl",
        }
        if frame_id is not None:
            args["frameId"] = int(frame_id)
        elif self._stopped_frame and self._stopped_frame.get("id") is not None:
            args["frameId"] = int(self._stopped_frame["id"])
        body = client.request("evaluate", args) or {}
        result = str(body.get("result") or "")
        with self._lock:
            self._console.append(f"> {expr}")
            self._console.append(result)
        return {"result": result, "type": body.get("type"), **self.snapshot()}

    # -- internals ---------------------------------------------------------

    def _pytest_launch(self, node: str) -> dict[str, Any]:
        cleaned = (node or "").strip()
        if not cleaned:
            raise DebugSessionError("pytest node is required", status=400)
        return {
            "name": "Python: Pytest",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "cwd": str(self.root),
            "console": "internalConsole",
            "justMyCode": True,
            "args": [cleaned, "-q"],
        }

    def _rel(self, raw: str) -> str:
        text = (raw or "").strip().replace("\\", "/")
        if not text:
            raise DebugSessionError("missing path", status=400)
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                return candidate.resolve(strict=False).relative_to(self.root).as_posix()
            except ValueError as exc:
                raise DebugSessionError("path outside project", status=403) from exc
        target = (self.root / text).resolve(strict=False)
        try:
            return target.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise DebugSessionError("path outside project", status=403) from exc

    def _send_breakpoints(self, client: DapClient, rel: str, lines: list[int]) -> None:
        source_path = str((self.root / rel).resolve(strict=False))
        try:
            client.request(
                "setBreakpoints",
                {
                    "source": {"path": source_path, "name": Path(rel).name},
                    "breakpoints": [{"line": line} for line in lines],
                    "sourceModified": False,
                },
            )
        except DapError:
            # Adapter may not accept BPs until after launch; keep local store.
            pass

    def _thread_command(self, command: str) -> dict[str, Any]:
        client, thread_id = self._require_stopped()
        with self._lock:
            self._state = "running"
            self._stopped_reason = None
        try:
            client.request(command, {"threadId": thread_id})
        except DapError as exc:
            raise DebugSessionError(str(exc), status=409) from exc
        return self.snapshot()

    def _require_stopped(self) -> tuple[DapClient, int]:
        with self._lock:
            client = self._client
            state = self._state
            thread_id = self._thread_id
        if client is None or not client.alive:
            raise DebugSessionError("no active debug session", status=409)
        if state != "stopped" or thread_id is None:
            raise DebugSessionError("debuggee is not stopped", status=409)
        return client, thread_id

    def _on_event(self, event: str, body: dict[str, Any]) -> None:
        with self._lock:
            self._seq_events += 1
            if event == "output":
                category = str(body.get("category") or "console")
                output = str(body.get("output") or "")
                if output:
                    prefix = "" if category == "stdout" else f"[{category}] "
                    for line in output.splitlines() or [output]:
                        self._console.append(prefix + line)
                        if len(self._console) > 200:
                            self._console = self._console[-200:]
            elif event == "stopped":
                self._state = "stopped"
                self._thread_id = int(body.get("threadId") or 0) or self._thread_id
                self._stopped_reason = str(body.get("reason") or "stopped")
            elif event == "continued":
                self._state = "running"
                self._stopped_reason = None
                self._stopped_frame = None
            elif event == "terminated" or event == "exited":
                self._state = "terminated"
                self._thread_id = None
                self._stopped_frame = None
            elif event == "thread" and body.get("reason") == "started":
                tid = body.get("threadId")
                if tid is not None and self._thread_id is None:
                    self._thread_id = int(tid)

        if event == "stopped":
            # Best-effort top frame for the UI highlight.
            try:
                self.stack()
            except Exception:
                pass
