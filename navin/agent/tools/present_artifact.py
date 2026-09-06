"""Present a chat artifact in the WebUI Artifact Canvas panel."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.context import current_request_context
from navin.agent.tools.path_utils import closest_existing_match
from navin.artifacts.detect import extract_fenced_artifacts, upsert_fenced_artifacts
from navin.artifacts.store import ARTIFACT_TYPES, ArtifactError, ArtifactStore
from navin.bus.outbound_events import (
    ArtifactSelectEvent,
    ArtifactUpsertEvent,
    outbound_message_for_event,
)


def _emit_upsert(bus: Any, chat_id: str, artifact: dict[str, Any]) -> None:
    bus.outbound.put_nowait(
        outbound_message_for_event(
            channel="websocket",
            chat_id=chat_id,
            event=ArtifactUpsertEvent(artifact=dict(artifact)),
        )
    )


def _emit_select(bus: Any, chat_id: str, artifact_id: str) -> None:
    bus.outbound.put_nowait(
        outbound_message_for_event(
            channel="websocket",
            chat_id=chat_id,
            event=ArtifactSelectEvent(artifact_id=artifact_id),
        )
    )


def present_detected_artifacts(
    *,
    chat_id: str,
    text: str,
    bus: Any | None,
    root: Path | None = None,
    select: bool = True,
) -> list[dict[str, Any]]:
    """Upsert fenced HTML/Mermaid blocks and notify the WebUI when *bus* is set."""
    artifacts = upsert_fenced_artifacts(chat_id, text, root=root)
    if not artifacts or bus is None:
        return artifacts
    for artifact in artifacts:
        try:
            _emit_upsert(bus, chat_id, artifact)
        except Exception:
            continue
    if select and artifacts:
        try:
            _emit_select(bus, chat_id, str(artifacts[-1]["id"]))
        except Exception:
            pass
    return artifacts


class PresentArtifactTool(Tool):
    """Persist an artifact and open it in the WebUI Artifact Canvas."""

    _scopes = {"core", "subagent"}

    def __init__(self, bus: Any = None, working_dir: str | None = None) -> None:
        self._bus = bus
        self._working_dir = working_dir

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(bus=ctx.bus, working_dir=ctx.workspace)

    @property
    def name(self) -> str:
        return "present_artifact"

    @property
    def description(self) -> str:
        return (
            "Present a visual artifact beside the chat in the Navin Artifact "
            "Canvas (HTML preview, Markdown, Mermaid diagram, or a workspace "
            "file). Prefer this when the user should see a concrete result "
            "instead of only reading code in the transcript. For HTML or "
            "Mermaid, pass the full content. For an existing file, pass type "
            "file and path. You can also pass scan_content with a markdown "
            "reply containing ```html / ```mermaid fences to auto-present them."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": list(ARTIFACT_TYPES),
                    "description": (
                        "Artifact kind: html, markdown, mermaid, or file. "
                        "Omit when using scan_content only."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Short title shown in the canvas tab.",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "Artifact body for html / markdown / mermaid. "
                        "Required unless path or scan_content is set."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Workspace file to present as a file artifact "
                        "(type=file). Relative paths resolve against the project root."
                    ),
                },
                "id": {
                    "type": "string",
                    "description": (
                        "Optional stable id to update an existing artifact "
                        "instead of creating a new one."
                    ),
                },
                "select": {
                    "type": "boolean",
                    "description": "Focus this artifact in the canvas (default true).",
                },
                "scan_content": {
                    "type": "string",
                    "description": (
                        "Optional markdown text; any ```html or ```mermaid "
                        "fenced blocks are also upserted as artifacts."
                    ),
                },
            },
            "required": [],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> Any:
        ctx = current_request_context()
        if ctx is None or ctx.channel != "websocket":
            return ToolResult.error(
                "Error: Artifact Canvas only exists in the Navin WebUI / desktop."
            )
        if self._bus is None:
            return ToolResult.error(
                "Error: no message bus available to reach the Artifact Canvas."
            )

        chat_id = ctx.chat_id
        select = kwargs.get("select", True)
        if not isinstance(select, bool):
            select = str(select).strip().lower() not in {"0", "false", "no"}

        presented: list[dict[str, Any]] = []
        scan = kwargs.get("scan_content")
        if isinstance(scan, str) and scan.strip():
            presented.extend(
                present_detected_artifacts(
                    chat_id=chat_id, text=scan, bus=self._bus, select=select,
                )
            )

        artifact_type = str(kwargs.get("type") or "").strip().lower()
        content = kwargs.get("content")
        path_raw = str(kwargs.get("path") or "").strip()
        title = kwargs.get("title")
        artifact_id = kwargs.get("id")

        # Bare markdown content with fences and no type => auto-detect only.
        if (
            not artifact_type
            and not path_raw
            and isinstance(content, str)
            and extract_fenced_artifacts(content)
        ):
            presented.extend(
                present_detected_artifacts(
                    chat_id=chat_id, text=content, bus=self._bus, select=select,
                )
            )
            content = None

        substitution_note = ""
        if artifact_type or path_raw or isinstance(content, str):
            try:
                store = ArtifactStore(chat_id)
                body: str | bytes
                source_path: str | None = None
                if path_raw or artifact_type == "file":
                    if not path_raw:
                        return ToolResult.error("Error: type=file requires path.")
                    root = Path(self._working_dir or os.getcwd())
                    target = Path(path_raw).expanduser()
                    if not target.is_absolute():
                        target = root / target
                    target = target.resolve()
                    if not target.exists() or not target.is_file():
                        recovered = closest_existing_match(target, root)
                        if recovered is None:
                            return ToolResult.error(f"Error: file not found: {path_raw}")
                        substitution_note = (
                            f" (note: {path_raw} not found; presented closest "
                            f"match {recovered.name} instead)"
                        )
                        target = recovered
                    body = target.read_bytes()
                    source_path = str(target)
                    if not artifact_type:
                        artifact_type = "file"
                    if not title:
                        title = target.name
                    if artifact_type == "file":
                        suffix = target.suffix.lower()
                        if suffix in {".html", ".htm"}:
                            artifact_type = "html"
                            body = body.decode("utf-8", errors="replace")
                        elif suffix in {".md", ".markdown"}:
                            artifact_type = "markdown"
                            body = body.decode("utf-8", errors="replace")
                        elif suffix in {".mmd", ".mermaid"}:
                            artifact_type = "mermaid"
                            body = body.decode("utf-8", errors="replace")
                else:
                    if content is None:
                        return ToolResult.error(
                            "Error: content is required for html / markdown / mermaid."
                        )
                    if not artifact_type:
                        return ToolResult.error(
                            "Error: type is required when passing content."
                        )
                    body = str(content)

                artifact = store.upsert(
                    artifact_type=artifact_type,
                    title=str(title) if title is not None else None,
                    content=body,
                    artifact_id=str(artifact_id) if artifact_id else None,
                    source_path=source_path,
                )
            except ArtifactError as exc:
                return ToolResult.error(f"Error: {exc.message}")
            except OSError as exc:
                return ToolResult.error(f"Error: could not read file: {exc}")

            try:
                _emit_upsert(self._bus, chat_id, artifact)
                if select:
                    _emit_select(self._bus, chat_id, str(artifact["id"]))
            except Exception as exc:
                return ToolResult.error(
                    f"Error: could not reach the Artifact Canvas: {exc}"
                )
            presented.append(artifact)

        if not presented:
            return ToolResult.error(
                "Error: provide type (+ content or path), or scan_content "
                "with ```html / ```mermaid fences."
            )

        labels = ", ".join(
            f"{a.get('title')} ({a.get('type')})" for a in presented
        )
        return f"Presented {len(presented)} artifact(s) in Canvas: {labels}.{substitution_note}"
