# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Montage tool actions ``motion_templates`` and ``motion_render``.

Kept here so the tool module only routes arguments; every rule about
templates, storyboards and outputs lives with the engine.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from navin.montage.motion.catalog import TEMPLATES, get_template, list_templates
from navin.montage.motion.media import PathResolver
from navin.montage.motion.renderer import CODECS, RenderSettings, render_motion
from navin.montage.motion.storyboard import load_storyboard_json, render_storyboard

DEFAULT_EXPORT_DIR = "marketing/montage/exports"


def templates_payload() -> dict[str, Any]:
    return {
        "templates": list_templates(),
        "storyboard_contract": {
            "version": 1,
            "format": "16:9 | 9:16 | 1:1 (optional resolution: 4k for 16:9)",
            "fps": "30 | 60",
            "brand": "brand kit object or path (defaults to the marketing desk brand)",
            "scenes": [
                {
                    "template": "<name from templates>",
                    "duration": "seconds",
                    "props": "object matching props_schema",
                    "transition": "cut | fade | slide | zoom | whip (how the scene enters)",
                },
                {"type": "footage", "path": "processed clip", "duration": "seconds", "start": "trim start"},
            ],
            "audio": {"voiceover": "path", "music": "path", "sfx": [{"kind": "whoosh", "at": 3.5}]},
        },
        "brand_kit_contract": {
            "name": "Acme",
            "logo_path": "path",
            "logo_dark_path": "optional path",
            "colors": {"primary": "#hex", "secondary": "#hex", "accent": "#hex", "background": "#hex", "text": "#hex"},
            "fonts": {"heading": "family", "body": "family"},
            "font_files": ["optional .ttf/.otf/.woff2 paths"],
            "tagline": "optional",
            "url": "optional",
        },
        "codecs": sorted(CODECS),
        "hint": (
            "Plan scenes as a storyboard (hero_intro or logo_sting to open, "
            "feature_callout / device_mockup_3d over real screen footage, stats_counter "
            "for proof, cta_end_card to close) then call montage(action=motion_render, "
            "storyboard=...). Overlays (lower_third) can render alone with "
            "template=lower_third codec=prores4444."
        ),
    }


def _progress_emitter() -> Callable[[float | None, str], None]:
    """Throttled task_progress updates for the current chat turn."""
    from navin.utils.task_progress import emit_task_progress

    state = {"last": -1.0, "t": 0.0}
    loop = asyncio.get_running_loop()

    def emit(fraction: float | None, label: str) -> None:
        now = time.monotonic()
        pct = None if fraction is None else round(max(0.0, min(1.0, fraction)) * 100, 1)
        if pct is not None and pct < 100 and (pct - state["last"] < 2 and now - state["t"] < 2):
            return
        state["last"] = pct if pct is not None else state["last"]
        state["t"] = now
        loop.create_task(emit_task_progress(label=label, percent=pct))

    return emit


def _output_path(root: Path, output: str | None, default_name: str, suffix: str) -> Path:
    target = (output or "").strip() or f"{DEFAULT_EXPORT_DIR}/{default_name}{suffix}"
    path = Path(target).expanduser()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("output must stay inside the workspace") from exc
    return path.with_suffix(suffix)


def _parse_json_arg(value: str | None, name: str) -> Any:
    text = (value or "").strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    return text


async def motion_render(
    root: Path,
    *,
    resolve: PathResolver,
    resolve_file: Callable[[str], str],
    storyboard: str | None = None,
    template: str | None = None,
    props: str | None = None,
    brand: str | None = None,
    output: str | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    duration: float | None = None,
    codec: str | None = None,
) -> dict[str, Any]:
    """Render a storyboard (preferred) or a single template to a file.

    ``resolve`` maps media references to allowed files (``None`` when
    refused); ``resolve_file`` resolves the storyboard / brand JSON paths and
    raises when they are outside the workspace.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    emit = _progress_emitter()
    try:
        brand_value = _parse_json_arg(brand, "brand")
        if isinstance(brand_value, str):
            brand_value = resolve_file(brand_value)
        if (storyboard or "").strip():
            raw_value = _parse_json_arg(storyboard, "storyboard")
            base_dir = root
            if isinstance(raw_value, str):
                raw_value = resolve_file(raw_value)
            raw, base = load_storyboard_json(raw_value, base_dir=root)
            if brand_value is not None:
                raw = {**raw, "brand": brand_value}
            elif isinstance(raw.get("brand"), str):
                raw = {**raw, "brand": resolve_file(raw["brand"])}
            base_dir = base or root
            out = _output_path(root, output, f"motion-{stamp}", ".mp4")

            def on_story(row: dict[str, Any]) -> None:
                emit(row.get("fraction"), "Rendering motion video")

            result = await render_storyboard(
                raw, out, base_dir=base_dir, resolve=resolve, on_progress=on_story
            )
        elif (template or "").strip():
            info = get_template(template or "")
            if info is None:
                return {
                    "ok": False,
                    "error": f"unknown template {template!r}",
                    "templates": sorted(TEMPLATES),
                }
            props_value = _parse_json_arg(props, "props") or {}
            if not isinstance(props_value, dict):
                return {"ok": False, "error": "props must be a JSON object"}
            chosen = (codec or "h264").strip().lower()
            if chosen not in CODECS:
                return {"ok": False, "error": f"codec must be one of {', '.join(sorted(CODECS))}"}
            out = _output_path(root, output, f"{info.name}-{stamp}", CODECS[chosen]["suffix"])
            settings = RenderSettings(
                width=int(width or 1920),
                height=int(height or 1080),
                fps=int(fps or 30),
                duration=float(duration) if duration else info.default_duration,
                codec=chosen,
            )

            def on_frames(progress: Any) -> None:
                emit(progress.fraction, f"Rendering {info.title}")

            result = await render_motion(
                output=out,
                template=info.name,
                props=props_value,
                brand=brand_value,
                settings=settings,
                resolve=resolve,
                base_dir=root,
                on_progress=on_frames,
            )
        else:
            return {
                "ok": False,
                "error": "motion_render needs storyboard= (JSON or path) or template= with props=",
                "fix": "Call montage(action=motion_templates) for names, props and the storyboard contract.",
            }
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if result.get("ok") and result.get("output"):
        try:
            result["output"] = Path(result["output"]).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
        result["next_step"] = (
            "Call the message tool with this output path in the media parameter "
            "to deliver the video to the user."
        )
    return result


__all__ = ["DEFAULT_EXPORT_DIR", "motion_render", "templates_payload"]
