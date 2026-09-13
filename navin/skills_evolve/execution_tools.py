# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Real domain scoring and isolated Python execution for skill evaluations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult


class ExecutionUnavailableError(RuntimeError):
    pass


def run_solution(root: Path, inputs: list[Any]) -> Any:
    """Execute candidate Python with no network, home, credentials or host writes.

    Expected results are compared by the host, never given to this process.
    Merely exiting with zero or printing a success claim cannot pass the check.
    """
    if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
        raise ExecutionUnavailableError("Isolated Python evaluation requires bubblewrap on Linux/WSL.")
    argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--clearenv"]
    for directory in ("/usr", "/lib", "/lib64", "/bin"):
        if Path(directory).exists():
            argv += ["--ro-bind", directory, directory]
    argv += ["--ro-bind", str(root), "/work", "--tmpfs", "/tmp", "--dev", "/dev", "--proc", "/proc", "--chdir", "/work"]
    script = (
        "import json,sys,resource,importlib.util; "
        "resource.setrlimit(resource.RLIMIT_CPU,(2,2)); "
        "resource.setrlimit(resource.RLIMIT_AS,(268435456,268435456)); "
        "resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576)); "
        "resource.setrlimit(resource.RLIMIT_NPROC,(16,16)); "
        "resource.setrlimit(resource.RLIMIT_NOFILE,(32,32)); "
        "resource.setrlimit(resource.RLIMIT_CORE,(0,0)); "
        "values=json.loads(sys.stdin.read()); "
        "spec=importlib.util.spec_from_file_location('solution','/work/solution.py'); "
        "mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); "
        "print(json.dumps([mod.solve(value) for value in values]))"
    )
    argv += ["/usr/bin/python3", "-I", "-S", "-B", "-c", script]
    try:
        with tempfile.TemporaryFile(mode="w+") as output, tempfile.TemporaryFile(mode="w+") as error:
            result = subprocess.run(argv, input=json.dumps(inputs), stdout=output, stderr=error, text=True, timeout=8, check=False)
            output.seek(0)
            error.seek(0)
            stdout, stderr = output.read(1048576), error.read(1048576)
    except subprocess.TimeoutExpired:
        return {"execution_error": "The program exceeded its execution deadline."}
    except OSError as exc:
        raise ExecutionUnavailableError(f"Isolated execution unavailable: {type(exc).__name__}") from exc
    if result.returncode and "bwrap:" in stderr:
        raise ExecutionUnavailableError(stderr.strip()[:300])
    if result.returncode:
        return {"execution_error": stderr[-1000:]}
    try:
        return json.loads(stdout)
    except ValueError:
        return {"execution_error": "The program did not return valid JSON results."}


def source_digest(root: Path) -> str:
    return hashlib.sha256((root / "solution.py").read_bytes()).hexdigest()


class ExecutionVerifyTool(Tool):
    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(self, root: Path, task):
        self.root, self.task = root, task
        self.verified_digest = ""

    @property
    def name(self):
        return "verify"

    @property
    def description(self):
        return "Run the fixed behavioral checks for solution.py in an isolated process. Call after the final edit."

    @property
    def parameters(self):
        return {"type": "object", "properties": {}, "additionalProperties": False}

    @property
    def read_only(self):
        return True

    async def execute(self, **kwargs):
        actual = await asyncio.to_thread(run_solution, self.root, self.task.inputs)
        passed = actual == self.task.expected
        self.verified_digest = source_digest(self.root) if passed else ""
        return ToolResult(json.dumps({"passed": passed, "checks": len(self.task.inputs),
                                      "detail": "Behavior verified." if passed else "The function produces incorrect results."}))


class ExecutionDeskTool(Tool):
    _plugin_discoverable = False
    _scopes: set[str] = set()

    def __init__(self, root: Path, module: str):
        self.root, self.module = root, module

    @property
    def name(self):
        return self.module

    @property
    def description(self):
        return "Score the specified candidate or qualify the specified notice using the original task.json requirements."

    @property
    def parameters(self):
        return {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"], "additionalProperties": False}

    @property
    def read_only(self):
        return True

    async def execute(self, id: str, **kwargs):
        data = json.loads((self.root / "task.json").read_text())
        if self.module == "career":
            from navin.career.prospecting import score_candidate
            candidate = next((row for row in data["candidates"] if row["id"] == id), None)
            if candidate is None:
                return ToolResult.error("Unknown candidate.")
            score = score_candidate(candidate, data["mission"], data["criteria"])
            return ToolResult(json.dumps(score))
        from navin.tenders.score import score_tender
        from navin.tenders.writer import go_nogo
        notice = next((row for row in data["notices"] if row["id"] == id), None)
        if notice is None:
            return ToolResult.error("Unknown notice.")
        score = score_tender(notice, data["profile"])
        decision = go_nogo({**notice, "score": score["score"], "score_breakdown": score["breakdown"]}, data["profile"])
        return ToolResult(json.dumps({"id": id, **score, **decision}))
