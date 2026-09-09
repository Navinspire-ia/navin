# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Music generation tool (Lyria via OpenRouter / Navin)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.path_utils import project_rooted_path
from navin.agent.tools.schema import StringSchema, tool_parameters_schema
from navin.config.paths import get_media_dir
from navin.config_base import Base
from navin.providers.media_credentials import (
    media_credentials_ready,
    resolve_media_tool_enabled,
)
from navin.providers.media_usage import report_media_usage
from navin.providers.music_generation import (
    MusicGenerationError,
    MusicGenerationProvider,
    get_music_gen_provider,
)
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from navin.utils.artifacts import (
    ArtifactError,
    generated_music_tool_result,
    store_generated_music_artifact,
)
from navin.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from navin.config.schema import ProviderConfig


class MusicGenerationToolConfig(Base):
    """Music generation tool configuration."""

    enabled: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    # Vide = aucun choix, comme les autres outils média.
    provider: str = ""
    # Défaut économique : clip 30 s (0,04 $). Lyria Pro (chanson, 0,08 $) opt-in.
    model: str = "google/lyria-3-clip-preview"
    save_dir: str = "generated-music"


@tool_parameters(
    tool_parameters_schema(
        prompt=StringSchema(
            "Detailed music prompt: genre, mood, tempo, structure (verse/chorus), "
            "instruments, vocals/lyrics language, and target length.",
            min_length=1,
        ),
        reference_image=StringSchema(
            "Optional local path of an image to inspire the composition "
            "(generated artifact path or user-provided image).",
        ),
        required=["prompt"],
    )
)
class MusicGenerationTool(Tool):
    """Generate persistent music artifacts through the configured music provider."""

    config_key = "music_generation"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return MusicGenerationToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        config = ctx.config.music_generation
        provider_configs = getattr(ctx, "image_generation_provider_configs", None) or {}
        return resolve_media_tool_enabled(
            config.enabled,
            media_credentials_ready(config.provider, provider_configs.get(config.provider)),
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=ctx.workspace,
            config=ctx.config.music_generation,
            provider_configs=ctx.image_generation_provider_configs,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: MusicGenerationToolConfig,
        provider_configs: dict[str, ProviderConfig] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.provider_configs = dict(provider_configs or {})

    @property
    def name(self) -> str:
        return "generate_music"

    @property
    def description(self) -> str:
        return (
            "Generate music or short audio clips (default Lyria Clip 30s at 0.04 USD; "
            "Lyria Pro full song at 0.08 USD only when the user explicitly asks) "
            "through the configured provider and store them as persistent artifacts. "
            "Returns artifact ids and local paths."
        )

    def _provider_config(self) -> ProviderConfig | None:
        return self.provider_configs.get(self.config.provider)

    def _provider_client(self) -> MusicGenerationProvider | None:
        provider = self._provider_config()
        cls = get_music_gen_provider(self.config.provider)
        if cls is None:
            return None
        return cls(
            api_key=provider.api_key if provider else None,
            api_base=provider.api_base if provider else None,
            extra_headers=provider.extra_headers if provider else None,
            extra_body=provider.extra_body if provider else None,
            proxy=provider.proxy if provider else None,
        )

    def _resolve_reference_image(self, value: str) -> str:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                project_rooted_path(
                    value, workspace, [access.allowed_root, get_media_dir()],
                ),
                workspace=workspace,
                allowed_root=access.allowed_root,
                extra_allowed_roots=[get_media_dir()] if access.allowed_root is not None else None,
                strict=True,
            )
        except WorkspaceBoundaryError as exc:
            raise MusicGenerationError(
                "reference_image must be inside the workspace or navin media directory"
            ) from exc
        except OSError as exc:
            raise MusicGenerationError(f"reference image not found: {value}") from exc
        if not resolved.is_file():
            raise MusicGenerationError(f"reference image is not a file: {value}")
        if detect_image_mime(resolved.read_bytes()) is None:
            raise MusicGenerationError(f"unsupported reference image: {value}")
        return str(resolved)

    async def execute(
        self,
        prompt: str,
        reference_image: str | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._provider_client()
        if client is None:
            return ToolResult.error(
                f"Error: unsupported music generation provider '{self.config.provider}'"
            )

        try:
            ref = self._resolve_reference_image(reference_image) if reference_image else None
            response = await client.generate(
                prompt=prompt,
                model=self.config.model,
                reference_image=ref,
            )
            artifact = store_generated_music_artifact(
                response.audio,
                mime=response.mime,
                prompt=prompt,
                model=self.config.model,
                source_images=[ref] if ref else None,
                save_dir=self.config.save_dir,
                provider=self.config.provider,
                transcript=response.content or None,
            )
            await report_media_usage(self.config.model, response.raw, units=1.0)
            return generated_music_tool_result([artifact])
        except (ArtifactError, MusicGenerationError, OSError) as exc:
            from navin.providers.user_facing_errors import user_facing_llm_error

            return ToolResult.error(user_facing_llm_error(str(exc)))
