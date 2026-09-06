"""Image generation tool."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.path_utils import project_rooted_path
from navin.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.config.paths import get_media_dir
from navin.config_base import Base
from navin.providers.image_generation import (
    ImageGenerationError,
    ImageGenerationProvider,
    get_image_gen_provider,
)
from navin.providers.media_credentials import (
    media_credentials_ready,
    resolve_media_tool_enabled,
)
from navin.providers.media_usage import report_media_usage
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path
from navin.utils.artifacts import (
    ArtifactError,
    generated_image_tool_result,
    store_generated_image_artifact,
)
from navin.utils.helpers import detect_image_mime

if TYPE_CHECKING:
    from navin.config.schema import ProviderConfig


class ImageGenerationToolConfig(Base):
    """Image generation tool configuration.

    ``enabled`` is tri-state: unset means "on as soon as the selected provider
    holds a usable credential", so a fresh install with an API key can already
    illustrate content instead of silently producing text only.
    """

    enabled: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    # Abonnés Navin : provider managed + Seedream. BYOK peut changer librement.
    # Seedream 4.5 exige ≥ ~3,7M pixels (2K+), pas 1K.
    # Vide = aucun choix. Les abonnés reçoivent "navin" écrit explicitement par
    # la synchro du catalogue ; sans abonnement, ne rien présélectionner.
    provider: str = ""
    model: str = "google/gemini-3.1-flash-image"
    default_aspect_ratio: str = "1:1"
    default_image_size: str = "2K"
    max_images_per_turn: int = Field(default=4, ge=1, le=8)
    save_dir: str = "generated"
    # Run the deterministic (Pillow) visual gate on each still generated inside
    # the Marketing module and attach the verdict. This makes the QA a real
    # interceptor of the generation flow (pixels/dimensions/aspect/sharpness),
    # not just a tool the agent may forget to call. Fidelity/vision checks still
    # need the visual_qa tool with references.
    marketing_auto_qa: bool = True


@tool_parameters(
    tool_parameters_schema(
        prompt=StringSchema(
            "Detailed image generation or edit prompt. Include style, subject, composition, colors, and constraints.",
            min_length=1,
        ),
        reference_images=ArraySchema(
            StringSchema("Local path of an existing image artifact or user-provided image to use as an edit reference."),
            description="Optional local image paths. Use generated artifact paths for iterative edits.",
        ),
        aspect_ratio=StringSchema(
            "Optional output aspect ratio, e.g. 1:1, 16:9, 9:16, 4:3.",
        ),
        image_size=StringSchema(
            "Optional output size hint supported by the configured provider, e.g. 1K, 2K, 4K, or 1024x1024.",
        ),
        count=IntegerSchema(
            description="Number of images to generate in this turn.",
            minimum=1,
            maximum=8,
        ),
        required=["prompt"],
    )
)
class ImageGenerationTool(Tool):
    """Generate persistent image artifacts through the configured image provider."""

    config_key = "image_generation"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return ImageGenerationToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        config = ctx.config.image_generation
        provider_configs = getattr(ctx, "image_generation_provider_configs", None) or {}
        return resolve_media_tool_enabled(
            config.enabled,
            media_credentials_ready(config.provider, provider_configs.get(config.provider)),
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(
            workspace=ctx.workspace,
            config=ctx.config.image_generation,
            provider_configs=ctx.image_generation_provider_configs,
        )

    def __init__(
        self,
        *,
        workspace: str | Path,
        config: ImageGenerationToolConfig,
        provider_config: ProviderConfig | None = None,
        provider_configs: dict[str, ProviderConfig] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config
        self.provider_configs = dict(provider_configs or {})
        if provider_config is not None and "openrouter" not in self.provider_configs:
            self.provider_configs["openrouter"] = provider_config

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def description(self) -> str:
        return (
            "Generate or edit images and store them as persistent artifacts. "
            "Returns artifact ids and local paths. For edits, pass prior generated image paths "
            "or user image paths as reference_images."
        )

    def _provider_config(self) -> ProviderConfig | None:
        return self.provider_configs.get(self.config.provider)

    def _provider_client(self) -> ImageGenerationProvider | None:
        provider = self._provider_config()
        cls = get_image_gen_provider(self.config.provider)
        if cls is None:
            return None
        kwargs = {
            "api_key": provider.api_key if provider else None,
            "api_base": provider.api_base if provider else None,
            "extra_headers": provider.extra_headers if provider else None,
            "extra_body": provider.extra_body if provider else None,
            "proxy": provider.proxy if provider else None,
        }
        return cls(**kwargs)

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
            raise ImageGenerationError(
                "reference_images must be inside the workspace or navin media directory"
            ) from exc
        except OSError as exc:
            raise ImageGenerationError(f"reference image not found: {value}") from exc
        if not resolved.is_file():
            raise ImageGenerationError(f"reference image is not a file: {value}")
        raw = resolved.read_bytes()
        if detect_image_mime(raw) is None:
            raise ImageGenerationError(f"unsupported reference image: {value}")
        return str(resolved)

    def _resolve_reference_images(self, values: list[str] | None) -> list[str]:
        if not values:
            return []
        return [self._resolve_reference_image(value) for value in values if value]

    def _maybe_apply_marketing_qa(self, artifacts: list[dict[str, Any]]) -> None:
        """Attach a deterministic visual QA verdict to marketing stills.

        Enforcement lives here, in the generation path, so a marketing deliverable
        always carries a machine verdict instead of relying on the agent to call
        the visual_qa tool. Best-effort: QA must never fail image generation.
        """
        if not self.config.marketing_auto_qa:
            return
        try:
            from navin.agent.tools.context import current_request_context
            from navin.command.modules import (
                PRODUCT_MODULE_METADATA_KEY,
                normalize_product_module,
            )

            request = current_request_context()
            metadata = (request.metadata if request else {}) or {}
            module = normalize_product_module(
                metadata.get(PRODUCT_MODULE_METADATA_KEY)
            )
            if module != "marketing":
                return
            from navin.marketing.visual_qa import deterministic_gate, pillow_ready

            if not pillow_ready():
                return
            for artifact in artifacts:
                path = artifact.get("path")
                if not path:
                    continue
                try:
                    verdict = deterministic_gate(path)
                except Exception:  # noqa: BLE001 - never fail generation on QA
                    continue
                artifact["visual_qa"] = {
                    "verdict": verdict.get("verdict"),
                    "score": verdict.get("score"),
                    "gate": "deterministic",
                    "note": (
                        "Run the visual_qa tool with brand/product references for "
                        "fidelity and text checks before delivery."
                        if verdict.get("verdict") != "PASS"
                        else "Deterministic pixel checks passed; add reference-based "
                        "fidelity QA before final delivery."
                    ),
                }
        except Exception:  # noqa: BLE001 - QA is advisory, never blocks generation
            return

    async def execute(
        self,
        prompt: str,
        reference_images: list[str] | None = None,
        aspect_ratio: str | None = None,
        image_size: str | None = None,
        count: int | None = None,
        **kwargs: Any,
    ) -> str:
        client = self._provider_client()
        if client is None:
            return ToolResult.error(f"Error: unsupported image generation provider '{self.config.provider}'")

        requested = count or 1
        if requested > self.config.max_images_per_turn:
            return ToolResult.error(
                "Error: count exceeds tools.imageGeneration.maxImagesPerTurn "
                f"({self.config.max_images_per_turn})"
            )

        try:
            refs = self._resolve_reference_images(reference_images)
            artifacts: list[dict[str, Any]] = []
            while len(artifacts) < requested:
                response = await client.generate(
                    prompt=prompt,
                    model=self.config.model,
                    reference_images=refs,
                    aspect_ratio=aspect_ratio or self.config.default_aspect_ratio,
                    image_size=image_size or self.config.default_image_size,
                )
                produced = 0
                for image_data_url in response.images:
                    artifact = store_generated_image_artifact(
                        image_data_url,
                        prompt=prompt,
                        model=self.config.model,
                        source_images=refs,
                        save_dir=self.config.save_dir,
                        provider=self.config.provider,
                    )
                    artifacts.append(artifact)
                    produced += 1
                    if len(artifacts) >= requested:
                        break
                await report_media_usage(
                    self.config.model, response.raw, units=produced
                )
            self._maybe_apply_marketing_qa(artifacts)
            return generated_image_tool_result(artifacts)
        except (ArtifactError, ImageGenerationError, OSError) as exc:
            from navin.providers.user_facing_errors import user_facing_llm_error

            return ToolResult.error(user_facing_llm_error(str(exc)))
