# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic and vision-assisted visual QA for Marketing assets."""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from navin.agent.skill_routing import build_action_skill_context
from navin.providers.visual_qa import (
    VisualQAProvider,
    VisualQAProviderResult,
)
from navin.utils.atomic_io import atomic_write_text

try:
    from PIL import Image, ImageChops, ImageFilter, ImageStat
except ImportError:  # pragma: no cover - readiness catches optional installs
    Image = ImageChops = ImageFilter = ImageStat = None  # type: ignore[assignment]


FIDELITY_CLAIMS = frozenset(
    {"product_fidelity", "logo_fidelity", "text_accuracy", "color_fidelity"}
)
_STATUS_RANK = {"PASS": 0, "WARN": 1, "BLOCK": 2}


@dataclass(frozen=True, slots=True)
class VisualQAPolicy:
    """Configurable scoring and deterministic check thresholds."""

    pass_score: float = 85.0
    warn_score: float = 65.0
    ratio_tolerance: float = 0.02
    min_sharpness: float = 80.0
    min_contrast: float = 24.0
    marketplace_white_min: int = 250
    marketplace_white_fraction: float = 0.95
    default_safe_zone_percent: float = 5.0
    deterministic_weight: float = 0.45
    vision_weight: float = 0.55
    block_on_vision_failure: bool = True

    def __post_init__(self) -> None:
        if not 0 <= self.warn_score <= self.pass_score <= 100:
            raise ValueError("visual QA scores must satisfy 0 <= warn <= pass <= 100")
        if not math.isclose(
            self.deterministic_weight + self.vision_weight, 1.0, abs_tol=0.001
        ):
            raise ValueError("visual QA weights must sum to 1")


def pillow_ready() -> bool:
    return Image is not None


def _finding(
    check: str,
    status: str,
    score: float,
    evidence: list[str],
    recommendation: str = "",
    *,
    source: str = "deterministic",
) -> dict[str, Any]:
    return {
        "check": check,
        "source": source,
        "status": status,
        "score": round(max(0.0, min(100.0, score)), 2),
        "evidence": evidence,
        "recommendation": recommendation,
    }


def _parse_ratio(value: str | None) -> float | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*", value)
    if not match:
        return None
    width, height = float(match.group(1)), float(match.group(2))
    return width / height if height > 0 else None


def _edge_variance(image: Any) -> float:
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return float(ImageStat.Stat(edges).var[0])


def _contrast(image: Any) -> float:
    return float(ImageStat.Stat(image.convert("L").resize((256, 256))).stddev[0])


def _corner_background(image: Any, fraction: float = 0.06) -> Any:
    width, height = image.size
    dx, dy = max(1, round(width * fraction)), max(1, round(height * fraction))
    crops = (
        image.crop((0, 0, dx, dy)),
        image.crop((width - dx, 0, width, dy)),
        image.crop((0, height - dy, dx, height)),
        image.crop((width - dx, height - dy, width, height)),
    )
    samples: list[tuple[int, int, int]] = []
    for crop in crops:
        samples.extend(_pixels(crop.convert("RGB").resize((8, 8))))
    channels = list(zip(*samples))
    return tuple(round(sum(channel) / len(channel)) for channel in channels)


def _foreground_bbox(image: Any, background: tuple[int, int, int]) -> tuple[int, int, int, int] | None:
    rgb = image.convert("RGB")
    backdrop = Image.new("RGB", rgb.size, background)
    difference = ImageChops.difference(rgb, backdrop).convert("L")
    mask = difference.point(lambda value: 255 if value >= 18 else 0)
    return mask.getbbox()


def _white_background_fraction(image: Any, threshold: int) -> float:
    rgb = image.convert("RGB").resize((128, 128))
    band = max(1, round(min(rgb.size) * 0.06))
    width, height = rgb.size
    pixels: list[Any] = []
    for crop in (
        rgb.crop((0, 0, width, band)),
        rgb.crop((0, height - band, width, height)),
        rgb.crop((0, band, band, height - band)),
        rgb.crop((width - band, band, width, height - band)),
    ):
        pixels.extend(_pixels(crop))
    return sum(min(pixel) >= threshold for pixel in pixels) / max(1, len(pixels))


def _pixels(image: Any) -> list[Any]:
    flattened = getattr(image, "get_flattened_data", None)
    return list(flattened() if callable(flattened) else image.getdata())


def deterministic_checks(
    image_path: Path,
    *,
    policy: VisualQAPolicy,
    requirements: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Inspect dimensions, pixels, alpha, background and safe zones with Pillow."""
    if not pillow_ready():
        raise RuntimeError("Pillow is required for visual QA")
    requirements = dict(requirements or {})
    with Image.open(image_path) as opened:
        opened.load()
        image = opened.copy()
    width, height = image.size
    metadata = {
        "path": str(image_path),
        "format": getattr(opened, "format", None),
        "mode": image.mode,
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 6) if height else None,
    }
    checks: list[dict[str, Any]] = []

    expected_width = requirements.get("width")
    expected_height = requirements.get("height")
    min_width = int(requirements.get("min_width") or 0)
    min_height = int(requirements.get("min_height") or 0)
    exact_ok = (expected_width in (None, width)) and (expected_height in (None, height))
    minimum_ok = width >= min_width and height >= min_height
    dimensions_ok = exact_ok and minimum_ok and width > 0 and height > 0
    expected = (
        f"exact {expected_width or '*'}x{expected_height or '*'}, "
        f"minimum {min_width or 1}x{min_height or 1}"
    )
    checks.append(
        _finding(
            "dimensions",
            "PASS" if dimensions_ok else "BLOCK",
            100 if dimensions_ok else 0,
            [f"observed {width}x{height}; expected {expected}"],
            "" if dimensions_ok else "Export at the required pixel dimensions.",
        )
    )

    expected_ratio = _parse_ratio(str(requirements.get("aspect_ratio") or ""))
    actual_ratio = width / height if height else 0.0
    ratio_drift = (
        abs(actual_ratio / expected_ratio - 1.0) if expected_ratio else 0.0
    )
    ratio_ok = expected_ratio is None or ratio_drift <= policy.ratio_tolerance
    checks.append(
        _finding(
            "aspect_ratio",
            "PASS" if ratio_ok else "BLOCK",
            100 if ratio_ok else max(0, 100 - ratio_drift * 500),
            [
                f"observed {actual_ratio:.4f}; "
                f"expected {requirements.get('aspect_ratio') or 'not constrained'}; "
                f"drift {ratio_drift:.2%}"
            ],
            "" if ratio_ok else "Reframe without stretching to the required ratio.",
        )
    )

    sharpness = _edge_variance(image)
    sharp_ok = sharpness >= policy.min_sharpness
    checks.append(
        _finding(
            "sharpness",
            "PASS" if sharp_ok else "WARN",
            min(100, sharpness / max(1, policy.min_sharpness) * 85),
            [f"edge variance {sharpness:.2f}; minimum {policy.min_sharpness:.2f}"],
            "" if sharp_ok else "Use a sharper source or reduce enlargement.",
        )
    )

    contrast = _contrast(image)
    contrast_ok = contrast >= policy.min_contrast
    checks.append(
        _finding(
            "contrast",
            "PASS" if contrast_ok else "WARN",
            min(100, contrast / max(1, policy.min_contrast) * 85),
            [f"luminance standard deviation {contrast:.2f}; minimum {policy.min_contrast:.2f}"],
            "" if contrast_ok else "Increase subject and text contrast.",
        )
    )

    has_alpha = "A" in image.getbands()
    alpha_extrema = image.getchannel("A").getextrema() if has_alpha else (255, 255)
    alpha_required = requirements.get("alpha")
    alpha_ok = (
        alpha_required is None
        or (bool(alpha_required) and has_alpha and alpha_extrema[0] < 255)
        or (not bool(alpha_required) and (not has_alpha or alpha_extrema == (255, 255)))
    )
    checks.append(
        _finding(
            "alpha",
            "PASS" if alpha_ok else "BLOCK",
            100 if alpha_ok else 0,
            [f"mode {image.mode}; alpha range {alpha_extrema}"],
            "" if alpha_ok else "Export with the required transparency policy.",
        )
    )

    marketplace = str(requirements.get("marketplace") or "").strip().lower()
    white_required = marketplace in {"amazon", "marketplace_white"} or bool(
        requirements.get("white_background")
    )
    white_fraction = _white_background_fraction(
        image, policy.marketplace_white_min
    )
    white_ok = not white_required or white_fraction >= policy.marketplace_white_fraction
    checks.append(
        _finding(
            "marketplace_background",
            "PASS" if white_ok else "BLOCK",
            100 if white_ok else white_fraction * 100,
            [
                f"near-white pixels {white_fraction:.2%} at RGB >= "
                f"{policy.marketplace_white_min}; required {white_required}"
            ],
            "" if white_ok else "Use a pure white marketplace background.",
        )
    )

    background = _corner_background(image)
    bbox = _foreground_bbox(image, background)
    safe = requirements.get("safe_zone_percent")
    safe_percent = (
        float(safe) if isinstance(safe, int | float) else policy.default_safe_zone_percent
    )
    safe_pixels_x = width * safe_percent / 100
    safe_pixels_y = height * safe_percent / 100
    if bbox:
        left, top, right, bottom = bbox
        safe_ok = (
            left >= safe_pixels_x
            and top >= safe_pixels_y
            and width - right >= safe_pixels_x
            and height - bottom >= safe_pixels_y
        )
        evidence = [
            f"foreground bbox {bbox}; required margin {safe_percent:.1f}% "
            f"({safe_pixels_x:.1f}px x, {safe_pixels_y:.1f}px y)"
        ]
    else:
        safe_ok = False
        evidence = ["no foreground could be separated from the corner background"]
    checks.append(
        _finding(
            "safe_zones",
            "PASS" if safe_ok else "WARN",
            100 if safe_ok else 50,
            evidence,
            "" if safe_ok else "Move critical content away from placement edges.",
        )
    )
    metadata["background_rgb"] = background
    metadata["foreground_bbox"] = bbox
    return metadata, checks


def _vision_findings(result: VisualQAProviderResult) -> list[dict[str, Any]]:
    dimensions = result.analysis.get("dimensions") or {}
    return [
        _finding(
            name,
            str(item["status"]),
            float(item["score"]),
            list(item["evidence"]),
            str(item.get("recommendation") or ""),
            source="vision",
        )
        for name, item in dimensions.items()
        if isinstance(item, dict)
    ]


def _weighted_average(findings: list[dict[str, Any]]) -> float:
    scores = [float(item["score"]) for item in findings]
    return sum(scores) / len(scores) if scores else 0.0


def _overall_verdict(
    score: float, findings: list[dict[str, Any]], policy: VisualQAPolicy
) -> str:
    strongest = max(
        (str(item.get("status") or "BLOCK") for item in findings),
        key=lambda status: _STATUS_RANK.get(status, 2),
        default="BLOCK",
    )
    if strongest == "BLOCK" or score < policy.warn_score:
        return "BLOCK"
    if strongest == "WARN" or score < policy.pass_score:
        return "WARN"
    return "PASS"


def deterministic_gate(
    candidate: str | Path,
    *,
    requirements: dict[str, Any] | None = None,
    policy: VisualQAPolicy | None = None,
) -> dict[str, Any]:
    """Pixel-only gate (no provider, no references) for auto-enforcement.

    Runs the Pillow checks (dimensions, aspect, sharpness, contrast, alpha,
    marketplace background, safe zones) and returns a conservative verdict.
    Cheap and always available when Pillow is installed, so it can run right
    after generation without needing brand references or a vision model.
    """
    active_policy = policy or VisualQAPolicy()
    candidate_path = Path(candidate).expanduser().resolve()
    if not candidate_path.is_file():
        raise FileNotFoundError(f"candidate image not found: {candidate}")
    if not pillow_ready():
        return {
            "verdict": "SKIPPED",
            "reason": "Pillow not installed",
            "findings": [],
            "score": None,
        }
    metadata, findings = deterministic_checks(
        candidate_path, policy=active_policy, requirements=requirements
    )
    score = _weighted_average(findings)
    verdict = _overall_verdict(score, findings, active_policy)
    return {
        "verdict": verdict,
        "score": round(score, 2),
        "metadata": metadata,
        "findings": findings,
    }


async def run_visual_qa(
    candidate: str | Path,
    *,
    references: list[str | Path] | None = None,
    claims: list[str] | None = None,
    requirements: dict[str, Any] | None = None,
    provider: VisualQAProvider | None = None,
    policy: VisualQAPolicy | None = None,
    workspace: str | Path | None = None,
) -> dict[str, Any]:
    """Run deterministic and vision checks and return a conservative gate."""
    active_policy = policy or VisualQAPolicy()
    candidate_path = Path(candidate).expanduser().resolve()
    if not candidate_path.is_file():
        raise FileNotFoundError(f"candidate image not found: {candidate}")
    reference_paths = [Path(path).expanduser().resolve() for path in references or []]
    missing = [str(path) for path in reference_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"reference image not found: {missing[0]}")
    normalized_claims = sorted(
        {str(claim).strip().lower() for claim in claims or [] if str(claim).strip()}
    )
    metadata, deterministic = deterministic_checks(
        candidate_path, policy=active_policy, requirements=requirements
    )
    vision: list[dict[str, Any]] = []
    provider_meta: dict[str, Any] = {
        "provider": None,
        "model": None,
        "usage": {},
        "cost_usd": None,
    }
    skill_metadata: dict[str, Any] | None = None

    fidelity_without_reference = sorted(
        set(normalized_claims) & FIDELITY_CLAIMS if not reference_paths else set()
    )
    for claim in fidelity_without_reference:
        vision.append(
            _finding(
                claim,
                "BLOCK",
                0,
                ["fidelity was claimed but no reference image was supplied"],
                "Attach an authoritative reference before claiming fidelity.",
                source="gate",
            )
        )

    if provider is None:
        vision.append(
            _finding(
                "vision_analysis",
                "BLOCK" if active_policy.block_on_vision_failure else "WARN",
                0,
                ["no configured visual QA provider was available"],
                "Configure a multimodal chat provider and rerun visual QA.",
                source="gate",
            )
        )
    else:
        try:
            arguments = {
                "candidate": candidate_path,
                "references": reference_paths,
                "claims": normalized_claims,
                "context": dict(requirements or {}),
            }
            analyze = getattr(provider, "analyze_with_skill_context", None)
            if callable(analyze):
                skills = await asyncio.to_thread(
                    build_action_skill_context, "marketing", "visual-qa", workspace=workspace,
                )
                skill_metadata = skills.metadata
                result = await analyze(**arguments, skill_context=skills)
            else:
                # Installed adapters retain their existing analyze contract.
                # No skill metadata is claimed if an adapter cannot accept it.
                result = await provider.analyze(**arguments)
            vision.extend(_vision_findings(result))
            provider_meta = {
                "provider": result.provider,
                "model": result.model,
                "usage": result.usage,
                "cost_usd": result.cost_usd,
            }
        except Exception as exc:
            vision.append(
                _finding(
                    "vision_analysis",
                    "BLOCK" if active_policy.block_on_vision_failure else "WARN",
                    0,
                    [f"provider response was unusable: {exc}"],
                    "Rerun with a healthy multimodal provider. Do not approve automatically.",
                    source="gate",
                )
            )

    deterministic_score = _weighted_average(deterministic)
    vision_score = _weighted_average(vision)
    score = (
        deterministic_score * active_policy.deterministic_weight
        + vision_score * active_policy.vision_weight
    )
    findings = deterministic + vision
    verdict = _overall_verdict(score, findings, active_policy)
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "candidate": metadata,
        "references": [str(path) for path in reference_paths],
        "claims": normalized_claims,
        "requirements": dict(requirements or {}),
        "policy": asdict(active_policy),
        "provider": provider_meta,
        "skill_context": skill_metadata,
        "scores": {
            "deterministic": round(deterministic_score, 2),
            "vision": round(vision_score, 2),
            "overall": round(score, 2),
        },
        "verdict": verdict,
        "findings": findings,
        "pass": verdict == "PASS",
    }


def format_markdown_report(report: dict[str, Any]) -> str:
    """Render a durable human-readable companion to the JSON report."""
    candidate = report.get("candidate") or {}
    lines = [
        "# Marketing visual QA",
        "",
        f"- Verdict: **{report.get('verdict', 'BLOCK')}**",
        f"- Score: {((report.get('scores') or {}).get('overall', 0))}/100",
        f"- Candidate: `{candidate.get('path', '')}`",
        f"- Size: {candidate.get('width', 0)}x{candidate.get('height', 0)}",
        f"- Model: `{((report.get('provider') or {}).get('model') or 'unavailable')}`",
        f"- Cost USD: {((report.get('provider') or {}).get('cost_usd'))}",
        "",
        "## Findings",
        "",
    ]
    for finding in report.get("findings") or []:
        lines.append(
            f"### {finding.get('status', 'BLOCK')} - {finding.get('check', 'unknown')}"
        )
        lines.append(f"- Source: {finding.get('source', 'unknown')}")
        lines.append(f"- Score: {finding.get('score', 0)}/100")
        for evidence in finding.get("evidence") or []:
            lines.append(f"- Evidence: {evidence}")
        recommendation = str(finding.get("recommendation") or "").strip()
        if recommendation:
            lines.append(f"- Fix: {recommendation}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_visual_qa_report(
    report: dict[str, Any],
    workspace: str | Path,
    *,
    report_name: str | None = None,
) -> dict[str, str]:
    """Atomically save JSON and Markdown under ``marketing/qa``."""
    root = Path(workspace).expanduser().resolve()
    output = root / "marketing" / "qa"
    stem = report_name or (
        f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}-"
        f"{Path(str((report.get('candidate') or {}).get('path') or 'asset')).stem}"
    )
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-") or "visual-qa"
    json_path = output / f"{safe_stem}.json"
    markdown_path = output / f"{safe_stem}.md"
    atomic_write_text(
        json_path,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(markdown_path, format_markdown_report(report))
    return {"json": str(json_path), "markdown": str(markdown_path)}
