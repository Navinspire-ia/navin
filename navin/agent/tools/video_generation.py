"""Video generation tool."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.config.paths import get_media_dir
from navin.config_base import Base
from navin.providers.video_generation import (
    VideoGenerationError,
    VideoGenerationProvider,
    get_video_gen_provider,
)
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from navin.utils.artifacts import (
    ArtifactError,
    generated_video_tool_result,
    store_generated_video_artifact,
)
from navin.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from navin.config.schema import ProviderConfig


class VideoGenerationToolConfig(Base):
    """Video generation tool configuration."""

    enabled: bool = False
    provider: str = "gemini"
    model: str = "veo-3.1-generate-preview"
    default_aspect_ratio: str = "16:9"
    default_duration_seconds: int = Field(default=8, ge=1, le=60)
    default_resolution: str = ""
    max_wait_seconds: int = Field(default=600, ge=60, le=1800)
    save_dir: str = "generated-video"


@tool_parameters(
    tool_parameters_schema(
        prompt=StringSchema(
            "Detailed video generation prompt: subject, action, camera movement, "
            "style, lighting, and any on-screen text quoted exactly.",
            min_length=1,
        ),
        reference_image=StringSchema(
            "Optional local path of an image to animate or use as the first frame "
            "(generated artifact path or user-provided image).",
        ),
        aspect_ratio=StringSchema(
            "Optional output aspect ratio, e.g. 16:9 (landscape/YouTube) or 9:16 (vertical/Reels).",
        ),
        duration_seconds=IntegerSchema(
            description="Optional clip duration in seconds (provider limits apply, typically 4-10s).",
            minimum=1,
            maximum=60,
        ),
        resolution=StringSchema(
            "Optional resolution hint supported by the provider, e.g. 720p, 1080p, 1280x720.",
        ),
        required=["prompt"],
    )
)
class VideoGenerationTool(Tool):
    """Generate persistent video artifacts through the configured video provider."""

    config_key = "video_generation"

    @classmethod
    def config_cls(cls):
        return VideoGenerationToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.video_generation.enabled

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=ctx.workspace,
            config=ctx.config.video_generation,
            provider_configs=ctx.image_generation_provider_configs,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: VideoGenerationToolConfig,
        provider_configs: dict[str, ProviderConfig] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.provider_configs = dict(provider_configs or {})

    @property
    def name(self) -> str:
        return "generate_video"

    @property
    def description(self) -> str:
        return (
            "Generate short videos (text-to-video or image-to-video) through the "
            "configured provider and store them as persistent artifacts. "
            "Returns artifact ids and local paths. Generation can take several minutes."
        )

    def _provider_config(self) -> ProviderConfig | None:
        return self.provider_configs.get(self.config.provider)

    def _provider_client(self) -> VideoGenerationProvider | None:
        provider = self._provider_config()
        cls = get_video_gen_provider(self.config.provider)
        if cls is None:
            return None
        return cls(
            api_key=provider.api_key if provider else None,
            api_base=provider.api_base if provider else None,
            extra_headers=provider.extra_headers if provider else None,
            extra_body=provider.extra_body if provider else None,
            proxy=provider.proxy if provider else None,
            max_wait=float(self.config.max_wait_seconds),
        )

    def _resolve_reference_image(self, value: str) -> str:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                value,
                workspace=workspace,
                allowed_root=access.allowed_root,
                extra_allowed_roots=[get_media_dir()] if access.allowed_root is not None else None,
                strict=True,
            )
        except WorkspaceBoundaryError as exc:
            raise VideoGenerationError(
                "reference_image must be inside the workspace or navin media directory"
            ) from exc
        except OSError as exc:
            raise VideoGenerationError(f"reference image not found: {value}") from exc
        if not resolved.is_file():
            raise VideoGenerationError(f"reference image is not a file: {value}")
        if detect_image_mime(resolved.read_bytes()) is None:
            raise VideoGenerationError(f"unsupported reference image: {value}")
        return str(resolved)

    async def execute(
        self,
        prompt: str,
        reference_image: str | None = None,
        aspect_ratio: str | None = None,
        duration_seconds: int | None = None,
        resolution: str | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._provider_client()
        if client is None:
            return ToolResult.error(
                f"Error: unsupported video generation provider '{self.config.provider}'"
            )

        try:
            ref = self._resolve_reference_image(reference_image) if reference_image else None
            response = await client.generate(
                prompt=prompt,
                model=self.config.model,
                reference_image=ref,
                aspect_ratio=aspect_ratio or self.config.default_aspect_ratio,
                duration_seconds=duration_seconds or self.config.default_duration_seconds,
                resolution=resolution or self.config.default_resolution or None,
            )
            artifact = store_generated_video_artifact(
                response.video,
                mime=response.mime,
                prompt=prompt,
                model=self.config.model,
                source_images=[ref] if ref else None,
                save_dir=self.config.save_dir,
                provider=self.config.provider,
            )
            return generated_video_tool_result([artifact])
        except (ArtifactError, VideoGenerationError, OSError) as exc:
            return ToolResult.error(f"Error: {exc}")
