"""One line per tool call: what was asked (hashed) and what came back (classed).

The record is the whole memory of S3, so it must be cheap to write, safe to
keep, and enough to learn from::

    {
      "v": 1, "id": "3f9c...", "ts": "2026-09-05T00:12:03Z",
      "tool": "exec", "key": "exec|npm|test", "args": "a91b...",
      "shape": {"prog": "npm", "sub": "test", "nargs": 1},
      "cls": "error", "fp": "c0ffee12", "obs": "npm ERR! missing script: test",
      "exit": 1, "ms": 1834, "ok": false,
      "prev": ["read_file:ok", "exec:ok", "exec:error"],
      "session": "webui:chat-1", "turn": "t-42"
    }

* ``args`` is a salted SHA-256 of the *scrubbed* arguments: secrets never
  reach the disk, not even hashed. The salt is per project and random.
* ``key`` and ``shape`` are the normalized features the local head learns
  from: tool, program, sub-command, host, extension, argument names. No
  values, no paths, no text.
* ``cls`` is the observation class. ``fp`` is a short fingerprint of the
  normalized observation (same output twice = same fingerprint), ``obs`` a
  scrubbed head of at most 96 characters. The raw output is never stored.
* ``prev`` is the class of the last three calls of the same session, so the
  head can learn "after that, this".
* ``turn`` links to the S1 episode of the same turn when S1 runs; S1 off
  changes nothing here.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

RECORD_VERSION = 1

# Observation classes, in the order the panel lists them.
CLASSES: tuple[str, ...] = ("ok", "changed", "empty", "not_found", "denied", "timeout", "error")
FAILURE_CLASSES = frozenset({"not_found", "denied", "timeout", "error"})
# "Useless" calls: the world did not move and the answer was a refusal.
USELESS_CLASSES = frozenset({"not_found", "denied", "timeout"})

MAX_OBS_CHARS = 96
MAX_OBS_SCAN_CHARS = 4096
MAX_ARG_CHARS = 8192
HISTORY = 3

# Tools whose success means the world changed, not that something was read.
WRITE_TOOLS = frozenset(
    {
        "write_file",
        "apply_patch",
        "edit_file",
        "create_file",
        "delete_file",
        "move_file",
        "rename_file",
        "mkdir",
        "atomic_write",
        "git_commit",
        "git_push",
        "message",
        "notify",
        "send_message",
        "send_email",
        "payment",
    }
)
# Never a skip hint, whatever the model says (S3.5).
NEVER_SKIP_TOOLS = WRITE_TOOLS | frozenset({"exec", "shell", "spawn", "cron", "database", "crm", "leads"})

_SHELL_TOOLS = frozenset({"exec", "shell", "exec_session", "sandbox", "spawn"})
_BROWSER_TOOLS = frozenset({"browser", "browser_use", "scrape", "web", "web_fetch", "open_preview"})
_PATH_KEYS = ("path", "file", "file_path", "filename", "target", "source", "dest", "directory", "dir")
_URL_KEYS = ("url", "href", "link", "endpoint")
_COMMAND_KEYS = ("command", "cmd", "script", "args")
_ACTION_KEYS = ("action", "op", "operation", "mode", "kind", "method")

# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------

REDACTED = "<redacted>"


def _redact_kv(match: re.Match[str]) -> str:
    """Keep the variable name, drop the value: ``API_KEY=<redacted>``."""
    return f"{match.group(1)}{match.group(2)}{match.group(3)}{REDACTED}"


def _redact_url_credentials(match: re.Match[str]) -> str:
    return f"{match.group(1)}{REDACTED}@"


_Replacement = str | Callable[[re.Match[str]], str]

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], _Replacement], ...] = (
    # PEM blocks first: they contain everything below.
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), REDACTED),
    # Well-known token prefixes.
    (re.compile(r"\b(?:sk|pk|rk)[-_](?:live|test|proj|ant|or)?[-_]?[A-Za-z0-9_-]{12,}"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), REDACTED),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}"), REDACTED),
    # JWT.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), REDACTED),
    # Authorization headers.
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), REDACTED),
    (re.compile(r"(?i)\bauthorization\s*[:=]\s*\S+"), REDACTED),
    # key=value with a secret-looking name (env assignments, query strings, JSON).
    (
        re.compile(
            r"(?i)\b([A-Z0-9_.-]*(?:PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|"
            r"CLIENT[_-]?SECRET|CREDENTIALS?|AUTH)[A-Z0-9_]*)(\"?\s*[:=]\s*)(\"?)([^\s\"'&;,]{4,})"
        ),
        _redact_kv,
    ),
    # Credentials inside URLs.
    (re.compile(r"(?i)(://)([^\s/:@]+):([^\s/@]+)@"), _redact_url_credentials),
    # Long opaque hex / base64 blobs (hashes, session ids, raw keys).
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), REDACTED),
    (re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}(?![A-Za-z0-9+/=])"), REDACTED),
)


def scrub_secrets(text: str) -> str:
    """Replace anything that looks like a credential with ``<redacted>``."""
    if not text:
        return ""
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


# --------------------------------------------------------------------------
# Arguments -> features
# --------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*")
_FLAG_RE = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*")
_EXIT_RE = re.compile(r"(?i)(?:exit(?:ed)?(?: with)? code[:\s]+|returncode[:=]\s*|exit[:=]\s*)(-?\d{1,4})\b")


def _as_dict(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"_raw": arguments}
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    if arguments is None:
        return {}
    return {"_value": arguments}


def _first_str(params: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and all(isinstance(v, str) for v in value):
            return " ".join(value)
    return None


def _shell_shape(command: str) -> dict[str, Any]:
    """Program, sub-command, flag count of the first pipeline stage."""
    text = _WS_RE.sub(" ", command.strip())
    # Skip env assignments and common wrappers.
    tokens = text.split(" ")
    while tokens and (re.match(r"^[A-Z_][A-Z0-9_]*=", tokens[0]) or tokens[0] in ("sudo", "env", "nice", "time")):
        tokens.pop(0)
    if tokens and tokens[0] in ("cd", "pushd") and "&&" in tokens:
        tokens = tokens[tokens.index("&&") + 1 :]
    stage: list[str] = []
    for token in tokens:
        if token in ("|", "||", "&&", ";", ">", ">>", "2>", "<"):
            break
        stage.append(token)
    prog = stage[0].rsplit("/", 1)[-1] if stage else ""
    sub = ""
    flags = 0
    for token in stage[1:]:
        if _FLAG_RE.match(token):
            flags += 1
            continue
        if not sub and _WORD_RE.fullmatch(token) and not token.startswith("."):
            sub = token
    if prog in ("python", "python3", "node", "npx", "uv", "poetry", "pnpm", "yarn", "bun") and sub:
        # "python -m pytest": the meaningful program is the module / script.
        prog = f"{prog} {sub}"
        sub = ""
        for token in stage[2:]:
            if not _FLAG_RE.match(token) and _WORD_RE.fullmatch(token):
                sub = token
                break
    return {"prog": prog[:40], "sub": sub[:40], "flags": min(flags, 9), "pipe": len(stage) < len(tokens)}


def _url_shape(url: str) -> dict[str, Any]:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return {"host": ""}
    host = (parts.hostname or "")[:80]
    depth = len([p for p in parts.path.split("/") if p])
    return {"host": host, "depth": min(depth, 9), "query": bool(parts.query)}


def _path_shape(path: str) -> dict[str, Any]:
    clean = path.strip().rstrip("/")
    name = clean.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""
    return {"ext": ext[:12], "depth": min(clean.count("/"), 9), "glob": any(c in clean for c in "*?[")}


@dataclass(frozen=True, slots=True)
class CallFeatures:
    tool: str
    key: str
    shape: dict[str, Any]
    args_hash: str


def normalize_call(tool_name: str, arguments: Any, *, salt: str) -> CallFeatures:
    """Features of one call: no values, no paths, no text; one salted hash."""
    tool = (tool_name or "").strip()[:64] or "unknown"
    params = _as_dict(arguments)
    shape: dict[str, Any]
    if tool in _SHELL_TOOLS or (_first_str(params, _COMMAND_KEYS) and tool not in _BROWSER_TOOLS):
        command = _first_str(params, _COMMAND_KEYS) or ""
        shape = _shell_shape(scrub_secrets(command))
        key = f"{tool}|{shape.get('prog', '')}|{shape.get('sub', '')}"
    elif tool in _BROWSER_TOOLS or _first_str(params, _URL_KEYS):
        url = _first_str(params, _URL_KEYS) or ""
        action = _first_str(params, _ACTION_KEYS) or ""
        shape = {**_url_shape(url), "action": action[:24]}
        key = f"{tool}|{shape.get('action', '')}|{shape.get('host', '')}"
    elif _first_str(params, _PATH_KEYS):
        shape = _path_shape(_first_str(params, _PATH_KEYS) or "")
        key = f"{tool}|.{shape.get('ext', '')}"
    else:
        action = _first_str(params, _ACTION_KEYS) or ""
        names = sorted(str(k) for k in params if not str(k).startswith("_"))[:8]
        shape = {"action": action[:24], "keys": names}
        key = f"{tool}|{shape.get('action', '')}|{','.join(names)}"
    shape["nargs"] = min(len(params), 16)
    try:
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)[:MAX_ARG_CHARS]
    except (TypeError, ValueError):
        canonical = str(params)[:MAX_ARG_CHARS]
    digest = hashlib.sha256(f"{salt}:{tool}:{scrub_secrets(canonical)}".encode()).hexdigest()[:16]
    return CallFeatures(tool=tool, key=key[:120], shape=shape, args_hash=digest)


# --------------------------------------------------------------------------
# Observation -> class
# --------------------------------------------------------------------------

_TIMEOUT_RE = re.compile(r"(?i)\b(timed? ?out|timeout|deadline exceeded|cancelled after \d+s|hung)\b")
_DENIED_RE = re.compile(
    r"(?i)\b(permission denied|access denied|denied|forbidden|unauthori[sz]ed|not allowed|not permitted|"
    r"eacces|eperm|operation not permitted|403|401|blocked by|refused|requires? approval|read[- ]only)\b"
)
_NOT_FOUND_RE = re.compile(
    r"(?i)\b(no such file|not found|enoent|404|does not exist|doesn't exist|unknown command|"
    r"command not found|no matches?|cannot find|could not find|unknown tool|no module named|"
    r"module not found|not installed|unknown option)\b"
)


def observation_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, BaseException):
        return f"{type(result).__name__}: {result}"
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def classify_observation(
    tool_name: str,
    result: Any,
    *,
    is_error: bool,
    read_only: bool | None = None,
) -> str:
    """One of ``CLASSES`` for a tool result or a raised exception."""
    text = observation_text(result)
    head = text[:MAX_OBS_SCAN_CHARS]
    if isinstance(result, TimeoutError):
        return "timeout"
    if _TIMEOUT_RE.search(head) and (is_error or "cancelled after" in head.lower()):
        return "timeout"
    if is_error:
        if _DENIED_RE.search(head):
            return "denied"
        if _NOT_FOUND_RE.search(head):
            return "not_found"
        return "error"
    exit_code = parse_exit_code(head)
    if exit_code not in (None, 0):
        if _NOT_FOUND_RE.search(head):
            return "not_found"
        if _DENIED_RE.search(head):
            return "denied"
        return "error"
    writes = (read_only is False) if read_only is not None else tool_name in WRITE_TOOLS
    if writes:
        return "changed"
    if not text.strip():
        return "empty"
    return "ok"


def parse_exit_code(text: str) -> int | None:
    match = _EXIT_RE.search(text or "")
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def observation_summary(result: Any) -> tuple[str, str]:
    """``(fingerprint, head)``: a short hash of the normalized output and a scrubbed head."""
    text = scrub_secrets(observation_text(result)[:MAX_OBS_SCAN_CHARS])
    normalized = _WS_RE.sub(" ", text).strip()
    fingerprint = hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:12]
    head = ""
    for line in text.splitlines():
        candidate = _WS_RE.sub(" ", line).strip()
        if candidate:
            head = candidate
            break
    if len(head) > MAX_OBS_CHARS:
        head = head[: MAX_OBS_CHARS - 3].rstrip() + "..."
    return fingerprint, head


# --------------------------------------------------------------------------
# Record
# --------------------------------------------------------------------------


def now_stamp(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))


_SEQUENCE = itertools.count()


@dataclass(slots=True)
class Trajectory:
    """One journal line, before serialization."""

    tool: str
    key: str
    args_hash: str
    shape: dict[str, Any]
    cls: str
    fingerprint: str
    obs: str
    exit_code: int | None
    duration_ms: int | None
    prev: list[str] = field(default_factory=list)
    session: str = ""
    turn: str | None = None
    ts: str = ""
    id: str = ""

    def finish(self, now: float | None = None) -> Trajectory:
        if not self.ts:
            self.ts = now_stamp(now)
        if not self.id:
            # Unique per row even for the same call twice in the same second:
            # the id decides the train / held-out bucket, so two identical
            # calls must be able to land on different sides.
            raw = (
                f"{self.ts}|{time.time_ns()}|{next(_SEQUENCE)}|{self.session}|{self.tool}|"
                f"{self.args_hash}|{self.fingerprint}|{self.duration_ms}"
            )
            self.id = hashlib.sha1(raw.encode()).hexdigest()[:16]
        return self

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "v": RECORD_VERSION,
            "id": self.id,
            "ts": self.ts,
            "tool": self.tool,
            "key": self.key,
            "args": self.args_hash,
            "shape": self.shape,
            "cls": self.cls,
            "fp": self.fingerprint,
            "obs": self.obs,
            "exit": self.exit_code,
            "ms": self.duration_ms,
            "ok": self.cls not in FAILURE_CLASSES,
            "prev": list(self.prev)[-HISTORY:],
            "session": self.session,
        }
        if self.turn:
            record["turn"] = self.turn
        return record


def build_trajectory(
    *,
    tool_name: str,
    arguments: Any,
    result: Any,
    is_error: bool,
    salt: str,
    duration_ms: int | None,
    prev: list[str] | None,
    session: str | None,
    turn: str | None,
    read_only: bool | None = None,
    now: float | None = None,
) -> Trajectory:
    features = normalize_call(tool_name, arguments, salt=salt)
    fingerprint, head = observation_summary(result)
    cls = classify_observation(features.tool, result, is_error=is_error, read_only=read_only)
    return Trajectory(
        tool=features.tool,
        key=features.key,
        args_hash=features.args_hash,
        shape=features.shape,
        cls=cls,
        fingerprint=fingerprint,
        obs=head,
        exit_code=parse_exit_code(observation_text(result)[:MAX_OBS_SCAN_CHARS]),
        duration_ms=duration_ms,
        prev=list(prev or [])[-HISTORY:],
        session=session or "",
        turn=turn,
    ).finish(now)


def record_is_valid(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and isinstance(record.get("tool"), str)
        and isinstance(record.get("key"), str)
        and record.get("cls") in CLASSES
        and isinstance(record.get("args"), str)
    )


class SessionHistory:
    """Last ``HISTORY`` outcomes per session, process-wide and bounded."""

    def __init__(self, max_sessions: int = 512) -> None:
        self._rings: dict[str, deque[str]] = {}
        self._max = max_sessions

    def prev(self, session: str) -> list[str]:
        ring = self._rings.get(session)
        return list(ring) if ring else []

    def push(self, session: str, tool: str, cls: str) -> None:
        ring = self._rings.get(session)
        if ring is None:
            if len(self._rings) >= self._max:
                self._rings.pop(next(iter(self._rings)))
            ring = deque(maxlen=HISTORY)
            self._rings[session] = ring
        ring.append(f"{tool}:{cls}")

    def clear(self) -> None:
        self._rings.clear()
