"""Work out, without being told, whether an extension carries a usable server.

A curated table does not scale to a registry, and asking a user for the path of
a server inside a zip is asking them to do the machine's job. Everything needed
is already in the archive: ``contributes.languages`` in the manifest says which
files the extension claims, and the server is a file whose name follows one of
a handful of conventions.

Guessing is not enough though, because most extensions carry no server at all
and a wrong guess would be registered as a working one. So a candidate is only
accepted after it has answered an LSP ``initialize`` over stdio. Installing is
therefore a proof rather than a promise: what lands in the table has run.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

_REGISTRY = "https://open-vsx.org"

# A handshake is a process spawn plus one round trip. Servers that need to index
# first still answer initialize before indexing, so this is generous.
_HANDSHAKE_TIMEOUT_S = 12.0

# Each candidate costs a process spawn, so the ranking has to be good enough
# that the answer is usually first. This caps the wait when it is not.
_MAX_CANDIDATES = 8

# A language server bundled for distribution is a large file; the extension's
# own client code and its helper scripts are not. Below this a candidate whose
# name says nothing is not worth a process spawn.
_BUNDLE_MIN_BYTES = 200 * 1024
_MAX_BUNDLES = 3

# A server built for the browser talks over a worker port, not stdio, and would
# hang the handshake. Test fixtures and bundler leftovers are noise.
_EXCLUDED_PARTS = ("test", "tests", "__mocks__", "browser", "web", "webpack")

_SERVER_NAME_RE = re.compile(
    r"(language[-_.]?server|languageserver|[-_.]lsp$|^lsp$|^server$|[-_.]server$)",
    re.IGNORECASE,
)
_NODE_SUFFIXES = (".js", ".mjs", ".cjs")

# The executable bit does not survive a zip, so a compiled server is recognised
# by what it is rather than by its permissions or its name: ELF, Mach-O (both
# byte orders, plus fat binaries) and PE.
_EXECUTABLE_MAGIC = (
    b"\x7fELF",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"MZ",
)

# Servers disagree on how to be told to speak stdio, and the ones that need a
# subcommand are common enough to be worth a second attempt.
_NODE_ARGS = (["--stdio"], [])
_NATIVE_ARGS = ([], ["lsp", "stdio"], ["--stdio"], ["lsp"])

# Probing is bounded overall, not just per candidate: a handful of servers that
# each hang for the full timeout must not turn one install into several minutes.
_PROBE_BUDGET_S = 100.0

# Extensions for languages VS Code already knows do not redeclare them, so the
# manifest says "onLanguage:php" and nothing else. Without this, every such
# extension looks like it handles no files at all.
_BUILTIN_LANGUAGE_SUFFIXES: dict[str, list[str]] = {
    "bat": [".bat", ".cmd"],
    "c": [".c", ".h"],
    "clojure": [".clj", ".cljs", ".edn"],
    "coffeescript": [".coffee"],
    "cpp": [".cpp", ".cc", ".cxx", ".hpp", ".hh"],
    "csharp": [".cs"],
    "css": [".css"],
    "dart": [".dart"],
    "dockerfile": [".dockerfile"],
    "fsharp": [".fs", ".fsi", ".fsx"],
    "go": [".go"],
    "groovy": [".groovy", ".gradle"],
    "handlebars": [".hbs"],
    "html": [".html", ".htm"],
    "ini": [".ini"],
    "java": [".java"],
    "javascript": [".js", ".mjs", ".cjs"],
    "javascriptreact": [".jsx"],
    "json": [".json"],
    "jsonc": [".jsonc"],
    "julia": [".jl"],
    "latex": [".tex"],
    "less": [".less"],
    "lua": [".lua"],
    "makefile": [".mk"],
    "markdown": [".md", ".markdown"],
    "objective-c": [".m"],
    "objective-cpp": [".mm"],
    "perl": [".pl", ".pm"],
    "php": [".php", ".phtml"],
    "powershell": [".ps1", ".psm1"],
    "python": [".py", ".pyi"],
    "r": [".r"],
    "razor": [".cshtml"],
    "ruby": [".rb"],
    "rust": [".rs"],
    "scss": [".scss"],
    "shellscript": [".sh", ".bash", ".zsh"],
    "sql": [".sql"],
    "swift": [".swift"],
    "typescript": [".ts", ".mts", ".cts"],
    "typescriptreact": [".tsx"],
    "xml": [".xml"],
    "xsl": [".xsl"],
    "yaml": [".yaml", ".yml"],
}


def search(query: str, *, size: int = 20) -> list[dict[str, Any]]:
    """Extensions matching ``query`` on Open VSX, most downloaded first."""
    from navin.lsp.vsix import VsixError

    query = query.strip()
    if not query:
        return []
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.get(
                f"{_REGISTRY}/api/-/search",
                params={
                    "query": query,
                    "size": max(1, min(size, 50)),
                    "sortBy": "downloadCount",
                    "sortOrder": "desc",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise VsixError(f"could not reach Open VSX: {exc}", status=503) from exc

    results = []
    for entry in payload.get("extensions") or []:
        namespace = str(entry.get("namespace") or "")
        name = str(entry.get("name") or "")
        if not namespace or not name:
            continue
        icon = str((entry.get("files") or {}).get("icon") or "")
        results.append(
            {
                "slug": f"{namespace}/{name}",
                "displayName": str(entry.get("displayName") or name),
                "description": str(entry.get("description") or ""),
                "version": str(entry.get("version") or ""),
                "downloads": int(entry.get("downloadCount") or 0),
                # Served by the registry itself, so the browser loads it directly
                # rather than us proxying image bytes through the gateway.
                "icon": icon if icon.startswith(f"{_REGISTRY}/") else "",
            }
        )
    return results


def _manifest(root: Path) -> dict[str, Any]:
    from navin.quality.linters import _read_json

    return _read_json(root / "extension" / "package.json")


def declared_languages(root: Path) -> dict[str, Any] | None:
    """The file types the extension claims, as an lsp table fragment.

    ``contributes.languages`` is how the extension tells VS Code what it
    handles, so it is the same answer a human would copy out by hand. When the
    language is one VS Code ships with, the extension only names it in its
    activation events and the suffixes have to be filled in from what that name
    means.
    """
    manifest = _manifest(root)
    suffixes: list[str] = []
    language_ids: dict[str, str] = {}
    ids: list[str] = []

    def claim(identifier: str, claimed: list[str]) -> None:
        if not claimed:
            return
        if identifier not in ids:
            ids.append(identifier)
        for suffix in claimed:
            if suffix not in language_ids:
                suffixes.append(suffix)
                language_ids[suffix] = identifier

    for language in (manifest.get("contributes") or {}).get("languages") or []:
        if not isinstance(language, dict):
            continue
        identifier = str(language.get("id") or "").strip()
        extensions = language.get("extensions")
        if not identifier or not isinstance(extensions, list):
            continue
        claim(
            identifier,
            [
                str(suffix)
                for suffix in extensions
                if isinstance(suffix, str) and suffix.startswith(".") and len(suffix) > 1
            ],
        )

    # Only as a fallback: an extension that already declared its own language
    # also lists the ones it merely reads, the way Svelte activates on
    # TypeScript, and taking those would put it in front of the server that owns
    # them.
    if not suffixes:
        for event in manifest.get("activationEvents") or []:
            if not isinstance(event, str) or not event.startswith("onLanguage:"):
                continue
            identifier = event.split(":", 1)[1].strip()
            claim(identifier, _BUILTIN_LANGUAGE_SUFFIXES.get(identifier, []))

    if not suffixes:
        return None
    return {
        "languages": ids,
        "extensions": suffixes,
        "root_markers": ["package.json", ".git"],
        "language_ids": language_ids,
        # Below the packaged servers, which were picked per language rather than
        # by whatever the user installed last.
        "priority": 60,
    }


def _rank(path: Path, root: Path) -> int:
    """Lower sorts first. Encodes which conventions are the strongest signal."""
    stem = path.stem.lower()
    name = path.name.lower()
    score = 50
    if "language-server" in name or "languageserver" in name or "language_server" in name:
        score = 0
    elif stem == "server" or stem.endswith("-server") or stem.endswith("_server"):
        score = 10
    elif "server" in name:
        score = 20
    elif "lsp" in name:
        score = 25
    # A bundled build sits nearer the root than the sources it was built from.
    return score + len(path.relative_to(root).parts)


def _is_native(path: Path) -> bool:
    """A compiled program, judged by its first bytes rather than its name.

    The name cannot be trusted here: rust-analyzer's server is called
    ``rust-analyzer``, and the permission bits that would otherwise give it away
    are dropped when the archive is unpacked.
    """
    if path.suffix.lower() not in {"", ".exe"}:
        return False
    try:
        with path.open("rb") as stream:
            head = stream.read(4)
    except OSError:
        return False
    return any(head.startswith(magic) for magic in _EXECUTABLE_MAGIC)


def server_candidates(root: Path) -> list[tuple[Path, str]]:
    """Files in the extension that could be a language server, best first."""
    node: list[Path] = []
    native: list[Path] = []
    bundles: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = {part.lower() for part in relative.parts[:-1]}
        if parts & set(_EXCLUDED_PARTS):
            continue
        if path.suffix.lower() in _NODE_SUFFIXES:
            if _SERVER_NAME_RE.search(path.stem):
                node.append(path)
            elif "node_modules" not in parts and path.stat().st_size >= _BUNDLE_MIN_BYTES:
                # Some servers are named after the product rather than the role -
                # intelephense.js is one - and the only thing they have in common
                # is being a bundle the extension built itself.
                bundles.append(path)
        elif _is_native(path):
            # Every shipped executable is worth trying: an extension carries one
            # so rarely, and for such a specific reason, that the name it was
            # given says less than its presence does.
            native.append(path)

    def by_rank(paths: list[Path]) -> list[Path]:
        return sorted(paths, key=lambda p: _rank(p, root))

    # A compiled server is the extension's own build, while a node candidate can
    # be a dependency that merely looks like one, so natives are tried first.
    ordered = [
        *((path, "native") for path in by_rank(native)),
        *((path, "node") for path in by_rank(node)),
        *((path, "node") for path in by_rank(bundles)[:_MAX_BUNDLES]),
    ]
    return ordered[:_MAX_CANDIDATES]


def launch_variants(path: Path, runtime: str) -> list[list[str]]:
    """The command lines worth trying for one candidate, likeliest first."""
    if runtime == "native":
        return [[str(path), *args] for args in _NATIVE_ARGS]
    return [["node", str(path), *args] for args in _NODE_ARGS]


def _read_message(stream: Any) -> dict[str, Any] | None:
    """One LSP message, or None once the stream ends."""
    length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        header = line.strip()
        if not header:
            break
        if header.lower().startswith(b"content-length:"):
            with suppress(ValueError):
                length = int(header.split(b":", 1)[1])
    if length <= 0:
        return None
    body = stream.read(length)
    if not body:
        return None
    try:
        message = json.loads(body)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


def handshake(argv: list[str], root: Path, *, timeout_s: float = _HANDSHAKE_TIMEOUT_S) -> (
    dict[str, Any] | None
):
    """Start the candidate and return its capabilities, or None if it is not one.

    This does not reuse LspClient: probing needs to give up in seconds and to
    notice a process that exits immediately, whereas a configured server is
    given three quarters of a minute because it is known to be one. Everything
    that can go wrong here means the same thing to the caller - not a server -
    so the reason is logged rather than raised.
    """
    from navin.lsp.client import path_to_uri
    from navin.utils.proc import no_window_kwargs

    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": path_to_uri(root),
                "workspaceFolders": [{"uri": path_to_uri(root), "name": root.name}],
                "initializationOptions": {},
                "capabilities": {"textDocument": {"hover": {}}, "workspace": {}},
            },
        }
    ).encode("utf-8")

    try:
        process = subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(root),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("vsix candidate {} did not start: {}", argv[0], exc)
        return None

    answer: list[dict[str, Any]] = []

    def read() -> None:
        assert process.stdout is not None
        while True:
            message = _read_message(process.stdout)
            if message is None:
                return
            # Servers are free to log or ask questions before answering, so the
            # reply is matched by id rather than by being first.
            if message.get("id") == 1:
                answer.append(message)
                return

    reader = threading.Thread(target=read, name="vsix-probe", daemon=True)
    reader.start()
    try:
        assert process.stdin is not None
        process.stdin.write(b"Content-Length: %d\r\n\r\n%s" % (len(request), request))
        process.stdin.flush()
    except OSError:
        answer.clear()
    reader.join(timeout_s)

    with suppress(Exception):
        process.kill()
        process.wait(timeout=5)

    if not answer:
        logger.debug("vsix candidate {} did not answer initialize", argv[-2:])
        return None
    capabilities = (answer[0].get("result") or {}).get("capabilities")
    if not isinstance(capabilities, dict) or not capabilities:
        # Answering with no capability at all leaves nothing to route to it.
        return None
    return capabilities


def detect(root: Path) -> tuple[Path, list[str], str, dict[str, Any], dict[str, Any]]:
    """Find and prove the language server inside an extracted extension.

    Returns the server file, the arguments it needs, its runtime, the lsp table
    fragment, and the capabilities it reported.
    """
    from navin.lsp.vsix import VsixError

    spec = declared_languages(root)
    if spec is None:
        raise VsixError(
            "this extension declares no file types, so there is nothing to route to it",
            status=422,
        )

    candidates = server_candidates(root)
    if not candidates:
        raise VsixError(
            "no language server found inside this extension; it is probably one "
            "that only adds editor features, which needs the VS Code extension host",
            status=422,
        )

    probe_root = Path(tempfile.mkdtemp(prefix="navin-vsix-probe-"))
    deadline = time.monotonic() + _PROBE_BUDGET_S
    try:
        for path, runtime in candidates:
            if runtime == "node" and shutil.which("node") is None:
                continue
            if runtime == "native":
                with suppress(OSError):
                    path.chmod(path.stat().st_mode | 0o111)
            for argv in launch_variants(path, runtime):
                if time.monotonic() >= deadline:
                    logger.info("vsix probe ran out of its {}s budget", _PROBE_BUDGET_S)
                    break
                capabilities = handshake(argv, probe_root)
                if capabilities is None:
                    continue
                args = argv[2:] if runtime == "node" else argv[1:]
                logger.info("vsix probe accepted {} {}", path.name, args)
                return path, args, runtime, spec, capabilities
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)

    raise VsixError(
        "found files that looked like a language server, but none answered an "
        "LSP handshake, so this extension needs the VS Code extension host",
        status=422,
    )
