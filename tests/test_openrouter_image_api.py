# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OpenRouter Images API helpers (dedicated POST /images)."""

from __future__ import annotations

from navin.providers.image_generation import (
    _openrouter_image_size_fields,
    _openrouter_images_from_response,
)


def test_size_fields_map_resolution_tiers():
    assert _openrouter_image_size_fields("1K") == {"resolution": "1K"}
    assert _openrouter_image_size_fields("2k") == {"resolution": "2K"}
    assert _openrouter_image_size_fields("512") == {"resolution": "512"}


def test_seedream_45_bumps_1k_to_2k():
    assert _openrouter_image_size_fields(
        "1K", model="bytedance-seed/seedream-4.5"
    ) == {"resolution": "2K"}
    assert _openrouter_image_size_fields(
        None, model="bytedance-seed/seedream-4.5"
    ) == {"resolution": "2K"}
    assert _openrouter_image_size_fields(
        "2K", model="bytedance-seed/seedream-4.5"
    ) == {"resolution": "2K"}


def test_size_fields_map_explicit_pixels():
    assert _openrouter_image_size_fields("2048x2048") == {"size": "2048x2048"}


def test_images_from_dedicated_api_response():
    # Minimal valid 1x1 PNG
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    images = _openrouter_images_from_response(
        {
            "data": [
                {"b64_json": png_b64, "media_type": "image/png"},
            ]
        }
    )
    assert len(images) == 1
    assert images[0].startswith("data:image/png;base64,")
