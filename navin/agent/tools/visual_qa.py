"""Marketing-scoped visual quality gate tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pydantic import AliasChoices, Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.context import current_request_context
from navin.agent.tools.path_utils import resolve_workspace_path
from navin.config_base import Base
from navin.marketing.visual_qa import (
    VisualQAPolicy,
    pillow_ready,
    run_visual_qa,
    write_visual_qa_report,
)
from navin.providers.visual_qa import (
    VisualQAProviderError,
    get_visual_qa_provider,
    visual_qa_credentials_ready,
)
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError
from navin.utils.llm_runtime import runtime_from_provider_snapshot


class VisualQAToolConfig(Base):
    """Configuration for the Marketing visual QA gate."""

    enabled: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    provider: str = "chat"
    preset: str = ""
    pass_score: float = Field(
        default=85.0,
        ge=0,
        le=100,
        validation_alias=AliasChoices("passScore", "pass_score"),
        serialization_alias="passScore",
    )
    warn_score: float = Field(
        default=65.0,
        ge=0,
        le=100,
        validation_alias=AliasChoices("warnScore", "warn_score"),
        serialization_alias="warnScore",
    )
    block_on_vision_failure: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "blockOnVisionFailure", "block_on_vision_failure"
        ),
        serialization_alias="blockOnVisionFailure",
    )
    min_sharpness: float = Field(
        default=80.0,
        ge=0,
        validation_alias=AliasChoices("minSharpness", "min_sharpness"),
        serialization_alias="minSharpness",
    )
    min_contrast: float = Field(
        default=24.0,
        ge=0,
        validation_alias=AliasChoices("minContrast", "min_contrast"),
        serialization_alias="minContrast",
    )


def visual_qa_readiness(
    config: VisualQAToolConfig,
    snapshot_loader: Callable[..., Any] | None,
) -> dict[str, Any]:
    """Structured dependency, adapter and credential readiness."""
    dependency = pillow_ready()
    adapter = get_visual_qa_provider(config.provider) is not None
    runtime = None
    error = ""
    if snapshot_loader is not None:
        try:
            snapshot = snapshot_loader(
                preset_name=config.preset or None
            )
            runtime = runtime_from_provider_snapshot(snapshot)
        except Exception as exc:
            error = str(exc)
    credentials = visual_qa_credentials_ready(runtime)
    return {
        "ready": dependency and adapter and credentials,
        "dependency": dependency,
        "provider": config.provider,
        "adapter": adapter,
        "credentials": credentials,
        "model": str(getattr(runtime, "model", "") or ""),
        "error": error,
    }


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "candidate": {
                "type": "string",
                "minLength": 1,
                "description": "Workspace path to the visual asset under review.",
            },
            "references": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 8,
                "description": "Authoritative product, logo, copy or brand reference images.",
            },
            "claims": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "product_fidelity",
                        "logo_fidelity",
                        "text_accuracy",
                        "color_fidelity",
                        "composition",
                    ],
                },
                "description": "Visual claims that the gate must verify.",
            },
            "requirements": {
                "type": "object",
                "description": (
                    "Optional width, height, min_width, min_height, aspect_ratio, "
                    "alpha, marketplace, white_background and safe_zone_percent."
                ),
                "additionalProperties": True,
            },
            "report_name": {
                "type": "string",
                "description": "Optional stable report basename.",
            },
        },
        "required": ["candidate"],
        "additionalProperties": False,
    }
)
class VisualQATool(Tool):
    """Run the Marketing visual gate and persist JSON plus Markdown evidence."""

    config_key = "visual_qa"
    _scopes = {"core"}

    @classmethod
    def config_cls(cls):
        return VisualQAToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        config = ctx.config.visual_qa
        if config.enabled is not None:
            return config.enabled
        return visual_qa_readiness(config, ctx.provider_snapshot_loader)["ready"]

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=ctx.workspace,
            config=ctx.config.visual_qa,
            snapshot_loader=ctx.provider_snapshot_loader,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: VisualQAToolConfig,
        snapshot_loader: Callable[..., Any] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.snapshot_loader = snapshot_loader

    @property
    def name(self) -> str:
        return "visual_qa"

    @property
    def description(self) -> str:
        return (
            "Marketing-only visual delivery gate. Checks pixels with Pillow, "
            "compares product/logo/text/color/composition with a configured "
            "multimodal chat provider, and writes evidence reports under marketing/qa."
        )

    def _runtime(self) -> Any | None:
        request = current_request_context()
        if request is not None and request.runtime is not None and not self.config.preset:
            return request.runtime
        if self.snapshot_loader is None:
            return request.runtime if request is not None else None
        snapshot = self.snapshot_loader(preset_name=self.config.preset or None)
        return runtime_from_provider_snapshot(snapshot)

    def _resolve_path(self, value: str) -> Path:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        return resolve_workspace_path(
            value,
            workspace=workspace,
            allowed_dir=access.allowed_root or workspace,
        )

    async def execute(
        self,
        candidate: str,
        references: list[str] | None = None,
        claims: list[str] | None = None,
        requirements: dict[str, Any] | None = None,
        report_name: str | None = None,
        **_: Any,
    ) -> str:
        try:
            candidate_path = self._resolve_path(candidate)
            reference_paths = [self._resolve_path(path) for path in references or []]
            factory = get_visual_qa_provider(self.config.provider)
            runtime = self._runtime()
            provider = (
                factory(runtime=runtime, provider_name=self.config.provider)
                if factory is not None and visual_qa_credentials_ready(runtime)
                else None
            )
            policy = VisualQAPolicy(
                pass_score=self.config.pass_score,
                warn_score=self.config.warn_score,
                min_sharpness=self.config.min_sharpness,
                min_contrast=self.config.min_contrast,
                block_on_vision_failure=self.config.block_on_vision_failure,
            )
            report = await run_visual_qa(
                candidate_path,
                references=reference_paths,
                claims=claims,
                requirements=requirements,
                provider=provider,
                policy=policy,
            )
            access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
            workspace = access.project_path or self.workspace
            report["reports"] = write_visual_qa_report(
                report, workspace, report_name=report_name
            )
            return json.dumps(report, ensure_ascii=False, indent=2)
        except (
            FileNotFoundError,
            OSError,
            RuntimeError,
            ValueError,
            VisualQAProviderError,
            WorkspaceBoundaryError,
        ) as exc:
            return ToolResult.error(f"Error: visual QA failed: {exc}")

