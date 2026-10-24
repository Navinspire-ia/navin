# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Built-in motion template library (catalogue + props schemas).

Each template is a self-contained ``templates/<name>.html`` that loads the
vendored runtime (three.js, GSAP, ``navin-motion.js``), reads its props from
``NavinMotion.props`` and the brand kit from CSS variables, declares its
default duration and implements ``window.__navinSeek(t)`` through
``NavinMotion.mount``. Media props (images, MP4 clips) are local paths; the
renderer serves them over loopback and seeks video layers frame by frame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from navin.montage.motion.vendor import TEMPLATES_DIR


@dataclass(frozen=True, slots=True)
class TemplateInfo:
    name: str
    title: str
    description: str
    default_duration: float
    min_duration: float
    props: dict[str, dict[str, Any]] = field(default_factory=dict)
    example: dict[str, Any] = field(default_factory=dict)
    video_layers: tuple[str, ...] = ()
    overlay: bool = False

    @property
    def path(self):  # noqa: ANN201 - Path, kept lazy for frozen slots
        return TEMPLATES_DIR / f"{self.name}.html"

    def props_schema(self) -> dict[str, Any]:
        """JSON Schema for the props object."""
        required = [k for k, v in self.props.items() if v.get("required")]
        properties = {
            key: {k: v for k, v in spec.items() if k != "required"} for key, spec in self.props.items()
        }
        schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "default_duration_s": self.default_duration,
            "min_duration_s": self.min_duration,
            "props_schema": self.props_schema(),
            "example_props": self.example,
            "video_layers": list(self.video_layers),
            "overlay": self.overlay,
        }


def _s(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, **extra}


def _n(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "number", "description": description, **extra}


def _b(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "boolean", "description": description, **extra}


_MEDIA = (
    "Local path to an image (png/jpg/webp) or an MP4/MOV/WebM clip. Clips are "
    "seeked frame-accurately during render."
)
_BBOX = {
    "type": "array",
    "items": {"type": "number"},
    "minItems": 4,
    "maxItems": 4,
    "description": "Normalized [x, y, width, height] (0-1) of the region to highlight in the media.",
}

TEMPLATES: dict[str, TemplateInfo] = {
    info.name: info
    for info in (
        TemplateInfo(
            name="hero_intro",
            title="3D hero intro",
            description=(
                "Cinematic 3D reveal: the logo (or a product image) on a glossy "
                "rounded slab under sweeping key and rim lights, camera dolly-in, "
                "floating particles, then headline and subline rise in."
            ),
            default_duration=5.0,
            min_duration=2.5,
            props={
                "headline": _s("Main line under the 3D object."),
                "subheadline": _s("Smaller supporting line."),
                "image": _s("Optional product image shown on the slab instead of the logo. " + _MEDIA),
                "eyebrow": _s("Small uppercase label above the headline."),
            },
            example={"headline": "Meet Acme", "subheadline": "The fastest way to ship."},
        ),
        TemplateInfo(
            name="kinetic_title",
            title="Kinetic typography",
            description=(
                "Lines of type revealed word by word or letter by letter through "
                "masks, with an accent highlight sweeping under emphasized words."
            ),
            default_duration=4.0,
            min_duration=1.5,
            props={
                "lines": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "1-4 short lines, revealed in order.",
                    "required": True,
                },
                "emphasis": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Words to color with the accent and underline.",
                },
                "style": _s("Reveal style.", enum=["rise", "chars", "scale"], default="rise"),
                "align": _s("Text alignment.", enum=["center", "left"], default="center"),
                "eyebrow": _s("Small uppercase label above the lines."),
                "exit": _b("Animate the lines out at the end.", default=True),
            },
            example={"lines": ["Stop guessing.", "Start shipping."], "emphasis": ["shipping."]},
        ),
        TemplateInfo(
            name="feature_callout",
            title="Feature callout",
            description=(
                "Screen footage or a screenshot in a floating window; the camera "
                "zooms into a bounding box, a spotlight dims the rest and a label "
                "with a pointer names the feature."
            ),
            default_duration=6.0,
            min_duration=2.5,
            props={
                "media": _s(_MEDIA, required=True),
                "video_start": _n("Seconds into the clip where the scene starts.", default=0),
                "bbox": _BBOX,
                "label": _s("Feature name shown in the callout pill."),
                "title": _s("Optional title above the window."),
                "zoom": _n("Zoom factor toward the bbox (1 = none, max 3).", default=1.8),
                "zoom_out": _b("Pull back to the full window before the end.", default=True),
            },
            example={
                "media": "marketing/montage/demos/app.mp4",
                "bbox": [0.55, 0.2, 0.3, 0.25],
                "label": "One-click export",
                "title": "Export anywhere",
            },
            video_layers=("media",),
        ),
        TemplateInfo(
            name="device_mockup_3d",
            title="3D device mockup",
            description=(
                "The screenshot or clip mapped onto a 3D laptop, phone or browser "
                "window with studio lighting, a soft shadow, tilt and a camera move."
            ),
            default_duration=6.0,
            min_duration=2.5,
            props={
                "media": _s(_MEDIA, required=True),
                "video_start": _n("Seconds into the clip where the scene starts.", default=0),
                "device": _s("Device frame.", enum=["laptop", "phone", "browser"], default="laptop"),
                "camera": _s("Camera move.", enum=["orbit", "push", "tilt"], default="orbit"),
                "headline": _s("Optional line of text beside or under the device."),
                "url": _s("Address bar text for device=browser."),
            },
            example={"media": "marketing/montage/demos/app.mp4", "device": "laptop", "headline": "Your whole team, one screen"},
            video_layers=("media",),
        ),
        TemplateInfo(
            name="stats_counter",
            title="Stats counter",
            description="Up to four big numbers that count up with labels and progress arcs.",
            default_duration=4.5,
            min_duration=2.0,
            props={
                "title": _s("Optional heading above the stats."),
                "stats": {
                    "type": "array",
                    "required": True,
                    "description": "1-4 stats.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "number"},
                            "label": {"type": "string"},
                            "prefix": {"type": "string"},
                            "suffix": {"type": "string"},
                            "decimals": {"type": "integer"},
                        },
                        "required": ["value", "label"],
                    },
                },
            },
            example={
                "title": "Teams ship faster",
                "stats": [
                    {"value": 3.2, "suffix": "x", "decimals": 1, "label": "faster releases"},
                    {"value": 98, "suffix": "%", "label": "uptime"},
                    {"value": 12000, "suffix": "+", "label": "teams"},
                ],
            },
        ),
        TemplateInfo(
            name="split_compare",
            title="Before / after",
            description="Two images or clips side by side with a wipe divider that sweeps across.",
            default_duration=5.0,
            min_duration=2.0,
            props={
                "before": _s(_MEDIA, required=True),
                "after": _s(_MEDIA, required=True),
                "before_label": _s("Label on the left/top side.", default="Before"),
                "after_label": _s("Label on the right/bottom side.", default="After"),
                "title": _s("Optional title."),
                "video_start": _n("Seconds into the clips where the scene starts.", default=0),
            },
            example={"before": "before.png", "after": "after.png", "title": "From chaos to clarity"},
            video_layers=("before", "after"),
        ),
        TemplateInfo(
            name="lower_third",
            title="Lower third",
            description=(
                "Name and role bar that slides in with an accent line and out "
                "again. Transparent background: render as prores4444/webm_alpha "
                "for overlays."
            ),
            default_duration=5.0,
            min_duration=2.0,
            props={
                "name": _s("Primary line (person or product).", required=True),
                "role": _s("Secondary line."),
                "position": _s("Corner.", enum=["left", "right"], default="left"),
                "background": _b("Paint the brand background instead of transparency.", default=False),
            },
            example={"name": "Sarah Chen", "role": "Head of Product, Acme"},
            overlay=True,
        ),
        TemplateInfo(
            name="cta_end_card",
            title="CTA end card",
            description="Logo, tagline, URL and a call-to-action button with a light sweep.",
            default_duration=4.0,
            min_duration=2.0,
            props={
                "headline": _s("Main line; defaults to the brand tagline."),
                "button": _s("Button text.", default="Try it free"),
                "url": _s("URL text; defaults to the brand url."),
            },
            example={"headline": "Ship your next release today", "button": "Start free"},
        ),
        TemplateInfo(
            name="logo_sting",
            title="Logo sting",
            description="Short punchy logo reveal: light streaks converge, the logo flips in with a glint and a particle burst.",
            default_duration=2.5,
            min_duration=1.5,
            props={"tagline": _s("Optional line under the logo; defaults to none.")},
            example={},
        ),
        TemplateInfo(
            name="gradient_stage",
            title="Gradient stage",
            description="Animated brand mesh-gradient background with optional title, for intros or as a backdrop.",
            default_duration=6.0,
            min_duration=1.0,
            props={
                "title": _s("Optional centered title."),
                "subtitle": _s("Optional subtitle."),
                "eyebrow": _s("Optional small uppercase label."),
                "intensity": _n("Color intensity 0-1.5.", default=1),
                "speed": _n("Flow speed multiplier.", default=1),
                "fade_out": _b("Fade the copy out at the end.", default=True),
            },
            example={"title": "Build at the speed of thought", "subtitle": "Acme for teams"},
        ),
    )
}


def get_template(name: str) -> TemplateInfo | None:
    return TEMPLATES.get((name or "").strip().lower())


def list_templates() -> list[dict[str, Any]]:
    return [info.to_dict() for info in TEMPLATES.values()]


__all__ = ["TEMPLATES", "TemplateInfo", "get_template", "list_templates"]
