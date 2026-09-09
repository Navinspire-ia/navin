# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Built-in render / export profiles for major platforms."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderProfile:
    """Named export target: resolution + aspect ratio for ffmpeg / HyperFrames."""

    id: str
    label: str
    width: int
    height: int
    aspect_ratio: str
    # Included in montage(action=package) default set when True.
    default_package: bool = True

    @property
    def ffmpeg_vf(self) -> str:
        return (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
            f"crop={self.width}:{self.height}"
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
            "default_package": self.default_package,
        }


# Canonical platform presets (product contract).
RENDER_PROFILES: tuple[RenderProfile, ...] = (
    RenderProfile(
        "youtube_landscape",
        "YouTube Landscape",
        1920,
        1080,
        "16:9",
    ),
    RenderProfile(
        "youtube_4k",
        "YouTube 4K",
        3840,
        2160,
        "16:9",
        default_package=False,
    ),
    RenderProfile(
        "youtube_shorts",
        "YouTube Shorts",
        1080,
        1920,
        "9:16",
    ),
    RenderProfile(
        "instagram_reels",
        "Instagram Reels",
        1080,
        1920,
        "9:16",
    ),
    RenderProfile(
        "instagram_feed",
        "Instagram Feed",
        1080,
        1080,
        "1:1",
    ),
    RenderProfile(
        "tiktok",
        "TikTok",
        1080,
        1920,
        "9:16",
    ),
    RenderProfile(
        "linkedin",
        "LinkedIn",
        1920,
        1080,
        "16:9",
    ),
    RenderProfile(
        "cinematic",
        "Cinematic",
        2560,
        1080,
        "21:9",
        default_package=False,
    ),
)

_BY_ID = {p.id: p for p in RENDER_PROFILES}


def get_profile(profile_id: str) -> RenderProfile | None:
    return _BY_ID.get((profile_id or "").strip().lower())


def list_profiles() -> list[dict[str, object]]:
    return [p.as_dict() for p in RENDER_PROFILES]


def resolve_package_profiles(raw: str | None) -> list[RenderProfile]:
    """Parse profiles= for package: empty/default, ``all``, or comma ids."""
    text = (raw or "").strip().lower()
    if not text or text in {"default", "social"}:
        return [p for p in RENDER_PROFILES if p.default_package]
    if text == "all":
        return list(RENDER_PROFILES)
    out: list[RenderProfile] = []
    seen: set[str] = set()
    for part in text.split(","):
        pid = part.strip()
        if not pid or pid in seen:
            continue
        profile = get_profile(pid)
        if profile is None:
            continue
        seen.add(profile.id)
        out.append(profile)
    return out or [p for p in RENDER_PROFILES if p.default_package]


def render_profiles_help() -> str:
    lines = ["Built-in render profiles:", ""]
    for p in RENDER_PROFILES:
        flag = " (default package)" if p.default_package else " (opt-in: profiles=all or id)"
        lines.append(
            f"- {p.id}: {p.label} {p.width}x{p.height} ({p.aspect_ratio}){flag}"
        )
    lines.append("")
    lines.append(
        "Use montage(action=package, path=..., profiles=default|all|id1,id2) "
        "or montage(action=render, composition=..., profile=youtube_shorts)."
    )
    return "\n".join(lines)
