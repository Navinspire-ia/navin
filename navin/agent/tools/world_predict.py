"""``world_predict``: what the project's world model expects from a tool call.

Registered only while the project's ``.navin/world-model.json`` says
``advise`` **and** every gate is open (frozen exam ``up``, offline A/B
``gain``); see ``navin.world_model``. Read-only and advisory: it predicts a
class and a confidence, it never runs, blocks or replaces a call.

It also owns the per-turn runtime-context block: at most three confident
beliefs of the head, so the agent hears "npm test usually fails here" once,
before choosing its tools. With ``advise`` off the tool is absent and the
prompt is exactly what it was.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_context, is_heartbeat_turn
from navin.agent.tools.schema import StringSchema, tool_parameters_schema

_PARAMETERS = tool_parameters_schema(
    tool=StringSchema(
        "Name of the tool you are about to call (exec, read_file, browser, apply_patch...).",
        min_length=2,
        max_length=64,
    ),
    arguments=StringSchema(
        "The arguments you would pass, as JSON or as the shell command line itself.",
        max_length=4000,
    ),
    required=["tool"],
)


@tool_parameters(_PARAMETERS)
class WorldPredictTool(Tool):
    """Ask what usually happens when this project calls a tool like that."""

    _scopes = {"core"}

    def __init__(self, *, workspace: str | Path) -> None:
        self._workspace = Path(workspace).expanduser()

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        from navin.world_model.advisor import advice_open

        return advice_open(getattr(ctx, "workspace", None))

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "world_predict"

    @property
    def description(self) -> str:
        return (
            "Predict the outcome class of a tool call in this project before making it "
            "(ok, changed, empty, not_found, denied, timeout, error) with a confidence, from "
            "the project's own past calls. Advisory only: it never replaces the real call, "
            "and a write, delete, mail or payment call must always be made for real."
        )

    @property
    def read_only(self) -> bool:
        return True

    def runtime_context_provider(self):  # type: ignore[no-untyped-def]
        async def provide(request: Any):  # type: ignore[no-untyped-def]
            from navin.runtime_context import RuntimeContextBlock
            from navin.world_model.advisor import advice_block_text

            workspace = getattr(request, "workspace", None) or self._workspace
            if (getattr(request, "session_key", "") or "").strip().lower() == "heartbeat":
                return None
            text = await asyncio.to_thread(advice_block_text, workspace)
            return RuntimeContextBlock(source="world-model", content=text) if text else None

        return provide

    def _project(self) -> Path:
        ctx = current_request_context()
        if ctx is not None and ctx.workspace is not None:
            return Path(ctx.workspace)
        return self._workspace

    async def execute(self, tool: str, arguments: str | None = None, **kwargs: Any) -> Any:
        if is_heartbeat_turn():
            return ToolResult.error("world_predict is not available on the heartbeat.")
        name = (tool or "").strip()
        if len(name) < 2:
            return ToolResult.error("Error: tool must be a tool name.")
        from navin.world_model.advisor import advice_open

        project = self._project()
        if not advice_open(project):
            return "world_predict is not active for this project (advise off or gate closed). Make the call."
        return await asyncio.to_thread(self._predict, project, name, arguments or "")

    @staticmethod
    def _predict(project: Path, tool: str, arguments: str) -> str:
        from navin.world_model import checkpoints as ck
        from navin.world_model.journal import project_salt
        from navin.world_model.settings import read_settings
        from navin.world_model.trajectory import normalize_call

        active = ck.active_checkpoint(project)
        if active is None:
            return "No active world model checkpoint. Make the call."
        params: Any = arguments
        if tool in ("exec", "shell", "exec_session", "sandbox") and arguments and not arguments.lstrip().startswith("{"):
            params = {"command": arguments}
        features = normalize_call(tool, params, salt=project_salt(project))
        prediction = active.model.predict(features.tool, features.key, features.args_hash, [])
        threshold = read_settings(project).confidence_threshold
        confident = prediction.confidence >= threshold and prediction.support >= 3
        head = (
            f"{features.key.replace('|', ' ').strip()}: expected {prediction.cls} "
            f"({round(prediction.confidence * 100)}%, {prediction.support} past calls at level {prediction.level})."
        )
        if not confident:
            head += " Low confidence: make the call and read the result."
        elif prediction.cls in ("not_found", "denied", "timeout"):
            head += " Usually useless here: check the precondition (path, permission, service) before calling."
        elif prediction.cls == "error":
            head += " Usually fails here: expect an error and read it; do not retry blindly."
        return head + " Advisory only; the real call decides."
