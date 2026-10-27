# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Optional cloud execution: AWS Bedrock AgentCore, through the Navin site.

Off by default. In Settings the user picks where code and the browser run:
``local`` (this machine, the default) or ``aws`` (an isolated microVM per
session for code and shell, and an isolated Chromium for the browser), and
accepts the terms once. Time in the cloud comes off the plan's AI budget.

The app never holds AWS credentials: it calls the Navin site with its license
(device + activation token), the site opens the AgentCore sessions and, for
the browser, signs the CDP WebSocket the app then connects to.

The cloud sandbox does not see this computer: files go up and come back down
explicitly with the ``sandbox_files`` tool.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any, Literal

from navin.agent.tools.base import Tool, tool_parameters
from navin.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from navin.config_base import Base

ExecutionTarget = Literal["local", "aws"]

CODE_TIMEOUT_S = 330.0
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_ERRORS = {
    "unauthorized_device": "this device is not signed in to a Navin subscription",
    "no_managed_budget": "cloud execution needs a paid Navin plan",
    "budget_exhausted": "the AI budget of this period is used up",
    "account_suspended": "the account is suspended",
    "aws_busy": "AWS is busy, try again in a moment",
    "files_too_large": "files are larger than 20 MB in one go",
}


class ExecutionConfig(Base):
    """Where the agent runs code and its browser (Settings > Execution)."""

    target: ExecutionTarget = "local"
    # The user accepted once: files are sent explicitly, time is billed on the AI budget.
    aws_accepted: bool = False


class CloudSandboxError(RuntimeError):
    pass


def _full_config() -> Any:
    from navin.config.loader import load_config

    return load_config()


_CLOUD_TARGET_CACHE: dict[tuple[str, int, int], bool] = {}


def _config_stamp() -> tuple[str, int, int] | None:
    try:
        from navin.config.loader import get_config_path

        path = get_config_path()
        try:
            stat = path.stat()
        except FileNotFoundError:
            return (str(path), -1, -1)
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except Exception:  # noqa: BLE001
        return None


def cloud_target_active(config: Any | None = None) -> bool:
    """True when the user chose AWS, accepted the terms and has a license.

    Asked before every exec: without a config the answer is kept until the
    config file changes, instead of parsing and decrypting it each time
    (~10 ms per command).
    """
    stamp = _config_stamp() if config is None else None
    if stamp is not None and stamp in _CLOUD_TARGET_CACHE:
        return _CLOUD_TARGET_CACHE[stamp]
    try:
        cfg = config or _full_config()
        execution = cfg.tools.execution
        active = execution.target == "aws" and bool(execution.aws_accepted) and bool(cfg.license.device)
    except Exception:  # noqa: BLE001 - any doubt means local
        active = False
    if stamp is not None:
        _CLOUD_TARGET_CACHE.clear()
        _CLOUD_TARGET_CACHE[stamp] = active
    return active


async def sandbox_call(action: str, payload: dict[str, Any] | None = None, *, timeout_s: float = 60.0) -> dict[str, Any]:
    """POST /api/sandbox/<action> on the Navin site with this device's license."""
    from navin.license_client import team_post

    config = _full_config()
    status, body = await asyncio.to_thread(
        team_post, config, f"/api/sandbox/{action}", dict(payload or {}), timeout_s=timeout_s
    )
    if status >= 400:
        code = str(body.get("error") or f"http_{status}")
        detail = _ERRORS.get(code) or str(body.get("message") or code)
        raise CloudSandboxError(f"cloud sandbox: {detail}")
    return body


def format_exec_result(body: dict[str, Any]) -> str:
    """Same shape as the local exec tool: stdout, STDERR block, exit code."""
    parts: list[str] = []
    stdout = str(body.get("stdout") or "")
    stderr = str(body.get("stderr") or "")
    if stdout:
        parts.append(stdout.rstrip("\n"))
    if stderr:
        parts.append("STDERR:\n" + stderr.rstrip("\n"))
    texts = [str(c.get("text")) for c in body.get("content") or [] if c.get("text") and c.get("text") not in (stdout, stderr)]
    if texts and not stdout:
        parts.append("\n".join(texts))
    exit_code = body.get("exitCode")
    if exit_code is None:
        exit_code = 1 if body.get("isError") else 0
    parts.append(f"Exit code: {exit_code}")
    parts.append("(ran in the AWS cloud sandbox: this computer's files are not there; use sandbox_files to send or fetch files)")
    return "\n".join(parts)


async def run_cloud_command(command: str) -> str:
    try:
        body = await sandbox_call("code", {"op": "command", "command": command}, timeout_s=CODE_TIMEOUT_S)
    except CloudSandboxError as exc:
        return f"Error: {exc}"
    return format_exec_result(body)


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "upload (computer -> cloud sandbox), download (cloud sandbox -> computer), list, or run_code",
            enum=["upload", "download", "list", "run_code"],
        ),
        paths=ArraySchema(
            StringSchema("A file path, relative to the workspace (upload) or to the sandbox (download)"),
            description="Files to upload or download",
        ),
        directory=StringSchema("Sandbox directory to list (list), default '.'"),
        code=StringSchema("Code to run in the sandbox (run_code)"),
        language=StringSchema("run_code language", enum=["python", "javascript", "typescript"]),
        required=["action"],
    )
)
class SandboxFilesTool(Tool):
    """Move files between this computer and the AWS cloud sandbox, or run code there."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "sandbox_files"

    @property
    def description(self) -> str:
        return (
            "Cloud execution is on (AWS): exec and the browser run in an isolated cloud sandbox that "
            "cannot see this computer. Use this tool to upload workspace files to the sandbox before "
            "running them, download results back into the workspace, list sandbox files, or run "
            "Python/JavaScript/TypeScript code there. Sandbox files last for the session (up to 1 h)."
        )

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return cloud_target_active()

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    def __init__(self, workspace: str) -> None:
        self._workspace = Path(workspace).expanduser().resolve()

    def _local(self, raw: str) -> Path:
        path = (self._workspace / raw).resolve()
        if path != self._workspace and self._workspace not in path.parents:
            raise CloudSandboxError(f"{raw} is outside the workspace")
        return path

    async def execute(
        self,
        action: str,
        paths: list[str] | None = None,
        directory: str | None = None,
        code: str | None = None,
        language: str | None = None,
        **_: Any,
    ) -> str:
        try:
            if action == "upload":
                return await self._upload(paths or [])
            if action == "download":
                return await self._download(paths or [])
            if action == "list":
                body = await sandbox_call("code", {"op": "list", "directory": directory or "."})
                return format_exec_result(body)
            if action == "run_code":
                if not code:
                    return "Error: run_code needs code"
                body = await sandbox_call(
                    "code", {"op": "code", "code": code, "language": language or "python"}, timeout_s=CODE_TIMEOUT_S
                )
                return format_exec_result(body)
            return f"Error: unknown action {action}"
        except CloudSandboxError as exc:
            return f"Error: {exc}"

    async def _upload(self, paths: list[str]) -> str:
        files: list[dict[str, str]] = []
        total = 0
        for raw in paths:
            local = self._local(raw)
            if not local.is_file():
                return f"Error: {raw} is not a file"
            data = await asyncio.to_thread(local.read_bytes)
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                return "Error: more than 20 MB in one upload; send fewer files at a time"
            rel = local.relative_to(self._workspace).as_posix()
            files.append({"path": rel, "base64": base64.b64encode(data).decode("ascii")})
        if not files:
            return "Error: no files to upload"
        await sandbox_call("code", {"op": "write", "files": files}, timeout_s=120)
        return f"Uploaded {len(files)} file(s) to the cloud sandbox: " + ", ".join(f["path"] for f in files)

    async def _download(self, paths: list[str]) -> str:
        if not paths:
            return "Error: no files to download"
        body = await sandbox_call("code", {"op": "read", "paths": paths}, timeout_s=120)
        saved: list[str] = []
        for block in body.get("content") or []:
            name = str(block.get("uri") or block.get("name") or "").removeprefix("file://").lstrip("/")
            if not name:
                continue
            target = self._local(name)
            if not await asyncio.to_thread(_save_block, target, block):
                continue
            saved.append(target.relative_to(self._workspace).as_posix())
        if not saved:
            return "Error: nothing came back (check the paths with action=list)"
        return "Downloaded into the workspace: " + ", ".join(saved)



__all__ = [
    "CloudSandboxError",
    "ExecutionConfig",
    "SandboxFilesTool",
    "cloud_target_active",
    "format_exec_result",
    "run_cloud_command",
    "sandbox_call",
]



def _save_block(target: Path, block: dict[str, Any]) -> bool:
    """Write one downloaded block to disk (worker thread); False when it has no body."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if block.get("base64"):
        target.write_bytes(base64.b64decode(block["base64"]))
    elif block.get("text") is not None:
        target.write_text(str(block["text"]), encoding="utf-8")
    else:
        return False
    return True
