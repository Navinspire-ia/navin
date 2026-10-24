# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Registry and chat-provider adapter for Marketing visual QA.

The adapter deliberately speaks the existing ``LLMProvider.chat_with_retry``
contract. It does not add another vendor SDK or credential store.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

import json_repair

from navin.providers.base import LLMResponse
from navin.providers.image_generation import image_path_to_data_url

if TYPE_CHECKING:
    from navin.agent.skill_routing import ActionSkillContext


class VisualQAProviderError(RuntimeError):
    """Raised when vision analysis cannot produce a trustworthy result."""


@dataclass(frozen=True, slots=True)
class VisualQAProviderResult:
    analysis: dict[str, Any]
    provider: str
    model: str
    usage: dict[str, int]
    cost_usd: float | None = None


class VisualQAProvider(Protocol):
    """Provider-independent vision analysis contract."""

    async def analyze(
        self,
        *,
        candidate: Path,
        references: list[Path],
        claims: list[str],
        context: dict[str, Any],
    ) -> VisualQAProviderResult: ...


VisualQAFactory = Callable[..., VisualQAProvider]
_VISUAL_QA_PROVIDERS: dict[str, VisualQAFactory] = {}


def register_visual_qa_provider(name: str, factory: VisualQAFactory) -> None:
    """Register one adapter factory by stable name."""
    normalized = name.strip().lower().replace("-", "_")
    if not normalized:
        raise ValueError("visual QA provider name cannot be empty")
    _VISUAL_QA_PROVIDERS[normalized] = factory


def get_visual_qa_provider(name: str) -> VisualQAFactory | None:
    return _VISUAL_QA_PROVIDERS.get(name.strip().lower().replace("-", "_"))


def visual_qa_provider_names() -> tuple[str, ...]:
    return tuple(_VISUAL_QA_PROVIDERS)


def visual_qa_credentials_ready(runtime: Any | None) -> bool:
    """Return whether a captured chat runtime can serve a real request."""
    if runtime is None:
        return False
    provider = getattr(runtime, "provider", None)
    model = str(getattr(runtime, "model", "") or "").strip()
    if provider is None or not model:
        return False
    return provider.__class__.__name__ != "UnconfiguredProvider"


def _extract_json(content: str | None) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        raise VisualQAProviderError("vision provider returned empty content")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = json_repair.loads(text)
        except Exception as exc:
            raise VisualQAProviderError("vision provider returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise VisualQAProviderError("vision provider JSON must be an object")
    return parsed


_DIMENSIONS = ("product_fidelity", "logo_fidelity", "text_accuracy", "color_fidelity", "composition")
_STATUSES = {"PASS", "WARN", "BLOCK"}


def normalize_visual_analysis(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize untrusted model JSON without inventing PASSes."""
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, dict):
        raise VisualQAProviderError("vision JSON is missing dimensions")
    normalized: dict[str, Any] = {}
    for name in _DIMENSIONS:
        item = dimensions.get(name)
        if not isinstance(item, dict):
            raise VisualQAProviderError(f"vision JSON is missing {name}")
        raw_status = str(item.get("status") or "").upper()
        if raw_status not in _STATUSES:
            raise VisualQAProviderError(f"vision JSON has invalid status for {name}")
        score = item.get("score")
        if not isinstance(score, int | float) or isinstance(score, bool):
            raise VisualQAProviderError(f"vision JSON has invalid score for {name}")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(entry, str) and entry.strip() for entry in evidence
        ):
            raise VisualQAProviderError(f"vision JSON has no usable evidence for {name}")
        normalized[name] = {
            "status": raw_status,
            "score": max(0.0, min(100.0, float(score))),
            "evidence": [entry.strip() for entry in evidence[:8]],
            "recommendation": str(item.get("recommendation") or "").strip(),
        }
    return {"dimensions": normalized}


VIDEO_DIMENSIONS = (
    "composition",
    "text_legibility",
    "brand_consistency",
    "black_bars",
    "secrets_visible",
    "loading_or_blank",
    "pacing",
)

_VIDEO_CONTRACT = (
    "You are a strict video QA inspector for a premium product demo video. You see sampled "
    "frames (each labelled with its frame number, timestamp in seconds and scene index) and "
    "a contact sheet of the whole cut. Frames are downscaled to phone size on purpose: judge "
    "text legibility as a viewer on a mobile screen would. Rubric: composition (framing, "
    "hierarchy, safe areas); text_legibility (all on-screen text readable at mobile size); "
    "brand_consistency (colors, logo and type match the brand context); black_bars (no "
    "letterbox or pillarbox bars, no empty borders); secrets_visible (BLOCK if any password, "
    "API key, token, private email or credential is readable); loading_or_blank (no loading "
    "spinners, skeleton screens or blank/white frames); pacing (one idea per scene, hook in "
    "the first 2 seconds, no scene that drags). Return JSON only with this exact shape: "
    '{"dimensions":{"composition":{"status":"PASS|WARN|BLOCK","score":0,'
    '"evidence":["visible fact"],"recommendation":""},"text_legibility":{...},'
    '"brand_consistency":{...},"black_bars":{...},"secrets_visible":{...},'
    '"loading_or_blank":{...},"pacing":{...}},'
    '"issues":[{"frame":1,"check":"text_legibility","status":"WARN|BLOCK",'
    '"evidence":"visible fact","recommendation":""}]}. '
    "List one issue per affected frame so the editor can re-render only that scene. "
    "Every dimension needs concrete visible evidence; never invent a PASS."
)


def normalize_video_analysis(value: dict[str, Any], *, frame_count: int) -> dict[str, Any]:
    """Validate untrusted model JSON for a video rubric.

    Dimensions are mandatory like the still rubric; ``issues`` are optional and
    any issue pointing at a frame that was not sent is dropped.
    """
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, dict):
        raise VisualQAProviderError("vision JSON is missing dimensions")
    normalized: dict[str, Any] = {}
    for name in VIDEO_DIMENSIONS:
        item = dimensions.get(name)
        if not isinstance(item, dict):
            raise VisualQAProviderError(f"vision JSON is missing {name}")
        raw_status = str(item.get("status") or "").upper()
        if raw_status not in _STATUSES:
            raise VisualQAProviderError(f"vision JSON has invalid status for {name}")
        score = item.get("score")
        if not isinstance(score, int | float) or isinstance(score, bool):
            raise VisualQAProviderError(f"vision JSON has invalid score for {name}")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(entry, str) and entry.strip() for entry in evidence
        ):
            raise VisualQAProviderError(f"vision JSON has no usable evidence for {name}")
        normalized[name] = {
            "status": raw_status,
            "score": max(0.0, min(100.0, float(score))),
            "evidence": [entry.strip() for entry in evidence[:8]],
            "recommendation": str(item.get("recommendation") or "").strip(),
        }
    issues: list[dict[str, Any]] = []
    raw_issues = value.get("issues")
    for item in raw_issues if isinstance(raw_issues, list) else []:
        if not isinstance(item, dict):
            continue
        frame = item.get("frame")
        check = str(item.get("check") or "").strip()
        status = str(item.get("status") or "").upper()
        evidence = str(item.get("evidence") or "").strip()
        if (
            not isinstance(frame, int)
            or isinstance(frame, bool)
            or not 1 <= frame <= frame_count
            or check not in VIDEO_DIMENSIONS
            or status not in {"WARN", "BLOCK"}
            or not evidence
        ):
            continue
        issues.append(
            {
                "frame": frame,
                "check": check,
                "status": status,
                "evidence": evidence[:300],
                "recommendation": str(item.get("recommendation") or "").strip()[:300],
            }
        )
    return {"dimensions": normalized, "issues": issues[:64]}


def _response_cost(response: LLMResponse) -> float | None:
    usage = response.usage if isinstance(response.usage, dict) else {}
    for key in ("cost_usd", "total_cost", "cost"):
        value = usage.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    return None


class ChatVisualQAProvider:
    """Vision adapter over a configured Navin chat provider."""

    def __init__(
        self,
        *,
        runtime: Any,
        provider_name: str = "chat",
        max_tokens: int = 1800,
    ) -> None:
        if not visual_qa_credentials_ready(runtime):
            raise VisualQAProviderError("visual QA provider credentials are not ready")
        self.runtime = runtime
        self.provider_name = provider_name
        self.max_tokens = max_tokens

    async def analyze(
        self,
        *,
        candidate: Path,
        references: list[Path],
        claims: list[str],
        context: dict[str, Any],
    ) -> VisualQAProviderResult:
        return await self.analyze_with_skill_context(
            candidate=candidate, references=references, claims=claims,
            context=context, skill_context=None,
        )

    async def analyze_with_skill_context(
        self,
        *,
        candidate: Path,
        references: list[Path],
        claims: list[str],
        context: dict[str, Any],
        skill_context: ActionSkillContext | None,
    ) -> VisualQAProviderResult:
        """The calling desk may supply its loaded playbooks for this one call."""
        contract = (
            "You are a strict visual QA inspector. Compare the candidate with the "
            "reference images when present. Never infer fidelity without a reference. "
            "Return JSON only with this exact shape: "
            '{"dimensions":{"product_fidelity":{"status":"PASS|WARN|BLOCK",'
            '"score":0,"evidence":["visible fact"],"recommendation":""},'
            '"logo_fidelity":{...},"text_accuracy":{...},'
            '"color_fidelity":{...},"composition":{...}}}. '
            "Every dimension needs concrete visible evidence. BLOCK a claimed fidelity "
            "dimension when no adequate reference exists. "
        )
        prompt = (
            contract +
            f"Claims: {json.dumps(claims, ensure_ascii=True)}. "
            f"Context: {json.dumps(context, ensure_ascii=True, default=str)}."
        )
        content: list[dict[str, Any]] = [
            {"type": "text", "text": "Candidate image"},
            {
                "type": "image_url",
                "image_url": {"url": image_path_to_data_url(candidate)},
            },
        ]
        for index, reference in enumerate(references, start=1):
            content.extend(
                [
                    {"type": "text", "text": f"Reference image {index}"},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_path_to_data_url(reference)},
                    },
                ]
            )
        content.append({"type": "text", "text": prompt})
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        if skill_context is not None:
            messages.insert(0, {"role": "system", "content": skill_context.augment_system(contract)})
        response = await self.runtime.provider.chat_with_retry(
            messages=messages,
            model=self.runtime.model,
            max_tokens=self.max_tokens,
            temperature=0.0,
        )
        if response.finish_reason == "error":
            raise VisualQAProviderError(response.content or "vision provider failed")
        analysis = normalize_visual_analysis(_extract_json(response.content))
        usage = {
            str(key): int(value)
            for key, value in (response.usage or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        return VisualQAProviderResult(
            analysis=analysis,
            provider=self.provider_name,
            model=self.runtime.model,
            usage=usage,
            cost_usd=_response_cost(response),
        )

    async def analyze_video(
        self,
        *,
        frames: list[dict[str, Any]],
        contact_sheet: Path | None,
        context: dict[str, Any],
        skill_context: ActionSkillContext | None = None,
    ) -> VisualQAProviderResult:
        """Rubric review of sampled video frames.

        Each frame dict carries ``path``, ``timestamp`` and ``scene_index``;
        the frame number the model cites is its 1-based position here.
        """
        if not frames:
            raise VisualQAProviderError("video QA needs at least one sampled frame")
        content: list[dict[str, Any]] = []
        if contact_sheet is not None:
            content.extend(
                [
                    {"type": "text", "text": "Contact sheet of the whole cut"},
                    {"type": "image_url", "image_url": {"url": image_path_to_data_url(contact_sheet)}},
                ]
            )
        for number, frame in enumerate(frames, start=1):
            content.extend(
                [
                    {
                        "type": "text",
                        "text": (
                            f"Frame {number} at {float(frame.get('timestamp') or 0):.2f}s, "
                            f"scene {frame.get('scene_index')}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_path_to_data_url(Path(frame["path"]))},
                    },
                ]
            )
        content.append(
            {
                "type": "text",
                "text": _VIDEO_CONTRACT
                + f" Context: {json.dumps(context, ensure_ascii=True, default=str)}.",
            }
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
        if skill_context is not None:
            messages.insert(0, {"role": "system", "content": skill_context.augment_system(_VIDEO_CONTRACT)})
        response = await self.runtime.provider.chat_with_retry(
            messages=messages,
            model=self.runtime.model,
            max_tokens=max(self.max_tokens, 2400),
            temperature=0.0,
        )
        if response.finish_reason == "error":
            raise VisualQAProviderError(response.content or "vision provider failed")
        analysis = normalize_video_analysis(_extract_json(response.content), frame_count=len(frames))
        usage = {
            str(key): int(value)
            for key, value in (response.usage or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        return VisualQAProviderResult(
            analysis=analysis,
            provider=self.provider_name,
            model=self.runtime.model,
            usage=usage,
            cost_usd=_response_cost(response),
        )


register_visual_qa_provider("chat", ChatVisualQAProvider)
