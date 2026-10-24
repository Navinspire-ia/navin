# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Marketing-scoped visual quality gate tool."""

from __future__ import annotations

import asyncio
import json
import re
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
from navin.visual.compare import compare_images, render_html, render_url
from navin.visual.figma import FigmaError, fetch_figma_frame
from navin.visual.video_steps import extract_keyframes, steps_from_frames

_LOCAL_URL = re.compile(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)")


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
            "action": {
                "type": "string",
                "enum": ["qa", "compare", "figma", "video_steps"],
                "description": "qa (default), compare, figma or video_steps.",
            },
            "candidate": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "qa: workspace path to the visual asset. compare: image, .html "
                    "file or local URL (http://localhost/127.0.0.1) to render."
                ),
            },
            "reference": {
                "type": "string",
                "description": "compare: workspace path of the reference image.",
            },
            "figma": {
                "type": "string",
                "description": "figma: Figma file URL (with ?node-id=) or file key.",
            },
            "node_id": {"type": "string", "description": "figma: node id like 1:2."},
            "video": {"type": "string", "description": "video_steps: workspace video path."},
            "max_frames": {"type": "integer", "minimum": 1, "maximum": 200},
            "scene_threshold": {"type": "number", "minimum": 0, "maximum": 1},
            "threshold": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "compare: target score (default 0.95), reported as passed.",
            },
            "out_dir": {
                "type": "string",
                "description": "Workspace output dir for compare/figma/video_steps (default visual/).",
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
        "additionalProperties": False,
    }
)
class VisualQATool(Tool):
    """Run the Marketing visual gate and persist JSON plus Markdown evidence."""

    config_key = "visual_qa"
    # subagent/teammate: the Designer and Marketing teammates run the video QA loop.
    _scopes = {"core", "subagent", "teammate"}

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
            "multimodal chat provider, and writes evidence reports under marketing/qa "
            "(action=qa, default, needs candidate). Visual builder actions: "
            "compare (reference image vs candidate image/.html/local URL; returns "
            "score 0..1, diff_path, report_path, zones), figma (download a frame "
            "as PNG reference, needs FIGMA_TOKEN), video_steps (ffmpeg keyframes "
            "of a recording as ordered deduplicated steps). Loop: capture the "
            "reference (figma or a screenshot), generate the HTML, call compare, "
            "fix the divergent zones, repeat until score >= threshold."
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

    def _out_dir(self, value: str | None) -> Path:
        out = self._resolve_path(value or "visual")
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _builder(self, action: str, kw: dict[str, Any]) -> dict[str, Any]:
        out = self._out_dir(kw.get("out_dir"))
        if action == "compare":
            if not kw.get("reference") or not kw.get("candidate"):
                raise ValueError("compare needs reference and candidate")
            ref = self._resolve_path(kw["reference"])
            cand = str(kw["candidate"])
            name = Path(cand).stem or "candidate"
            if _LOCAL_URL.match(cand):
                got = render_url(cand, out / "candidate_url.png")
            elif cand.startswith(("http://", "https://")):
                raise ValueError("only local URLs (localhost/127.0.0.1) can be rendered")
            else:
                path = self._resolve_path(cand)
                if path.suffix.lower() in {".html", ".htm"}:
                    got = render_html(
                        path.read_text(encoding="utf-8"), out / f"{name}_render.png"
                    )
                else:
                    got = path
            diff = out / f"{name}_diff.png"
            report_path = out / f"{name}_compare.json"
            report = compare_images(ref, got, diff, report_path)
            threshold = float(0.95 if kw.get("threshold") is None else kw["threshold"])
            report.update(
                candidate_image=str(got),
                diff_path=str(diff),
                report_path=str(report_path),
                threshold=threshold,
                passed=report["score"] >= threshold,
            )
            return report
        if action == "figma":
            if not kw.get("figma"):
                raise ValueError("figma needs a Figma URL or file key")
            node = kw.get("node_id")
            target = out / f"figma_{str(node or 'frame').replace(':', '-')}.png"
            return fetch_figma_frame(kw["figma"], node, target)
        if action == "video_steps":
            if not kw.get("video"):
                raise ValueError("video_steps needs video")
            video = self._resolve_path(kw["video"])
            frames = extract_keyframes(
                video,
                out / f"{video.stem}_frames",
                int(kw.get("max_frames") or 20),
                float(0.3 if kw.get("scene_threshold") is None else kw["scene_threshold"]),
            )
            steps = steps_from_frames(frames)
            return {"video": str(video), "frames": len(frames), "steps": steps}
        raise ValueError(f"unknown action: {action}")

    async def execute(
        self,
        candidate: str | None = None,
        references: list[str] | None = None,
        claims: list[str] | None = None,
        requirements: dict[str, Any] | None = None,
        report_name: str | None = None,
        action: str = "qa",
        **kw: Any,
    ) -> str:
        if action and action != "qa":
            try:
                result = await asyncio.to_thread(
                    self._builder, action, {**kw, "candidate": candidate}
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            except (
                FigmaError,
                OSError,
                RuntimeError,
                ValueError,
                WorkspaceBoundaryError,
            ) as exc:
                return ToolResult.error(f"Error: visual {action} failed: {exc}")
        if not candidate:
            return ToolResult.error("Error: visual QA failed: candidate is required")
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

